"""
토스증권 스크리너 라우터 — 13인의 거장 필터 세트

GET  /toss/gurus              프리셋 목록
GET  /toss/session            세션 상태
POST /toss/session/refresh    세션 강제 재발급
POST /toss/screen             커스텀 필터로 직접 조회
GET  /toss/{guru}             거장 프리셋으로 조회 (예: /toss/공통, /toss/벤저민, /toss/버핏)

토스 세션은 서버 메모리에 캐시되며 TTL(30분) 만료 시 자동 재발급된다. 로그인 불필요.
필터 근거: financial 레포 docs/tossfilter.md
"""
import logging

from fastapi import APIRouter, Body, HTTPException, Query

logger = logging.getLogger("toss_router")
router = APIRouter(prefix="/toss", tags=["toss"])


@router.get(
    "/gurus",
    summary="거장 프리셋 목록",
    description="사용 가능한 필터 세트와 각 세트의 원칙·필터 개수·조이고 푸는 순서를 반환합니다.",
)
async def list_gurus():
    from services.toss_screener_service import GURU_PRESETS, GURU_ALIASES, MEASURED_COUNTS

    items = []
    for key, p in GURU_PRESETS.items():
        items.append({
            "key": key,
            "path": f"/toss/{key}",
            "name": p["name"],
            "style": p["style"],
            "principle": p["principle"],
            "filterCount": len(p["filters"]),
            "measuredCount": MEASURED_COUNTS.get(key),  # 2026-07-14 국내 실측치 (참고용)
            "tighten": p.get("tighten"),
            "loosen": p.get("loosen"),
            "note": p.get("note"),
            "aliases": sorted(a for a, v in GURU_ALIASES.items() if v == key),
        })
    return {"count": len(items), "gurus": items}


@router.get(
    "/session",
    summary="토스 세션 상태 조회",
    description="현재 캐시된 XSRF 토큰의 생존 시간을 확인합니다. 토큰 전체는 노출하지 않습니다.",
)
async def session_status():
    from services.toss_screener_service import get_session_info
    return await get_session_info()


@router.post(
    "/session/refresh",
    summary="토스 세션 강제 재발급",
    description="wts-api /api/v3/init을 호출해 XSRF 토큰·쿠키를 새로 발급받습니다.",
)
async def session_refresh():
    from services.toss_screener_service import ensure_session, get_session_info
    try:
        await ensure_session(force=True)
        return {"status": "ok", **(await get_session_info())}
    except Exception as e:
        logger.exception(f"[Toss] 세션 재발급 실패: {e}")
        raise HTTPException(status_code=502, detail=f"토스 세션 발급 실패: {e}")


@router.post(
    "/screen",
    summary="커스텀 필터로 스크리너 조회",
    description=(
        "토스 스크리너 필터 배열을 그대로 전달합니다. 프리셋을 변형하거나 새 조합을 실험할 때 사용합니다.\n\n"
        "**단위**: 비율은 소수(10% → 0.1, 부채비율 100% → 1), 금액은 원 단위 raw(3,000억 → 300000000000)\n\n"
        "**기간 enum**: `기간_선택_QUARTER_TTM`=QUARTER|TTM, `기간_선택_TTM3_TTM5`=TTM_3|TTM_5, "
        "`기간_선택_DAY_TO_YEAR`=DAY_1|DAY_5|DAY_20|DAY_60|DAY_120|DAY_240"
    ),
)
async def screen_custom(
    filters: list[dict] = Body(..., embed=True, description="토스 필터 조건 배열"),
    nation: str = Query("kr", description="kr(국내) | us(해외)"),
    size: int = Query(50, ge=1, le=100),
    page: int = Query(1, ge=1),
):
    from services.toss_screener_service import screen, flatten_result
    try:
        raw = await screen(filters, nation=nation, size=size, page=page)
        return flatten_result(raw)
    except Exception as e:
        logger.exception(f"[Toss] 커스텀 조회 실패: {e}")
        raise HTTPException(status_code=502, detail=str(e))


@router.get(
    "/{guru}",
    summary="거장 프리셋으로 스크리너 조회",
    description=(
        "13인의 거장 필터 세트로 종목을 조회합니다.\n\n"
        "경로 예시: `/toss/공통`, `/toss/벤저민`, `/toss/그레이엄`, `/toss/buffett`, `/toss/버핏`\n\n"
        "⚠️ `슈웨거`(정배열)와 `템플턴`(52주 신저가)은 철학이 정반대이고, "
        "`버리`는 소형주 전용이라 시가총액이 나머지와 반대입니다."
    ),
)
async def screen_guru(
    guru: str,
    nation: str = Query("kr", description="kr(국내) | us(해외)"),
    size: int = Query(50, ge=1, le=100),
    page: int = Query(1, ge=1),
):
    from services.toss_screener_service import screen_by_guru, GURU_PRESETS
    try:
        return await screen_by_guru(guru, nation=nation, size=size, page=page)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"'{guru}' 프리셋이 없습니다. 사용 가능: {', '.join(GURU_PRESETS.keys())}",
        )
    except Exception as e:
        logger.exception(f"[Toss] '{guru}' 조회 실패: {e}")
        raise HTTPException(status_code=502, detail=str(e))
