"""
고래 수급 분석 서비스
VWAP / OBV Divergence / MFI 계산 + 리포트 포맷팅
(기존 analyze_whale_step.py + format-whale-result.step.ts 통합)
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger("whale_service")

_np = None


def _get_np():
    global _np
    if _np is None:
        import numpy as n
        _np = n
    return _np


def analyze_and_format(symbol: str, market_data: list[dict]) -> dict:
    """OHLCV 리스트를 받아 수급 분석 리포트를 반환합니다."""
    np = _get_np()

    if not market_data:
        raise ValueError("No market data provided")

    logger.info(f"[Whale] {symbol} 분석 중 ({len(market_data)}포인트)")

    closes  = np.array([d["close"]  for d in market_data], dtype=np.float64)
    highs   = np.array([d["high"]   for d in market_data], dtype=np.float64)
    lows    = np.array([d["low"]    for d in market_data], dtype=np.float64)
    volumes = np.array([d["volume"] for d in market_data], dtype=np.float64)

    # ── 1. VWAP ─────────────────────────────────────────
    vwap_total = (np.sum(closes * volumes) / np.sum(volumes)) if np.sum(volumes) > 0 else closes[-1]

    lookback = min(30, len(closes))
    vwap_short_val = np.sum(closes[-lookback:] * volumes[-lookback:])
    vwap_short_vol = np.sum(volumes[-lookback:])
    vwap_short = (vwap_short_val / vwap_short_vol) if vwap_short_vol > 0 else closes[-1]

    # ── 2. OBV ──────────────────────────────────────────
    obv = np.zeros(len(closes))
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv[i] = obv[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            obv[i] = obv[i - 1] - volumes[i]
        else:
            obv[i] = obv[i - 1]

    divergence_window = min(14, len(closes))
    price_trend = closes[-1] - closes[-divergence_window]
    obv_trend   = obv[-1]   - obv[-divergence_window]

    if price_trend < 0 and obv_trend > 0:
        divergence_signal = "bullish_divergence"
    elif price_trend > 0 and obv_trend < 0:
        divergence_signal = "bearish_divergence"
    else:
        divergence_signal = "neutral"

    # ── 3. MFI ──────────────────────────────────────────
    typical_price  = (highs + lows + closes) / 3
    raw_money_flow = typical_price * volumes
    mfi_period = 14

    if len(closes) > mfi_period:
        positive_flow, negative_flow = [], []
        for i in range(1, len(closes)):
            if typical_price[i] > typical_price[i - 1]:
                positive_flow.append(raw_money_flow[i])
                negative_flow.append(0.0)
            else:
                positive_flow.append(0.0)
                negative_flow.append(raw_money_flow[i])
        pos_mf = sum(positive_flow[-mfi_period:])
        neg_mf = sum(negative_flow[-mfi_period:])
        mfi = 100 - (100 / (1 + pos_mf / neg_mf)) if neg_mf != 0 else 100.0
    else:
        mfi = 50.0

    current_price    = closes[-1]
    vwap_diff_pct    = ((current_price - vwap_short) / vwap_short) * 100
    volume_spike     = bool(volumes[-1] > np.mean(volumes[-30:]) * 1.5)

    analysis = {
        "currentPrice":     float(current_price),
        "vwapShort":        float(vwap_short),
        "vwapTotal":        float(vwap_total),
        "vwapDiffPercent":  float(vwap_diff_pct),
        "obvTrend":         "up" if obv_trend > 0 else "down",
        "divergence":       divergence_signal,
        "mfi":              float(mfi),
        "volumeSpike":      volume_spike,
    }

    # ── 포맷팅 (format-whale-result.step.ts 로직) ────────
    signals   = []
    sentiment = "neutral"

    if vwap_diff_pct < -5:
        signals.append(f"🐋 세력 추정 평단가(${round(vwap_short)})보다 5% 이상 저렴합니다.")
        sentiment = "bullish"
    elif vwap_diff_pct > 10:
        signals.append(f"⚠️ 세력 평단가(${round(vwap_short)})보다 10% 이상 비쌉니다. 차익 실현 주의.")
        sentiment = "bearish"
    else:
        signals.append(f"📊 세력 평단가(${round(vwap_short)})와 비슷한 수준입니다.")

    if divergence_signal == "bullish_divergence":
        signals.append("🔥 [강력 매수 신호] 가격은 하락 중이나 자금(OBV)은 유입되고 있습니다 (개미 털기 의심).")
        sentiment = "strong_bullish"
    elif divergence_signal == "bearish_divergence":
        signals.append("🚨 [위험 신호] 가격은 버티고 있으나 자금(OBV)이 조용히 빠져나가고 있습니다.")
        sentiment = "strong_bearish"

    if mfi > 80:
        signals.append("📈 과매수 구간입니다 (MFI > 80).")
    if mfi < 20:
        signals.append("📉 과매도 구간입니다 (MFI < 20).")
    if volume_spike:
        signals.append("💥 최근 거래량이 급증했습니다. 변동성 확대 주의.")

    report = {
        "title":                f"{symbol} 고래 수급 분석 리포트",
        "symbol":               symbol,
        "generatedAt":          datetime.now(tz=timezone.utc).isoformat(),
        "sentiment":            sentiment,
        "currentPrice":         float(current_price),
        "estimatedWhalePrice":  float(vwap_short),
        "summary":              "\n".join(signals),
        "details":              analysis,
    }

    logger.info(f"[Whale] {symbol}: sentiment={sentiment}")
    return report
