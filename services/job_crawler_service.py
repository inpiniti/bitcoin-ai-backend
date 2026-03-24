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

# #52 원티드 공개 API (v4 → job_category_ids 파라미터)
# job_category_ids[]=518: 프론트엔드 개발자
# years=-1: 전체 경력
# job_sort: job.latest_order = 최신순
WANTED_API_URL = (
    "https://www.wanted.co.kr/api/v4/jobs"
    "?job_sort=job.latest_order"
    "&years=-1"
    "&job_category_ids[]=518"
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


# ── #51 IT 직군 필터 ──────────────────────────────────────────────────────────

# 통과 조건: 제목에 아래 키워드 중 하나 이상 포함
IT_ALLOW_KEYWORDS = [
    "프론트엔드", "frontend", "front-end",
    "react", "vue", "angular", "next.js", "nuxt",
    "javascript", "typescript", "웹 개발", "웹개발",
    "ui개발", "ui 개발",
]

# 차단 조건: IT 키워드가 있더라도 아래 키워드가 포함되면 제외
IT_BLOCK_KEYWORDS = [
    "호텔", "카지노", "프런트데스크", "프런트 데스크",
    "안내", "리셉션", "접수", "프론트 직원", "프론트직원",
]


def filter_it_jobs(jobs: list[JobListing], block_only: bool = False) -> list[JobListing]:
    """
    #51 #53 IT 개발 직군 공고 필터링.

    Args:
        jobs: 필터링할 공고 목록
        block_only: True이면 BLOCK 키워드만 적용 (사람인·잡코리아 등 키워드 검색 기반 크롤러용).
                    False(기본)이면 ALLOW 키워드도 검사 (전체 공고 API 기반 크롤러용).

    - IT_BLOCK_KEYWORDS 포함 → 무조건 차단
    - block_only=False일 때 IT_ALLOW_KEYWORDS 미포함 → 차단
    """
    result = []
    for job in jobs:
        title_lower = job.title.lower()
        if any(kw in title_lower for kw in IT_BLOCK_KEYWORDS):
            logger.debug(f"[Filter] 차단: {job.title}")
            continue
        if block_only or any(kw in title_lower for kw in IT_ALLOW_KEYWORDS):
            result.append(job)
        else:
            logger.debug(f"[Filter] IT 키워드 없음, 스킵: {job.title}")
    return result


def _filter_saramin(jobs: list[JobListing]) -> list[JobListing]:
    """#53 사람인 전용 필터: 이미 키워드 검색이므로 block_only=True"""
    return filter_it_jobs(jobs, block_only=True)


# ── #54 잡코리아 크롤러 ───────────────────────────────────────────────────────

JOBKOREA_BASE = "https://www.jobkorea.co.kr"

# Pfd_cd=12000: 대기업 필터 (추정값, 실 적용 시 확인 필요)
JOBKOREA_SEARCH_URL = (
    JOBKOREA_BASE
    + "/Search/"
    "?stext={keyword}"
    "&Pfd_cd=12000"
    "&ord=RegDt"
    "&page={page}"
)


def _parse_jobkorea_page(html: str) -> list[JobListing]:
    """잡코리아 검색 결과 HTML 파싱"""
    soup = BeautifulSoup(html, "lxml")
    jobs: list[JobListing] = []

    for card in soup.select(".post-list-card"):
        try:
            corp_tag = card.select_one(".post-list-corp-name")
            company = corp_tag.get_text(strip=True) if corp_tag else ""

            title_tag = card.select_one(".post-list-info-title")
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            href = title_tag.get("href", "")
            url = JOBKOREA_BASE + href if href.startswith("/") else href

            date_tag = card.select_one(".post-list-info-recruit-meta .date")
            deadline = None
            if date_tag:
                try:
                    from datetime import datetime as _dt
                    deadline = _dt.strptime(date_tag.get_text(strip=True), "%Y.%m.%d").date()
                except ValueError:
                    pass

            if company and title and url:
                jobs.append(JobListing(
                    site="jobkorea",
                    company=company,
                    title=title,
                    url=url,
                    deadline=deadline,
                ))
        except Exception as e:
            logger.debug(f"[Jobkorea] 파싱 오류 스킵: {e}")

    return jobs


async def crawl_jobkorea(
    keyword: str = "프론트엔드",
    max_pages: int = 3,
) -> list[JobListing]:
    """잡코리아 대기업 채용공고 크롤링."""
    jobs: list[JobListing] = []

    async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            url = JOBKOREA_SEARCH_URL.format(keyword=keyword, page=page)
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"[Jobkorea] HTTP {resp.status_code} (page {page})")
                    break

                page_jobs = _parse_jobkorea_page(resp.text)
                logger.info(f"[Jobkorea] page {page}: {len(page_jobs)}건")
                if not page_jobs:
                    break

                jobs.extend(page_jobs)

                if page < max_pages:
                    await asyncio.sleep(REQUEST_DELAY)

            except httpx.RequestError as e:
                logger.error(f"[Jobkorea] 요청 오류 (page {page}): {e}")
                break

    logger.info(f"[Jobkorea] 총 {len(jobs)}건 수집")
    return jobs


# ── #55 점핏(Jumpit) 크롤러 ───────────────────────────────────────────────────

JUMPIT_BASE = "https://jumpit.saramin.co.kr"

# occupationCode=5: 프론트엔드 개발자 (점핏 직군 코드)
JUMPIT_API_URL = (
    JUMPIT_BASE
    + "/api/v2/position"
    "?sort=rsp_rate"
    "&occupationCode=5"
    "&page={page}"
)

JUMPIT_HEADERS = {
    **HEADERS,
    "Referer": "https://www.jumpit.co.kr/",
}


def _parse_jumpit_response(data: dict) -> list[JobListing]:
    """점핏 API JSON 응답 파싱"""
    jobs: list[JobListing] = []
    for item in data.get("result", []):
        try:
            job_id = item.get("id")
            title = item.get("title", "")
            company_name = item.get("company", {}).get("name", "")
            closed_at = item.get("closedAt")
            locations = item.get("locations", [])
            location = locations[0].get("name", "") if locations else ""

            if not (job_id and title and company_name):
                continue

            deadline = None
            if closed_at:
                try:
                    from datetime import datetime as _dt
                    deadline = _dt.fromisoformat(closed_at.rstrip("Z")).date()
                except (ValueError, AttributeError):
                    pass

            jobs.append(JobListing(
                site="jumpit",
                company=company_name,
                title=title,
                url=f"{JUMPIT_BASE}/position/{job_id}",
                deadline=deadline,
                location=location,
            ))
        except Exception as e:
            logger.debug(f"[Jumpit] 파싱 오류 스킵: {e}")

    return jobs


async def crawl_jumpit(max_pages: int = 3) -> list[JobListing]:
    """점핏 IT 특화 채용공고 크롤링 (프론트엔드 직군)."""
    jobs: list[JobListing] = []

    async with httpx.AsyncClient(headers=JUMPIT_HEADERS, timeout=15) as client:
        for page in range(1, max_pages + 1):
            url = JUMPIT_API_URL.format(page=page)
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"[Jumpit] HTTP {resp.status_code} (page {page})")
                    break

                data = resp.json()
                page_jobs = _parse_jumpit_response(data)
                logger.info(f"[Jumpit] page {page}: {len(page_jobs)}건")

                if not page_jobs:
                    break

                jobs.extend(page_jobs)

                if page < max_pages:
                    await asyncio.sleep(REQUEST_DELAY)

            except httpx.RequestError as e:
                logger.error(f"[Jumpit] 요청 오류 (page {page}): {e}")
                break

    logger.info(f"[Jumpit] 총 {len(jobs)}건 수집")
    return jobs


# ── 통합 크롤러 ───────────────────────────────────────────────────────────────

async def crawl_all_jobs(
    keyword: str = "프론트엔드",
    saramin_pages: int = 3,
    wanted_pages: int = 3,
    jobkorea_pages: int = 3,
    jumpit_pages: int = 3,
) -> list[JobListing]:
    """
    사람인 + 원티드 + 잡코리아 + 점핏 동시 크롤링 후 URL 기준 중복 제거.

    - 키워드 검색 기반(사람인·잡코리아): block_only 필터
    - 전체 공고 API 기반(원티드·점핏): allow+block 필터

    Returns:
        중복 제거된 JobListing 목록
    """
    saramin_task = crawl_saramin(keyword=keyword, max_pages=saramin_pages)
    wanted_task = crawl_wanted(max_pages=wanted_pages)
    jobkorea_task = crawl_jobkorea(keyword=keyword, max_pages=jobkorea_pages)
    jumpit_task = crawl_jumpit(max_pages=jumpit_pages)

    saramin_jobs, wanted_jobs, jobkorea_jobs, jumpit_jobs = await asyncio.gather(
        saramin_task, wanted_task, jobkorea_task, jumpit_task
    )

    # #53 사이트별 필터 전략 분리
    keyword_based = _filter_saramin(saramin_jobs) + filter_it_jobs(jobkorea_jobs, block_only=True)
    api_based = filter_it_jobs(wanted_jobs) + filter_it_jobs(jumpit_jobs)
    all_jobs = keyword_based + api_based

    # URL 기준 중복 제거
    seen: set[str] = set()
    unique: list[JobListing] = []
    for job in all_jobs:
        if job.url not in seen:
            seen.add(job.url)
            unique.append(job)

    logger.info(
        f"[CrawlAll] 사람인 {len(saramin_jobs)} + 원티드 {len(wanted_jobs)}"
        f" + 잡코리아 {len(jobkorea_jobs)} + 점핏 {len(jumpit_jobs)}"
        f" → IT 필터 + 중복 제거 후 {len(unique)}건"
    )
    return unique
