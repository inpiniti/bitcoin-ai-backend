"""
S&P 500 종목 리스트 서비스

Wikipedia 'List of S&P 500 companies' 테이블 스크래핑.
24시간 캐시로 불필요한 요청 방지.
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("sp500_list_service")

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# ── 캐시 ──────────────────────────────────────────────────────────────────────

_cache: Optional[list[dict]] = None
_cache_time: float = 0
_CACHE_TTL = 86400  # 24시간


@dataclass
class SP500Stock:
    ticker: str
    name: str
    sector: str

    def to_dict(self) -> dict:
        return {"ticker": self.ticker, "name": self.name, "sector": self.sector}


async def fetch_sp500_list() -> list[SP500Stock]:
    """
    Wikipedia에서 S&P 500 종목 리스트를 스크래핑.
    24시간 캐시 적용.

    Returns:
        SP500Stock 리스트 (~503개)
    """
    global _cache, _cache_time

    if _cache is not None and (time.time() - _cache_time) < _CACHE_TTL:
        logger.debug(f"[SP500List] 캐시 사용 ({len(_cache)}개)")
        return _cache

    logger.info("[SP500List] Wikipedia 스크래핑 시작")
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(WIKI_URL, headers={
            "User-Agent": "Mozilla/5.0 (compatible; SP500Bot/1.0)"
        })

    if resp.status_code != 200:
        raise Exception(f"Wikipedia 요청 실패: HTTP {resp.status_code}")

    soup = BeautifulSoup(resp.text, "lxml")
    table = soup.find("table", {"id": "constituents"})
    if not table:
        raise Exception("Wikipedia S&P 500 테이블을 찾을 수 없습니다")

    stocks: list[SP500Stock] = []
    for row in table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) < 4:
            continue
        try:
            ticker = cols[0].get_text(strip=True).replace(".", "-")
            name = cols[1].get_text(strip=True)
            sector = cols[2].get_text(strip=True)
            stocks.append(SP500Stock(ticker=ticker, name=name, sector=sector))
        except (IndexError, AttributeError) as e:
            logger.debug(f"[SP500List] 행 파싱 스킵: {e}")

    _cache = stocks
    _cache_time = time.time()
    logger.info(f"[SP500List] {len(stocks)}개 종목 로드 완료")
    return stocks


def group_by_sector(stocks: list[SP500Stock]) -> dict[str, list[SP500Stock]]:
    """GICS 섹터별 그룹핑"""
    groups: dict[str, list[SP500Stock]] = {}
    for s in stocks:
        groups.setdefault(s.sector, []).append(s)
    return groups
