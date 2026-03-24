"""
#49 카카오 메시지 포맷 압축 TDD
#50 자세히보기 링크 URL 제어
#51 비개발 직군 공고 필터링

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
# #49 메시지 압축: 항목당 1줄, 20개도 카카오 미리보기에 들어오도록
# ══════════════════════════════════════════════════════════════════════════════

class TestJobReportCompact:

    def _text(self, jobs, **kw) -> str:
        text, _ = build_job_report(jobs, **kw)
        return text

    def test_single_job_occupies_one_line(self):
        """#49: 공고 1개는 목록에서 정확히 1줄만 차지해야 함"""
        jobs = make_jobs(1)
        text = self._text(jobs)
        lines = text.splitlines()
        # 헤더 2줄(제목+구분선) + 공고 1줄 + 푸터 2줄(구분선+서명) = 5줄
        assert len(lines) == 5, f"예상 5줄, 실제 {len(lines)}줄:\n{text}"

    def test_no_url_per_item(self):
        """#49: 개별 항목 줄에 URL이 없어야 함"""
        jobs = make_jobs(3)
        text = self._text(jobs)
        job_lines = [l for l in text.splitlines() if l and l[0].isdigit()]
        for line in job_lines:
            assert "https://" not in line, f"URL이 공고 줄에 포함됨: {line}"

    def test_20_jobs_under_kakao_limit(self):
        """#49: 20개 공고 메시지가 카카오 API 한도(2000자) 이내"""
        jobs = make_jobs(20)
        text = self._text(jobs)
        assert len(text) <= 2000, f"메시지 {len(text)}자 (2000자 초과)"

    def test_20_jobs_much_shorter_than_old_format(self):
        """#49: 20개 공고가 구 포맷(4줄×20=80줄)보다 훨씬 짧아야 함 → 20줄 이내"""
        jobs = make_jobs(20)
        text = self._text(jobs)
        lines = text.splitlines()
        assert len(lines) <= 25, f"예상 25줄 이내, 실제 {len(lines)}줄"

    def test_5_jobs_line_count(self):
        """#49: 5개 공고는 10줄 이내 (구 포맷은 5×4=20줄)"""
        jobs = make_jobs(5)
        text = self._text(jobs)
        lines = text.splitlines()
        assert len(lines) <= 10, f"예상 10줄 이내, 실제 {len(lines)}줄"

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
