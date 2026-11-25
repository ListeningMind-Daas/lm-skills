#!/usr/bin/env python3
"""3축 니즈 분해 + 우선순위 후보 (find-customer-needs STEP 6~7 · 이 스킬 고유 스크립트).

입력은 `build_universe.py`가 만든 유니버스 CSV다. 축 사전(themes 스키마)의 테마 이름을
`Pain:…` · `Feature:…` · `Attribute:…`로 적어 두면, build_universe가 붙인 `theme_primary`에서
축과 니즈 항목을 읽는다. 분류는 **정규식 사전 규칙**이고 확률·신뢰도 점수를 만들지 않는다.

지키는 것:
  · 같은 검색의 중복 행(build_universe의 `dup_of` — 시계열·지표 일치, 계약 T5 ①)은 한 번만 센다
  · 표기변형(canonical 그룹) 볼륨은 **하한(그룹 최댓값)과 상한(그룹 합)** 을 둘 다 낸다(계약 T5 ②).
    비중은 각자의 분모로 두 번 계산하고, 순위·판정은 하한 기준이며 상한에서 뒤집히면 flip_under_hi에 적는다
  · 카테고리 경계: 축 사전(--axes)의 `exclude` 패턴에 걸린 검색어(인접 카테고리)는 집계에서 빼고,
    `category_terms`를 하나도 포함하지 않는 검색어는 «경계 점검» 목록으로 올린다
  · 추세는 그룹 대표 검색어 한 개의 시계열로 계산한다 — 하한 기준이다(상한 시계열을 만들지 않는다)
  · 항목 추세는 시계열이 있는 그룹(결측 없음)을 **합산한 뒤** 항목 게이트(--item-gate)를 건다 — 그룹 하나하나가 게이트 아래인
    긴 꼬리(Pain)도 합산 기저가 충분하면 추세가 나온다. 기저 0 폭발은 합산 게이트가 막는다
  · 카테고리 기준선(--baseline, 기본 = --category 검색어)의 전년 동기를 함께 내고, 항목 추세를 기준선 대비로도 낸다
    — 카테고리 전체와 같은 폭으로 줄어든 불편은 «불편이 줄었다»가 아니라 «카테고리와 같이 움직였다»이다
  · 브랜드 포함 검색어와 논브랜드를 **따로** 집계·순위화한다 — 브랜드 볼륨이 니즈 순위를 덮는다
  · 추세는 `volume_trend`가 아니라 `monthly_volume`의 **전년 동기 3개월 창**으로 직접 계산
  · 기본은 **마지막 달 제외** 창 — 마지막 달은 아직 채워지는 중일 수 있다(미완결 의심 건수를 md에 적는다)
  · 비교 창에 결측 월이 있으면 0으로 채우지 않고 «결측 월»로 남긴다

    python3 voc_axes.py --universe keyword_universe.csv --monthly metrics_all.json --axes axes.json \\
        --category 전동칫솔 --queried-at 2026-09-28 --coverage "수렴(R2 기여 12%)"
"""
import argparse, csv, json, pathlib, re, sys, unicodedata
from collections import defaultdict

AXES = ("Pain", "Feature", "Attribute")
UNCLASSIFIED = "unclassified"
EXCLUDED = "경계 밖"


def load(path):
    o = json.load(open(path, encoding="utf-8"))
    return o["data"] if isinstance(o, dict) and "data" in o else o


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def window(end, size):
    return [shift(end, i) for i in range(size - 1, -1, -1)]


def acc(series, months):
    """(합계, 결측 월 수) — nv_code:1 행은 total 키가 없다. 0이 아니라 결측이다."""
    tot = miss = 0
    for m in months:
        r = series.get(m)
        if r is None or r.get("total") is None:
            miss += 1
        else:
            tot += r["total"]
    return tot, miss


def num(v):
    try:
        return float(v) if v not in ("", None) else None
    except ValueError:
        return None


def simple_canon(kw):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", kw).lower())


def split_axis(theme):
    """'Pain:충전 불량' → ('Pain', '충전 불량'). 축 접두어가 없으면 미분류."""
    if ":" in theme:
        ax, item = theme.split(":", 1)
        if ax.strip() in AXES:
            return ax.strip(), item.strip()
    return UNCLASSIFIED, theme or "(없음)"


def months_between(a, b):
    return (int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))


def last_month_suspect(s, ref):
    """check_refmonth.py와 같은 규칙 — 최신 월 MoM이 실제 하락이고 전년 같은 구간보다 30%p 이상 나쁘면 의심."""
    def mom(year):
        c, p = s.get(f"{year}-{ref[5:7]}"), s.get(shift(f"{year}-{ref[5:7]}", 1))
        if not c or not p or c.get("total") is None or not p.get("total"):
            return None
        return c["total"] / p["total"] - 1
    cur, prev = mom(int(ref[:4])), mom(int(ref[:4]) - 1)
    return cur is not None and prev is not None and cur < 0 and cur - prev < -0.30


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--universe", required=True, help="build_universe.py 산출 CSV")
    ap.add_argument("--monthly", help="keyword_info(all) 응답 JSON — 2단 대상(select_stage2.py)만 조회한 것")
    ap.add_argument("--axes", help="축 사전 JSON — exclude(인접 카테고리 정규식)·category_terms(경계 점검)를 읽는다")
    ap.add_argument("--category", required=True, help="산출 파일명에 쓰는 카테고리 이름")
    ap.add_argument("--baseline", help="카테고리 기준선 검색어(쉼표 구분) — 기본은 --category와 같은 검색어")
    ap.add_argument("--gate", type=int, default=3000,
                    help="그룹 추세(group_yoy) 최소 전년 동기 창 볼륨 (기본 3000)")
    ap.add_argument("--item-gate", type=int, default=1000,
                    help="항목 합산 추세 최소 전년 동기 창 볼륨 — 시계열 있는 그룹을 합산한 기저 (기본 1000)")
    ap.add_argument("--window", type=int, default=3, help="비교 창 개월 수 (기본 3)")
    ap.add_argument("--rel-threshold", type=float, default=0.10,
                    help="기준선 대비 판정 임계 — (1+항목)/(1+기준선)−1 (기본 ±10%%)")
    ap.add_argument("--include-last-month", action="store_true",
                    help="마지막 달을 창에 포함 — 쓰면 리포트에 반드시 명시")
    ap.add_argument("--queried-at", default="", help="조회 실행일 YYYY-MM-DD (헤더 3종 표기용)")
    ap.add_argument("--coverage", default="",
                    help="coverage_curve.py 판정 요약 — 예: '보류(R1 절단)'. 비우면 경고")
    ap.add_argument("--top", type=int, default=5, help="md에 축별로 싣는 항목 수")
    ap.add_argument("--out-dir", default=".")
    a = ap.parse_args()

    rows = list(csv.DictReader(open(a.universe, encoding="utf-8-sig")))
    if not rows:
        sys.exit("유니버스 CSV가 비었다")

    # ── 카테고리 경계
    excl, terms = [], []
    if a.axes:
        ax_cfg = json.load(open(a.axes, encoding="utf-8"))
        excl = [re.compile(p) for p in ax_cfg.get("exclude", [])]
        terms = [simple_canon(t) for t in ax_cfg.get("category_terms", [])]
    for r in rows:
        c = r["canonical"]
        hit = next((p.pattern for p in excl if p.search(c)), "")
        r["_boundary"] = (f"{EXCLUDED}({hit})" if hit else
                          "경계 점검" if (terms and not any(t in c for t in terms)) else "")
        r["_dup"] = (r.get("dup_of") or "").strip()
    agg = [r for r in rows if not r["_dup"] and not r["_boundary"].startswith(EXCLUDED)]

    # ── canonical 그룹: 대표 = 볼륨 최댓값 행. 그룹 볼륨 하한 = 최댓값 · 상한 = 합 (중복·경계 밖 제외)
    groups = defaultdict(list)
    for r in agg:
        groups[r["canonical"]].append(r)
    rep_of, bounds = {}, {}
    for c, members in groups.items():
        rep_of[c] = max(members, key=lambda r: num(r["volume_total"]) or -1)
        vals = [num(r["volume_total"]) for r in members if num(r["volume_total"]) is not None]
        bounds[c] = (max(vals), sum(vals)) if vals else (None, None)

    # ── 시계열 (배열 순서를 가정하지 않는다 — month 최댓값이 기준월)
    series_by_kw, ref = {}, ""
    for d in (load(a.monthly) if a.monthly else []):
        mv = [r for r in (d.get("monthly_volume") or []) if isinstance(r, dict) and r.get("month")]
        if mv:
            series_by_kw[d["keyword"]] = {r["month"]: r for r in mv}
            ref = max(ref, max(r["month"] for r in mv))
    if not ref:
        ref = max((r.get("data_ref_month") or "" for r in rows), default="")
    end = ref if (a.include_last_month or not ref) else shift(ref, 1)
    cur = window(end, a.window) if end else []
    prev = [shift(m, 12) for m in cur]
    yoy_window = f"{cur[0]}~{cur[-1]} vs {prev[0]}~{prev[-1]}" if cur else ""
    suspects = sum(1 for s in series_by_kw.values() if ref and last_month_suspect(s, ref))

    # ── 카테고리 기준선 (전년 동기 · 같은 창)
    base_kws = [x.strip() for x in (a.baseline or a.category).split(",") if x.strip()]
    base_c = {simple_canon(x) for x in base_kws}
    bc = bp = 0; base_used = []
    for kw, s in series_by_kw.items():
        if simple_canon(kw) in base_c:
            cs, mc = acc(s, cur); ps, mp = acc(s, prev)
            if not (mc or mp) and ps:
                bc += cs; bp += ps; base_used.append(kw)
    base_yoy = round(bc / bp - 1, 4) if bp else ""

    grp = {}
    for c, rep in rep_of.items():
        axis, item = split_axis(rep.get("theme_primary", ""))
        lo, hi = bounds[c]
        s = None
        for m in sorted(groups[c], key=lambda r: -(num(r["volume_total"]) or -1)):
            if m["keyword"] in series_by_kw:
                s = series_by_kw[m["keyword"]]; break
        if s is None:
            status, cs, ps = "시계열 없음", None, None
        else:
            cs, mc = acc(s, cur); ps, mp = acc(s, prev)
            status = ("결측 월" if (mc or mp) else
                      "게이트 미달" if ps < a.gate else "계산됨")
        grp[c] = {"axis": axis, "item": item, "lo": lo, "hi": hi, "status": status, "cur": cs, "prev": ps,
                  "scope": "브랜드" if rep.get("brand") else "논브랜드", "rep": rep["keyword"],
                  "boundary": rep["_boundary"]}

    # ── 키워드 단위 산출 (행 보존 — 중복·경계 밖 행도 표시만 하고 남긴다)
    out = pathlib.Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    kw_rows = []
    for r in rows:
        g = grp.get(r["canonical"]) if not r["_boundary"].startswith(EXCLUDED) else None
        own_axis, own_item = split_axis(r.get("theme_primary", ""))
        facet_axes = {split_axis(f)[0] for f in (r.get("facets") or "").split("|") if f}
        is_rep = bool(g) and r["keyword"] == g["rep"]
        gy = (round(g["cur"] / g["prev"] - 1, 4)
              if (is_rep and g["status"] == "계산됨" and g["prev"]) else "")
        axis = EXCLUDED if r["_boundary"].startswith(EXCLUDED) else (g["axis"] if g else own_axis)
        kw_rows.append({
            "keyword": r["keyword"], "canonical": r["canonical"],
            "axis": axis, "need_item": g["item"] if g else own_item, "facets": r.get("facets", ""),
            "multi_axis": "Y" if len(facet_axes - {UNCLASSIFIED}) > 1 else "",
            "brand": r.get("brand", ""), "brand_scope": g["scope"] if g else ("브랜드" if r.get("brand") else "논브랜드"),
            "volume_total": r["volume_total"],
            "group_volume_lo": "" if not g or g["lo"] is None else int(g["lo"]),
            "group_volume_hi": "" if not g or g["hi"] is None else int(g["hi"]),
            "cpc": r.get("cpc", ""),
            "discovery_round": r.get("discovery_round", ""),
            "canonical_rep": "Y" if is_rep else "",
            "dup_of": r["_dup"], "boundary": r["_boundary"],
            "group_yoy": gy, "yoy_status": g["status"] if is_rep else "",
            "label_axis": "추정", "label_group_volume": "추정",
        })
    order_k = AXES + (UNCLASSIFIED, EXCLUDED)
    kw_rows.sort(key=lambda r: order_k.index(r["axis"]) * 1e15 - (num(r["volume_total"]) or 0))
    f1 = out / f"voc_decomposition_{a.category}.csv"
    with open(f1, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(kw_rows[0].keys())); w.writeheader(); w.writerows(kw_rows)

    # ── 니즈 항목 단위 (축 × 항목 × 브랜드 범위)
    items = defaultdict(lambda: {"groups": [], "kws": 0})
    for r in agg:
        g = grp[r["canonical"]]
        items[(g["axis"], g["item"], g["scope"])]["kws"] += 1
    for c, g in grp.items():
        items[(g["axis"], g["item"], g["scope"])]["groups"].append(g)

    B = ("lo", "hi")

    def gv(g, b):
        return g[b] or 0

    axis_total = {b: defaultdict(float) for b in B}
    for (ax, _, sc), it in items.items():
        for b in B:
            axis_total[b][(ax, sc)] += sum(gv(g, b) for g in it["groups"])

    def call_of(yoy, cov):
        if yoy == "":
            return ""
        if cov < 0.5:
            return "잠정"
        return "상승" if yoy >= 0.2 else "하락" if yoy <= -0.2 else "보합"

    it_rows = []
    for (ax, item, sc), it in items.items():
        gs = it["groups"]
        vol = {b: sum(gv(g, b) for g in gs) for b in B}
        pool = [g for g in gs if g["status"] in ("계산됨", "게이트 미달")]    # 시계열 있고 결측 없는 그룹 — 합산
        c_sum, p_sum = sum(g["cur"] for g in pool), sum(g["prev"] for g in pool)
        ok = pool if (pool and p_sum >= a.item_gate) else []
        cov = {b: (sum(gv(g, b) for g in ok) / vol[b]) if vol[b] else 0 for b in B}
        yoy = round(c_sum / p_sum - 1, 4) if ok else ""
        if ok:
            status = "계산됨"
        else:
            cnt = defaultdict(int)
            for g in gs:
                cnt[g["status"]] += 1
            status = "계산 불가: " + " · ".join(f"{k} {v}" for k, v in sorted(cnt.items()))
            if pool:
                status += f" (합산 게이트 미달 · 전년 창 {p_sum:,})"
        rel = round((1 + yoy) / (1 + base_yoy) - 1, 4) if (yoy != "" and base_yoy != "" and base_yoy > -1) else ""
        call = call_of(yoy, cov["lo"])
        rel_call = ("" if rel == "" else "잠정" if call == "잠정" else
                    "기준선보다 강함" if rel >= a.rel_threshold else
                    "기준선보다 약함" if rel <= -a.rel_threshold else "기준선과 같이 움직임")
        top = sorted(gs, key=lambda g: (-gv(g, "lo"), -gv(g, "hi")))[:3]
        share = {b: round(vol[b] / axis_total[b][(ax, sc)], 4) if axis_total[b][(ax, sc)] else "" for b in B}
        it_rows.append({
            "axis": ax, "need_item": item, "brand_scope": sc,
            "keywords": it["kws"], "canonical_groups": len(gs),
            "volume_12m_lo": int(vol["lo"]), "volume_12m_hi": int(vol["hi"]),
            "volume_share_in_axis_lo": share["lo"], "volume_share_in_axis_hi": share["hi"],
            "axis_rank": 0, "axis_rank_hi": 0, "yoy_window": yoy_window, "yoy": yoy,
            "yoy_series_basis": "대표 검색어(하한 기준)" if ok else "", "yoy_groups": len(ok),
            "yoy_basis_prev_year": p_sum if ok else "",
            "yoy_coverage_lo": round(cov["lo"], 3), "yoy_coverage_hi": round(cov["hi"], 3),
            "yoy_status": status, "trend_call": call,
            "trend_call_hi": call_of(yoy, cov["hi"]), "flip_under_hi": "",
            "baseline_yoy": base_yoy if yoy != "" else "", "yoy_rel_baseline": rel, "trend_call_rel": rel_call,
            "top_keywords": " · ".join(g["rep"] for g in top), "data_ref_month": ref,
        })
    for key in {(r["axis"], r["brand_scope"]) for r in it_rows}:
        sub = [r for r in it_rows if (r["axis"], r["brand_scope"]) == key]
        for col, vcol, tie in (("axis_rank", "volume_12m_lo", "volume_12m_hi"),
                               ("axis_rank_hi", "volume_12m_hi", "volume_12m_lo")):
            for i, r in enumerate(sorted(sub, key=lambda r: (-r[vcol], -r[tie], r["need_item"])), 1):
                r[col] = i
    for r in it_rows:
        fl = []
        if r["axis_rank"] != r["axis_rank_hi"]:
            fl.append(f"순위 {r['axis_rank']}→{r['axis_rank_hi']}")
        if r["trend_call"] != r["trend_call_hi"]:
            fl.append(f"판정 {r['trend_call'] or '—'}→{r['trend_call_hi'] or '—'}")
        r["flip_under_hi"] = " · ".join(fl)
    order = {v: i for i, v in enumerate(AXES + (UNCLASSIFIED,))}
    it_rows.sort(key=lambda r: (order[r["axis"]], r["brand_scope"] != "논브랜드", r["axis_rank"]))
    f2 = out / f"roadmap_priority_{a.category}.csv"
    with open(f2, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(it_rows[0].keys())); w.writeheader(); w.writerows(it_rows)

    # ── 요약 수치 (canonical 그룹 기준 · 하한/상한 각자의 분모)
    tot = {b: sum(gv(g, b) for g in grp.values()) for b in B}
    cls = {b: sum(gv(g, b) for g in grp.values() if g["axis"] != UNCLASSIFIED) for b in B}
    nb = {b: sum(gv(g, b) for g in grp.values() if g["scope"] == "논브랜드") for b in B}
    no_vol = sum(1 for g in grp.values() if g["lo"] is None)
    flips = [r for r in it_rows if r["flip_under_hi"]]
    n_dup = sum(1 for r in rows if r["_dup"])
    ex_rows = [r for r in rows if r["_boundary"].startswith(EXCLUDED) and not r["_dup"]]
    chk = sorted((g for g in grp.values() if g["boundary"] == "경계 점검"), key=lambda g: -gv(g, "lo"))

    def pr(x):
        return f"하한 기준 {x['lo'] / tot['lo']:.1%} ~ 상한 기준 {x['hi'] / tot['hi']:.1%}"
    lag = months_between(ref, a.queried_at[:7]) if (ref and len(a.queried_at) >= 7) else None
    last_mode = "포함(--include-last-month)" if a.include_last_month else "제외(기본)"
    st = defaultdict(int)
    for g in grp.values():
        st[g["status"]] += 1

    # ── md
    L = [f"# 고객 니즈 3축 분해 — {a.category}", "",
         f"- 데이터 기준월: **{ref or '미확인'}** · 조회일: **{a.queried_at or '미기재'}** · "
         f"지연: **{f'{lag}개월' if lag is not None else '미계산'}** (기준월은 응답에서 읽는다)",
         f"- 추세 비교 창: {yoy_window or '없음'} · 마지막 달 {last_mode} · 게이트(전년 창) 그룹 {a.gate:,}건 · "
         f"항목 합산 {a.item_gate:,}건 [가정]",
         f"- 마지막 달 미완결 의심: {suspects}/{len(series_by_kw)}행 (최신 월 MoM이 전년 같은 구간보다 30%p 이상 나쁨 · "
         "check_refmonth.py와 같은 규칙) [추정]",
         f"- 카테고리 기준선({', '.join(base_kws)}) 전년 동기: "
         + (f"**{base_yoy:+.0%}** [실제 데이터 계산] — 항목 추세를 이 값과 함께 읽는다(«기준선 대비» 열)"
            if base_yoy != "" else "계산 불가 — 기준선 검색어의 시계열이 2단에 없다 [데이터 공백]. "
            "select_stage2.py로 기준선을 2단에 넣을 것"),
         f"- 수집 범위 판정: {a.coverage or '⚠ 미기재 — coverage_curve.py 판정을 넣을 것(절단이면 보류)'}",
         f"- 분류율(canonical 그룹 볼륨 기준 [추정]): {pr(cls)} · 논브랜드 비중 {pr(nb)} · "
         f"검색량 미반환 그룹 {no_vol}개" if tot["lo"] else "- 분류율: 계산 불가(볼륨 없음)",
         f"- 그룹 볼륨 합 [추정]: 하한 {int(tot['lo']):,} ~ 상한 {int(tot['hi']):,} — 하한 = 표기변형 그룹 최댓값"
         "(변형이 같은 검색의 중복) · 상한 = 그룹 합(변형이 서로 다른 검색). 응답만으로 어느 쪽인지 알 수 없어 둘 다 싣는다",
         f"- 같은 검색의 중복 행(시계열·지표 일치, build_universe `dup_of`) {n_dup}행은 한 번만 셌다 [추정]",
         f"- 카테고리 경계: 경계 밖(exclude) {len(ex_rows)}행 · "
         f"{sum(num(r['volume_total']) or 0 for r in ex_rows):,.0f}건 집계 제외 · 경계 점검 대상 {len(chk)}그룹 [가정]"
         if a.axes else "- 카테고리 경계: ⚠ --axes 미지정 — 인접 카테고리 검색어가 섞였는지 점검하지 않았다",
         f"- 순위·판정은 하한 기준 · 상한 기준에서 순위·판정이 뒤집히는 항목 {len(flips)}개(표의 «상한 시 뒤집힘»)",
         "- 전년 동기 추세는 그룹 대표 검색어의 시계열로 계산했다 — 하한 기준이며 상한 시계열은 없다",
         f"- 추세 상태(그룹 수): " + " · ".join(f"{k} {v}" for k, v in sorted(st.items())),
         "", "> 검색량 단위는 건(사람 수 아님). 검색은 관심 신호이며 구매·사용·만족도가 아니다.",
         "> 불편 검색이 줄었다고 불만이 줄었다고 단정하지 않는다 — 카테고리 전체 검색이 함께 줄었는지 기준선과 비교한다.",
         "> 순위는 검색량 순이며 로드맵 결정이 아니라 **검토 후보**다.", ""]
    for ax in AXES:
        for sc in ("논브랜드", "브랜드"):
            sel = [r for r in it_rows if r["axis"] == ax and r["brand_scope"] == sc][: a.top]
            if not sel:
                continue
            L += [f"## {ax} · {sc}", "",
                  "| 순위(하한) | 니즈 항목 | 12개월 검색량 [추정 · 하한 ~ 상한] | 축 내 비중 [추정 · 하한 / 상한] | "
                  "전년 동기 [실제 데이터 계산 · 대표 검색어 합산 = 하한 기준] | 판정 | 기준선 대비 [추정] | 상한 시 뒤집힘 | 대표 검색어 |",
                  "|---:|---|---:|---:|---:|---|---|---|---|"]
            for r in sel:
                y = f"{r['yoy']:+.0%}" if r["yoy"] != "" else f"— ({r['yoy_status']})"
                rl = (f"{r['yoy_rel_baseline']:+.0%} {r['trend_call_rel']}" if r["yoy_rel_baseline"] != "" else "—")
                L.append(f"| {r['axis_rank']} | {r['need_item']} [추정·분류] | "
                         f"하한 {r['volume_12m_lo']:,} ~ 상한 {r['volume_12m_hi']:,} | "
                         f"{r['volume_share_in_axis_lo']:.0%} / {r['volume_share_in_axis_hi']:.0%} | {y} | "
                         f"{r['trend_call'] or '—'} | {rl} | "
                         f"{('⚠ ' + r['flip_under_hi']) if r['flip_under_hi'] else '—'} | {r['top_keywords']} |")
            extra = [r["need_item"] for r in it_rows if r["axis"] == ax and r["brand_scope"] == sc
                     and r["axis_rank_hi"] <= a.top < r["axis_rank"]]
            if extra:
                L.append(f"\n⚠ 상한 기준이면 상위 {a.top}에 드는 항목: {', '.join(extra)}")
            L.append("")
    if a.axes:
        L += ["## 카테고리 경계 점검", "",
              "경계 밖(exclude 패턴 — 집계 제외): "
              + (", ".join(f"{r['keyword']}({int(num(r['volume_total']) or 0):,})"
                           for r in sorted(ex_rows, key=lambda r: -(num(r["volume_total"]) or 0))[:15]) or "없음"), "",
              "카테고리어를 포함하지 않는 검색어(집계에는 남아 있다 — 인접 카테고리면 exclude에 추가, 맞으면 category_terms 보강): "
              + (", ".join(f"{g['rep']}({g['axis']} · {int(gv(g, 'lo')):,})" for g in chk[:15]) or "없음"), ""]
    un = sorted([g for g in grp.values() if g["axis"] == UNCLASSIFIED],
                key=lambda g: (-gv(g, "lo"), -gv(g, "hi")))[:15]
    if un:
        L += ["## 미분류 상위 검색어(하한 순) — 축 사전 보강 후보", "",
              ", ".join(f"{g['rep']}({int(gv(g, 'lo')):,}~{int(gv(g, 'hi')):,})" for g in un), ""]
    L += ["## 라벨", "",
          "- `[실제 데이터]` 검색량·monthly_volume과 그로부터의 직접 계산(전년 동기 · 기준선 전년 동기)",
          "- `[추정]` 축·니즈 항목 분류(정규식 사전 규칙) · 표기변형 그룹 볼륨 하한(최댓값)·상한(합)과 그 합계·비중·추세 커버리지 · "
          "같은 검색 중복 판정 · 기준선 대비 · 마지막 달 미완결 의심",
          f"- `[가정]` 게이트 그룹 {a.gate:,} · 항목 합산 {a.item_gate:,} · 판정 임계 ±20% · 기준선 대비 임계 ±{a.rel_threshold:.0%} · "
          f"추세 커버리지 50% 미만은 잠정 · 마지막 달 {last_mode} · 순위·판정을 하한으로 매김 · 카테고리 경계 패턴",
          "- `[데이터 공백]` 시계열 없음·결측 월·게이트 미달 그룹, 검색량 미반환 키워드, 기준선 시계열 없음", ""]
    f3 = out / f"roadmap_priority_{a.category}.md"
    f3.write_text("\n".join(L), encoding="utf-8")

    print(f"기준월 {ref or '미확인'} · 비교 창 {yoy_window or '없음'} · 마지막 달 {last_mode} · 미완결 의심 {suspects}행")
    print(f"카테고리 기준선({', '.join(base_kws)}) 전년 동기: "
          + (f"{base_yoy:+.1%}" if base_yoy != "" else "계산 불가 — 기준선 시계열 없음"))
    print(f"{len(rows)}행 → 중복 {n_dup}행 · 경계 밖 {len(ex_rows)}행 제외 → canonical {len(grp)}그룹 · 분류율 {pr(cls)} · "
          f"논브랜드 비중 {pr(nb)}" if tot["lo"] else f"{len(rows)}행 → 볼륨 없음")
    if flips:
        print(f"⚠ 상한 기준에서 순위·판정이 뒤집히는 항목 {len(flips)}개: "
              + " · ".join(f"{r['axis']}:{r['need_item']}({r['brand_scope']}) {r['flip_under_hi']}" for r in flips[:8]))
    if chk:
        print(f"경계 점검 대상 {len(chk)}그룹(카테고리어 미포함) — 상위: " + ", ".join(g["rep"] for g in chk[:5]))
    print("추세 상태: " + " · ".join(f"{k} {v}" for k, v in sorted(st.items())))
    if not a.coverage:
        print("⚠ --coverage 미기재 — 수집이 절단(limit 도달)이면 순위가 긴 꼬리 Pain을 놓친다")
    print(f"→ {f1}\n→ {f2}\n→ {f3}")


if __name__ == "__main__":
    main()
