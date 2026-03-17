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
    load_automation_settings_active,
    save_auto_trade_log,
    update_last_run_date,
    get_last_run_date,
)
from services.yahoo_service import fetch_stock_history_for_trade

logger = logging.getLogger("auto_trade_service")

CHUNK_SIZE = 5


# ─────────────────────────────────────────────
# 티커 그룹 로더
# ─────────────────────────────────────────────

async def _load_target_tickers(target_group: str, holdings: list[dict]) -> list[dict]:
    if target_group == "myholdings":
        return [{"ticker": h["pdno"], "name": h.get("prdt_name", "")} for h in holdings]

    if target_group in ("sp500", "qqq"):
        url = (
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
            if target_group == "sp500"
            else "https://en.wikipedia.org/wiki/Nasdaq-100"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        import re
        tickers = list(dict.fromkeys(re.findall(r'<td>([A-Z]{1,5})</td>', resp.text)))[:505]
        return [{"ticker": t, "name": t} for t in tickers if t]

    if target_group == "superinvestor":
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://www.dataroma.com/m/holdings.php?m=ALL",
                headers={"User-Agent": "Mozilla/5.0"},
            )
        import re
        tickers = list(dict.fromkeys(re.findall(r'symbol=([A-Z]{1,5})"', resp.text)))[:100]
        return [{"ticker": t, "name": t} for t in tickers if t]

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


async def run_auto_trade_dl(is_test: bool = False) -> dict:
    logs: list[str] = []

    def log(msg: str):
        logger.info(msg)
        logs.append(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")

    today_str = date.today().isoformat()
    mode = "[TEST]" if is_test else ""

    try:
        _log_dst_info()

        # ── 1. automation_settings 로드 ───────────────
        log("automation_settings 로드 중...")
        cfg = await load_automation_settings_active()
        if not cfg:
            raise RuntimeError(
                "is_active=true 인 automation_settings 가 없습니다. "
                "클라이언트 자동매매 설정 패널에서 설정을 활성화해주세요."
            )

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
        sell_threshold = float(cfg.get("sell_condition", 20)) / 100  # 20 → 0.2
        trade_enabled = bool(cfg.get("trade_enabled", False))
        if not trade_enabled:
            log("[모의매매] trade_enabled=false → 실제 주문 없이 로그만 기록합니다.")

        log(f"설정 로드 완료 | 그룹={target_group} | 모델={model_id} | buy>={buy_threshold} | sell>={sell_threshold}")

        # ── 중복 실행 방지 (실제매매만 적용) ────────────
        if trade_enabled:
            last_run = await get_last_run_date()
            if last_run == today_str:
                return {"skipped": True, "reason": f"금일({today_str}) 이미 실행됨"}
            await update_last_run_date(today_str)

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

        holdings = [h for h in balance_res["holdings"] if int(h.get("ccld_qty_smtl1", 0)) > 0]
        holding_tickers = {h["pdno"] for h in holdings}
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
                if candles and len(candles) >= max(lookback + 5, 30):
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
                    if buy_prob >= buy_threshold:
                        buy_list.append({**stock, "buy_prob": round(buy_prob, 4)})
                        log(f"  BUY: {ticker} (확률={buy_prob:.3f})")
                except Exception as e:
                    logger.warning(f"[{ticker}] 예측 실패: {e}")

        log(f"매수 후보: {len(buy_list)}개")
        buy_tickers = {s["ticker"] for s in buy_list}

        # ── 7. 매도 신호 스캔 ─────────────────────────
        log("매도 신호 스캔 중 (보유 종목)...")
        sell_list: list[dict] = []

        for holding in holdings:
            ticker = holding["pdno"]
            if ticker in buy_tickers:
                continue
            candles = await load_ticker_data(ticker)
            if not candles:
                continue
            try:
                _, sell_prob = dl_model_service.predict(model, meta, get_feature_matrix(candles))
                if sell_prob >= sell_threshold:
                    sell_list.append({
                        "ticker": ticker,
                        "name": holding.get("prdt_name", ""),
                        "qty": int(holding.get("ccld_qty_smtl1", 0)),
                        "sell_prob": round(sell_prob, 4),
                    })
                    log(f"  SELL: {ticker} (확률={sell_prob:.3f})")
            except Exception as e:
                logger.warning(f"[{ticker}] 예측 실패: {e}")

        log(f"매도 후보: {len(sell_list)}개")

        # ── 8. 매도 주문 실행 (선행) ──────────────────
        sell_results = []
        for item in sell_list:
            ticker = item["ticker"]
            price_res = await kis_service.get_current_price_with_exchange_search(appkey, appsecret, ticker)
            if not price_res["success"]:
                log(f"  {ticker} 현재가 조회 실패, 건너뜀")
                continue
            price = price_res["price"]
            exchange = price_res.get("exchange", "NAS")
            qty = item["qty"]
            if trade_enabled:
                result = await kis_service.sell_overseas_stock(appkey, appsecret, account_no, account_code, ticker, qty, price, exchange)
                sell_results.append({"ticker": ticker, "qty": qty, "price": price, "result": result})
                log(f"  [실제매매] 매도: {ticker} {qty}주 @ ${price} → {'성공' if result['success'] else '실패'}: {result.get('order_no') or result.get('error')}")
            else:
                sell_results.append({"ticker": ticker, "qty": qty, "price": price, "simulated": True})
                log(f"  [모의매매] 매도 예정: {ticker} {qty}주 @ ${price} (주문 미실행)")

        # ── 9. 매수 주문 실행 ─────────────────────────
        # 사용 가능한 달러 현금 조회 (output3.frcr_buy_amt_smtl: 외화 매수 가능금액)
        bal_summary = balance_res.get("summary", {})
        available_cash = float(bal_summary.get("frcr_buy_amt_smtl", 0) or 0)
        log(f"매수 가능 현금: ${available_cash:.2f}")

        buy_count = len(buy_list)
        per_ticker_amount = (available_cash / buy_count) if buy_count > 0 else 0
        log(f"티커당 배분: ${per_ticker_amount:.2f} ({buy_count}개 균등 분배)")

        buy_results = []
        for item in buy_list:
            ticker = item["ticker"]
            price_res = await kis_service.get_current_price_with_exchange_search(appkey, appsecret, ticker)
            if not price_res["success"]:
                log(f"  {ticker} 현재가 조회 실패, 건너뜀")
                continue
            price = price_res["price"]
            exchange = price_res.get("exchange", "NAS")
            qty = int(per_ticker_amount / price) if price > 0 else 0
            if trade_enabled:
                result = await kis_service.buy_overseas_stock(appkey, appsecret, account_no, account_code, ticker, qty, price, exchange)
                buy_results.append({"ticker": ticker, "qty": qty, "price": price, "result": result})
                log(f"  [실제매매] 매수: {ticker} {qty}주 @ ${price} (배분=${per_ticker_amount:.2f}) → {'성공' if result['success'] else '실패'}: {result.get('order_no') or result.get('error')}")
            else:
                buy_results.append({"ticker": ticker, "qty": qty, "price": price, "simulated": True})
                log(f"  [모의매매] 매수 예정: {ticker} {qty}주 @ ${price} (배분=${per_ticker_amount:.2f}, 주문 미실행)")

        # ── 10. 로그 저장 ─────────────────────────────
        summary = {
            "date": today_str,
            "is_test": is_test,
            "model_id": model_id,
            "target_group": target_group,
            "holdings_count": len(holdings),
            "buy_signals": len(buy_list),
            "sell_signals": len(sell_list),
            "buy_orders": len(buy_results),
            "sell_orders": len(sell_results),
            "logs": logs,
        }
        await save_auto_trade_log(summary)
        log(f"{mode} 자동매매 완료")
        return summary

    except Exception as e:
        error_msg = f"자동매매 오류: {e}"
        logger.exception(error_msg)
        logs.append(error_msg)
        try:
            await save_auto_trade_log({"date": today_str, "is_test": is_test, "error": str(e), "logs": logs})
        except Exception:
            pass
        raise
