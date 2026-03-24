"""
#52 원티드 API 수정
#53 IT 필터 block_only 모드
#54 잡코리아 크롤러
#55 점핏 크롤러
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from services.job_crawler_service import (
    JobListing,
    filter_it_jobs,
    _parse_saramin_page,
)


# ══════════════════════════════════════════════════════════════════════════════
# #53 IT 필터 block_only 모드
# ══════════════════════════════════════════════════════════════════════════════

class TestFilterItJobsBlockOnly:

    def _job(self, title: str, site: str = "saramin") -> JobListing:
        return JobListing(site=site, company="테스트", title=title, url=f"https://x.com/{title}")

    def test_block_only_false_by_default_requires_allow_keyword(self):
        """기본(block_only=False): allow 키워드 없으면 차단"""
        job = self._job("신입 공채 모집")
        assert filter_it_jobs([job]) == []

    def test_block_only_true_passes_without_allow_keyword(self):
        """#53: block_only=True이면 allow 키워드 없어도 통과"""
        job = self._job("신입 공채 모집")
        result = filter_it_jobs([job], block_only=True)
        assert len(result) == 1

    def test_block_only_true_still_blocks_hotel(self):
        """#53: block_only=True라도 호텔 프론트는 차단"""
        job = self._job("호텔 프론트 직원")
        assert filter_it_jobs([job], block_only=True) == []

    def test_block_only_true_blocks_casino(self):
        """#53: block_only=True라도 카지노는 차단"""
        job = self._job("카지노 딜러 및 프론트 모집")
        assert filter_it_jobs([job], block_only=True) == []

    def test_block_only_true_passes_react_job(self):
        """#53: React 직무는 block_only=True에서 통과"""
        job = self._job("React 시니어 개발자 모집")
        assert len(filter_it_jobs([job], block_only=True)) == 1

    def test_saramin_crawl_uses_block_only(self):
        """#53: crawl_all_jobs 내부에서 saramin은 block_only=True로 처리돼야 함"""
        from services import job_crawler_service
        # saramin은 이미 키워드 검색이므로 allow 체크 불필요
        # 제목에 IT 키워드 없어도 block 키워드만 없으면 통과
        saramin_jobs = [
            JobListing(site="saramin", company="삼성SDS", title="채용 공고", url="https://x.com/1"),
            JobListing(site="saramin", company="파라다이스호텔", title="호텔 프론트 직원", url="https://x.com/2"),
        ]
        filtered = job_crawler_service._filter_saramin(saramin_jobs)
        assert len(filtered) == 1
        assert filtered[0].company == "삼성SDS"


# ══════════════════════════════════════════════════════════════════════════════
# #52 원티드 API - 새 파라미터
# ══════════════════════════════════════════════════════════════════════════════

class TestWantedCrawler:

    def test_wanted_url_uses_job_category(self):
        """#52: 원티드 URL이 job_category_ids 파라미터를 사용해야 함"""
        from services.job_crawler_service import WANTED_API_URL
        assert "job_category_ids" in WANTED_API_URL or "tag_ids" in WANTED_API_URL, \
            "원티드 API URL이 구버전 파라미터(tag_type_slugs)를 사용 중"

    @pytest.mark.asyncio
    async def test_wanted_422_handled_gracefully(self):
        """#52: 422 응답 시 빈 목록 반환 (예외 없음)"""
        from services import job_crawler_service

        mock_resp = MagicMock()
        mock_resp.status_code = 422

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await job_crawler_service.crawl_wanted()

        assert result == []

    @pytest.mark.asyncio
    async def test_wanted_filters_small_companies(self):
        """원티드: 직원수 1000명 미만 회사 제외"""
        from services import job_crawler_service

        mock_data = {"data": [
            {"id": 1, "position": "FE", "company": {"name": "스타트업", "employees_count": 50}, "due_time": None, "address": {}},
            {"id": 2, "position": "FE", "company": {"name": "대기업", "employees_count": 2000}, "due_time": None, "address": {}},
        ]}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_data

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await job_crawler_service.crawl_wanted(max_pages=1)

        assert len(result) == 1
        assert result[0].company == "대기업"


# ══════════════════════════════════════════════════════════════════════════════
# #54 잡코리아 크롤러
# ══════════════════════════════════════════════════════════════════════════════

JOBKOREA_HTML = """
<html><body>
<div class="tplList">
  <div class="post-list-card">
    <a class="post-list-corp-name" href="#">카카오</a>
    <a class="post-list-info-title" href="/Recruit/GI_Read/12345">프론트엔드 개발자 (React)</a>
    <span class="post-list-info-recruit-meta">
      <em>경력 3년+</em>
      <em>서울</em>
      <em class="date">2026.04.30</em>
    </span>
  </div>
  <div class="post-list-card">
    <a class="post-list-corp-name" href="#">네이버</a>
    <a class="post-list-info-title" href="/Recruit/GI_Read/67890">Vue.js 웹 개발자</a>
    <span class="post-list-info-recruit-meta">
      <em>신입/경력</em>
      <em>성남</em>
      <em class="date">2026.05.15</em>
    </span>
  </div>
</div>
</body></html>
"""


class TestJobkoreaCrawler:

    def test_parse_jobkorea_page_exists(self):
        """#54: _parse_jobkorea_page 함수가 존재해야 함"""
        from services.job_crawler_service import _parse_jobkorea_page
        assert callable(_parse_jobkorea_page)

    def test_parse_jobkorea_returns_jobs(self):
        """#54: HTML 파싱 결과 2개 공고 반환"""
        from services.job_crawler_service import _parse_jobkorea_page
        jobs = _parse_jobkorea_page(JOBKOREA_HTML)
        assert len(jobs) == 2

    def test_parse_jobkorea_company_names(self):
        """#54: 회사명 파싱"""
        from services.job_crawler_service import _parse_jobkorea_page
        jobs = _parse_jobkorea_page(JOBKOREA_HTML)
        companies = [j.company for j in jobs]
        assert "카카오" in companies
        assert "네이버" in companies

    def test_parse_jobkorea_site_label(self):
        """#54: site = 'jobkorea'"""
        from services.job_crawler_service import _parse_jobkorea_page
        jobs = _parse_jobkorea_page(JOBKOREA_HTML)
        assert all(j.site == "jobkorea" for j in jobs)

    def test_parse_jobkorea_url_absolute(self):
        """#54: URL이 절대경로"""
        from services.job_crawler_service import _parse_jobkorea_page
        jobs = _parse_jobkorea_page(JOBKOREA_HTML)
        assert all(j.url.startswith("https://www.jobkorea.co.kr") for j in jobs)

    def test_crawl_jobkorea_function_exists(self):
        """#54: crawl_jobkorea 함수가 존재해야 함"""
        from services.job_crawler_service import crawl_jobkorea
        assert callable(crawl_jobkorea)


# ══════════════════════════════════════════════════════════════════════════════
# #55 점핏(Jumpit) 크롤러
# ══════════════════════════════════════════════════════════════════════════════

class TestJumpitCrawler:

    def test_crawl_jumpit_function_exists(self):
        """#55: crawl_jumpit 함수가 존재해야 함"""
        from services.job_crawler_service import crawl_jumpit
        assert callable(crawl_jumpit)

    def test_parse_jumpit_response(self):
        """#55: 점핏 JSON 응답 파싱"""
        from services.job_crawler_service import _parse_jumpit_response
        sample = {
            "result": [
                {
                    "id": 101,
                    "title": "프론트엔드 개발자",
                    "company": {"name": "삼성SDS"},
                    "closedAt": "2026-04-30",
                    "locations": [{"name": "서울"}],
                },
                {
                    "id": 102,
                    "title": "React 개발자",
                    "company": {"name": "LG CNS"},
                    "closedAt": None,
                    "locations": [],
                },
            ]
        }
        jobs = _parse_jumpit_response(sample)
        assert len(jobs) == 2
        assert jobs[0].site == "jumpit"
        assert jobs[0].company == "삼성SDS"
        assert jobs[1].company == "LG CNS"

    def test_parse_jumpit_url_format(self):
        """#55: 점핏 공고 URL 형식"""
        from services.job_crawler_service import _parse_jumpit_response
        sample = {"result": [{"id": 999, "title": "FE", "company": {"name": "A"}, "closedAt": None, "locations": []}]}
        jobs = _parse_jumpit_response(sample)
        assert "jumpit.saramin.co.kr" in jobs[0].url or "jumpit.co.kr" in jobs[0].url

    @pytest.mark.asyncio
    async def test_crawl_jumpit_returns_list_on_error(self):
        """#55: 오류 시 빈 목록 반환"""
        from services import job_crawler_service

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await job_crawler_service.crawl_jumpit()

        assert result == []

    def test_crawl_all_jobs_includes_jobkorea_and_jumpit(self):
        """#54 #55: crawl_all_jobs 소스 코드에 jobkorea, jumpit이 포함돼야 함"""
        import inspect
        from services.job_crawler_service import crawl_all_jobs
        src = inspect.getsource(crawl_all_jobs)
        assert "crawl_jobkorea" in src, "crawl_all_jobs에 잡코리아 미포함"
        assert "crawl_jumpit" in src, "crawl_all_jobs에 점핏 미포함"
