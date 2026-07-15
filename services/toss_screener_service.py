"""
토스증권 스크리너 서비스

토스증권의 비공개 스크리너 API를 호출해 13인의 거장 필터 세트로 종목을 조회합니다.

인증 흐름 (로그인 불필요 — 익명 세션으로 동작):
  1. GET  https://wts-api.tossinvest.com/api/v3/init
     → XSRF-TOKEN / SESSION / UTK / BTK / _browserId 쿠키를 한 번에 발급받는다
  2. POST https://wts-cert-api.tossinvest.com/api/v2/screener/screen
     → 위 쿠키 + `deviceId` 쿠키 + `x-xsrf-token` 헤더로 조회

⚠️ `deviceId` 쿠키는 init이 내려주지 않는다. 클라이언트가 직접 생성해야 하며,
   이게 없으면 서버가 400을 반환한다 (원인 메시지도 주지 않는다).

세션은 프로세스 메모리에 캐시되고 TTL이 지나면 자동 갱신된다.
필터 세트의 근거: financial 레포 docs/tossfilter.md (13인 거장 조언 종합)
"""
import asyncio
import logging
import time
import uuid
from typing import Any

import httpx

logger = logging.getLogger("toss_screener")

INIT_URL = "https://wts-api.tossinvest.com/api/v3/init"
SCREEN_URL = "https://wts-cert-api.tossinvest.com/api/v2/screener/screen"
# 종목 정보 API — productCode(US20020523001 등) → 실제 심볼(NFLX 등). 인증 불필요.
INFO_URL = "https://wts-info-api.tossinvest.com/api/v2/stock-infos"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
APP_VERSION = "v260710.1801"

SESSION_TTL_SEC = 30 * 60  # 30분마다 갱신
HTTP_TIMEOUT = 20.0

# ──────────────────────────────────────────────────────────────
# 세션 관리 (토큰 한 번 받아 재사용, TTL 만료 시 자동 재발급)
# ──────────────────────────────────────────────────────────────

_session_lock = asyncio.Lock()
_client: httpx.AsyncClient | None = None
_xsrf_token: str | None = None
_issued_at: float = 0.0
# deviceId는 프로세스 생명주기 동안 고정 (실제 앱도 기기당 하나를 유지한다)
_device_id: str = f"WTS-{uuid.uuid4().hex}"


async def _issue_session() -> None:
    """init 엔드포인트를 호출해 쿠키·XSRF 토큰을 새로 발급받는다."""
    global _client, _xsrf_token, _issued_at

    if _client is not None:
        await _client.aclose()

    _client = httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        headers={
            "user-agent": USER_AGENT,
            "accept": "application/json",
            "accept-language": "ko-KR,ko;q=0.9",
            "app-version": APP_VERSION,
            "referer": "https://www.tossinvest.com/screener",
        },
        follow_redirects=True,
    )
    # deviceId는 서버가 주지 않으므로 우리가 심는다 (없으면 screen이 400)
    _client.cookies.set("deviceId", _device_id, domain=".tossinvest.com")

    res = await _client.get(INIT_URL)
    res.raise_for_status()

    token = _client.cookies.get("XSRF-TOKEN")
    if not token:
        raise RuntimeError("토스 init 응답에 XSRF-TOKEN 쿠키가 없습니다.")

    _xsrf_token = token
    _issued_at = time.time()
    logger.info(f"[Toss] 세션 발급 완료 (deviceId={_device_id[:12]}…, ttl={SESSION_TTL_SEC}s)")


async def ensure_session(force: bool = False) -> None:
    """세션이 없거나 TTL이 지났으면 재발급한다."""
    async with _session_lock:
        expired = (time.time() - _issued_at) > SESSION_TTL_SEC
        if force or _client is None or _xsrf_token is None or expired:
            await _issue_session()


async def get_session_info() -> dict:
    """현재 세션 상태 (토큰 전체는 노출하지 않는다)."""
    age = time.time() - _issued_at if _issued_at else None
    return {
        "active": _xsrf_token is not None,
        "device_id": _device_id,
        "xsrf_token_prefix": (_xsrf_token[:8] + "…") if _xsrf_token else None,
        "age_sec": round(age) if age is not None else None,
        "ttl_sec": SESSION_TTL_SEC,
        "expires_in_sec": round(SESSION_TTL_SEC - age) if age is not None else None,
    }


# ──────────────────────────────────────────────────────────────
# 조회
# ──────────────────────────────────────────────────────────────

async def screen(
    filters: list[dict],
    nation: str = "kr",
    size: int = 50,
    page: int = 1,
    sort: dict | None = None,
) -> dict:
    """
    스크리너 조회. 세션 만료로 실패하면 한 번 재발급 후 재시도한다.

    Args:
        filters: 필터 조건 배열 (build_* 헬퍼 참조)
        nation: "kr"(국내) | "us"(해외)
        size: 페이지당 종목 수
        page: 페이지 번호 (1부터)
        sort: {"column","label","order"} — 표시 순서일 뿐 결과 집합에는 영향 없음
    """
    body = {
        "pagingParam": {"key": None, "number": page, "size": size},
        "filters": filters,
        "nation": nation,
    }
    if sort:
        body["sort"] = sort

    for attempt in (1, 2):
        await ensure_session(force=(attempt == 2))
        assert _client is not None and _xsrf_token is not None

        res = await _client.post(
            SCREEN_URL,
            json=body,
            headers={"x-xsrf-token": _xsrf_token, "content-type": "application/json"},
        )
        if res.status_code == 200:
            return res.json()

        # 400/401/403은 세션 만료로도 나타난다. 첫 시도면 세션을 새로 받아 한 번 더.
        if attempt == 1 and res.status_code in (400, 401, 403):
            logger.warning(f"[Toss] HTTP {res.status_code} — 세션 재발급 후 재시도")
            continue

        raise RuntimeError(
            f"토스 스크리너 조회 실패: HTTP {res.status_code} {res.text[:200]}"
            " (필터 id·기간 enum·단위가 올바른지 확인하세요)"
        )

    raise RuntimeError("토스 스크리너 조회 실패: 재시도 후에도 응답 없음")


def flatten_result(raw: dict) -> dict:
    """토스 응답을 프론트에서 쓰기 쉬운 평평한 구조로 변환."""
    result = raw.get("result") or {}
    stocks = []
    for s in result.get("stocks") or []:
        row = {
            "ticker": (s.get("stockCode") or "").lstrip("A"),  # A095570 → 095570
            "stockCode": s.get("stockCode"),
            "name": s.get("name"),
            "logoImageUrl": s.get("logoImageUrl"),
            "price": (s.get("base") or {}).get("krw"),
            "prevClose": (s.get("close") or {}).get("krw"),
        }
        for col in s.get("columns") or []:
            value = col.get("value")
            if isinstance(value, dict):  # {"krw":..., "usd":...}
                value = value.get("krw") if value.get("krw") is not None else value.get("usd")
            row[col.get("label") or col.get("id")] = value
        stocks.append(row)

    return {
        "totalCount": result.get("totalCount", 0),
        "page": result.get("page", 1),
        "lastPage": result.get("lastPage", True),
        "count": len(stocks),
        "stocks": stocks,
    }


# ──────────────────────────────────────────────────────────────
# 심볼 보강 (해외 종목)
#
# 국내(kr)는 stockCode "A095570"에서 A만 떼면 그게 실제 종목코드(095570)다.
# 해외(us)는 stockCode가 토스 내부 코드(US20020523001·NAS0230914001…)라 실제 티커가
# 아니다. 종목 정보 API(INFO_URL)로 productCode → 심볼(NFLX·ARM…)을 배치 조회해 채운다.
# 조회 실패 시 기존 ticker(내부 코드)를 그대로 두어 화면이 깨지지 않게 한다.
# ──────────────────────────────────────────────────────────────

_SYMBOL_CHUNK = 100  # URL 길이 보호를 위해 한 번에 조회할 코드 수


async def _resolve_symbols(codes: list[str]) -> dict[str, str]:
    """productCode 목록 → {code: 심볼} 매핑. 인증 불필요. 실패는 조용히 빈 값."""
    uniq = list(dict.fromkeys(c for c in codes if c))  # 순서 유지 + 중복 제거
    if not uniq:
        return {}

    chunks = [uniq[i : i + _SYMBOL_CHUNK] for i in range(0, len(uniq), _SYMBOL_CHUNK)]
    mapping: dict[str, str] = {}

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        headers={
            "user-agent": USER_AGENT,
            "accept": "application/json",
            "referer": "https://www.tossinvest.com/",
        },
    ) as client:
        async def fetch(chunk: list[str]) -> None:
            try:
                # 콤마는 그대로 전달(테스트로 검증). httpx params 인코딩을 피해 URL 직접 구성.
                res = await client.get(f"{INFO_URL}?codes={','.join(chunk)}")
                if res.status_code != 200:
                    logger.warning(f"[Toss] 심볼 조회 HTTP {res.status_code}")
                    return
                for r in res.json().get("result") or []:
                    code, symbol = r.get("code"), r.get("symbol")
                    if code and symbol:
                        mapping[code] = symbol
            except Exception as e:
                logger.warning(f"[Toss] 심볼 조회 실패: {e}")

        await asyncio.gather(*(fetch(c) for c in chunks))

    return mapping


async def enrich_tickers(flat: dict, nation: str) -> dict:
    """해외 종목의 ticker를 실제 심볼로 교체한다(국내는 그대로). flat을 제자리 수정 후 반환."""
    if not nation or nation.lower() != "us":
        return flat

    stocks = flat.get("stocks") or []
    mapping = await _resolve_symbols([s.get("stockCode") for s in stocks])
    if mapping:
        for s in stocks:
            symbol = mapping.get(s.get("stockCode"))
            if symbol:
                s["ticker"] = symbol
    return flat


# ──────────────────────────────────────────────────────────────
# 필터 빌더
#
# 단위 규칙 (실측 검증 완료):
#   - 모든 비율은 소수: 10% → 0.1, 부채비율 100% → 1, 이자보상배율 300% → 3
#   - 금액은 원 단위 raw: 3,000억 → 300_000_000_000
#   - 거래량은 주 단위 raw
# 기간 enum (실측 검증 완료):
#   - 기간_선택_QUARTER_TTM : "QUARTER"(직전 분기) | "TTM"(최근 1년 / 연속증가는 연도별)
#   - 기간_선택_TTM3_TTM5   : "TTM_3" | "TTM_5"
#   - 기간_선택_DAY_TO_MONTH: "DAY_1" | "DAY_5"(1주) | "DAY_20"(1개월)
#   - 기간_선택_DAY_TO_YEAR : "DAY_1" | "DAY_5" | "DAY_20" | "DAY_60"(3개월)
#                            | "DAY_120"(6개월) | "DAY_240"(12개월)
# ──────────────────────────────────────────────────────────────

억 = 100_000_000
조 = 1_000_000_000_000


def _range(
    frm: float | None,
    to: float | None,
    include_from: bool = True,
    include_to: bool = False,
) -> dict:
    return {
        "id": "NUMBER_RANGE_DEFAULT",
        "type": "NUMBER_RANGE",
        "value": {
            "from": frm,
            "to": to,
            "includeFrom": include_from if frm is not None else None,
            "includeTo": include_to if to is not None else None,
        },
    }


def _period(period_id: str, value: str) -> dict:
    return {"id": period_id, "type": "PERIOD", "value": value}


def F(fid: str, frm=None, to=None, include_from=True, include_to=False) -> dict:
    """기간 없는 숫자범위 필터"""
    return {"id": fid, "conditions": [_range(frm, to, include_from, include_to)]}


def FQ(fid: str, period: str, frm=None, to=None, include_from=True, include_to=False) -> dict:
    """재무 지표 필터 (QUARTER | TTM)"""
    return {
        "id": fid,
        "conditions": [
            _period("기간_선택_QUARTER_TTM", period),
            _range(frm, to, include_from, include_to),
        ],
    }


def FY(fid: str, period: str, frm=None, to=None, include_from=True, include_to=False) -> dict:
    """연평균 지표 필터 (TTM_3 | TTM_5)"""
    return {
        "id": fid,
        "conditions": [
            _period("기간_선택_TTM3_TTM5", period),
            _range(frm, to, include_from, include_to),
        ],
    }


def FD(fid: str, period: str, frm=None, to=None, include_from=True, include_to=False) -> dict:
    """시세 지표 필터 (DAY_1 ~ DAY_240)"""
    return {
        "id": fid,
        "conditions": [
            _period("기간_선택_DAY_TO_YEAR", period),
            _range(frm, to, include_from, include_to),
        ],
    }


def 신저가(within_days: int, weeks: int) -> dict:
    return {
        "id": "CUSTOM_N주_신저가_달성_경과일",
        "conditions": [{
            "id": "WEEK_NEW_PRICE_HIT",
            "type": "WEEK_NEW_PRICE_HIT_WITHIN",
            "value": {"within": within_days, "numberOfWeeks": weeks},
        }],
    }


def 이동평균선_배열(short=5, mid=20, long=60, within=1, align="positive") -> dict:
    return {
        "id": "CUSTOM_이동평균선_배열",
        "conditions": [{
            "id": "이동평균선_배열",
            "type": "MOVING_AVERAGE_ALIGN_ARRAY",
            "value": [{
                "shortPeriod": short, "midPeriod": mid, "longPeriod": long,
                "within": within, "alignType": align,
            }],
        }],
    }


# ──────────────────────────────────────────────────────────────
# 13인의 거장 필터 세트
# 근거: financial 레포 docs/tossfilter.md
# ──────────────────────────────────────────────────────────────

GURU_PRESETS: dict[str, dict[str, Any]] = {
    "공통": {
        "name": "13인 공통분모",
        "style": "합의",
        "principle": "어느 스타일로 가든 이걸 깔고 시작하라 — 13인이 가장 많이 겹친 조건만 모았다.",
        "filters": [
            F("시가총액", 3000 * 억),
            FQ("부채_비율", "TTM", None, 1, include_to=True),
            FQ("이자_보상_배율", "TTM", 3),
            FQ("영업_이익률", "TTM", 0.1),
            FQ("ROE", "TTM", 0.1),
        ],
        "tighten": "ROE·영업이익률을 15%로 상향",
        "loosen": "이자보상배율 제거 → 시가총액 1,000억으로 완화 (부채비율은 절대 먼저 풀지 말 것)",
    },
    "그레이엄": {
        "name": "벤저민 그레이엄",
        "style": "가치 · 안전마진",
        "principle": "스크리너는 헐값 후보를 고를 뿐, 내재가치는 계산해주지 않는다.",
        "filters": [
            F("시가총액", 3000 * 억),
            F("PER", 0, 15, include_to=True),
            F("PBR", 0, 1.5, include_to=True),
            FQ("부채_비율", "TTM", None, 1, include_to=True),
            FQ("유동_비율", "TTM", 2),
            FQ("순이익_연속_증가", "TTM", 4),  # 연도별 4년 연속
        ],
        "tighten": "이자보상배율 500%↑ 또는 배당 연속지급 7년↑ 추가",
        "loosen": "순이익 연속증가 4→2년 → 유동비율 200→100% → PBR 1.5→2배 (PER·PBR 상한은 마지막)",
    },
    "클라먼": {
        "name": "세스 클라먼",
        "style": "가치 · 안전마진",
        "principle": "오를 종목이 아니라, 틀려도 원금을 지킬 헐값만 거른다.",
        "filters": [
            F("주가", 1000, None, include_from=False),  # 동전주 제외
            F("PBR", 0, 1),
            F("PER", 0, 10),
            FQ("부채_비율", "TTM", None, 1, include_to=True),
            FQ("이자_보상_배율", "TTM", 5),
            FQ("영업_이익률", "TTM", 0.1),
        ],
        "tighten": "부채비율 100→50% 이하, PBR 1→0.7배",
        "loosen": "이자보상배율 500→300%, 부채비율 200% 이하 (PER·PBR은 안전마진의 본체이니 마지막)",
    },
    "파브라이": {
        "name": "모니시 파브라이",
        "style": "가치 · 단도투자",
        "principle": "앞면이면 크게 벌고, 뒷면이어도 별로 안 잃는 조합만 남긴다.",
        "filters": [
            F("시가총액", 1000 * 억),
            F("PER", 0, 10),
            F("PBR", 0, 1),
            FQ("부채_비율", "TTM", None, 1, include_to=True),
            FQ("이자_보상_배율", "TTM", 3),
            FQ("ROE", "TTM", 0.05),
        ],
        "tighten": "PBR 0.7배 또는 ROE 10%",
        "loosen": "이자보상배율 제거 후 시가총액 하한 인하",
    },
    "그린블라트": {
        "name": "조엘 그린블라트",
        "style": "마법공식 (가장 단순)",
        "principle": "좋은 기업(ROC)을 싼값(EY)에. EV/EBITDA + ROA로 마법공식을 근사한다.",
        "filters": [
            F("시가총액", 3000 * 억),
            F("EV_EBITDA", 0, 10),
            FQ("ROA", "TTM", 0.1),
            FQ("영업_이익률", "TTM", 0.1),
        ],
        "tighten": "EV/EBITDA 8배로 (ROA·영업이익률은 건드리지 말 것)",
        "loosen": "시가총액 1,000억으로 인하",
        "note": "ROE 대신 ROA를 쓰는 이유: ROE는 부채로 부풀릴 수 있다. "
                "스크리너는 순위 합산을 못 하니, 결과를 EV/EBITDA 순위 + ROA 순위로 직접 더해야 진짜 마법공식이다.",
    },
    "코스톨라니": {
        "name": "앙드레 코스톨라니",
        "style": "대형 우량",
        "principle": "스크리너는 개(주가)만 재고 주인(경제·심리)은 못 잰다.",
        "filters": [
            F("시가총액", 1 * 조),
            FQ("부채_비율", "TTM", None, 1, include_to=True),
            FQ("이자_보상_배율", "TTM", 5),
            FQ("ROE", "TTM", 0.1),
            F("PER", 0, 15, include_to=True),
            FQ("영업_이익_연속_증가", "TTM", 3),
        ],
        "tighten": "PER 15→10배, ROE 10→15%",
        "loosen": "이자보상배율 500→300%, 영업이익 연속증가 3→2년",
    },
    "슈웨거": {
        "name": "잭 슈웨거",
        "style": "추세 ⚠️ (템플턴과 정반대)",
        "principle": "스크리너는 후보를 걸러줄 뿐, 손절선을 그어주지는 않는다.",
        "filters": [
            FD("거래대금", "DAY_20", 100 * 억),  # 1개월 평균 100억 이상
            이동평균선_배열(5, 20, 60, within=1, align="positive"),
            FY("연평균_순이익_증감률", "TTM_3", 0.2),
            FQ("부채_비율", "TTM", None, 1, include_to=True),
        ],
        "tighten": "거래량 비율 200%↑ 또는 52주 신고가 추가",
        "loosen": "연평균 순이익 증감률 20→10% (거래대금·부채비율은 리스크 관리 마지노선)",
        "note": "⚠️ 템플턴(신저가)과 동시에 켜면 결과가 0개가 된다. 시장 전체 방향과 손절선은 스크리너 밖에서 정하라.",
    },
    "버핏": {
        "name": "워런 버핏",
        "style": "퀄리티 · 해자",
        "principle": "싼 주식이 아니라, 숫자에 새겨진 해자의 흔적을 찾는다.",
        "filters": [
            F("시가총액", 3000 * 억),
            FQ("매출_총_이익률", "TTM", 0.4),  # 해자의 첫 신호
            FQ("ROE", "TTM", 0.15),
            FQ("순_이익률", "TTM", 0.15),
            FQ("부채_비율", "TTM", None, 1, include_to=True),
            FQ("순이익_연속_증가", "TTM", 3),
        ],
        "tighten": "매출총이익률 40→50%, 순이익 연속증가 3→4년",
        "loosen": "시가총액 1,000억, 부채비율 150% (ROE·순이익률은 해자의 핵심이니 마지막)",
    },
    "피셔": {
        "name": "필립 피셔",
        "style": "성장 · 스커틀벗",
        "principle": "스크리너는 발로 뛸 후보를 추려주는 문지기일 뿐이다.",
        "filters": [
            FY("연평균_매출액_증감률", "TTM_3", 0.2),
            FQ("영업_이익률", "TTM", 0.1),
            FQ("영업_이익_연속_증가", "TTM", 3),
            FY("연평균_순이익_증감률", "TTM_3", 0.1),
            FQ("부채_비율", "TTM", None, 2, include_to=True),
        ],
        "tighten": "매출 증감률 20→30%, 순이익 증감률 10→20%",
        "loosen": "영업이익 연속증가 3→2년 (가장 엄격해서 결과 개수에 제일 민감)",
    },
    "뉴욕주민": {
        "name": "뉴욕주민",
        "style": "퀄리티 · 데이터 회의주의",
        "principle": "증감률이 아니라 연속증가를 봐라 — 일회성 손익 하나로 증감률은 수십 % 튄다.",
        "filters": [
            F("시가총액", 3000 * 억),
            FQ("부채_비율", "TTM", None, 1, include_to=True),
            FQ("이자_보상_배율", "TTM", 3),
            FQ("영업_이익률", "TTM", 0.1),
            FQ("ROE", "TTM", 0.1),
            FQ("순이익_연속_증가", "TTM", 3),
            F("PER", 0, 20, include_to=True),
        ],
        "tighten": "PER 20→15배 또는 시가총액 1조↑",
        "loosen": "이자보상배율 제거, 순이익 연속증가 3→2년 (부채비율·ROE·영업이익률은 사업의 질 핵심 3종)",
    },
    "린치": {
        "name": "피터 린치",
        "style": "PEG (성장주를 싸게)",
        "principle": "PER 20배 ÷ 성장률 20% = PEG 1.0 — 내 마지노선이다.",
        "filters": [
            F("시가총액", 1000 * 억),
            F("PER", 0, 20),
            FY("연평균_순이익_증감률", "TTM_3", 0.2),
            FQ("부채_비율", "TTM", None, 1, include_to=True),
            FQ("영업_이익률", "TTM", 0.1),
            FD("거래대금", "DAY_1", 5 * 억),
        ],
        "tighten": "순이익 증감률 20→30% 또는 PER 20→15배 (PEG 0.75)",
        "loosen": "영업이익률 조건부터 제거",
        "note": "이 세트로는 회생주(적자 → PER 산출 불가로 자동 배제)와 자산주(NCAV 필터 없음)를 찾을 수 없다.",
    },
    "다모다란": {
        "name": "애스워스 다모다란",
        "style": "가치 + 가치함정 방벽",
        "principle": "스크리너는 가격(pricing) 도구지 가치(valuation) 도구가 아니다.",
        "filters": [
            F("시가총액", 3000 * 억),
            F("주가", 1000, None, include_from=False),
            F("PER", 0, 15, include_to=True),
            F("PBR", 0, 1.5, include_to=True),
            FQ("ROE", "TTM", 0.1),  # ← 저PBR을 가치함정에서 걸러내는 방벽
            FQ("영업_이익률", "TTM", 0.1),
            FQ("이자_보상_배율", "TTM", 3),
            FQ("부채_비율", "TTM", None, 2, include_to=True),
            FY("연평균_순이익_증감률", "TTM_3", 0.1),
        ],
        "tighten": "PER 15→12배, ROE 10→12%",
        "loosen": "PBR 1.5→2배 (이자보상배율·부채비율 = 생존 게이트는 가장 나중에)",
        "note": "PBR의 동인은 ROE다. ROE가 자기자본비용(국내 8~10%) 밑이면 PBR 1배 미만은 저평가가 아니라 정당한 가격이다.",
    },
    "템플턴": {
        "name": "존 템플턴",
        "style": "역발상 ⚠️ (슈웨거와 정반대)",
        "principle": "최대 비관의 순간이 최고의 매수 시점 — 단, 재무 안정성으로 그물코를 걸러라.",
        "filters": [
            F("시가총액", 3000 * 억),
            신저가(within_days=3, weeks=52),
            F("PER", 0, 20),
            FQ("부채_비율", "QUARTER", None, 1, include_to=True),
            FQ("이자_보상_배율", "TTM", 3),
        ],
        "tighten": "이자보상배율 500%, PER 상한 15배",
        "loosen": "신저가 52주→12주, 3일 이내→20일 이내 (부채비율·이자보상배율은 바겐세일과 부도의 경계선)",
        "note": "⚠️ 성장성 '연속증가' 계열을 함께 켜지 마라 — 신저가 종목은 실적이 부진해 결과가 0개가 된다.",
    },
    "버리": {
        "name": "마이클 버리",
        "style": "소외 소형주 ⚠️ (시가총액이 나머지와 정반대)",
        "principle": "스크리너는 냄새나는 자루를 골라줄 뿐, 열어서 읽는 건 내 몫이다.",
        "filters": [
            F("시가총액", 300 * 억, 3000 * 억),  # 소형주 전용
            F("PBR", 0, 1),
            F("EV_EBITDA", 0, 10),
            FQ("부채_비율", "QUARTER", None, 2, include_to=True),
            FD("거래대금", "DAY_20", 5 * 억),
        ],
        "tighten": "EV/EBITDA 8배로",
        "loosen": "부채비율 조건 제거 (5개 중 가장 왜곡이 심한 필터 — 지주사·금융업)",
        "note": "대형주는 이미 수천 명이 뜯어봤다. 3,000억 미만이 개인이 기관과 경쟁하지 않아도 되는 유일한 영역이다.",
    },
}

# 라우터 경로 별칭 (성/이름/풀네임 아무거나 허용)
GURU_ALIASES: dict[str, str] = {
    "공통": "공통", "common": "공통", "공통분모": "공통",
    "벤저민": "그레이엄", "그레이엄": "그레이엄", "벤저민-그레이엄": "그레이엄", "graham": "그레이엄",
    "세스": "클라먼", "클라먼": "클라먼", "세스-클라먼": "클라먼", "klarman": "클라먼",
    "모니시": "파브라이", "파브라이": "파브라이", "모니시-파브라이": "파브라이", "pabrai": "파브라이",
    "조엘": "그린블라트", "그린블라트": "그린블라트", "조엘-그린블라트": "그린블라트", "greenblatt": "그린블라트",
    "앙드레": "코스톨라니", "코스톨라니": "코스톨라니", "앙드레-코스톨라니": "코스톨라니", "kostolany": "코스톨라니",
    "잭": "슈웨거", "슈웨거": "슈웨거", "잭-슈웨거": "슈웨거", "schwager": "슈웨거",
    "워런": "버핏", "버핏": "버핏", "워런-버핏": "버핏", "buffett": "버핏",
    "필립": "피셔", "피셔": "피셔", "필립-피셔": "피셔", "fisher": "피셔",
    "뉴욕주민": "뉴욕주민", "newyorker": "뉴욕주민",
    "피터": "린치", "린치": "린치", "피터-린치": "린치", "lynch": "린치",
    "애스워스": "다모다란", "다모다란": "다모다란", "애스워스-다모다란": "다모다란", "damodaran": "다모다란",
    "존": "템플턴", "템플턴": "템플턴", "존-템플턴": "템플턴", "templeton": "템플턴",
    "마이클": "버리", "버리": "버리", "마이클-버리": "버리", "burry": "버리",
}


# 2026-07-14 국내(kr) 실측 결과 종목 수. 시장 상황에 따라 변한다 — 감각 기준으로만 쓸 것.
# 거장들이 권한 "손으로 검토 가능한 20~40개" 대비 과소/과다 여부를 판단하는 데 쓴다.
MEASURED_COUNTS: dict[str, int] = {
    "공통": 116,     # 다소 많음 → ROE·영업이익률 15%로 조이면 적정
    "그레이엄": 3,    # ⚠️ 너무 적음 → 순이익 연속증가 4→2년부터 완화할 것
    "클라먼": 124,
    "파브라이": 141,
    "그린블라트": 39,  # 적정
    "코스톨라니": 8,   # ⚠️ 적음 → 이자보상배율 500→300%, 영업이익 연속증가 3→2년
    "슈웨거": 2,      # ⚠️ 너무 적음 → 연평균 순이익 증감률 20→10%
    "버핏": 18,       # 적정 하한
    "피셔": 36,       # 적정
    "뉴욕주민": 26,    # 적정
    "린치": 73,
    "다모다란": 23,    # 적정
    "템플턴": 16,      # 적정 (신저가 종목 수라 장세에 따라 크게 변동)
    "버리": 151,
}


def resolve_guru(key: str) -> str | None:
    """별칭을 정규 키로 변환. 없으면 None."""
    return GURU_ALIASES.get(key.strip().lower()) or GURU_ALIASES.get(key.strip())


async def screen_by_guru(
    guru_key: str,
    nation: str = "kr",
    size: int = 50,
    page: int = 1,
) -> dict:
    """거장 프리셋으로 조회."""
    key = resolve_guru(guru_key)
    if key is None:
        raise KeyError(guru_key)

    preset = GURU_PRESETS[key]
    raw = await screen(preset["filters"], nation=nation, size=size, page=page)
    flat = await enrich_tickers(flatten_result(raw), nation)

    return {
        "guru": key,
        "name": preset["name"],
        "style": preset["style"],
        "principle": preset["principle"],
        "tighten": preset.get("tighten"),
        "loosen": preset.get("loosen"),
        "note": preset.get("note"),
        "filterCount": len(preset["filters"]),
        **flat,
    }
