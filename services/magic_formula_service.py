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


# ═════════════════════════════════════════════════════════════
# 종합 스코어카드 (여러 거장의 정량 기준을 팩터로 결합)
#   각 팩터를 개별 백테스트해 '어느 거장이 통했나'를 보여주고,
#   팩터 순위 백분위 평균으로 종합 점수를 매긴다.
# ═════════════════════════════════════════════════════════════

# dir: +1 = 높을수록 좋음, -1 = 낮을수록 좋음
FACTORS = [
    {"key": "roc",          "label": "자본수익률(ROC)",   "guru": "그린블라트·버핏",    "dir": 1,  "col": "roc"},
    {"key": "ep",           "label": "이익수익률(E/P)",   "guru": "그린블라트·그레이엄", "dir": 1,  "col": "_ep"},
    {"key": "gross_margin", "label": "매출총이익률",       "guru": "버핏",            "dir": 1,  "col": "gross_margin"},
    {"key": "net_margin",   "label": "순이익률",           "guru": "버핏",            "dir": 1,  "col": "net_margin"},
    {"key": "roe",          "label": "ROE",               "guru": "버핏",            "dir": 1,  "col": "roe"},
    {"key": "debt_to_ni",   "label": "부채/이익(낮을수록)", "guru": "버핏",           "dir": -1, "col": "debt_to_ni"},
    {"key": "eps_growth",   "label": "EPS성장(YoY)",      "guru": "린치·버핏",       "dir": 1,  "col": "_eps_yoy"},
]


def _round(v, nd: int = 4):
    x = _f(v)
    return round(x, nd) if x is not None else None


def _attach_eps_growth(events: list[dict]) -> None:
    """ticker별 시계열 정렬 후 EPS의 전년동기(4분기 전) 대비 성장률을 e['_eps_yoy']에 주입."""
    from collections import defaultdict as _dd
    by_ticker = _dd(list)
    for e in events:
        by_ticker[e.get("ticker") or ""].append(e)
    for evs in by_ticker.values():
        evs.sort(key=lambda e: e.get("earnings_date") or "")
        for i, e in enumerate(evs):
            e["_eps_yoy"] = None
            cur = _f(e.get("eps_act"))
            if cur is not None and i >= 4:
                prev = _f(evs[i - 4].get("eps_act"))
                if prev is not None and prev != 0:
                    e["_eps_yoy"] = (cur - prev) / abs(prev)


def _attach_factors(events: list[dict]) -> None:
    """팩터 계산에 필요한 파생값(_ep, _eps_yoy)을 이벤트에 주입."""
    _attach_eps_growth(events)
    for e in events:
        e["_ep"] = _cheapness(e)


def _rank_factor(items: list[dict], factor: dict) -> dict:
    """팩터 방향(dir)을 반영한 순위(1=최고) {id: rank}. 값 없으면 제외."""
    col, d = factor["col"], factor["dir"]
    valid = [it for it in items if _f(it.get(col)) is not None]
    valid.sort(key=lambda it: _f(it.get(col)), reverse=(d == 1))
    return {id(it): i + 1 for i, it in enumerate(valid)}


def _composite(items: list[dict], min_factors: int = 3) -> list[dict]:
    """팩터별 순위 백분위(0=최고~1=최악)의 평균을 종합점수(_score)로, 낮을수록 상위."""
    pcts: dict[int, list[float]] = {id(it): [] for it in items}
    for f in FACTORS:
        ranks = _rank_factor(items, f)
        n = len(ranks)
        if n <= 1:
            continue
        for it in items:
            r = ranks.get(id(it))
            if r is not None:
                pcts[id(it)].append((r - 1) / (n - 1))
    scored = []
    for it in items:
        ps = pcts[id(it)]
        if len(ps) >= min_factors:
            it["_score"] = sum(ps) / len(ps)
            it["_nf"] = len(ps)
            scored.append(it)
    scored.sort(key=lambda it: it["_score"])
    return scored


def _bt_ranking(evs: list[dict], rank_fn, top_pct: int) -> dict:
    """연도별로 rank_fn(그룹)→상/하위 top_pct% 평균 ret_hold 비교, 전체 집계·판정."""
    from collections import defaultdict as _dd

    def _avg(lst):
        rs = [_f(e.get("ret_hold")) for e in lst]
        rs = [r for r in rs if r is not None]
        return (sum(rs) / len(rs)) if rs else None

    by_year = _dd(list)
    for e in evs:
        d = e.get("earnings_date") or ""
        if len(d) >= 4:
            by_year[d[:4]].append(e)

    pool_top, pool_bottom, pool_all = [], [], []
    wins = total = 0
    for year in sorted(by_year.keys()):
        ranked = rank_fn(by_year[year])
        n = len(ranked)
        if n < 10:
            continue
        k = max(1, n * top_pct // 100)
        pool_top += ranked[:k]
        pool_bottom += ranked[-k:]
        pool_all += ranked
        t, a = _avg(ranked[:k]), _avg(ranked)
        if t is not None and a is not None:
            total += 1
            if t > a:
                wins += 1

    t, b, a = _avg(pool_top), _avg(pool_bottom), _avg(pool_all)
    sp = (t - b) if (t is not None and b is not None) else None
    if sp is None or total == 0:
        v = "no_data"
    elif sp >= 0.01 and (wins / total) >= 0.6:
        v = "works"
    elif sp > 0:
        v = "weak"
    else:
        v = "fails"
    return {
        "top_ret": _round(t), "bottom_ret": _round(b), "all_ret": _round(a),
        "spread": _round(sp), "years_win": wins, "years_total": total, "verdict": v,
    }


def scorecard_backtest(top_pct: int = 20) -> dict:
    """팩터별 + 종합 백테스트. 어느 거장 기준이 실제 엣지가 있었는지 보여준다."""
    labeled = earnings_repo.list_labeled_events()
    _attach_factors(labeled)
    evs = [e for e in labeled if _f(e.get("ret_hold")) is not None]

    factors_out = []
    for f in FACTORS:
        def _rank_fn(group, f=f):
            ranks = _rank_factor(group, f)
            usable = [e for e in group if id(e) in ranks]
            usable.sort(key=lambda e: ranks[id(e)])
            return usable
        res = _bt_ranking(evs, _rank_fn, top_pct)
        res.update({"key": f["key"], "label": f["label"], "guru": f["guru"]})
        factors_out.append(res)

    # 통하는 팩터 우선 정렬(spread 내림차순)
    factors_out.sort(key=lambda r: (r["spread"] is not None, r["spread"] or -999), reverse=True)

    composite = _bt_ranking(evs, lambda group: _composite(group), top_pct)
    logger.info(f"[scorecard] backtest: 이벤트 {len(evs)}개, 종합 verdict={composite['verdict']}")
    return {
        "top_pct": top_pct,
        "horizon": "발표~다음발표(≈3개월) 평균 보유수익률",
        "factors": factors_out,
        "composite": composite,
    }


def scorecard_ranking(limit: int = 30) -> dict:
    """ticker별 최신 실적으로 종합 점수 랭킹 (오늘의 매수 후보)."""
    labeled = earnings_repo.list_labeled_events()
    unlabeled = earnings_repo.list_unlabeled_events()
    allev = labeled + unlabeled
    _attach_factors(allev)

    latest: dict[str, dict] = {}
    for e in allev:
        tk = e.get("ticker")
        if not tk:
            continue
        d = e.get("earnings_date") or ""
        if tk not in latest or d > (latest[tk].get("earnings_date") or ""):
            latest[tk] = e

    scored = _composite(list(latest.values()))
    items = []
    for i, e in enumerate(scored[:limit]):
        items.append({
            "rank": i + 1,
            "ticker": e.get("ticker"),
            "gics_sector": e.get("gics_sector"),
            "earnings_date": e.get("earnings_date"),
            "score": round((1 - e["_score"]) * 100, 1),   # 0~100, 높을수록 좋음
            "nfactors": e["_nf"],
            "roc": _round(e.get("roc")),
            "ep": _round(e.get("_ep")),
            "net_margin": _round(e.get("net_margin")),
            "roe": _round(e.get("roe")),
            "eps_growth": _round(e.get("_eps_yoy")),
        })
    return {
        "universe": len(scored),
        "shown": len(items),
        "factors": [{"key": f["key"], "label": f["label"], "guru": f["guru"]} for f in FACTORS],
        "items": items,
    }
