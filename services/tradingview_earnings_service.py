"""
트레이딩뷰 실적 발표 캘린더 스크래핑

sp500_list_service.py 참고하여 구현.
BeautifulSoup + httpx 사용.
"""
import logging
import json
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("tradingview_earnings_service")

# 트레이딩뷰는 동적 콘텐츠이므로 API 엔드포인트 직접 사용
# https://api.tradingview.com/api/v1/calendar/earnings
TV_EARNINGS_API = "https://api.tradingview.com/api/v1/calendar/earnings"

_cache_earnings: Optional[dict] = None
_cache_time_earnings: float = 0
_CACHE_TTL = 3600  # 1시간


@dataclass
class EarningsEvent:
    ticker: str
    date: str  # YYYY-MM-DD
    eps_estimate: Optional[float] = None
    eps_actual: Optional[float] = None
    revenue_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None


async def fetch_earnings_calendar(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 100
) -> list[EarningsEvent]:
    """
    트레이딩뷰 API에서 실적 발표 캘린더 조회.

    Args:
        date_from: 시작 날짜 (YYYY-MM-DD, 기본: 오늘)
        date_to: 종료 날짜 (YYYY-MM-DD, 기본: 90일 후)
        limit: 최대 반환 개수

    Returns:
        EarningsEvent 리스트
    """
    if not date_from:
        date_from = datetime.utcnow().strftime("%Y-%m-%d")
    if not date_to:
        date_to = (datetime.utcnow() + timedelta(days=90)).strftime("%Y-%m-%d")

    logger.info(f"[TV Earnings] API 호출 시작: {date_from} ~ {date_to} (limit={limit})")

    try:
        params = {
            "from": date_from,
            "to": date_to,
            "limit": limit,
        }

        logger.debug(f"[TV Earnings] 파라미터: {params}")

        async with httpx.AsyncClient(timeout=15) as client:
            logger.debug(f"[TV Earnings] GET {TV_EARNINGS_API}")
            resp = await client.get(
                TV_EARNINGS_API,
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                }
            )

        logger.info(f"[TV Earnings] HTTP {resp.status_code}")

        if resp.status_code != 200:
            logger.error(f"[TV Earnings] API 요청 실패: HTTP {resp.status_code} - {resp.text[:200]}")
            return []

        data = resp.json()
        logger.debug(f"[TV Earnings] API 응답 키: {list(data.keys())}")

        events = []

        # API 응답 구조: {"data": [...]}
        items = data.get("data", [])
        logger.info(f"[TV Earnings] API 응답 항목 수: {len(items)}")

        for item in items:
            try:
                ticker = item.get("symbol", "").upper()
                date_str = item.get("date", "")

                if not ticker or not date_str:
                    continue

                events.append(EarningsEvent(
                    ticker=ticker,
                    date=date_str,
                    eps_estimate=_safe_float(item.get("eps_estimate")),
                    eps_actual=_safe_float(item.get("eps_actual")),
                    revenue_estimate=_safe_float(item.get("revenue_estimate")),
                    revenue_actual=_safe_float(item.get("revenue_actual")),
                ))
            except Exception as e:
                logger.debug(f"[TV Earnings] 행 파싱 스킵: {e}")

        logger.info(f"[TV Earnings] ✅ {len(events)}개 실적 발표 로드 완료")
        return events

    except Exception as e:
        logger.error(f"[TV Earnings] ❌ 스크래핑 실패: {type(e).__name__} {e}", exc_info=True)
        return []


def _safe_float(val: any) -> Optional[float]:
    """안전한 float 변환"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


async def fetch_earnings_by_ticker(ticker: str) -> list[EarningsEvent]:
    """특정 종목의 과거 실적 발표 조회 (365일)"""
    date_from = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
    date_to = datetime.utcnow().strftime("%Y-%m-%d")

    events = await fetch_earnings_calendar(date_from, date_to, limit=50)
    return [e for e in events if e.ticker == ticker]
