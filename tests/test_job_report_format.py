"""
#49 카카오 메시지 포맷 압축 TDD
#50 자세히보기 링크 URL 제어
#51 비개발 직군 공고 필터링
#56 채용 알림 메시지에 플랫폼 및 링크 누락 문제 수정

RED → GREEN 순으로 진행
"""
import pytest
from services.kakao_service import build_job_report
from services.job_crawler_service import _parse_saramin_page, JobListing


# ── 테스트용 공고 픽스처 ──────────────────────────────────────────────────────

def make_jobs(n: int, site: str = "saramin") -> list[dict]:
    return [
        {
            "id": f"uuid-{i}",
            "site": site,
            "company": f"대기업{i}",
            "title": f"프론트엔드 개발자{i}",
            "url": f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={i}",
            "deadline": "2026-04-30",
            "career": "경력 3년+",
        }
        for i in range(1, n + 1)
    ]


# ══════════════════════════════════════════════════════════════════════════════
# #49 메시지 압축: 2000자 제한 내, 카카오 미리보기에 최대한 표시
# ══════════════════════════════════════════════════════════════════════════════

class TestJobReportCompact:

    def _text(self, jobs, **kw) -> str:
        text, _ = build_job_report(jobs, **kw)
        return text

    def test_single_job_under_limit(self):
        """#49: 공고 1개 메시지가 2000자 이내"""
        jobs = make_jobs(1)
        text = self._text(jobs)
        assert len(text) <= 2000

    def test_20_jobs_under_kakao_limit(self):
        """#49: 20개 공고 메시지가 카카오 API 한도(2000자) 이내"""
        jobs = make_jobs(20)
        text = self._text(jobs)
        assert len(text) <= 2000, f"메시지 {len(text)}자 (2000자 초과)"

    def test_20_jobs_shorter_than_old_format(self):
        """#49: 20개 공고가 구 포맷(4줄×20=80줄)보다 짧아야 함 → 50줄 이내"""
        jobs = make_jobs(20)
        text = self._text(jobs)
        lines = text.splitlines()
        assert len(lines) <= 50, f"예상 50줄 이내, 실제 {len(lines)}줄"

    def test_format_contains_company_and_title(self):
        """#49: 압축 후에도 회사명과 직무명은 포함되어야 함"""
        jobs = make_jobs(1)
        text = self._text(jobs)
        assert "대기업1" in text
        assert "프론트엔드 개발자1" in text

    def test_format_contains_deadline(self):
        """#49: 마감일 포함되어야 함"""
        jobs = make_jobs(1)
        text = self._text(jobs)
        assert "04.30" in text or "2026-04-30" in text

    def test_total_count_shown(self):
        """#49: 전체 건수는 헤더에 표시되어야 함"""
        jobs = make_jobs(20)
        text = self._text(jobs)
        assert "20" in text


# ══════════════════════════════════════════════════════════════════════════════
# #56 플랫폼 태그 및 개별 링크 표시
# ══════════════════════════════════════════════════════════════════════════════

class TestJobReportPlatformAndLink:

    def _text(self, jobs, **kw) -> str:
        text, _ = build_job_report(jobs, **kw)
        return text

    def test_platform_tag_shown(self):
        """#56: 각 공고에 플랫폼 태그가 표시되어야 함"""
        jobs = make_jobs(1, site="saramin")
        text = self._text(jobs)
        assert "[사람인]" in text, f"플랫폼 태그 없음:\n{text}"

    def test_platform_tag_wanted(self):
        """#56: 원티드 플랫폼 태그 표시"""
        jobs = make_jobs(1, site="wanted")
        text = self._text(jobs)
        assert "[원티드]" in text

    def test_platform_tag_jobkorea(self):
        """#56: 잡코리아 플랫폼 태그 표시"""
        jobs = make_jobs(1, site="jobkorea")
        text = self._text(jobs)
        assert "[잡코리아]" in text

    def test_platform_tag_jumpit(self):
        """#56: 점핏 플랫폼 태그 표시"""
        jobs = make_jobs(1, site="jumpit")
        text = self._text(jobs)
        assert "[점핏]" in text

    def test_url_shown_per_item(self):
        """#56: 각 공고에 🔗 링크 줄이 있어야 함"""
        jobs = make_jobs(1)
        text = self._text(jobs)
        assert "🔗" in text, f"링크 없음:\n{text}"
        assert "https://" in text

    def test_url_on_separate_line(self):
        """#56: URL은 공고 제목 줄과 별도 줄에 표시"""
        jobs = make_jobs(1)
        text = self._text(jobs)
        digit_lines = [l for l in text.splitlines() if l and l[0].isdigit()]
        for line in digit_lines:
            assert "https://" not in line, f"URL이 번호 줄에 포함됨: {line}"

    def test_url_not_shown_when_missing(self):
        """#56: url 필드 없으면 🔗 줄 생략"""
        jobs = [{"site": "saramin", "company": "A", "title": "개발자", "url": "", "deadline": None}]
        text = self._text(jobs)
        assert "🔗" not in text


# ══════════════════════════════════════════════════════════════════════════════
# #50 자세히보기 링크: web_url 제어
# ══════════════════════════════════════════════════════════════════════════════

class TestJobReportWebUrl:

    def test_build_job_report_returns_web_url(self):
        """#50: build_job_report가 web_url도 함께 반환해야 함"""
        jobs = make_jobs(1)
        # 현재 build_job_report는 str만 반환 → 실패 예상
        result = build_job_report(jobs)
        assert isinstance(result, tuple), "web_url 포함을 위해 (text, url) 튜플 반환 필요"

    def test_web_url_points_to_saramin_search(self):
        """#50: web_url은 사람인 검색 결과 페이지여야 함"""
        jobs = make_jobs(1)
        result = build_job_report(jobs)
        assert isinstance(result, tuple)
        _, web_url = result
        assert "saramin.co.kr" in web_url


# ══════════════════════════════════════════════════════════════════════════════
# #51 비개발 직군 필터: IT 키워드 검증
# ══════════════════════════════════════════════════════════════════════════════

NON_IT_HTML = """
<html><body>
<div class="item_recruit">
  <div class="corp_name"><a>파라다이스호텔</a></div>
  <div class="job_tit"><a href="/zf_user/jobs/relay/view?rec_idx=1">카지노&amp;호텔 프론트 직원 모집</a></div>
  <div class="job_date"><span class="date">~04.30</span></div>
  <div class="job_condition"><span>신입/경력</span></div>
</div>
<div class="item_recruit">
  <div class="corp_name"><a>삼성전자</a></div>
  <div class="job_tit"><a href="/zf_user/jobs/relay/view?rec_idx=2">프론트엔드 개발자 (React)</a></div>
  <div class="job_date"><span class="date">~04.30</span></div>
  <div class="job_condition"><span>경력 3년+</span></div>
</div>
<div class="item_recruit">
  <div class="corp_name"><a>롯데호텔</a></div>
  <div class="job_tit"><a href="/zf_user/jobs/relay/view?rec_idx=3">프런트 데스크 안내 직원</a></div>
  <div class="job_date"><span class="date">~05.01</span></div>
  <div class="job_condition"><span>신입</span></div>
</div>
</body></html>
"""


class TestNonItJobFilter:

    def test_parse_without_filter_returns_all(self):
        """필터 없으면 3개 모두 파싱됨 (현재 동작 확인)"""
        jobs = _parse_saramin_page(NON_IT_HTML)
        assert len(jobs) == 3

    def test_it_keyword_filter_removes_non_it(self):
        """#51: IT 키워드 필터 적용 시 카지노·호텔 공고 제외, 삼성전자만 통과"""
        from services.job_crawler_service import filter_it_jobs
        jobs = _parse_saramin_page(NON_IT_HTML)
        filtered = filter_it_jobs(jobs)
        assert len(filtered) == 1
        assert filtered[0].company == "삼성전자"

    def test_react_keyword_passes(self):
        """#51: React 포함 공고는 통과"""
        from services.job_crawler_service import filter_it_jobs
        job = JobListing(site="saramin", company="A", title="React 개발자", url="https://x.com/1")
        assert len(filter_it_jobs([job])) == 1

    def test_hotel_front_blocked(self):
        """#51: '호텔 프론트' 공고는 차단"""
        from services.job_crawler_service import filter_it_jobs
        job = JobListing(site="saramin", company="B", title="호텔 프론트 직원", url="https://x.com/2")
        assert len(filter_it_jobs([job])) == 0

    def test_frontend_dev_passes(self):
        """#51: '프론트엔드 개발자' 공고는 통과"""
        from services.job_crawler_service import filter_it_jobs
        job = JobListing(site="saramin", company="C", title="프론트엔드 개발자", url="https://x.com/3")
        assert len(filter_it_jobs([job])) == 1

    def test_typescript_passes(self):
        """#51: TypeScript 포함 공고 통과"""
        from services.job_crawler_service import filter_it_jobs
        job = JobListing(site="saramin", company="D", title="TypeScript 웹 개발자", url="https://x.com/4")
        assert len(filter_it_jobs([job])) == 1

    def test_casino_front_blocked(self):
        """#51: '카지노 프론트' 공고 차단"""
        from services.job_crawler_service import filter_it_jobs
        job = JobListing(site="saramin", company="E", title="카지노&호텔 프론트 직원", url="https://x.com/5")
        assert len(filter_it_jobs([job])) == 0
