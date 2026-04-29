"""
테스트용 API - TimesFM 동작 확인
"""
import logging
from fastapi import APIRouter, HTTPException

logger = logging.getLogger("test_router")
router = APIRouter(prefix="/test", tags=["test"])


@router.post(
    "/timesfm",
    summary="TimesFM 간단 테스트",
    description="종목 20개로 TimesFM 예측이 제대로 작동하는지 확인합니다.",
)
async def test_timesfm():
    """
    종목 20개로 TimesFM 동작을 테스트합니다.

    Returns:
        {
            "status": "success",
            "total": 20,
            "success_count": 예측 성공 개수,
            "fail_count": 예측 실패 개수,
            "results": [
                {"ticker": "AAPL", "forecast": "up", "error": null},
                ...
            ]
        }
    """
    try:
        from services.sp500_list_service import fetch_sp500_list
        from services.data_collector import fetch_stock_history_yf
        from services import timesfm_service
        import asyncio

        logger.info("[Test] TimesFM 테스트 시작...")

        # Step 1: S&P500 종목 목록 조회 (처음 20개)
        logger.info("[Test] S&P500 종목 조회...")
        sp500_stocks = await fetch_sp500_list()
        test_stocks = sp500_stocks[:20]
        logger.info(f"[Test] {len(test_stocks)}개 종목 선택")

        # Step 2: 종목별 종가 데이터 수집 및 TimesFM 예측
        results = []
        success_count = 0
        fail_count = 0

        for stock in test_stocks:
            ticker = stock.ticker
            try:
                # 종가 데이터 수집 (200일)
                logger.info(f"[Test] {ticker}: 데이터 수집 중...")
                candles = await fetch_stock_history_yf(ticker, days=200)
                closes = [c["close"] for c in candles if c.get("close")]

                if not closes:
                    logger.warning(f"[Test] {ticker}: 종가 데이터 없음")
                    results.append({
                        "ticker": ticker,
                        "forecast": None,
                        "data_points": 0,
                        "error": "No price data"
                    })
                    fail_count += 1
                    continue

                # TimesFM 예측
                logger.info(f"[Test] {ticker}: TimesFM 예측 ({len(closes)}개 데이터)...")
                direction = await asyncio.to_thread(
                    timesfm_service.predict_direction,
                    closes
                )

                results.append({
                    "ticker": ticker,
                    "forecast": direction,
                    "data_points": len(closes),
                    "error": None
                })

                if direction:
                    success_count += 1
                    logger.info(f"[Test] {ticker}: {direction} ✓")
                else:
                    fail_count += 1
                    logger.warning(f"[Test] {ticker}: None (모델 로드 실패 또는 데이터 부족)")

            except Exception as e:
                logger.exception(f"[Test] {ticker}: 예외 발생")
                results.append({
                    "ticker": ticker,
                    "forecast": None,
                    "data_points": 0,
                    "error": str(e)
                })
                fail_count += 1

        logger.info(f"[Test] 완료: 성공={success_count}, 실패={fail_count}")

        return {
            "status": "success",
            "total": len(test_stocks),
            "success_count": success_count,
            "fail_count": fail_count,
            "results": results
        }

    except Exception as e:
        logger.exception(f"[Test] 테스트 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
