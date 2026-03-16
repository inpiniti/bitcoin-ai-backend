"""
기술적 지표 계산 서비스
dataProcessor.js 의 addDerivedData() Python 포팅 버전

입력: OHLCV 딕셔너리 리스트 (날짜 오름차순, 과거→현재)
  각 항목: {"date": str, "open": float, "high": float, "low": float, "close": float, "volume": float}

출력: 지표가 추가된 딕셔너리 리스트
"""
import logging
import numpy as np

logger = logging.getLogger("indicator_service")


def _sma(closes: list[float], period: int) -> list[float | None]:
    result = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        result[i] = sum(closes[i - period + 1 : i + 1]) / period
    return result


def _ema(closes: list[float], period: int) -> list[float | None]:
    result = [None] * len(closes)
    if len(closes) < period:
        return result
    k = 2 / (period + 1)
    result[period - 1] = sum(closes[:period]) / period
    for i in range(period, len(closes)):
        result[i] = closes[i] * k + result[i - 1] * (1 - k)
    return result


def _rsi(closes: list[float], period: int = 14) -> list[float | None]:
    result = [None] * len(closes)
    if len(closes) <= period:
        return result

    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(closes)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else 999
        result[i] = 100 - (100 / (1 + rs))

    return result


def _bollinger_bands(
    closes: list[float], period: int = 20, std_dev: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    upper, middle, lower = [None] * len(closes), [None] * len(closes), [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        ma = sum(window) / period
        std = (sum((x - ma) ** 2 for x in window) / period) ** 0.5
        middle[i] = ma
        upper[i] = ma + std_dev * std
        lower[i] = ma - std_dev * std
    return upper, middle, lower


def _volume_ma(volumes: list[float], period: int = 20) -> list[float | None]:
    return _sma(volumes, period)


def add_derived_data(candles: list[dict]) -> list[dict]:
    """
    OHLCV 데이터에 기술적 지표를 추가합니다.
    모델 메타데이터의 피처 목록과 관계없이 전체 지표를 계산하며,
    실제 사용 지표 필터링은 상위 레이어에서 수행합니다.
    """
    if not candles or len(candles) < 20:
        logger.warning("데이터 부족 (최소 20개 필요)")
        return candles

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    volumes = [c.get("volume", 0) for c in candles]

    # 이동평균
    sma5 = _sma(closes, 5)
    sma10 = _sma(closes, 10)
    sma20 = _sma(closes, 20)
    sma60 = _sma(closes, 60)
    ema5 = _ema(closes, 5)
    ema20 = _ema(closes, 20)

    # RSI
    rsi14 = _rsi(closes, 14)

    # 볼린저밴드
    bb_upper, bb_middle, bb_lower = _bollinger_bands(closes, 20, 2.0)

    # 거래량 이동평균
    vol_ma20 = _volume_ma(volumes, 20)

    result = []
    for i, candle in enumerate(candles):
        c = {**candle}

        # 이동평균
        c["sma5"] = sma5[i]
        c["sma10"] = sma10[i]
        c["sma20"] = sma20[i]
        c["sma60"] = sma60[i]
        c["ema5"] = ema5[i]
        c["ema20"] = ema20[i]

        # RSI
        c["rsi14"] = rsi14[i]

        # 볼린저밴드
        c["bb_upper"] = bb_upper[i]
        c["bb_middle"] = bb_middle[i]
        c["bb_lower"] = bb_lower[i]
        c["bb_width"] = (
            (bb_upper[i] - bb_lower[i]) / bb_middle[i]
            if bb_upper[i] and bb_lower[i] and bb_middle[i] and bb_middle[i] != 0
            else None
        )
        c["bb_pct_b"] = (
            (closes[i] - bb_lower[i]) / (bb_upper[i] - bb_lower[i])
            if bb_upper[i] and bb_lower[i] and (bb_upper[i] - bb_lower[i]) != 0
            else None
        )

        # 거래량
        c["vol_ma20"] = vol_ma20[i]
        c["vol_ratio"] = (
            volumes[i] / vol_ma20[i] if vol_ma20[i] and vol_ma20[i] != 0 else None
        )

        # 변화율
        c["change_1d"] = (
            (closes[i] - closes[i - 1]) / closes[i - 1] * 100 if i >= 1 and closes[i - 1] != 0 else None
        )
        c["change_5d"] = (
            (closes[i] - closes[i - 5]) / closes[i - 5] * 100 if i >= 5 and closes[i - 5] != 0 else None
        )
        c["change_20d"] = (
            (closes[i] - closes[i - 20]) / closes[i - 20] * 100 if i >= 20 and closes[i - 20] != 0 else None
        )

        # 트렌드 (단기 MA > 장기 MA)
        c["trend_5_20"] = (
            1 if sma5[i] and sma20[i] and sma5[i] > sma20[i] else (0 if sma5[i] and sma20[i] else None)
        )
        c["trend_20_60"] = (
            1 if sma20[i] and sma60[i] and sma20[i] > sma60[i] else (0 if sma20[i] and sma60[i] else None)
        )

        result.append(c)

    return result


def extract_features_for_model(candles: list[dict], feature_columns: list[str]) -> list[list[float]]:
    """
    모델 메타데이터의 피처 목록 기준으로 데이터를 추출합니다.
    None 값은 0으로 대체합니다.
    """
    rows = []
    for candle in candles:
        row = [float(candle.get(col) or 0) for col in feature_columns]
        rows.append(row)
    return rows
