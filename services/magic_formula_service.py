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
# 증감(_chg): 전년동기(4분기 전) 대비. 비율은 차이(pp), 금액/EPS는 성장률(pct)
_DELTA_SPEC = [
    ("roc", "roc_chg", "pp"),
    ("roe", "roe_chg", "pp"),
    ("gross_margin", "gross_margin_chg", "pp"),
    ("net_margin", "net_margin_chg", "pp"),
    ("debt_to_ni", "debt_to_ni_chg", "pp"),
    ("sga_to_gross", "sga_to_gross_chg", "pp"),
    ("_ep", "ep_chg", "pp"),
    ("eps_act", "eps_chg", "pct"),
    ("retained_earnings", "retained_earnings_chg", "pct"),
    ("cash_sti", "cash_sti_chg", "pct"),
]

FACTORS = [
    # ── 레벨(현재 수준) ──
    {"key": "roc",          "label": "자본수익률(ROC)", "guru": "그린블라트·버핏",    "dir": 1,  "col": "roc"},
    {"key": "ep",           "label": "이익수익률(E/P)", "guru": "그린블라트·그레이엄", "dir": 1,  "col": "_ep"},
    {"key": "gross_margin", "label": "매출총이익률",     "guru": "버핏",            "dir": 1,  "col": "gross_margin"},
    {"key": "net_margin",   "label": "순이익률",         "guru": "버핏",            "dir": 1,  "col": "net_margin"},
    {"key": "roe",          "label": "ROE",             "guru": "버핏",            "dir": 1,  "col": "roe"},
    {"key": "debt_to_ni",   "label": "부채/이익",        "guru": "버핏",            "dir": -1, "col": "debt_to_ni"},
    {"key": "sga_to_gross", "label": "판관비율",         "guru": "버핏",            "dir": -1, "col": "sga_to_gross"},
    # ── 증감(전년동기 대비) ──
    {"key": "roc_chg",  "label": "ROC 증감",         "guru": "증감",     "dir": 1,  "col": "roc_chg"},
    {"key": "ep_chg",   "label": "E/P 증감",         "guru": "증감",     "dir": 1,  "col": "ep_chg"},
    {"key": "gm_chg",   "label": "매출총이익률 증감",  "guru": "증감",     "dir": 1,  "col": "gross_margin_chg"},
    {"key": "nm_chg",   "label": "순이익률 증감",      "guru": "증감",     "dir": 1,  "col": "net_margin_chg"},
    {"key": "roe_chg",  "label": "ROE 증감",         "guru": "증감",     "dir": 1,  "col": "roe_chg"},
    {"key": "debt_chg", "label": "부채비율 증감(↓좋음)", "guru": "증감",  "dir": -1, "col": "debt_to_ni_chg"},
    {"key": "sga_chg",  "label": "판관비율 증감(↓좋음)", "guru": "증감",  "dir": -1, "col": "sga_to_gross_chg"},
    {"key": "eps_chg",  "label": "EPS 성장(YoY)",    "guru": "린치·버핏", "dir": 1, "col": "eps_chg"},
    {"key": "ret_chg",  "label": "이익잉여 성장",      "guru": "버핏",    "dir": 1,  "col": "retained_earnings_chg"},
    {"key": "cash_chg", "label": "현금 성장",         "guru": "버핏",    "dir": 1,  "col": "cash_sti_chg"},
]


def _round(v, nd: int = 4):
    x = _f(v)
    return round(x, nd) if x is not None else None


def _attach_all(events: list[dict]) -> None:
    """파생 팩터 주입: E/P(_ep) + 전년동기 대비 증감(*_chg). (events 전체를 넘겨 히스토리 확보)"""
    from collections import defaultdict as _dd
    for e in events:
        e["_ep"] = _cheapness(e)
    by_ticker = _dd(list)
    for e in events:
        by_ticker[e.get("ticker") or ""].append(e)
    for evs in by_ticker.values():
        evs.sort(key=lambda e: e.get("earnings_date") or "")
        for i, e in enumerate(evs):
            for base, out, mode in _DELTA_SPEC:
                e[out] = None
                if i >= 4:
                    cur = _f(e.get(base))
                    prev = _f(evs[i - 4].get(base))
                    if cur is not None and prev is not None:
                        if mode == "pp":
                            e[out] = cur - prev
                        elif prev != 0:
                            e[out] = (cur - prev) / abs(prev)


def _rank_factor(items: list[dict], factor: dict) -> dict:
    """팩터 방향(dir)을 반영한 순위(1=최고) {id: rank}. 값 없으면 제외."""
    col, d = factor["col"], factor["dir"]
    valid = [it for it in items if _f(it.get(col)) is not None]
    valid.sort(key=lambda it: _f(it.get(col)), reverse=(d == 1))
    return {id(it): i + 1 for i, it in enumerate(valid)}


def _composite(items: list[dict], factors: list[dict], min_factors: int = 2) -> list[dict]:
    """주어진 factors 순위 백분위(0=최고~1=최악)의 평균을 종합점수(_score)로, 낮을수록 상위."""
    pcts: dict[int, list[float]] = {id(it): [] for it in items}
    for f in factors:
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


def _factor_rank_fn(f: dict):
    """팩터 f 로 그룹을 정렬(상위=좋음)하는 함수 반환."""
    def _fn(group):
        ranks = _rank_factor(group, f)
        usable = [e for e in group if id(e) in ranks]
        usable.sort(key=lambda e: ranks[id(e)])
        return usable
    return _fn


def _factor_results(evs: list[dict], top_pct: int) -> list[dict]:
    """모든 팩터(레벨+증감) 개별 백테스트 결과."""
    out = []
    for f in FACTORS:
        res = _bt_ranking(evs, _factor_rank_fn(f), top_pct)
        res.update({"key": f["key"], "label": f["label"], "guru": f["guru"]})
        out.append(res)
    return out


def scorecard_backtest(top_pct: int = 20) -> dict:
    """레벨+증감 팩터 개별 백테스트 + '통하는(spread>0) 팩터만' 종합 백테스트."""
    labeled = earnings_repo.list_labeled_events()
    _attach_all(labeled)
    evs = [e for e in labeled if _f(e.get("ret_hold")) is not None]

    factors_out = _factor_results(evs, top_pct)
    win_keys = {r["key"] for r in factors_out if r["spread"] is not None and r["spread"] > 0}
    winners = [f for f in FACTORS if f["key"] in win_keys]
    winner_labels = [f["label"] for f in winners]

    factors_out.sort(key=lambda r: (r["spread"] is not None, r["spread"] or -999), reverse=True)

    if winners:
        composite = _bt_ranking(evs, lambda g: _composite(g, winners), top_pct)
    else:
        composite = {"top_ret": None, "bottom_ret": None, "all_ret": None, "spread": None,
                     "years_win": 0, "years_total": 0, "verdict": "no_data"}
    logger.info(f"[scorecard] backtest: {len(evs)}건, 통과팩터 {len(winners)}개 {winner_labels}, "
                f"종합 verdict={composite['verdict']}")
    return {
        "top_pct": top_pct,
        "horizon": "발표~다음발표(≈3개월) 평균 보유수익률",
        "factors": factors_out,
        "composite": composite,
        "winners": winner_labels,
    }


def scorecard_ranking(limit: int = 30) -> dict:
    """통과 팩터(spread>0)만으로 종합 점수 랭킹 (오늘의 매수 후보)."""
    labeled = earnings_repo.list_labeled_events()
    unlabeled = earnings_repo.list_unlabeled_events()
    allev = labeled + unlabeled
    _attach_all(allev)                       # 라벨+미라벨 통합으로 증감 히스토리 확보

    # 통과 팩터 결정(라벨 데이터 백테스트)
    evs = [e for e in labeled if _f(e.get("ret_hold")) is not None]
    win_keys = {r["key"] for r in _factor_results(evs, 20)
                if r["spread"] is not None and r["spread"] > 0}
    winners = [f for f in FACTORS if f["key"] in win_keys] or list(FACTORS)

    latest: dict[str, dict] = {}
    for e in allev:
        tk = e.get("ticker")
        if not tk:
            continue
        d = e.get("earnings_date") or ""
        if tk not in latest or d > (latest[tk].get("earnings_date") or ""):
            latest[tk] = e

    scored = _composite(list(latest.values()), winners)
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
            "eps_growth": _round(e.get("eps_chg")),
        })
    return {
        "universe": len(scored),
        "shown": len(items),
        "factors_used": [f["label"] for f in winners],
        "items": items,
    }


# ═════════════════════════════════════════════════════════════
# 가중치 학습 + 워크포워드 (랭킹에 ML)
#   학습창(과거)에서 팩터별 스프레드로 가중치를 정하고,
#   그 가중치로 검증창(안 본 미래)을 랭킹해 '학습가중 vs 등가중'을 비교.
# ═════════════════════════════════════════════════════════════

def _avg_ret(lst: list[dict]):
    rs = [_f(e.get("ret_hold")) for e in lst]
    rs = [r for r in rs if r is not None]
    return (sum(rs) / len(rs)) if rs else None


def _spread_of(ranked: list[dict], top_pct: int):
    """정렬된(상위=좋음) 리스트에서 상위-하위 top_pct% ret_hold 스프레드 + 상/하위 리스트."""
    n = len(ranked)
    if n < 10:
        return None, [], []
    k = max(1, n * top_pct // 100)
    top, bot = ranked[:k], ranked[-k:]
    t, b = _avg_ret(top), _avg_ret(bot)
    sp = (t - b) if (t is not None and b is not None) else None
    return sp, top, bot


def _factor_spread(evs: list[dict], factor: dict, top_pct: int = 20):
    """단일 팩터로 evs 정렬 후 스프레드 (가중치 학습용)."""
    ranks = _rank_factor(evs, factor)
    usable = [e for e in evs if id(e) in ranks]
    usable.sort(key=lambda e: ranks[id(e)])
    sp, _, _ = _spread_of(usable, top_pct)
    return sp


def _weighted_rank(events: list[dict], factors: list[dict], weights: dict) -> list[dict]:
    """factors의 순위 백분위를 weights로 가중평균해 정렬(상위=좋음). _wscore(0=최고) 부여."""
    per: dict[int, list] = {id(e): [] for e in events}
    for f in factors:
        w = weights.get(f["key"], 0.0)
        if w <= 0:
            continue
        ranks = _rank_factor(events, f)
        n = len(ranks)
        if n <= 1:
            continue
        for e in events:
            r = ranks.get(id(e))
            if r is not None:
                per[id(e)].append((w, (r - 1) / (n - 1)))
    scored = []
    for e in events:
        ws = per[id(e)]
        if len(ws) >= 2:
            tw = sum(w for w, _ in ws)
            e["_wscore"] = (sum(w * p for w, p in ws) / tw) if tw > 0 else 1.0
            scored.append(e)
    scored.sort(key=lambda e: e["_wscore"])
    return scored


def walkforward(top_pct: int = 20, limit: int = 30) -> dict:
    """
    워크포워드: 과거 학습창에서 팩터 가중치(=스프레드) 학습 → 안 본 다음 해로 검증.
    학습가중 랭킹 vs 등가중 랭킹의 out-of-sample 성과 비교.
    """
    from collections import defaultdict as _dd

    labeled = earnings_repo.list_labeled_events()
    unlabeled = earnings_repo.list_unlabeled_events()
    allev = labeled + unlabeled
    _attach_all(allev)
    evs = [e for e in labeled if _f(e.get("ret_hold")) is not None]

    by_year = _dd(list)
    for e in evs:
        d = e.get("earnings_date") or ""
        if len(d) >= 4:
            by_year[d[:4]].append(e)
    years = sorted(by_year.keys())

    rows = []
    pl_top, pl_bot, pe_top, pe_bot = [], [], [], []
    MIN_TRAIN = 3
    for i in range(MIN_TRAIN, len(years)):
        test_evs = by_year[years[i]]
        if len(test_evs) < 10:
            continue
        train_evs = [e for y in years[:i] for e in by_year[y]]
        w = {f["key"]: max(0.0, _factor_spread(train_evs, f) or 0.0) for f in FACTORS}
        wf = [f for f in FACTORS if w[f["key"]] > 0]
        if not wf:
            continue
        lr = _weighted_rank(test_evs, wf, w)                              # 학습가중
        er = _weighted_rank(test_evs, wf, {f["key"]: 1.0 for f in wf})    # 등가중
        lsp, lt, lb = _spread_of(lr, top_pct)
        esp, et, eb = _spread_of(er, top_pct)
        pl_top += lt; pl_bot += lb; pe_top += et; pe_bot += eb
        rows.append({
            "year": years[i], "n": len(test_evs),
            "learned_spread": _round(lsp), "equal_spread": _round(esp),
            "winner": ("learned" if (lsp is not None and esp is not None and lsp > esp)
                       else ("equal" if esp is not None else None)),
        })

    la_t, la_b = _avg_ret(pl_top), _avg_ret(pl_bot)
    ea_t, ea_b = _avg_ret(pe_top), _avg_ret(pe_bot)
    lsp_all = (la_t - la_b) if (la_t is not None and la_b is not None) else None
    esp_all = (ea_t - ea_b) if (ea_t is not None and ea_b is not None) else None
    if lsp_all is None or esp_all is None:
        verdict = "no_data"
    elif lsp_all > esp_all + 0.002:
        verdict = "learned"
    elif lsp_all >= esp_all - 0.002:
        verdict = "tie"
    else:
        verdict = "equal"

    # ── 전체 라벨로 최종 가중치 학습 + 오늘의 가중 랭킹 ──
    fw = {f["key"]: max(0.0, _factor_spread(evs, f) or 0.0) for f in FACTORS}
    tot = sum(fw.values()) or 1.0
    weights_disp = sorted(
        [{"label": f["label"], "guru": f["guru"], "weight": round(fw[f["key"]] / tot * 100, 1)}
         for f in FACTORS if fw[f["key"]] > 0],
        key=lambda x: x["weight"], reverse=True,
    )
    wf_final = [f for f in FACTORS if fw[f["key"]] > 0]

    latest: dict[str, dict] = {}
    for e in allev:
        tk = e.get("ticker")
        if not tk:
            continue
        d = e.get("earnings_date") or ""
        if tk not in latest or d > (latest[tk].get("earnings_date") or ""):
            latest[tk] = e
    ranked = _weighted_rank(list(latest.values()), wf_final, fw)
    top_picks = [{
        "rank": i + 1, "ticker": e.get("ticker"), "gics_sector": e.get("gics_sector"),
        "earnings_date": e.get("earnings_date"),
        "score": round((1 - e["_wscore"]) * 100, 1),
        "roc": _round(e.get("roc")), "ep": _round(e.get("_ep")),
        "eps_growth": _round(e.get("eps_chg")),
    } for i, e in enumerate(ranked[:limit])]

    logger.info(f"[walkforward] folds={len(rows)} learned={lsp_all} equal={esp_all} verdict={verdict}")
    return {
        "top_pct": top_pct,
        "by_year": rows,
        "overall": {"learned_spread": _round(lsp_all), "equal_spread": _round(esp_all), "verdict": verdict},
        "weights": weights_disp,
        "top_picks": top_picks,
    }
