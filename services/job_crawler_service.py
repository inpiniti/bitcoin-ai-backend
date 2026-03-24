"""
#43 구직 크롤러 서비스

사람인: BeautifulSoup 스크래핑 (대기업, 프론트엔드 개발자)
원티드: 공개 JSON API (#47)
"""
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("job_crawler_service")

# ── 공통 설정 ────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

REQUEST_DELAY = 1.5  # 요청 간 딜레이(초) - 차단 방지


@dataclass
class JobListing:
    site: str
    company: str
    title: str
    url: str
    deadline: Optional[date] = None
    career: str = ""
    location: str = ""

    def to_dict(self) -> dict:
        return {
            "site": self.site,
            "company": self.company,
            "title": self.title,
            "url": self.url,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "career": self.career,
            "location": self.location,
        }


# ── 사람인 크롤러 ─────────────────────────────────────────────────────────────

SARAMIN_BASE = "https://www.saramin.co.kr"

# 사람인 URL 파라미터
# searchword: 검색어
# company_type: ST020 = 대기업(1000인+)
# recruitSort: reg_dt = 최신순
# recruitPageCount: 페이지당 결과 수
SARAMIN_SEARCH_URL = (
    SARAMIN_BASE
    + "/zf_user/search/recruit"
    "?searchType=search"
    "&searchword={keyword}"
    "&company_type=ST020"
    "&recruitSort=reg_dt"
    "&recruitPageCount={per_page}"
    "&recruitPage={page}"
)


def _parse_saramin_deadline(text: str) -> Optional[date]:
    """'~04.15', '04.15(화)', '상시채용' 등 마감일 파싱"""
    text = text.strip().lstrip("~")
    m = re.search(r"(\d{2})\.(\d{2})", text)
    if not m:
        return None
    try:
        month, day = int(m.group(1)), int(m.group(2))
        year = date.today().year
        d = date(year, month, day)
        # 이미 지난 날짜면 내년으로
        if d < date.today():
            d = date(year + 1, month, day)
        return d
    except ValueError:
        return None


def _parse_saramin_page(html: str) -> list[JobListing]:
    """사람인 검색 결과 HTML에서 채용공고 목록 파싱"""
    soup = BeautifulSoup(html, "lxml")
    jobs: list[JobListing] = []

    for item in soup.select(".item_recruit"):
        try:
            # 회사명
            corp_tag = item.select_one(".corp_name a")
            company = corp_tag.get_text(strip=True) if corp_tag else ""

            # 공고 제목 + URL
            title_tag = item.select_one(".job_tit a")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            url = SARAMIN_BASE + href if href.startswith("/") else href

            # 마감일
            date_tag = item.select_one(".job_date .date")
            deadline = _parse_saramin_deadline(date_tag.get_text()) if date_tag else None

            # 경력
            career_tags = item.select(".job_condition span")
            career = career_tags[0].get_text(strip=True) if career_tags else ""

            # 근무지
            location_tag = item.select_one(".job_condition .work_place")
            location = location_tag.get_text(strip=True) if location_tag else ""

            if company and title and url:
                jobs.append(JobListing(
                    site="saramin",
                    company=company,
                    title=title,
                    url=url,
                    deadline=deadline,
                    career=career,
                    location=location,
                ))
        except Exception as e:
            logger.debug(f"[Saramin] 파싱 오류 항목 스킵: {e}")

    return jobs


async def crawl_saramin(
    keyword: str = "프론트엔드",
    max_pages: int = 3,
    per_page: int = 40,
) -> list[JobListing]:
    """
    사람인에서 대기업(1000인+) 채용공고 크롤링.

    Args:
        keyword: 검색 키워드 (기본: 프론트엔드)
        max_pages: 최대 크롤링 페이지 수
        per_page: 페이지당 공고 수 (최대 40)

    Returns:
        JobListing 목록
    """
    jobs: list[JobListing] = []

    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            url = SARAMIN_SEARCH_URL.format(
                keyword=keyword, per_page=per_page, page=page
            )
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"[Saramin] HTTP {resp.status_code} (page {page})")
                    break

                page_jobs = _parse_saramin_page(resp.text)
                logger.info(f"[Saramin] page {page}: {len(page_jobs)}건")

                if not page_jobs:
                    break  # 더 이상 결과 없음

                jobs.extend(page_jobs)

                if page < max_pages:
                    await asyncio.sleep(REQUEST_DELAY)

            except httpx.RequestError as e:
                logger.error(f"[Saramin] 요청 오류 (page {page}): {e}")
                break

    logger.info(f"[Saramin] 총 {len(jobs)}건 수집")
    return jobs


# ── 원티드 크롤러 (#47) ───────────────────────────────────────────────────────

# 원티드 공개 API
# tag_type_slugs[]=518: 프론트엔드 개발자
# years=-1: 전체 경력
# job_sort: job.latest_order = 최신순
WANTED_API_URL = (
    "https://www.wanted.co.kr/api/v4/jobs"
    "?job_sort=job.latest_order"
    "&years=-1"
    "&tag_type_slugs[]=518"
    "&limit={limit}"
    "&offset={offset}"
)

WANTED_HEADERS = {
    **HEADERS,
    "wanted-client-id": "wanted-jobs-web",
    "wanted-client-version": "0.0.1",
}

# 원티드 회사 규모 필터 (employees_count 기준)
WANTED_MIN_EMPLOYEES = 1000


def _parse_wanted_deadline(date_str: Optional[str]) -> Optional[date]:
    """원티드 마감일 ISO 문자열 → date"""
    if not date_str or date_str.lower() == "none":
        return None
    try:
        return datetime.fromisoformat(date_str.rstrip("Z")).date()
    except (ValueError, AttributeError):
        return None


async def crawl_wanted(
    max_pages: int = 3,
    per_page: int = 100,
) -> list[JobListing]:
    """
    원티드 공개 API로 프론트엔드 채용공고 크롤링.
    직원수 1000명+ 필터링은 응답 데이터 기준.

    Args:
        max_pages: 최대 페이지 수
        per_page: 페이지당 공고 수

    Returns:
        JobListing 목록 (대기업만)
    """
    jobs: list[JobListing] = []

    async with httpx.AsyncClient(headers=WANTED_HEADERS, timeout=15) as client:
        for page in range(max_pages):
            offset = page * per_page
            url = WANTED_API_URL.format(limit=per_page, offset=offset)
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"[Wanted] HTTP {resp.status_code} (offset {offset})")
                    break

                data = resp.json()
                items = data.get("data", [])
                if not items:
                    break

                for item in items:
                    company = item.get("company", {})
                    employees = company.get("employees_count") or 0

                    # 직원수 1000명+ 필터
                    if employees < WANTED_MIN_EMPLOYEES:
                        continue

                    job_id = item.get("id")
                    title = item.get("position", "")
                    company_name = company.get("name", "")
                    deadline_str = item.get("due_time")
                    location = item.get("address", {}).get("location", "")

                    if not (job_id and title and company_name):
                        continue

                    jobs.append(JobListing(
                        site="wanted",
                        company=company_name,
                        title=title,
                        url=f"https://www.wanted.co.kr/wd/{job_id}",
                        deadline=_parse_wanted_deadline(deadline_str),
                        career="",
                        location=location,
                    ))

                logger.info(f"[Wanted] offset {offset}: {len(items)}건 조회, 대기업 필터 후 {len(jobs)}건 누적")

                if len(items) < per_page:
                    break

                if page < max_pages - 1:
                    await asyncio.sleep(REQUEST_DELAY)

            except httpx.RequestError as e:
                logger.error(f"[Wanted] 요청 오류 (offset {offset}): {e}")
                break

    logger.info(f"[Wanted] 총 {len(jobs)}건 수집")
    return jobs


# ── 통합 크롤러 ───────────────────────────────────────────────────────────────

async def crawl_all_jobs(
    keyword: str = "프론트엔드",
    saramin_pages: int = 3,
    wanted_pages: int = 3,
) -> list[JobListing]:
    """
    사람인 + 원티드 동시 크롤링 후 URL 기준 중복 제거.

    Returns:
        중복 제거된 JobListing 목록
    """
    saramin_task = crawl_saramin(keyword=keyword, max_pages=saramin_pages)
    wanted_task = crawl_wanted(max_pages=wanted_pages)

    saramin_jobs, wanted_jobs = await asyncio.gather(saramin_task, wanted_task)
    all_jobs = saramin_jobs + wanted_jobs

    # URL 기준 중복 제거
    seen: set[str] = set()
    unique: list[JobListing] = []
    for job in all_jobs:
        if job.url not in seen:
            seen.add(job.url)
            unique.append(job)

    logger.info(f"[CrawlAll] 사람인 {len(saramin_jobs)} + 원티드 {len(wanted_jobs)} → 중복 제거 후 {len(unique)}건")
    return unique
