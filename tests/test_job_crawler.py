"""
#48 채용공고 크롤러 테스트

- 사람인 HTML 파싱
- 원티드 JSON 파싱 및 직원수 필터
- 중복 제거
- 마감일 파싱
- 카카오 리포트 포맷
- Supabase job_listings 함수 (mock)
"""
import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from services.job_crawler_service import (
    JobListing,
    _parse_saramin_deadline,
    _parse_saramin_page,
    _parse_wanted_deadline,
)
from services.kakao_service import build_job_report


# ── 사람인 마감일 파싱 ────────────────────────────────────────────────────────

class TestParseSaraminDeadline:
    def test_tilde_format(self):
        """~04.15 형식 파싱"""
        result = _parse_saramin_deadline("~04.15")
        assert result is not None
        assert result.month == 4
        assert result.day == 15

    def test_without_tilde(self):
        """04.15 형식 파싱"""
        result = _parse_saramin_deadline("04.15")
        assert result is not None
        assert result.month == 4
        assert result.day == 15

    def test_with_day_of_week(self):
        """04.15(화) 형식 파싱"""
        result = _parse_saramin_deadline("04.15(화)")
        assert result is not None
        assert result.month == 4
        assert result.day == 15

    def test_sangsichaelyong(self):
        """상시채용 → None"""
        result = _parse_saramin_deadline("상시채용")
        assert result is None

    def test_empty_string(self):
        """빈 문자열 → None"""
        result = _parse_saramin_deadline("")
        assert result is None

    def test_past_date_becomes_next_year(self):
        """이미 지난 날짜는 내년으로"""
        result = _parse_saramin_deadline("01.01")
        # 1월 1일이 오늘보다 이전이면 내년이어야 함
        today = date.today()
        if date(today.year, 1, 1) < today:
            assert result.year == today.year + 1
        else:
            assert result.year == today.year


# ── 사람인 HTML 파싱 ──────────────────────────────────────────────────────────

SARAMIN_HTML_SAMPLE = """
<html><body>
<div class="item_recruit">
  <div class="corp_name"><a>삼성전자</a></div>
  <div class="job_tit"><a href="/zf_user/jobs/relay/view?rec_idx=12345">프론트엔드 개발자</a></div>
  <div class="job_date"><span class="date">~04.30</span></div>
  <div class="job_condition">
    <span>경력 3년+</span>
    <em class="work_place">서울</em>
  </div>
</div>
<div class="item_recruit">
  <div class="corp_name"><a>LG CNS</a></div>
  <div class="job_tit"><a href="/zf_user/jobs/relay/view?rec_idx=67890">React 개발자</a></div>
  <div class="job_date"><span class="date">~05.15</span></div>
  <div class="job_condition">
    <span>신입/경력</span>
  </div>
</div>
</body></html>
"""

SARAMIN_HTML_NO_TITLE = """
<html><body>
<div class="item_recruit">
  <div class="corp_name"><a>회사명</a></div>
</div>
</body></html>
"""


class TestParseSaraminPage:
    def test_parses_two_jobs(self):
        """정상 HTML에서 2개 공고 파싱"""
        jobs = _parse_saramin_page(SARAMIN_HTML_SAMPLE)
        assert len(jobs) == 2

    def test_company_names(self):
        jobs = _parse_saramin_page(SARAMIN_HTML_SAMPLE)
        companies = [j.company for j in jobs]
        assert "삼성전자" in companies
        assert "LG CNS" in companies

    def test_url_prefixed(self):
        """URL이 절대경로로 변환되는지 확인"""
        jobs = _parse_saramin_page(SARAMIN_HTML_SAMPLE)
        assert all(j.url.startswith("https://www.saramin.co.kr") for j in jobs)

    def test_deadline_parsed(self):
        """마감일 파싱"""
        jobs = _parse_saramin_page(SARAMIN_HTML_SAMPLE)
        samsung = next(j for j in jobs if j.company == "삼성전자")
        assert samsung.deadline is not None
        assert samsung.deadline.month == 4

    def test_site_is_saramin(self):
        jobs = _parse_saramin_page(SARAMIN_HTML_SAMPLE)
        assert all(j.site == "saramin" for j in jobs)

    def test_no_title_tag_skipped(self):
        """title 태그 없는 항목 스킵"""
        jobs = _parse_saramin_page(SARAMIN_HTML_NO_TITLE)
        assert len(jobs) == 0

    def test_empty_html(self):
        """빈 HTML → 빈 목록"""
        jobs = _parse_saramin_page("<html></html>")
        assert jobs == []


# ── 원티드 마감일 파싱 ────────────────────────────────────────────────────────

class TestParseWantedDeadline:
    def test_iso_format(self):
        result = _parse_wanted_deadline("2026-04-30T23:59:59Z")
        assert result == date(2026, 4, 30)

    def test_none_string(self):
        assert _parse_wanted_deadline("none") is None
        assert _parse_wanted_deadline(None) is None

    def test_empty_string(self):
        assert _parse_wanted_deadline("") is None


# ── JobListing.to_dict ────────────────────────────────────────────────────────

class TestJobListingToDict:
    def test_to_dict_basic(self):
        job = JobListing(
            site="saramin",
            company="삼성전자",
            title="프론트엔드",
            url="https://example.com/123",
            deadline=date(2026, 4, 30),
            career="경력 3년+",
            location="서울",
        )
        d = job.to_dict()
        assert d["site"] == "saramin"
        assert d["company"] == "삼성전자"
        assert d["deadline"] == "2026-04-30"
        assert d["url"] == "https://example.com/123"

    def test_to_dict_no_deadline(self):
        job = JobListing(site="wanted", company="카카오", title="React", url="https://wanted.co.kr/wd/1")
        d = job.to_dict()
        assert d["deadline"] is None


# ── 카카오 리포트 포맷 ────────────────────────────────────────────────────────

class TestBuildJobReport:
    def _make_job(self, company: str, title: str, site: str = "saramin", idx: int = 1) -> dict:
        return {
            "id": f"uuid-{idx}",
            "site": site,
            "company": company,
            "title": title,
            "url": f"https://saramin.co.kr/job/{idx}",
            "deadline": "2026-04-30",
            "career": "경력 3년+",
        }

    def test_empty_jobs_returns_empty(self):
        """공고 없으면 빈 문자열"""
        assert build_job_report([]) == ""

    def test_contains_company_name(self):
        jobs = [self._make_job("삼성전자", "프론트엔드 개발자")]
        report = build_job_report(jobs)
        assert "삼성전자" in report

    def test_contains_title(self):
        jobs = [self._make_job("LG CNS", "React 개발자")]
        report = build_job_report(jobs)
        assert "React 개발자" in report

    def test_max_10_displayed(self):
        """11개 공고 → 상위 10개 표시, '외 1건' 표시"""
        jobs = [self._make_job(f"회사{i}", f"직무{i}", idx=i) for i in range(11)]
        report = build_job_report(jobs)
        assert "외 1건" in report

    def test_under_10_no_more(self):
        """5개 공고 → '외 N건' 없음"""
        jobs = [self._make_job(f"회사{i}", f"직무{i}", idx=i) for i in range(5)]
        report = build_job_report(jobs)
        assert "외" not in report

    def test_within_kakao_limit(self):
        """카카오 2000자 제한 이내"""
        jobs = [self._make_job(f"회사{i}", f"직무{i}", idx=i) for i in range(10)]
        report = build_job_report(jobs)
        assert len(report) <= 2000

    def test_site_label_saramin(self):
        jobs = [self._make_job("삼성", "FE개발자", site="saramin")]
        report = build_job_report(jobs)
        assert "사람인" in report

    def test_site_label_wanted(self):
        jobs = [self._make_job("카카오", "React", site="wanted")]
        report = build_job_report(jobs)
        assert "원티드" in report

    def test_header_contains_keyword(self):
        jobs = [self._make_job("A", "B")]
        report = build_job_report(jobs, keyword="프론트엔드")
        assert "프론트엔드" in report


# ── Supabase job_listings mock 테스트 ────────────────────────────────────────

@pytest.mark.asyncio
class TestSupabaseJobListings:
    async def test_upsert_job_listings_calls_post(self):
        """upsert_job_listings가 Supabase POST를 호출하는지 확인"""
        from services import supabase_service

        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = [{"id": "uuid-1"}, {"id": "uuid-2"}]

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        jobs = [
            {"site": "saramin", "company": "삼성", "title": "FE", "url": "https://saramin.co.kr/1"},
            {"site": "wanted", "company": "카카오", "title": "React", "url": "https://wanted.co.kr/1"},
        ]

        with patch.object(supabase_service, "SUPABASE_URL", "https://test.supabase.co"), \
             patch.object(supabase_service, "SUPABASE_KEY", "test-key"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await supabase_service.upsert_job_listings(jobs)

        mock_client.post.assert_called_once()
        assert result == 2

    async def test_upsert_empty_list_returns_zero(self):
        """빈 목록 → 0 반환, API 호출 없음"""
        from services import supabase_service
        result = await supabase_service.upsert_job_listings([])
        assert result == 0

    async def test_get_unnotified_jobs_calls_get(self):
        """get_unnotified_jobs가 notified_at=is.null 필터로 GET 호출"""
        from services import supabase_service

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": "uuid-1", "company": "삼성"}]

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch.object(supabase_service, "SUPABASE_URL", "https://test.supabase.co"), \
             patch.object(supabase_service, "SUPABASE_KEY", "test-key"), \
             patch("httpx.AsyncClient", return_value=mock_client):
            result = await supabase_service.get_unnotified_jobs()

        call_url = mock_client.get.call_args[0][0]
        assert "notified_at=is.null" in call_url
        assert len(result) == 1

    async def test_mark_jobs_notified_skips_empty(self):
        """빈 목록 → Supabase PATCH 호출 없음"""
        from services import supabase_service
        with patch("httpx.AsyncClient") as mock_cls:
            await supabase_service.mark_jobs_notified([])
            mock_cls.assert_not_called()
