#!/usr/bin/env python3
"""구매 의도 분리 — 의도 클래스·티어 부여와 거래 의도 비중 산출 (SKILL.md STEP 3~5).

입력은 build_universe.py가 만든 유니버스 CSV와 의도 사전(themes.json 스키마 + tier + category)이다.

  1. 같은 검색의 중복 제거(계약 T5 ①) — 응답에 요청하지 않은 어순·동의어 변형이 오고, 그중 일부는
     볼륨·CPC·경쟁도가 완전히 같다(US 실측: 여분 행이 볼륨의 33.5%). 유니버스의 `dup_of`(없으면
     series_dedup.dedup_map으로 다시 계산)로 한 번만 센다. canonical 그룹으로는 잡히지 않는다.
  2. 분류 — 클래스 패턴을 이 스크립트가 다시 평가한다(buyer_match.py). ASCII 마커는 토큰 단위라
     `best`⊂`bestbuy`, `or`⊂`for dyson` 같은 오탐이 없다. 한글 마커는 canonical 부분일치.
  3. 카테고리 경계 — `category.exclude`(·`category.pattern`)에 걸리는 행은 분포에서 빼고 따로 적는다.
  4. canonical 그룹 하한(최댓값)·상한(합) — 모든 비중을 각자의 분모로 두 번 계산하고 두 값으로 적는다
     (하한 기준 비중이 상한 기준보다 클 수 있어 «~» 범위로 쓰지 않는다). 순위·판정은 하한 기준.
  5. 전체 / 브랜드 포함 / 논브랜드 3단 분포(계약 T8) · 티어별 상위 기여 검색어.

선택: --stage2-out으로 2단(`all`) 대상, --lm-intents로 그 응답을 넣으면 LM `intents`(0/1 플래그)와 대조하고
2단 대상(미분류·충돌)이 LM에서 어떻게 읽히는지 요약한다. --requested로 요청↔응답 대조(미반환·표기 변경).

    python3 buyer_share.py universe.csv --themes intent_themes.json --requested keyword_set.json \\
        -o intent_class.csv --report buyer_signal.md \\
        --stage2-out stage2.json --stage2-gate 1000 --stage2-cap 50 [--lm-intents all.json] \\
        [--queried-at YYYY-MM-DD] [--category 무선청소기]
"""
import argparse, csv, json, pathlib, re, sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, dedup_map  # noqa: E402
from buyer_match import Marker, brand_marker, canon_bounds  # noqa: E402

TIERS = ["buyer", "brand_nav", "browser", "owner", "resale", "unclassified"]
TIER_KO = {"buyer": "거래 의도", "brand_nav": "브랜드 지명(의도 혼재)", "browser": "탐색",
           "owner": "구매 후·보유자", "resale": "중고·리셀", "unclassified": "미분류·헤드"}
LM_KO = {"t": "거래", "c": "비교", "i": "정보", "n": "지명"}
COLUMNS = ["keyword", "canonical", "dup_of", "volume_total", "group_volume_lo", "group_volume_hi",
           "is_group_rep", "in_scope", "brand", "intent_class", "tier", "facets", "conflict", "cpc", "competition",
           "lm_i", "lm_n", "lm_c", "lm_t", "lm_reading", "lm_check", "label_metric", "label_group_volume",
           "label_class"]


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_resp(path):
    o = json.load(open(path, encoding="utf-8"))
    if isinstance(o, dict):
        return o.get("data", []), (o.get("request_detail") or {}).get("keywords"), (o.get("cost_detail") or {}).get("total_cost")
    return o, None, None


def month_gap(ref, day):
    if not ref or not day:
        return ""
    return (int(day[:4]) * 12 + int(day[5:7])) - (int(ref[:4]) * 12 + int(ref[5:7]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("universe", help="build_universe.py 산출 CSV")
    ap.add_argument("--themes", required=True, help="의도 사전 (themes.json 스키마 + tier + category)")
    ap.add_argument("-o", "--out", default="intent_class.csv")
    ap.add_argument("--report", default="", help="요약 마크다운 경로")
    ap.add_argument("--requested", default="", help="1단에 요청한 키워드(배열 JSON 또는 응답의 request_detail)")
    ap.add_argument("--lm-intents", default="", help="2단 keyword_info(all) 응답 JSON")
    ap.add_argument("--stage2-out", default="", help="2단 조회 대상 목록을 쓸 JSON 경로")
    ap.add_argument("--stage2-gate", type=float, default=1000, help="2단 대상 최소 그룹 볼륨(하한 기준)")
    ap.add_argument("--stage2-cap", type=int, default=50, help="2단 대상 최대 개수")
    ap.add_argument("--queried-at", default="")
    ap.add_argument("--category", default="")
    a = ap.parse_args()

    cfg = json.load(open(a.themes, encoding="utf-8"))
    subs = cfg.get("rules", {}).get("변형치환", {})
    classes = [(t["name"], t.get("tier", ""), Marker(t["pattern"])) for t in cfg["themes"]]
    tier_of = {n: t for n, t, _ in classes}
    nm = cfg.get("no_match", {})
    tier_of[nm.get("with_brand", "")] = cfg.get("no_match_tier", {}).get("with_brand", "brand_nav")
    tier_of[nm.get("without_brand", "")] = cfg.get("no_match_tier", {}).get("without_brand", "unclassified")
    bad = sorted(n for n, t in tier_of.items() if t not in TIERS)
    if bad:
        sys.exit(f"tier가 없거나 잘못된 클래스: {bad} — 허용값 {TIERS}")
    bmark = brand_marker(cfg.get("brands", []), subs)
    # 브랜드 사전에 판매 채널명이 들어가면(px·올리브영·쿠팡) 채널 지명 검색이 «브랜드 포함»으로 가고, 채널 마커와 함께 걸린
    # 검색은 거래 의도이면서 브랜드 행이 되어 «논브랜드 안 거래 의도»에서 빠진다(2026-09-30 선크림: map-your-market 사전의 px)
    chan_brands = []
    for b in cfg.get("brands", []):
        bc, bb = canon_bounds(b, subs)
        hit = [n for n, t, m in classes if t == "buyer" and m.hit(bc, bb)]
        if hit:
            chan_brands.append(f"{b}({'·'.join(hit)})")
    if chan_brands:
        print(f"⚠️ 브랜드 사전에 거래 마커(채널·가격)에도 걸리는 이름이 있다: {', '.join(chan_brands)} — 판매 채널명은 brands에서 빼고 "
              "채널 지명 클래스에만 둔다(그대로 두면 채널 검색이 브랜드 행이 되어 논브랜드 거래 의도가 줄어든다)", file=sys.stderr)
    a.chan_brands = chan_brands
    cat = cfg.get("category") or {}
    cat_ex = Marker(cat["exclude"]) if cat.get("exclude") else None
    cat_in = Marker(cat["pattern"]) if cat.get("pattern") else None

    rows = list(csv.DictReader(open(a.universe, encoding="utf-8-sig")))
    if not rows:
        sys.exit("유니버스가 비었다")

    # ── 같은 검색의 중복 (T5 ①) — 유니버스에 dup_of가 없으면 지표로 다시 계산
    if "dup_of" not in rows[0]:
        pseudo = [{"keyword": r["keyword"], "ads_metrics": {
            "volume_total": num(r.get("volume_total")), "cpc": num(r.get("cpc")),
            "competition_index": num(r.get("competition_index")),
            "gg_volume_total": num(r.get("gg_volume_total")), "nv_volume_total": num(r.get("nv_volume_total"))}}
            for r in rows]
        dm = dedup_map(pseudo, subs)
        for r in rows:
            rep = dm.get(r["keyword"], {}).get("rep", r["keyword"])
            r["dup_of"] = "" if rep == r["keyword"] else rep

    # ── 분류 (토큰 단위 마커) · 카테고리 경계
    for r in rows:
        c, bd = canon_bounds(r["keyword"], subs)
        r["canonical"] = c
        r["_v"] = num(r.get("volume_total"))
        r["_brand"] = next((b for b, m in bmark if m.hit(c, bd)), "")
        facets = [n for n, _, m in classes if m.hit(c, bd)]
        r["_facets"] = facets
        r["_cls"] = facets[0] if facets else (nm.get("with_brand", "") if r["_brand"] else nm.get("without_brand", ""))
        r["_tier"] = tier_of.get(r["_cls"], "unclassified")
        ft = sorted({tier_of.get(f, "") for f in facets} - {""})
        r["_conflict"] = "|".join(ft) if len(ft) > 1 else ""
        out_scope = (cat_ex and cat_ex.hit(c, bd)) or (cat_in and not cat_in.hit(c, bd))
        r["_scope"] = "out" if out_scope else "in"
    # 같은 검색의 중복은 대표 행의 경계를 따른다(`robotic vacuum` = `robot vacuum`이 밖이면 함께 밖)
    by_kw = {r["keyword"]: r for r in rows}
    for r in rows:
        if r["dup_of"] in by_kw:
            r["_scope"] = by_kw[r["dup_of"]]["_scope"]

    live = [r for r in rows if not r["dup_of"] and r["_scope"] == "in"]
    groups = defaultdict(list)
    for r in live:
        groups[r["canonical"]].append(r)
    for rs in groups.values():
        vals = [r["_v"] for r in rs if r["_v"] is not None]
        lo, hi = (max(vals), sum(vals)) if vals else (None, None)
        rep = max(rs, key=lambda r: r["_v"] or -1)
        for r in rs:
            r["_lo"], r["_hi"], r["_rep"] = lo, hi, r is rep

    # ── LM intents (2단)
    lm, lm_by_c, ref_month, s2_req, s2_cost = {}, {}, "", None, None
    if a.lm_intents:
        data, s2_req, s2_cost = load_resp(a.lm_intents)
        for d in data:
            it = d.get("intents") or {}
            v = {k: num(it.get(k)) for k in ("i", "n", "c", "t")}
            lm[d["keyword"]] = v
            lm_by_c.setdefault(canon(d["keyword"], subs), v)
            months = [x["month"] for x in d.get("monthly_volume") or [] if isinstance(x, dict) and "total" in x]
            if months:
                ref_month = max(ref_month, max(months))

    out = []
    for r in rows:
        li = lm.get(r["keyword"]) or lm_by_c.get(r["canonical"]) or {}
        has = [k for k in ("t", "c", "i", "n") if (li.get(k) or 0) > 0]
        reading = ("+".join(LM_KO[k] for k in has) or "플래그 없음") if li else ""
        if li and r["_tier"] == "buyer":
            check = "일치" if "t" in has else "불일치"
        elif li and r["_tier"] == "browser":
            check = "일치" if {"i", "c"} & set(has) else "불일치"
        else:
            check = "참고" if li else ""
        grp = r["dup_of"] == "" and r["_scope"] == "in"
        out.append({
            "keyword": r["keyword"], "canonical": r["canonical"], "dup_of": r["dup_of"],
            "volume_total": r.get("volume_total", ""),
            "group_volume_lo": "" if not grp or r["_lo"] is None else int(r["_lo"]),
            "group_volume_hi": "" if not grp or r["_hi"] is None else int(r["_hi"]),
            "is_group_rep": int(grp and r["_rep"]), "in_scope": "yes" if r["_scope"] == "in" else "no(카테고리 밖)",
            "brand": r["_brand"], "intent_class": r["_cls"], "tier": r["_tier"],
            "facets": "|".join(r["_facets"]), "conflict": r["_conflict"],
            "cpc": r.get("cpc", ""), "competition": r.get("competition", ""),
            "lm_i": li.get("i", ""), "lm_n": li.get("n", ""), "lm_c": li.get("c", ""), "lm_t": li.get("t", ""),
            "lm_reading": reading, "lm_check": check,
            "label_metric": "실제 데이터", "label_group_volume": "추정", "label_class": "추정",
        })
    out.sort(key=lambda r: (-(num(r["group_volume_lo"]) or 0), -(num(r["volume_total"]) or 0)))
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS); w.writeheader(); w.writerows(out)

    report(a, out, rows, s2_req, s2_cost, ref_month, lm)


def report(a, out, rows, s2_req, s2_cost, ref_month, lm):
    reps = [r for r in out if r["is_group_rep"]]
    missing_vol = [r for r in reps if r["group_volume_lo"] == ""]
    reps = [r for r in reps if r["group_volume_lo"] != ""]
    B = ("lo", "hi")
    gv = lambda r, b: r[f"group_volume_{b}"]
    share = lambda n, d: n / d if d else 0.0

    def dist(sub, b):
        v = defaultdict(float)
        for r in sub:
            v[r["tier"]] += gv(r, b)
        return v, sum(v.values())

    scopes = {"전체": reps, "브랜드 포함": [r for r in reps if r["brand"]], "논브랜드": [r for r in reps if not r["brand"]]}
    strict = [r for r in reps if not r["conflict"]]
    ds = {b: {k: dist(v, b) for k, v in scopes.items()} for b in B}
    total = {b: ds[b]["전체"][1] for b in B}
    kpi = {}
    for b in B:
        v, t = ds[b]["전체"]
        sv, st = dist(strict, b)
        nv, nt = ds[b]["논브랜드"]
        kpi[b] = {"buyer": share(v["buyer"], t), "buyer_plus_brand": share(v["buyer"] + v["brand_nav"], t),
                  "strict": share(sv["buyer"], st),
                  "conflict": share(sum(gv(r, b) for r in reps if r["conflict"]), t),
                  "nonbrand_buyer": share(nv["buyer"], nt)}
    inscope = [r for r in out if r["in_scope"] == "yes"]
    dups = [r for r in inscope if r["dup_of"]]
    dup_vol = sum(num(r["volume_total"]) or 0 for r in dups)
    raw_tot = sum(num(r["volume_total"]) or 0 for r in inscope)
    raw_buy = sum(num(r["volume_total"]) or 0 for r in inscope if r["tier"] == "buyer")
    outside = [r for r in out if r["in_scope"] != "yes"]

    cpc = {b: {} for b in B}
    for b in B:
        for t in TIERS:
            pairs = [(num(r["cpc"]), gv(r, b)) for r in reps if r["tier"] == t and num(r["cpc"]) is not None]
            w = sum(x for _, x in pairs)
            cpc[b][t] = round(sum(c * x for c, x in pairs) / w, 2) if w else ""

    cand = [r for r in reps if r["tier"] == "unclassified" or r["conflict"]]
    stage2 = [r["keyword"] for r in cand if r["group_volume_lo"] >= a.stage2_gate][: a.stage2_cap]
    stage2_hi_only = [r["keyword"] for r in cand if r["group_volume_lo"] < a.stage2_gate <= r["group_volume_hi"]]
    if a.stage2_out:
        json.dump(stage2, open(a.stage2_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    flips = []
    for sc in scopes:
        order = {b: [t for t in sorted(TIERS, key=lambda t: -ds[b][sc][0][t]) if ds[b][sc][0][t]] for b in B}
        if order["lo"] != order["hi"]:
            flips.append(f"티어 순서({sc}): 하한 {' > '.join(TIER_KO[t] for t in order['lo'])} → "
                         f"상한 {' > '.join(TIER_KO[t] for t in order['hi'])}")
    unc = [r for r in reps if r["tier"] == "unclassified"]
    top_unc, top_unc_hi = unc[:10], sorted(unc, key=lambda r: -r["group_volume_hi"])[:10]
    if [r["keyword"] for r in top_unc] != [r["keyword"] for r in top_unc_hi]:
        flips.append("미분류 상위 순서: 상한 기준이면 " + " > ".join(r["keyword"] for r in top_unc_hi))
    if stage2_hi_only:
        flips.append(f"2단 대상: 상한 기준이면 게이트를 넘는 그룹 {len(stage2_hi_only)}개 추가 — " + ", ".join(stage2_hi_only[:10]))

    rng = lambda lo, hi: f"하한 {lo:,.0f} ~ 상한 {hi:,.0f}"
    two = lambda key: f"하한 기준 {kpi['lo'][key]:.1%} · 상한 기준 {kpi['hi'][key]:.1%}"
    gap = month_gap(ref_month, a.queried_at)
    L = [f"# 구매 의도 분리 — {a.category or '(카테고리 미기재)'}", "",
         f"- 조회일: {a.queried_at or '(미기재)'} · 데이터 기준월: "
         f"{ref_month or '미확인 (2단 all 응답이 없으면 알 수 없다)'}" + (f" · 지연 {gap}개월" if gap != "" else ""),
         "- 볼륨: `volume_total` = 데이터 기준월로 끝나는 12개월 합 (조회일 기준 아님) · 단위 «건»",
         f"- 집계 기준: canonical 그룹 {len(reps)}개 (카테고리 안 행 {len(inscope)}개) — 그룹 합 [추정] "
         f"{rng(total['lo'], total['hi'])} · 중복·경계 처리 전 행 단위 합 {int(raw_tot):,}",
         f"- 같은 검색의 중복 [추정]: {len(dups)}행 · {int(dup_vol):,}건을 한 번만 셌다(어순·동의어 변형의 지표 완전 일치 — "
         f"계약 T5 ①). 제외하지 않았다면 거래 의도 비중은 {share(raw_buy, raw_tot):.1%}(행 단위)",
         "- 그룹 볼륨: 하한 = 그룹 내 최댓값 · 상한 = 그룹 내 합. 하한 = 상한이어도 «중복이 없다»는 증명이 아니다 — "
         "중복 판정은 지표 완전 일치 규칙이 보는 만큼이다",
         f"- 카테고리 밖(category 경계) {len(outside)}행 · {int(sum(num(r['volume_total']) or 0 for r in outside)):,}건 — 분포에서 제외",
         f"- 볼륨 결측 그룹: {len(missing_vol)}개 [데이터 공백] — 분모에서 제외", ""]
    if getattr(a, "chan_brands", None):
        L += [f"- ⚠ 브랜드 사전에 거래 마커(채널·가격)에도 걸리는 이름: {', '.join(a.chan_brands)} — 판매 채널명이면 brands에서 빼고 "
              "다시 돌린다. 그대로면 그 채널 검색이 «브랜드 포함»으로 가서 논브랜드 거래 의도가 과소집계된다 [가정]", ""]
    L += reconcile(a, rows)
    L += ["## 핵심 수치 [추정 · 검색량 기준 근사 · 검색 ≠ 구매]", "",
          "비중은 하한·상한 각자의 분모로 계산한 **두 값**이다(범위가 아니다 — 하한 기준이 더 클 수 있다).", "",
          f"- 거래 의도 검색 비중: {two('buyer')}",
          f"- 브랜드 지명까지 거래 의도로 볼 때: {two('buyer_plus_brand')}",
          f"- 충돌 그룹 제외(엄격): {two('strict')} · 충돌 그룹 볼륨 {two('conflict')}",
          f"- 논브랜드 안에서 거래 의도 비중: {two('nonbrand_buyer')}", "",
          "## 상한 기준에서 뒤집히는 순위·판정", ""]
    same = all(r["group_volume_lo"] == r["group_volume_hi"] for r in reps)
    L += [f"- ⚠ {f}" for f in flips] or [
        ("- 비교할 차이 없음 — 중복 행을 합친 뒤 모든 그룹에서 하한 = 상한이다. «중복이 없다»·«결론이 견고하다»는 뜻이 아니다"
         if same else "- 없음 — 하한·상한 어느 쪽이든 순위·판정이 같다")]
    L += merged_rows(out)
    L += ["", "## 티어 분포 (그룹 볼륨 비중 [추정] · 하한 / 상한)", "",
          "| 티어 | 전체 | 브랜드 포함 | 논브랜드 | 가중 CPC 하한 / 상한 [추정] |", "|---|---:|---:|---:|---:|"]
    for t in TIERS:
        cells = [" / ".join(f"{share(ds[b][k][0][t], ds[b][k][1]):.1%}" if ds[b][k][1] else "–" for b in B) for k in scopes]
        L.append(f"| {TIER_KO[t]} | " + " | ".join(cells) + f" | {cpc['lo'][t]} / {cpc['hi'][t]} |")
    L += ["", "## 티어별 상위 기여 검색어 (하한 기준 · 티어 볼륨 중 몫)", ""]
    for t in TIERS:
        rs = [r for r in reps if r["tier"] == t]
        tv = sum(r["group_volume_lo"] for r in rs)
        if rs:
            L.append(f"- {TIER_KO[t]}: " + " · ".join(f"{r['keyword']} {r['group_volume_lo']:,} ({share(r['group_volume_lo'], tv):.0%})"
                                                     for r in rs[:3]))
    L += ["", "## 미분류 상위 (하한 순) — 브랜드 사전 누락 의심부터 본다", ""]
    L += [f"- {r['keyword']} ({rng(r['group_volume_lo'], r['group_volume_hi'])})" for r in top_unc] or ["- 없음"]
    L += ["", f"## 2단 조회 대상 (하한 기준 게이트): {len(stage2)}개 → `all` 10 × {len(stage2)} = {10 * len(stage2):,} 크레딧 "
          f"[가정: 게이트 {int(a.stage2_gate):,} · 최대 {a.stage2_cap}개]"]
    if lm:
        L += stage2_summary(out, reps, s2_req, s2_cost, lm, a.themes)
    L += ["", "라벨: 볼륨·CPC·경쟁도·LM intents = [실제 데이터] · 클래스·티어·그룹 하한·상한·비중·중복 판정 = [추정] · "
          "마커 사전·클래스 순서·카테고리 경계·순위를 하한으로 매기는 것·게이트 = [가정] · 결측·미반환 = [데이터 공백]"]
    text = "\n".join(L) + "\n"
    if a.report:
        open(a.report, "w", encoding="utf-8").write(text)
    print(text)
    print(f"→ {a.out}" + (f" · {a.report}" if a.report else "") + (f" · {a.stage2_out}" if a.stage2_out else ""))


def merged_rows(out, k=10):
    """같은 검색으로 합친 행(계약 T5 ①) — 대표 ← 합친 행, 볼륨이 큰 묶음부터."""
    b = defaultdict(list)
    for r in out:
        if r["dup_of"] and r["in_scope"] == "yes":
            b[r["dup_of"]].append(r)
    if not b:
        return ["", "## 같은 검색으로 합친 행 [추정]", "", "- 없음 — 지표 완전 일치 묶음이 없다(규칙이 보는 범위 안에서)"]
    vol = lambda rs: sum(num(r["volume_total"]) or 0 for r in rs)
    items = sorted(b.items(), key=lambda x: -vol(x[1]))
    L = ["", f"## 같은 검색으로 합친 행 [추정] — {len(items)}묶음 · {sum(len(v) for v in b.values())}행 · "
         f"{int(sum(vol(v) for v in b.values())):,}건을 대표 행 하나로 셌다", ""]
    L += [f"- {rep} ← " + ", ".join(r["keyword"] for r in rs) + f" (합친 {int(vol(rs)):,}건)" for rep, rs in items[:k]]
    if len(items) > k:
        L.append(f"- … 외 {len(items) - k}묶음 (`intent_class.csv`의 `dup_of`)")
    return L


def reconcile(a, rows):
    """요청↔응답 대조 (계약 §1) — 과금은 요청 키워드 수, 미반환은 «0»이 아니라 [데이터 공백]."""
    if not a.requested:
        return ["## 요청↔응답 대조", "", "- 요청 목록 미제공(--requested) — 미반환 키워드를 확인할 수 없다 [데이터 공백]", ""]
    data, req, cost = load_resp(a.requested)
    req = list(dict.fromkeys(req if req is not None else [x if isinstance(x, str) else x.get("keyword") for x in data]))
    cfg = json.load(open(a.themes, encoding="utf-8"))
    subs = cfg.get("rules", {}).get("변형치환", {})
    kws = {r["keyword"] for r in rows}
    # 대조는 canonical로 — 띄어쓰기·NFKC만 먼저 보고, 없으면 변형치환까지 적용한 canonical로 본다
    # (치환부터 보면 `코드리스청소기`가 `무선청소기` 그룹 전체에 붙는다)
    bycan0, bycan = defaultdict(list), defaultdict(list)
    for r in rows:
        bycan0[canon(r["keyword"])].append(r["keyword"])
        bycan[canon(r["keyword"], subs)].append(r["keyword"])
    reqc = {canon(k, subs) for k in req}
    look = lambda k: bycan0.get(canon(k)) or bycan.get(canon(k, subs)) or []
    miss = [k for k in req if k not in kws and not look(k)]
    ren = [(k, look(k)) for k in req if k not in kws and look(k)]
    unreq = [r for r in rows if r["keyword"] not in req and canon(r["keyword"], subs) not in reqc]
    uv = sum(num(r.get("volume_total")) or 0 for r in unreq)
    L = ["## 요청↔응답 대조 (canonical)", "",
         f"- 1단 과금: 요청 {len(req)}개 × 1 = {len(req):,}크레딧" + (f" (응답 cost_detail {cost:,})" if cost is not None else "")
         + " — 응답 행 수와 무관",
         f"- 미반환 {len(miss)}개 [데이터 공백] — «결측 0»이 아니다(과금은 됐다)"
         + (": " + ", ".join(miss[:15]) + (" …" if len(miss) > 15 else "") if miss else ""),
         f"- 표기가 바뀌어 반환 {len(ren)}개" + (": " + ", ".join(f"{k} → {'/'.join(v)}" for k, v in ren[:10])
                                             + (" …" if len(ren) > 10 else "") if ren else ""),
         f"- 요청하지 않은 행 {len(unreq)}개 · {int(uv):,}건 (과금 안 됨 — 분류·중복 판정에 그대로 들어갔다)", ""]
    return L


def stage2_summary(out, reps, s2_req, s2_cost, lm, themes):
    """2단 실측 — 실제로 조회한 수·크레딧은 응답에서 읽고, 미분류·충돌 그룹이 LM에서 어떻게 읽히는지 요약."""
    L = [""]
    if s2_req is None:
        s2_req = [k for k in lm]   # request_detail이 없는 저장본 — 반환 행 기준(미반환은 알 수 없다)
        L.append(f"## 2단 실행(응답 기준): 반환 {len(s2_req)}개 · 요청 목록(request_detail) 없음 — 미반환 확인 불가 [데이터 공백]"
                 + (f" · {s2_cost:,}크레딧(cost_detail)" if s2_cost is not None else ""))
    else:
        subs = json.load(open(themes, encoding="utf-8")).get("rules", {}).get("변형치환", {})
        got = {canon(k, subs) for k in lm}
        miss = [k for k in s2_req if k not in lm and canon(k, subs) not in got]
        L.append(f"## 2단 실행(응답 기준): 요청 {len(s2_req)}개 → "
                 + (f"{s2_cost:,}크레딧(cost_detail)" if s2_cost is not None else f"{10 * len(s2_req):,}크레딧(10 × 요청)")
                 + f" · 미반환 {len(miss)}개 [데이터 공백]" + (": " + ", ".join(miss[:10]) if miss else ""))
    chk = [r["lm_check"] for r in out if r["lm_check"] in ("일치", "불일치")]
    L += ["", f"## LM intents 대조 (규칙 분류는 바꾸지 않는다 · intents는 0/1 플래그 [실제 데이터])", "",
          (f"- 거래·탐색 티어 대조: 일치 {chk.count('일치')} / {len(chk)}" if chk else
           "- 거래·탐색 티어 대조: 해당 없음 — 2단 대상은 미분류·충돌 그룹이라 아래 요약으로 본다")]
    tgt = [r for r in reps if r["lm_reading"] and (r["tier"] == "unclassified" or r["conflict"])]
    if tgt:
        tot = sum(r["group_volume_lo"] for r in tgt)
        agg = defaultdict(lambda: [0, 0.0])
        for r in tgt:
            for k in r["lm_reading"].split("+"):
                agg[k][0] += 1; agg[k][1] += r["group_volume_lo"]
        L.append(f"- 2단 대상(미분류·충돌) {len(tgt)}그룹 · 하한 {tot:,}건이 LM에서 읽히는 방식(플래그 중복 가능): "
                 + " · ".join(f"{k} {n}그룹 {(v / tot if tot else 0):.0%}" for k, (n, v) in sorted(agg.items(), key=lambda x: -x[1][1])))
        L += [f"  - {r['keyword']} ({r['tier']}{' · 충돌 ' + r['conflict'] if r['conflict'] else ''}) → LM {r['lm_reading']}"
              for r in tgt[:10]]
        L.append("- 사전 보강 후보: LM이 «거래»로 읽는 미분류 헤드는 거래 마커 누락일 수 있다 — 사전을 고친 뒤 재실행 [추정]")
    return L


if __name__ == "__main__":
    main()
