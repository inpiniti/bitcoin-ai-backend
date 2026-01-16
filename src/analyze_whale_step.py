
"""
Step 3: Whale & Supply Analysis (Python)
Event Step - 'analyze-whale' 이벤트 구독
"""
import logging

# 전역 변수
np = None

def get_numpy():
    global np
    if np is None:
        import numpy as _np
        np = _np
    return np

# Event Step Configuration
config = {
    "name": "analyze-whale",
    "type": "event",
    "subscribes": ["analyze-whale"],
    "emits": ["format-whale-result"],
    "flows": ["whale-tracking-flow"]
}

async def handler(event, context):
    """
    거래량 기반 고래 매집 및 수급 이탈 분석
    """
    # Load Numpy Lazily
    np = get_numpy()
    
    job_id = event.get("jobId")
    symbol = event.get("symbol")
    market_data = event.get("marketData", [])
    
    try:
        if not market_data:
            raise ValueError("No market data provided")
            
        context.logger.info(f"[Step3:Whale] Analyzing supply for {symbol} ({len(market_data)} points)")

        # 데이터 추출 (List -> Numpy Array)
        closes = np.array([d['close'] for d in market_data], dtype=np.float64)
        highs = np.array([d['high'] for d in market_data], dtype=np.float64)
        lows = np.array([d['low'] for d in market_data], dtype=np.float64)
        volumes = np.array([d['volume'] for d in market_data], dtype=np.float64)
        
        # 1. VWAP (Volume Weighted Average Price) 계산
        # 전체 기간 VWAP (장기 평단)
        total_value = np.sum(closes * volumes)
        total_vol = np.sum(volumes)
        vwap_total = total_value / total_vol if total_vol > 0 else closes[-1]
        
        # 최근 30일(또는 30개 캔들) VWAP (단기 세력 평단)
        lookback = min(30, len(closes))
        vwap_short_val = np.sum(closes[-lookback:] * volumes[-lookback:])
        vwap_short_vol = np.sum(volumes[-lookback:])
        vwap_short = vwap_short_val / vwap_short_vol if vwap_short_vol > 0 else closes[-1]
        
        # 2. OBV (On-Balance Volume) 계산
        # 가격 변화에 따라 거래량을 더하거나 뺌
        obv = np.zeros(len(closes))
        for i in range(1, len(closes)):
            if closes[i] > closes[i-1]:
                obv[i] = obv[i-1] + volumes[i]
            elif closes[i] < closes[i-1]:
                obv[i] = obv[i-1] - volumes[i]
            else:
                obv[i] = obv[i-1]
                
        # OBV Divergence 확인 (최근 14일 기준)
        # 가격은 하락하는데 OBV는 상승 -> 매집 (Bullish Divergence)
        # 가격은 상승하는데 OBV는 하락 -> 이탈 (Bearish Divergence)
        divergence_window = min(14, len(closes))
        price_trend = closes[-1] - closes[-divergence_window]
        obv_trend = obv[-1] - obv[-divergence_window]
        
        divergence_signal = "neutral"
        if price_trend < 0 and obv_trend > 0:
            divergence_signal = "bullish_divergence" # 가격 하락 중 매집 발견
        elif price_trend > 0 and obv_trend < 0:
            divergence_signal = "bearish_divergence" # 가격 상승 중 자금 이탈
            
        # 3. MFI (Money Flow Index) - RSI의 거래량 버전
        # (간이 계산)
        typical_price = (highs + lows + closes) / 3
        raw_money_flow = typical_price * volumes
        
        positive_flow = []
        negative_flow = []
        
        mfi_period = 14
        if len(closes) > mfi_period:
            for i in range(1, len(closes)):
                if typical_price[i] > typical_price[i-1]:
                    positive_flow.append(raw_money_flow[i])
                    negative_flow.append(0)
                else:
                    positive_flow.append(0)
                    negative_flow.append(raw_money_flow[i])
            
            # 마지막 14개 합
            pos_mf = sum(positive_flow[-mfi_period:])
            neg_mf = sum(negative_flow[-mfi_period:])
            
            mfi = 100 - (100 / (1 + pos_mf / neg_mf)) if neg_mf != 0 else 100
        else:
            mfi = 50 # 데이터 부족 시 중립
            
        
        # 결과 종합
        current_price = closes[-1]
        
        # VWAP 격차율 (현재가가 세력 평단 대비 얼마나 싼가/비싼가)
        vwap_diff_percent = ((current_price - vwap_short) / vwap_short) * 100
        
        analysis_result = {
            "currentPrice": float(current_price),
            "vwapShort": float(vwap_short),
            "vwapTotal": float(vwap_total),
            "vwapDiffPercent": float(vwap_diff_percent),
            "obvTrend": "up" if obv_trend > 0 else "down",
            "divergence": divergence_signal,
            "mfi": float(mfi),
            "volumeSpike": bool(volumes[-1] > np.mean(volumes[-30:]) * 1.5) # 거래량 폭발 여부
        }
        
        context.logger.info(f"[Step3:Whale] Analysis complete: {analysis_result}")
        
        # State 업데이트 (생략 가능, Step 4에서 최종 저장함)
        
        # Step 4로 전달
        await context.emit({
            "topic": "format-whale-result",
            "data": {
                "jobId": job_id,
                "symbol": symbol,
                "analysis": analysis_result
            }
        })
        
    except Exception as e:
        context.logger.error(f"[Step3:Whale] Error: {str(e)}")
        import traceback
        context.logger.error(traceback.format_exc())
        
        # 에러 처리
        if job_id:
             job = await context.state.get("whale_jobs", job_id)
             if job:
                 job["status"] = "error"
                 job["error"] = str(e)
                 await context.state.set("whale_jobs", job_id, job)

