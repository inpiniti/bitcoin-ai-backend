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

# 실시간 주문 실패(거부) 시 같은 종목 재시도를 잠시 차단 (요청/로그 폭주 방지)
# 키: trade_id, 값: 마지막 실패 시각(UTC)
_realtime_failure_cooldown: dict[str, datetime] = {}
_REALTIME_FAILURE_COOLDOWN_SECONDS = 60

# gap 신호가 틱마다 연속 발생할 때 KIS 조회(미체결/잔고) 폭주를 막는 스로틀
# 키: trade_id, 값: 마지막 처리 시각(UTC)
_realtime_check_throttle: dict[str, datetime] = {}
_REALTIME_CHECK_THROTTLE_SECONDS = 5

# 미체결 주문을 취소 처리하기까지의 대기 시간 (이후 재주문 가능 상태로 복귀)
_REALTIME_PENDING_CANCEL_SECONDS = 600  # 10분


def _resolve_order_price(side: str, current_price: float, ask: float, bid: float) -> float:
    """즉시체결 유도용 주문가: 매수=매도호가(ask), 매도=매수호가(bid).
    호가가 비어있으면(0) 현재가로 폴백."""
    if side == 'buy':
        chosen = ask if ask and ask > 0 else current_price
    elif side == 'sell':
        chosen = bid if bid and bid > 0 else current_price
    else:
        chosen = current_price
    return round(chosen, 2) if chosen and chosen > 0 else round(current_price, 2)


def _norm_ticker(t: str) -> str:
    """미체결 pdno와 DB ticker 비교용 정규화 (점/하이픈/슬래시 제거, 대문자)."""
    return str(t or "").upper().replace(".", "").replace("-", "").replace("/", "")


def _parse_kis_order_dt(ord_dt: str, ord_tmd: str) -> datetime | None:
    """미체결내역 ord_dt(YYYYMMDD)+ord_tmd(HHMMSS, 한국시간)를 UTC datetime으로 변환."""
    try:
        dt = datetime.strptime(f"{(ord_dt or '').strip()}{(ord_tmd or '').strip()}", "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=ZoneInfo("Asia/Seoul")).astimezone(timezone.utc)
    except Exception:
        return None


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


def _parse_account_no(account_no: str) -> tuple[str, str]:
    """계좌번호 → (CANO 8자, ACNT_PRDT_CD 2자) 분리"""
    clean = (account_no or "").replace("-", "").replace(" ", "")
    if len(clean) < 10:
        raise ValueError(f"계좌번호 형식 오류: {account_no}")
    return clean[:8], clean[8:10]


async def execute_realtime_order(trade_id: str, order_data: dict, supabase_client, user_id: str | None = None):
    """실시간 감지 주문 실행 (실거래 안전 설계).

    핵심 원칙:
      - '주문 접수(rt_cd=0) ≠ 체결'. 주문 접수만으로 base_price/quantity를 갱신하지 않는다.
      - 미체결내역(inquire-nccs)으로 내 주문 상태를 확인한다:
          * 해당 종목 미체결 주문이 있으면 추가주문 금지(중복주문 방지).
          * 미체결이 10분 이상 경과하면 정정취소 API로 취소 → 재주문 가능 상태로 복귀.
      - 미체결이 없으면(=직전 주문이 체결 또는 취소 완료) KIS 잔고를 다시 읽어 quantity를
        실제 보유로 동기화하고, 보유가 바뀐 경우(=체결 발생)에만 base_price를 현재가로 갱신.
      - 신규 주문은 즉시체결 유도를 위해 매수=매도호가/매도=매수호가 지정가로 넣는다.

    Args:
        trade_id: realtime_trading.id
        order_data: handle_price_detection에서 전달되는 dict
        supabase_client: routers/realtime.py에서 만든 sync supabase 클라이언트
    """
    supabase = supabase_client

    ticker = order_data.get('ticker')
    market = order_data.get('market', 'NAS')
    side = order_data.get('side')
    qty = int(order_data.get('quantity', 0) or 0)
    current_price = float(order_data.get('price', 0) or 0)
    ask = float(order_data.get('ask', 0) or 0)
    bid = float(order_data.get('bid', 0) or 0)
    action = order_data.get('action')
    base_price_before = order_data.get('base_price_before')
    price_rate = order_data.get('price_rate')
    current_quantity = int(order_data.get('current_quantity', 0) or 0)

    price = _resolve_order_price(side, current_price, ask, bid)
    now = datetime.now(timezone.utc)

    def _record(**over):
        row = {
            'trade_id': trade_id,
            'ticker': ticker,
            'market': market,
            'side': side,
            'action': action,
            'quantity': qty,
            'price': price,
            'base_price_before': base_price_before,
            'base_price_after': base_price_before,
            'price_rate': price_rate,
            'success': False,
            'order_no': None,
            'error_message': None,
        }
        row.update(over)
        try:
            supabase.table('realtime_orders').insert(row).execute()
        except Exception as e:
            logger.error(f"[Realtime] 주문 이력 기록 실패: {e}")

    # ── A. 기준가만 갱신 (정상 케이스: 매도신호 + 보유 0주, 또는 gap_qty=0) ──
    if action == 'update_base_price':
        try:
            supabase.table('realtime_trading').update({
                'base_price': current_price,
                'updated_at': now.isoformat(),
            }).eq('id', trade_id).execute()
        except Exception as e:
            logger.error(f"[Realtime] {ticker} 기준가 갱신 실패: {e}")
            return
        logger.info(f"[Realtime] {ticker} 기준가만 갱신: {current_price}")
        _record(success=True, price=current_price, base_price_after=current_price)
        return

    if side not in ('buy', 'sell'):
        return

    # 스로틀: 틱이 몰려도 KIS 조회/주문 호출을 일정 간격으로 제한
    last_check = _realtime_check_throttle.get(trade_id)
    if last_check and (now - last_check).total_seconds() < _REALTIME_CHECK_THROTTLE_SECONDS:
        return
    _realtime_check_throttle[trade_id] = now

    # 실패(거부) 쿨다운
    last_fail = _realtime_failure_cooldown.get(trade_id)
    if last_fail and (now - last_fail).total_seconds() < _REALTIME_FAILURE_COOLDOWN_SECONDS:
        logger.debug(f"[Realtime] {ticker} 실패 쿨다운 중 — 스킵")
        return

    # 자격증명 조회 — trade 소유자(user_id)의 것만 사용 (멀티유저 격리).
    # user_id가 없으면(하위호환) 최신 1건으로 폴백.
    try:
        cred_q = supabase.table('kis_credentials').select('*')
        if user_id:
            cred_q = cred_q.eq('user_id', user_id)
        cred_res = cred_q.order('updated_at', desc=True).limit(1).execute()
        cred_rows = getattr(cred_res, 'data', None) or []
        cred = cred_rows[0] if cred_rows else None
    except Exception as e:
        logger.error(f"[Realtime] {ticker} 자격증명 조회 실패: {e}")
        return

    if not cred:
        logger.warning(f"[Realtime] {ticker} KIS 자격증명 없음 → 쿨다운")
        _realtime_failure_cooldown[trade_id] = now
        _record(error_message='KIS 자격증명 없음')
        return

    try:
        cano, prdt = _parse_account_no(cred.get('account_no'))
    except Exception as e:
        logger.warning(f"[Realtime] {ticker} 계좌번호 파싱 실패: {e} → 쿨다운")
        _realtime_failure_cooldown[trade_id] = now
        _record(error_message=f'계좌번호 파싱 실패: {e}')
        return

    appkey = cred.get('appkey')
    appsecret = cred.get('appsecret')
    ticker_norm = _norm_ticker(ticker)

    try:
        # ── B. 미체결 가드 + 10분 초과 취소 ──
        nccs = await kis_service.get_overseas_unfilled_orders(appkey, appsecret, cano, prdt, "NASD")
        if not nccs.get('success'):
            # 미체결 상태를 모르면 중복주문 위험 → 이번 틱은 보류 (쿨다운은 걸지 않음)
            logger.warning(f"[Realtime] {ticker} 미체결내역 조회 실패: {nccs.get('error')} → 이번 틱 보류")
            return

        unfilled = [o for o in nccs['orders'] if _norm_ticker(o.get('pdno')) == ticker_norm]

        if unfilled:
            def _age(o):
                dt = _parse_kis_order_dt(o.get('ord_dt', ''), o.get('ord_tmd', ''))
                return (now - dt).total_seconds() if dt else 0.0

            oldest = max(unfilled, key=_age)  # age가 가장 큰 = 가장 오래된 주문
            age = _age(oldest)

            if age >= _REALTIME_PENDING_CANCEL_SECONDS:
                odno = oldest.get('odno')
                excg = oldest.get('ovrs_excg_cd') or 'NASD'
                remain = int(float(oldest.get('nccs_qty', 0) or 0))
                cancel_res = await kis_service.cancel_overseas_order(
                    appkey, appsecret, cano, prdt, ticker, odno, remain, excg,
                )
                if cancel_res.get('success'):
                    logger.info(f"[Realtime] {ticker} 미체결 {age/60:.1f}분 경과 → 취소 (ODNO={odno}, {remain}주)")
                    _record(action='cancel', side='none', quantity=remain, price=current_price,
                            success=True, order_no=odno, error_message=f'미체결 {age/60:.0f}분 경과 취소')
                else:
                    logger.warning(f"[Realtime] {ticker} 미체결 취소 실패: {cancel_res.get('error')}")
                    _record(action='cancel', side='none', quantity=remain, price=current_price,
                            order_no=odno, error_message=f"취소 실패: {cancel_res.get('error')}")
            else:
                logger.debug(f"[Realtime] {ticker} 미체결 대기중({age/60:.1f}분) → 추가주문 보류")
            return  # 미체결이 있으면 어느 경우든 신규주문 안 함

        # ── C. 미체결 없음 → 잔고로 체결 확정/동기화 ──
        bal = await kis_service.get_overseas_balance(appkey, appsecret, cano, prdt)
        if not bal.get('success'):
            logger.warning(f"[Realtime] {ticker} 잔고 조회 실패: {bal.get('error')} → 이번 틱 보류")
            return

        real_qty = 0
        for h in bal.get('holdings', []):
            if _norm_ticker(h.get('pdno')) == ticker_norm:
                real_qty = int(float(h.get('ccld_qty_smtl1', 0) or 0))
                break

        if real_qty != current_quantity:
            # 직전 주문이 체결되어 보유수량이 변함 → 동기화 + 기준가를 현재가로 갱신
            try:
                # DB에서 현재 grid_step 조회
                trade_res = supabase.table('realtime_trading').select('grid_step').eq('id', trade_id).execute()
                current_step = 0
                if trade_res.data:
                    current_step = int(trade_res.data[0].get('grid_step', 0))
                
                next_step = current_step
                if real_qty > current_quantity:
                    next_step += 1
                elif real_qty < current_quantity:
                    next_step = max(0, next_step - 1)
                
                # 안전장치: 실제 보유량이 0이면 step도 0으로 초기화
                if real_qty == 0:
                    next_step = 0

                supabase.table('realtime_trading').update({
                    'quantity': real_qty,
                    'base_price': current_price,
                    'grid_step': next_step,
                    'updated_at': now.isoformat(),
                }).eq('id', trade_id).execute()
            except Exception as e:
                logger.error(f"[Realtime] {ticker} 체결 동기화 실패: {e}")
                return
            logger.info(
                f"[Realtime] {ticker} 체결 반영: 보유 {current_quantity} → {real_qty}, grid_step {current_step} → {next_step}, 기준가 → {current_price}"
            )
            _record(action='settle', side='none', quantity=abs(real_qty - current_quantity),
                    price=current_price, success=True, base_price_after=current_price)
            return  # 이번 틱 소진, 다음 갭에서 신규주문

        # ── D. 신규 주문 (즉시체결 유도 지정가: 매수=ask / 매도=bid) ──
        if side == 'sell':
            order_qty = min(qty, real_qty)
        else:
            order_qty = qty
        if order_qty <= 0:
            logger.debug(f"[Realtime] {ticker} 주문수량 0 → 스킵")
            return

        if side == 'buy':
            result = await kis_service.buy_overseas_stock(appkey, appsecret, cano, prdt, ticker, order_qty, price, market)
        else:
            result = await kis_service.sell_overseas_stock(appkey, appsecret, cano, prdt, ticker, order_qty, price, market)

        if result.get('success'):
            _realtime_failure_cooldown.pop(trade_id, None)
            logger.info(
                f"[Realtime] {side.upper()} 주문 접수(체결 대기): {ticker} {order_qty}주 @ {price} "
                f"(ODNO={result.get('order_no')})"
            )
            # base_price/quantity는 체결 확인(C단계) 후에만 갱신 — 접수만으로는 갱신하지 않음
            _record(quantity=order_qty, success=True, order_no=result.get('order_no'))
        else:
            err = result.get('error', '주문 실패')
            logger.warning(f"[Realtime] {side.upper()} 주문 실패: {err} → 쿨다운")
            _realtime_failure_cooldown[trade_id] = now
            _record(quantity=order_qty, error_message=err)

    except Exception as e:
        logger.error(f"[Realtime] {ticker} 실시간 주문 처리 예외: {e}")
        _realtime_failure_cooldown[trade_id] = now
        _record(error_message=f'예외: {e}')
