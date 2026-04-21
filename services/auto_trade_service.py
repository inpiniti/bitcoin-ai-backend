"""
자동매매 딥러닝 플로우 오케스트레이터

KIS 인증 정보 및 매매 조건은 Supabase 의 automation_settings 테이블에서 로드합니다.
(클라이언트의 AutomationSettingsPanel 에서 저장된 값, is_active=true 인 설정 사용)

실행 순서:
    1. automation_settings 로드 (KIS 키, 모델 ID, 매매 조건 등)
    2. KIS 토큰 발급
    3. 딥러닝 모델 로드
    4. 보유 종목 조회
    5. 매수 분석 대상 종목 로드
    6. 주가 데이터 + 지표 계산
    7. 딥러닝 매수 신호 스캔
    8. 딥러닝 매도 신호 스캔
    9. 매도 주문 실행 (선행)
   10. 매수 주문 실행
   11. 로그 저장
"""
import asyncio
import logging
from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

import httpx

from services import kis_service, indicator_service, dl_model_service
from services.supabase_service import (
    load_all_automation_settings_active,
    load_automation_settings_active,
    save_auto_trade_log,
)
from services.yahoo_service import fetch_stock_history_for_trade

logger = logging.getLogger("auto_trade_service")

CHUNK_SIZE = 5


def _calc_rebalance_qty(total_qty: int, ratio: float = 0.10) -> int:
    """Rebalance sell quantity (at least 1 share when position exists)."""
    if total_qty <= 0:
        return 0
    return max(1, int(total_qty * ratio))


# ─────────────────────────────────────────────
# 티커 그룹 로더
# ─────────────────────────────────────────────

async def _fetch_wikipedia_index(url: str) -> list[dict]:
    """Wikipedia 지수 구성종목 테이블 파싱 (id='constituents')"""
    from bs4 import BeautifulSoup
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", {"id": "constituents"})
    if not table:
        return []
    stocks = []
    seen: set[str] = set()
    for row in table.select("tbody tr"):
        tds = row.find_all("td")
        if not tds:
            continue
        ticker = tds[0].get_text(strip=True).replace(".", "-")
        name = tds[1].get_text(strip=True) if len(tds) > 1 else ticker
        if ticker and ticker not in seen:
            seen.add(ticker)
            stocks.append({"ticker": ticker, "name": name})
    return stocks


async def _load_target_tickers(target_group: str, holdings: list[dict]) -> list[dict]:
    if target_group == "myholdings":
        return [{"ticker": h["pdno"], "name": h.get("prdt_name", "")} for h in holdings]

    if target_group == "sp500":
        return await _fetch_wikipedia_index(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        )

    if target_group in ("qqq", "nasdaq100"):
        return await _fetch_wikipedia_index(
            "https://en.wikipedia.org/wiki/Nasdaq-100"
        )

    if target_group == "usall":
        # nasdaqtrader.com 공개 FTP 파일로 NASDAQ + NYSE/AMEX 전체 조회 (~6,000+)
        headers = {"User-Agent": "Mozilla/5.0"}
        async with httpx.AsyncClient(timeout=30) as client:
            nasdaq_res, other_res = await asyncio.gather(
                client.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", headers=headers),
                client.get("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", headers=headers),
            )

        stocks: list[dict] = []

        # NASDAQ: Symbol|Security Name|...|Test Issue(3)|...|ETF(6)|...
        if nasdaq_res.status_code == 200:
            for line in nasdaq_res.text.split("\n")[1:]:
                cols = line.split("|")
                if len(cols) < 7:
                    continue
                ticker = cols[0].strip()
                name = cols[1].strip()
                if ticker and cols[3].strip() != "Y" and cols[6].strip() != "Y" and "File Creation" not in ticker and len(ticker) <= 5:
                    stocks.append({"ticker": ticker, "name": name})

        # NYSE/AMEX: ACT Symbol|Security Name|Exchange|...|ETF(4)|...|Test Issue(6)|...
        if other_res.status_code == 200:
            for line in other_res.text.split("\n")[1:]:
                cols = line.split("|")
                if len(cols) < 7:
                    continue
                ticker = cols[0].strip()
                name = cols[1].strip()
                if ticker and cols[6].strip() != "Y" and cols[4].strip() != "Y" and "File Creation" not in ticker and len(ticker) <= 5:
                    stocks.append({"ticker": ticker, "name": name})

        logger.info(f"[usall] 전체 {len(stocks)}종목 로드")
        return stocks

    if target_group == "superinvestor":
        import re
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://www.dataroma.com/m/holdings.php?m=ALL",
                headers={"User-Agent": "Mozilla/5.0"},
            )
        tickers = list(dict.fromkeys(re.findall(r'symbol=([A-Z]{1,5})"', resp.text)))[:100]
        return [{"ticker": t, "name": t} for t in tickers if t]

    logger.warning(f"[_load_target_tickers] 미구현 그룹: {target_group!r}, 빈 목록 반환")
    return []


# ─────────────────────────────────────────────
# 핵심 플로우
# ─────────────────────────────────────────────

def _log_dst_info():
    """현재 DST 상태를 로그로 기록 (참고용)"""
    now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
    is_dst = bool(now_et.dst())
    season = "EDT(써머타임)" if is_dst else "EST(겨울)"
    logger.info(f"실행 시각: {now_et.strftime('%Y-%m-%d %H:%M')} ET ({season})")


async def run_auto_trade_dl(is_test: bool = False) -> list[dict]:
    """
    is_active=true 인 automation_settings 전체를 순차 실행하고
    각 실행 결과 summary 목록을 반환한다.
    """
    _log_dst_info()

    cfgs = await load_all_automation_settings_active()
    if not cfgs:
        raise RuntimeError(
            "is_active=true 인 automation_settings 가 없습니다. "
            "클라이언트 자동매매 설정 패널에서 설정을 활성화해주세요."
        )

    results = []
    for cfg in cfgs:
        cfg_name = cfg.get("name", cfg.get("id", "unknown"))
        logger.info(f"[AutoTrade] 설정 실행: {cfg_name}")
        try:
            summary = await _run_single_cfg(cfg, is_test=is_test)
            results.append(summary)
        except Exception as e:
            logger.exception(f"[AutoTrade] 설정 '{cfg_name}' 실행 오류: {e}")
            results.append({"cfg_name": cfg_name, "error": str(e)})

    return results


async def _run_single_cfg(cfg: dict, is_test: bool = False) -> dict:
    """단일 automation_settings 행에 대해 자동매매 플로우를 실행한다."""
    logs: list[str] = []

    def log(msg: str):
        logger.info(msg)
        logs.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")

    today_str = date.today().isoformat()
    mode = "[TEST]" if is_test else ""

    try:
        # KIS 인증 정보 추출
        appkey = cfg.get("kis_appkey", "").strip()
        appsecret = cfg.get("kis_secret", "").strip()
        kis_account = cfg.get("kis_account", "").strip()
        if not all([appkey, appsecret, kis_account]):
            raise RuntimeError("automation_settings 에 KIS 인증 정보(kis_appkey, kis_secret, kis_account)가 없습니다.")

        account_no, account_code = kis_service.parse_account(kis_account)

        # 매매 조건 추출
        model_id = cfg.get("ai_model_key", "").strip()
        if not model_id:
            raise RuntimeError("automation_settings 에 ai_model_key(딥러닝 모델 ID)가 설정되어 있지 않습니다.")

        target_group = cfg.get("ticker_group_key", "myholdings")
        buy_threshold = float(cfg.get("buy_condition", 60)) / 100   # 60 → 0.6
        sell_threshold = float(cfg.get("sell_condition", 20)) / 100      # 확률 조건: buy_prob 이하면 매도
        sell_profit_threshold = float(cfg.get("sell_profit_condition", 20))  # 수익률 조건: X% 이상이면 익절
        prevent_loss_sell = bool(cfg.get("prevent_loss_sell", False))        # 손실 중엔 매도 금지
        allow_loss_sell_for_buy_raw = cfg.get("allow_loss_sell_for_buy", None)
        if allow_loss_sell_for_buy_raw is None:
            allow_loss_sell_for_buy = not prevent_loss_sell
        else:
            allow_loss_sell_for_buy = bool(allow_loss_sell_for_buy_raw)
        trade_enabled = bool(cfg.get("trade_enabled", False))
        if not trade_enabled:
            log("[모의매매] trade_enabled=false → 실제 주문 없이 로그만 기록합니다.")

        log(
            f"설정 로드 완료 | 그룹={target_group} | 모델={model_id} | "
            f"buy>={buy_threshold} | sell확률<={sell_threshold} | sell수익>={sell_profit_threshold}% | "
            f"손실매도방지={prevent_loss_sell} | 손실10%매도(매수자금)={allow_loss_sell_for_buy}"
        )

        # ── 2. 딥러닝 모델 로드 ───────────────────────
        log(f"모델 로드 중: {model_id}")
        meta, model = await dl_model_service.get_model(model_id)
        feature_count: int = int(meta.get("feature_count", 0))
        log(f"모델 로드 완료 | feature_count={feature_count} | accuracy={meta.get('accuracy', 'N/A')}")

        # ── 3. 보유 종목 조회 ─────────────────────────
        log("보유 종목 조회 중...")
        balance_res = await kis_service.get_overseas_balance(appkey, appsecret, account_no, account_code)
        if not balance_res["success"]:
            raise RuntimeError(f"잔고 조회 실패: {balance_res['error']}")

        holdings = [h for h in balance_res["holdings"] if int(float(h.get("ccld_qty_smtl1", 0))) > 0]
        holding_tickers = {h["pdno"] for h in holdings}
        # 보유 종목별 수익률 및 단가 맵
        profit_rate_map = {
            h["pdno"]: float(h.get("evlu_pfls_rt1", 0) or 0)
            for h in holdings
        }
        # 평균단가 vs 현재가 (손실 여부 판단용)
        price_map = {
            h["pdno"]: {
                "avg": float(h.get("avg_unpr3", 0) or 0),
                "current": float(h.get("ovrs_now_pric1", 0) or 0),
            }
            for h in holdings
        }
        log(f"보유 종목: {len(holdings)}개")

        # ── 4. 매수 분석 대상 로드 ────────────────────
        log(f"매수 분석 대상 로드 중 ({target_group})...")
        target_stocks = await _load_target_tickers(target_group, holdings)
        log(f"분석 대상: {len(target_stocks)}개")

        # ── 데이터 캐시 ───────────────────────────────
        data_cache: dict[str, list[dict]] = {}

        async def load_ticker_data(ticker: str) -> list[dict] | None:
            if ticker in data_cache:
                return data_cache[ticker]
            try:
                candles = await fetch_stock_history_for_trade(ticker)
                if candles and len(candles) >= 31:
                    enriched = indicator_service.add_derived_data(candles)
                    data_cache[ticker] = enriched
                    return enriched
            except Exception as e:
                logger.warning(f"[{ticker}] 데이터 로드 실패: {e}")
            return None

        def get_feature_matrix(candles: list[dict]) -> list[list[float]]:
            return indicator_service.extract_features_for_model(candles)

        # ── 5 & 6. 매수 신호 스캔 ─────────────────────
        log("매수 신호 스캔 중...")
        buy_list: list[dict] = []
        all_buy_candidates: list[dict] = []  # 확률 미달 포함 전체 후보 (리포트용)

        for i in range(0, len(target_stocks), CHUNK_SIZE):
            chunk = target_stocks[i : i + CHUNK_SIZE]
            results = await asyncio.gather(*[load_ticker_data(s["ticker"]) for s in chunk], return_exceptions=True)

            for stock, candles in zip(chunk, results):
                ticker = stock["ticker"]
                if isinstance(candles, Exception) or not candles:
                    continue
                if ticker in holding_tickers:
                    continue
                try:
                    buy_prob, _ = dl_model_service.predict(model, meta, get_feature_matrix(candles))
                    logger.info(f"  [매수스캔] {ticker} buy_prob={buy_prob:.1%}")
                    candidate = {**stock, "buy_prob": round(buy_prob, 4)}
                    all_buy_candidates.append(candidate)
                    if buy_prob >= buy_threshold:
                        buy_list.append(candidate)
                        log(f"  BUY 신호: {ticker} (확률={buy_prob:.1%})")
                except Exception as e:
                    logger.warning(f"[{ticker}] 예측 실패: {e}")

        log(f"매수 후보: {len(buy_list)}개")
        buy_tickers = {s["ticker"] for s in buy_list}

        # ── 7. 매도 신호 스캔 ─────────────────────────
        log("매도 신호 스캔 중 (보유 종목)...")
        sell_list: list[dict] = []
        # 리포트용: 전체 보유종목의 확률/손익 기록
        sell_details: list[dict] = []

        for holding in holdings:
            ticker = holding["pdno"]
            profit_rate = profit_rate_map.get(ticker, 0.0)
            detail = {"ticker": ticker, "profit_rate": profit_rate, "buy_prob": None, "triggered": False, "skip_reason": None}

            if ticker in buy_tickers:
                detail["skip_reason"] = "매수신호"
                sell_details.append(detail)
                continue

            candles = await load_ticker_data(ticker)
            if not candles:
                detail["skip_reason"] = "데이터없음"
                sell_details.append(detail)
                continue

            try:
                buy_prob, _ = dl_model_service.predict(model, meta, get_feature_matrix(candles))
                detail["buy_prob"] = round(buy_prob, 4)
                logger.info(f"  [매도스캔] {ticker} buy_prob={buy_prob:.1%} profit={profit_rate:.2f}%")

                prob_signal = buy_prob <= sell_threshold
                profit_signal = profit_rate >= sell_profit_threshold

                # 손실 중 매도 방지
                if prevent_loss_sell:
                    prices = price_map.get(ticker, {})
                    avg_price = prices.get("avg", 0)
                    cur_price = prices.get("current", 0)
                    if cur_price > 0 and avg_price > 0 and cur_price < avg_price:
                        # 매수 후보가 있을 때만 손실 종목의 10%를 부분 매도해 매수 자금을 마련한다.
                        if allow_loss_sell_for_buy and buy_list:
                            total_qty = int(float(holding.get("ccld_qty_smtl1", 0)))
                            rebalance_qty = _calc_rebalance_qty(total_qty)
                            if rebalance_qty > 0:
                                sell_list.append({
                                    "ticker": ticker,
                                    "name": holding.get("prdt_name", ""),
                                    "qty": rebalance_qty,
                                    "sell_prob": round(1.0 - buy_prob, 4),
                                    "profit_rate": profit_rate,
                                    "sell_reason": "매수자금확보(손실종목10%매도)",
                                })
                                detail["triggered"] = True
                                log(
                                    f"  SELL 신호: {ticker} (매수자금확보: 손실구간 10% 부분매도, "
                                    f"현재가={cur_price} < 평균단가={avg_price})"
                                )
                                sell_details.append(detail)
                                continue

                        logger.info(f"  [{ticker}] 손실매도방지 (현재가={cur_price} < 평균단가={avg_price}) → 스킵")
                        detail["skip_reason"] = "손실매도방지"
                        sell_details.append(detail)
                        continue

                if prob_signal or profit_signal:
                    reason = []
                    if prob_signal:
                        reason.append(f"확률={buy_prob:.1%}≤{sell_threshold:.1%}")
                    if profit_signal:
                        reason.append(f"수익률={profit_rate:.2f}%≥{sell_profit_threshold}%")
                    sell_list.append({
                        "ticker": ticker,
                        "name": holding.get("prdt_name", ""),
                        "qty": int(float(holding.get("ccld_qty_smtl1", 0))),
                        "sell_prob": round(1.0 - buy_prob, 4),
                        "profit_rate": profit_rate,
                        "sell_reason": " | ".join(reason),
                    })
                    detail["triggered"] = True
                    log(f"  SELL 신호: {ticker} ({' | '.join(reason)})")
            except Exception as e:
                logger.warning(f"[{ticker}] 예측 실패: {e}")
                detail["skip_reason"] = "예측실패"

            sell_details.append(detail)

        log(f"매도 후보: {len(sell_list)}개")

        # ── 8. 매도 주문 실행 (선행) ──────────────────
        sell_results = []
        for item in sell_list:
            ticker = item["ticker"]
            price_res = await kis_service.get_current_price_with_exchange_search(appkey, appsecret, ticker)
            if not price_res["success"]:
                log(f"  {ticker} 현재가 조회 실패, 건너뜀")
                continue
            price = round(float(price_res["price"]), 2)
            exchange = price_res.get("exchange", "NAS")
            qty = item["qty"]
            if trade_enabled:
                result = await kis_service.sell_overseas_stock(appkey, appsecret, account_no, account_code, ticker, qty, price, exchange)
                sell_results.append({"ticker": ticker, "qty": qty, "price": price, "result": result})
                log(f"  [실제매매] 매도: {ticker} {qty}주 @ ${price:.2f} → {'성공' if result['success'] else '실패'}: {result.get('order_no') or result.get('error')}")
            else:
                sell_results.append({"ticker": ticker, "qty": qty, "price": price, "simulated": True})
                log(f"  [모의매매] 매도 예정: {ticker} {qty}주 @ ${price:.2f} (주문 미실행)")

        # ── 9. 매수 주문 실행 ─────────────────────────
        # 사용 가능한 달러 현금 조회 (매도 선행 결과를 반영)
        available_cash_before = float(balance_res.get("usd_available", 0.0) or 0.0)
        realized_sell_proceeds = sum(
            float(r.get("qty", 0) or 0) * float(r.get("price", 0) or 0)
            for r in sell_results
            if r.get("simulated") or r.get("result", {}).get("success")
        )

        available_cash = available_cash_before
        if sell_results:
            if trade_enabled:
                refreshed_balance = await kis_service.get_overseas_balance(appkey, appsecret, account_no, account_code)
                if refreshed_balance.get("success"):
                    available_cash = float(refreshed_balance.get("usd_available", 0.0) or 0.0)
                    log(f"매도 체결 반영 후 매수 가능 현금 재조회: ${available_cash:.2f}")
                else:
                    available_cash = available_cash_before + realized_sell_proceeds
                    log("매수 가능 현금 재조회 실패 → 직전 현금 + 매도대금 추정치로 계산")
            else:
                available_cash = available_cash_before + realized_sell_proceeds
                log(f"모의매매 기준 매도대금 반영: +${realized_sell_proceeds:.2f}")

        log(f"매수 가능 현금 (USD): ${available_cash:.2f}")

        buy_count = len(buy_list)
        per_ticker_amount = (available_cash / buy_count) if buy_count > 0 else 0
        # 리스크 관리: 종목당 최대 10% 캡 적용 (단일 종목 집중 방지)
        max_per_ticker = available_cash * 0.10
        per_ticker_amount = min(per_ticker_amount, max_per_ticker)
        log(f"티커당 배분: ${per_ticker_amount:.2f} ({buy_count}개 균등 분배, 최대 10% 캡 적용)")

        buy_results = []
        for item in buy_list:
            ticker = item["ticker"]
            price_res = await kis_service.get_current_price_with_exchange_search(appkey, appsecret, ticker)
            if not price_res["success"]:
                log(f"  {ticker} 현재가 조회 실패, 건너뜀")
                continue
            price = round(float(price_res["price"]), 2)
            exchange = price_res.get("exchange", "NAS")
            qty = int(per_ticker_amount / price) if price > 0 else 0
            if qty == 0:
                log(f"  [스킵] {ticker}: 수량 부족 (배분 ${per_ticker_amount:.2f} < 주가 ${price:.2f}/주)")
                continue
            if trade_enabled:
                result = await kis_service.buy_overseas_stock(appkey, appsecret, account_no, account_code, ticker, qty, price, exchange)
                buy_results.append({"ticker": ticker, "qty": qty, "price": price, "result": result})
                log(f"  [실제매매] 매수: {ticker} {qty}주 @ ${price:.2f} (배분=${per_ticker_amount:.2f}) → {'성공' if result['success'] else '실패'}: {result.get('order_no') or result.get('error')}")
            else:
                buy_results.append({"ticker": ticker, "qty": qty, "price": price, "simulated": True})
                log(f"  [모의매매] 매수 예정: {ticker} {qty}주 @ ${price:.2f} (배분=${per_ticker_amount:.2f}, 주문 미실행)")

        # ── 10. 로그 저장 ─────────────────────────────
        buy_order_count = len([r for r in buy_results if r.get("simulated") or r.get("result", {}).get("success")])
        sell_order_count = len([r for r in sell_results if r.get("simulated") or r.get("result", {}).get("success")])

        # Supabase 저장용
        supabase_log = {
            "date": today_str,
            "is_test": is_test,
            "setting_id": cfg.get("id"),
            "model_id": model_id,
            "target_group": target_group,
            "setting_name": cfg.get("name", ""),
            "holdings_count": len(holdings),
            "buy_signals": len(buy_list),
            "sell_signals": len(sell_list),
            "buy_orders": buy_order_count,
            "sell_orders": sell_order_count,
            "logs": logs,
        }
        await save_auto_trade_log(supabase_log)
        log(f"{mode} 자동매매 완료")

        # 카카오 리포트용 (상세 데이터 포함)
        summary = {
            **supabase_log,
            "sell_details": sell_details,
            "buy_details": sorted(all_buy_candidates, key=lambda x: x.get("buy_prob", 0), reverse=True),
            "sell_threshold": sell_threshold,
            "sell_profit_threshold": sell_profit_threshold,
            "buy_threshold": buy_threshold,
        }

        # ── 11. TOP20 종목 DB 저장 (+ TimesFM + RL 방향 신호) ────────
        try:
            from services.supabase_service import save_top_tickers_log
            from services import timesfm_service, rl_service as _rl_service

            top20_raw = sorted(all_buy_candidates, key=lambda x: x.get("buy_prob", 0), reverse=True)[:20]

            # RL 모델 로드 (설정된 경우)
            rl_model_id = cfg.get("rl_model_key", "") or ""
            rl_ppo_model = None
            rl_stage = 6
            if rl_model_id:
                try:
                    rl_model_record, rl_ppo_model = await _rl_service.load_rl_model(rl_model_id)
                    rl_stage = rl_model_record.get("model_json", {}).get("stage") or rl_model_record.get("stage") or 6
                    log(f"[RL] 모델 로드 완료: stage={rl_stage}")
                except Exception as e:
                    logger.warning(f"[RL] 모델 로드 실패 (계속 진행): {e}")

            # TimesFM + RL 예측 병렬 처리
            async def _ticker_signals(stock: dict) -> tuple:
                candles = data_cache.get(stock["ticker"])

                tf_signal = None
                if candles:
                    closes = [c["close"] for c in candles if c.get("close") is not None]
                    tf_signal = await asyncio.to_thread(timesfm_service.predict_direction, closes)

                rl_sig = None
                if rl_ppo_model and candles:
                    try:
                        rl_sig = await asyncio.to_thread(
                            _rl_service.get_latest_signal_sync, rl_ppo_model, candles, rl_stage
                        )
                    except Exception as e:
                        logger.warning(f"[RL] {stock['ticker']} 예측 실패: {e}")

                return tf_signal, rl_sig

            signal_results = await asyncio.gather(
                *[_ticker_signals(s) for s in top20_raw],
                return_exceptions=True,
            )

            top20_data = [
                {
                    "rank": i + 1,
                    "ticker": t["ticker"],
                    "name": t.get("name", ""),
                    "buy_prob": t["buy_prob"],
                    "timesfm_signal": res[0] if not isinstance(res, Exception) else None,
                    "rl_signal": res[1] if not isinstance(res, Exception) else None,
                }
                for i, (t, res) in enumerate(zip(top20_raw, signal_results))
            ]
            await save_top_tickers_log({
                "trade_date": today_str,
                "setting_id": cfg.get("id"),
                "setting_name": cfg.get("name", ""),
                "target_group": target_group,
                "tickers": top20_data,
                "buy_threshold": buy_threshold,
                "total_scanned": len(all_buy_candidates),
                "rl_model_key": rl_model_id or None,
            })
            rl_note = " + RL 신호" if rl_ppo_model else ""
            log(f"[TopTickers] TOP{len(top20_data)} 저장 완료 (TimesFM{rl_note} 포함)")
        except Exception as e:
            logger.warning(f"[TopTickers] 저장 실패 (매매 결과에는 영향 없음): {e}")

        # ── 12. 카카오 리포트 전송 ────────────────────
        try:
            from services.kakao_service import send_trade_report, build_trade_report_parts
            report_parts = build_trade_report_parts(summary, mode)
            sent = await send_trade_report(cfg, report_parts)
            if sent:
                log("[Kakao] 매매 리포트 전송 완료")
        except Exception as e:
            logger.warning(f"[Kakao] 리포트 전송 실패 (매매 결과에는 영향 없음): {e}")

        return summary

    except Exception as e:
        error_msg = f"자동매매 오류: {e}"
        logger.exception(error_msg)
        logs.append(error_msg)
        try:
            await save_auto_trade_log({"date": today_str, "is_test": is_test, "setting_id": cfg.get("id"), "setting_name": cfg.get("name", ""), "error": str(e), "logs": logs})
        except Exception:
            pass
        raise
