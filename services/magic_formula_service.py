"""
마법공식(Joel Greenblatt) 랭킹 + 백테스트.

ML 학습/예측 대신, 두 지표로 종목을 '정렬'만 한다 (학습 없음):
  ① ROC (자본수익률)      → 좋은 기업인가 (quality)
  ② earnings_yield (EBIT/EV) → 싼가 (cheapness)

두 지표의 순위를 더해(combined) 작을수록 상위 = '좋고 싼' 기업.

데이터는 earnings_events 를 그대로 재사용한다:
  - roc, earnings_yield : 랭킹 입력
  - ret_hold            : 백테스트용 사후 수익률(발표~다음발표 ≈ 3개월)
"""
import logging
import math
from collections import defaultdict
from typing import Optional

from services import earnings_repo

logger = logging.getLogger("magic_formula")


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _quality(e: dict) -> Optional[float]:
    """좋은 기업 지표: ROC(자본수익률) 우선, 없으면 ROE 폴백."""
    q = _f(e.get("roc"))
    return q if q is not None else _f(e.get("roe"))


def _cheapness(e: dict) -> Optional[float]:
    """
    싼 기업 지표(이익수익률). SEC 경로엔 earnings_yield 가 없으므로
    E/P = EPS ÷ 시작가(px_pre) 로 계산 (데이터 재사용, 스케일 일관).
    """
    eps = _f(e.get("eps_act"))
    px = _f(e.get("px_pre")) or _f(e.get("px_post"))
    if eps is not None and px and px > 0:
        return eps / px
    return _f(e.get("earnings_yield"))   # 혹시 저장돼 있으면 폴백


def _rank_desc(items: list[dict], key: str) -> dict:
    """key 내림차순 순위(1=최고)를 {id(item): rank} 로 반환. None 값은 제외."""
    valid = [it for it in items if _f(it.get(key)) is not None]
    valid.sort(key=lambda it: _f(it.get(key)), reverse=True)
    return {id(it): i + 1 for i, it in enumerate(valid)}


def _magic_rank(events: list[dict]) -> list[dict]:
    """
    quality(ROC)·cheapness(E/P) 둘 다 있는 이벤트에 순위합(_combined)을 매겨
    오름차순 정렬해 반환 (in-place 필드 추가). 계산값은 _q/_c 에 저장.
    """
    usable = []
    for e in events:
        q, c = _quality(e), _cheapness(e)
        if q is None or c is None:
            continue
        e["_q"], e["_c"] = q, c
        usable.append(e)
    q_ranks = _rank_desc(usable, "_q")
    c_ranks = _rank_desc(usable, "_c")
    for e in usable:
        e["_roc_rank"] = q_ranks[id(e)]
        e["_ey_rank"] = c_ranks[id(e)]
        e["_combined"] = e["_roc_rank"] + e["_ey_rank"]
    usable.sort(key=lambda e: e["_combined"])
    return usable


def ranking(limit: int = 30) -> dict:
    """
    ticker별 '가장 최근' 실적을 기준으로 마법공식 랭킹 (오늘의 매수 후보).
    """
    labeled = earnings_repo.list_labeled_events()
    unlabeled = earnings_repo.list_unlabeled_events()

    latest: dict[str, dict] = {}
    for e in labeled + unlabeled:
        if _quality(e) is None or _cheapness(e) is None:
            continue
        tk = e.get("ticker")
        if not tk:
            continue
        d = e.get("earnings_date") or ""
        if tk not in latest or d > (latest[tk].get("earnings_date") or ""):
            latest[tk] = e

    ranked = _magic_rank(list(latest.values()))
    items = []
    for i, e in enumerate(ranked[:limit]):
        items.append({
            "rank": i + 1,
            "ticker": e.get("ticker"),
            "gics_sector": e.get("gics_sector"),
            "earnings_date": e.get("earnings_date"),
            "roc": round(e["_q"], 4),            # 수익성(ROC/ROE)
            "earnings_yield": round(e["_c"], 4), # 이익수익률(E/P)
            "roc_rank": e["_roc_rank"],
            "ey_rank": e["_ey_rank"],
            "combined": e["_combined"],
        })
    logger.info(f"[magic] ranking: 후보 {len(ranked)}개 중 상위 {len(items)}개 반환")
    return {"universe": len(ranked), "shown": len(items), "items": items}


def backtest(top_pct: int = 20) -> dict:
    """
    과거 시뮬레이션: 연도별로 마법공식 랭킹 → 상위 top_pct% vs 하위 top_pct% vs 전체의
    평균 보유수익률(ret_hold ≈ 3개월)을 비교. '상위가 하위/전체를 이겼나'로 판정.
    """
    labeled = earnings_repo.list_labeled_events()
    evs = [e for e in labeled
           if _quality(e) is not None
           and _cheapness(e) is not None
           and _f(e.get("ret_hold")) is not None]

    by_year: dict[str, list[dict]] = defaultdict(list)
    for e in evs:
        d = e.get("earnings_date") or ""
        if len(d) >= 4:
            by_year[d[:4]].append(e)

    def _avg(lst: list[dict]):
        rs = [_f(e.get("ret_hold")) for e in lst]
        rs = [r for r in rs if r is not None]
        return (sum(rs) / len(rs)) if rs else None

    rows = []
    pool_top, pool_bottom, pool_all = [], [], []
    for year in sorted(by_year.keys()):
        group = by_year[year]
        ranked = _magic_rank(group)
        n = len(ranked)
        if n < 10:
            continue
        k = max(1, n * top_pct // 100)
        top, bottom = ranked[:k], ranked[-k:]
        pool_top += top
        pool_bottom += bottom
        pool_all += ranked

        t, b, a = _avg(top), _avg(bottom), _avg(ranked)
        rows.append({
            "year": year, "n": n, "k": k,
            "top_ret": round(t, 4) if t is not None else None,
            "bottom_ret": round(b, 4) if b is not None else None,
            "all_ret": round(a, 4) if a is not None else None,
            "spread": round(t - b, 4) if (t is not None and b is not None) else None,
        })

    # ── 전체 집계 (이벤트 가중) ──
    ov_top, ov_bot, ov_all = _avg(pool_top), _avg(pool_bottom), _avg(pool_all)
    spread = (ov_top - ov_bot) if (ov_top is not None and ov_bot is not None) else None
    win_rows = [r for r in rows if r["top_ret"] is not None and r["all_ret"] is not None]
    years_top_wins = sum(1 for r in win_rows if r["top_ret"] > r["all_ret"])
    years_total = len(win_rows)

    if spread is None or years_total == 0:
        verdict = "no_data"
    elif spread >= 0.01 and years_total and (years_top_wins / years_total) >= 0.6:
        verdict = "works"      # 상위가 하위보다 1%p+ & 다수 연도 시장 상회
    elif spread > 0:
        verdict = "weak"       # 미약한 우위
    else:
        verdict = "fails"      # 우위 없음

    logger.info(f"[magic] backtest: 이벤트 {len(evs)}개, spread={spread}, verdict={verdict}")
    return {
        "top_pct": top_pct,
        "horizon": "발표~다음발표(≈3개월) 평균 보유수익률",
        "by_year": rows,
        "overall": {
            "events": len(evs),
            "top_ret": round(ov_top, 4) if ov_top is not None else None,
            "bottom_ret": round(ov_bot, 4) if ov_bot is not None else None,
            "all_ret": round(ov_all, 4) if ov_all is not None else None,
            "spread": round(spread, 4) if spread is not None else None,
            "years_top_wins": years_top_wins,
            "years_total": years_total,
            "verdict": verdict,
        },
    }
