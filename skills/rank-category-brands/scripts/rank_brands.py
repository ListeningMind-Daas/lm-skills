#!/usr/bin/env python3
"""분야 브랜드 순위 · 전년 동기 · 3년 흐름 · 검색 점유 · 성별/연령대 순위 — rank-category-brands STEP 5 (이 스킬 고유).

  python3 scripts/rank_brands.py stage2_all.json --config brands.json --category running-shoes-kr \
      --stage1 stage1_ads_metrics.json --requested stage2_keywords.json --queried-at 2026-11-19 \
      --gate-monthly 1000 --seg-gate 12000 --top 10 --outdir out

함정 처리(SKILL.md STEP 5):
  T1 — 기준월은 monthly_volume 의 최댓값(배열 순서 가정 없음) · 조회일 · 지연을 요약 헤더에 쓴다
  T2 — 마지막 달은 기본 제외(분석 월 = 기준월 − 1) · 같은 규칙으로 의심 행 수를 센다 · --include-last-month 면 «잠정»
  T3 — volume_trend 는 판정에 쓰지 않는다(keyword_map 참고 컬럼만) · 전년 동기는 monthly_volume 에서 직접
  T4 — 전년 동기 · 3년 변화율은 기준 창 합이 게이트 미만이면 비우고 순위에서 뺀다 · 성별/연령대 순위도 게이트
  T5 — 완전 중복 행 합침 → 같은 검색의 중복(시계열 일치)은 한 번만 → canonical 묶음은 하한(묶음 최댓값)·상한(묶음 합)
  T6 — 결측 월은 0 이 아니다 · 여러 창을 나란히 보는 합계는 모든 창에 값이 있는 묶음으로 모집단을 고정한다
  T8 — 분모에 논브랜드 분야 검색을 따로 넣어 «브랜드끼리 몫»과 «분야 전체 관심 중 몫»을 나눈다 ·
       논브랜드 행 중 사전 브랜드와 닮은 행은 «별칭 누락 의심»으로 따로 세고 논브랜드 몫을 판정 보류로 표시한다
"""
import argparse, csv, json, pathlib, sys
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, collapse_identical, dedup_map, report_collapse  # noqa: E402
from rank_common import (COMPARE, COUNTED, COUNTED_BRAND, NONBRAND, alias_gap_suspects, classify,  # noqa: E402
                         load_config, load_rows,
                         series_of, shift, utf8_console, vol_total, window, wsum)

RANK_COLS = ["rank_lo", "rank_hi", "rank_flip", "in_top", "brand", "bucket_type", "groups_n",
             "volume_12m_lo", "volume_12m_hi", "incomplete_groups_12m", "share_brandset_lo", "share_genre_lo",
             "share_genre_hi", "yoy_base_12m", "yoy_compare_12m", "yoy_abs_12m", "yoy_12m", "yoy_groups_n",
             "yoy_3m", "yoy_gated_out", "rank_yoy", "brand_alone_12m", "stage2_coverage",
             "data_ref_month", "analysis_end_month", "last_month_included", "label"]
TREND_COLS = ["brand", "bucket_type", "groups_fixed_3y", "groups_excluded_3y", "window_y2", "window_y1", "window_y0",
              "volume_y2", "volume_y1", "volume_y0", "abs_chg_3y", "chg_3y", "chg_3y_gated_out", "trend_status", "label"]
SEG_COLS = ["segment_type", "segment", "segment_label", "brand", "demo_coverage", "segment_ratio",
            "segment_volume_12m", "share_in_segment", "index_vs_all", "rank_in_segment", "seg_gated_out",
            "gate_reason", "label"]
MAP_COLS = ["keyword", "canonical", "bucket", "kind", "stage", "group_id", "group_rep", "dup_of", "dup_basis",
            "volume_total_stage1", "volume_y0", "complete_y0", "complete_3y", "has_demography",
            "excluded_reason", "alias_suspect_brand", "api_volume_trend_do_not_use"]
# 응답 키 기준 표시 이름 — 구간 경계는 API 사양 확인 전 [가정]
AGE_LABEL = {"a13": "10대", "a20": "20대 초반", "a25": "20대 후반", "a30": "30대", "a40": "40대", "a50": "50대 이상"}
REASON = {"alone": "브랜드명 단독(다른 품목 수요 포함 — 순위·점유 밖, brand_alone_12m 로 따로)",
          "off_genre": "분야어 없는 검색(분야 밖 수요 섞임 — 순위·점유 밖)", "excluded": "exclude 정규식"}


def last_month_suspects(rows, ref):
    """check_refmonth.py 와 같은 규칙 — 최신 월 MoM 이 실제 하락이면서 전년 같은 구간보다 30%p 이상 나쁨."""
    prev, ly, lprev = shift(ref, 1), shift(ref, 12), shift(ref, 13)
    n = checked = 0
    for r in rows:
        s = series_of(r)
        if not all(s.get(m) for m in (ref, prev, ly, lprev)):
            continue
        checked += 1
        mom = s[ref] / s[prev] - 1
        if mom < 0 and mom - (s[ly] / s[lprev] - 1) < -0.30:
            n += 1
    return n, checked


def pct(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b else None


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("stage2")
    ap.add_argument("--config", required=True)
    ap.add_argument("--category", required=True)
    ap.add_argument("--stage1", help="1단 ads_metrics 응답 — 브랜드명 단독 볼륨 · 2단 커버리지에 쓴다")
    ap.add_argument("--requested", help="2단 요청 목록 — 미반환 키워드 대조")
    ap.add_argument("--queried-at", default=date.today().isoformat())
    ap.add_argument("--gate-monthly", type=int, default=1000,
                    help="변화율 기저 게이트 — 기준 창 월평균 (기본 1,000건 → 12개월 12,000 · 3개월 3,000) [가정]")
    ap.add_argument("--seg-gate", type=int, default=12000, help="성별/연령대 순위 게이트 — 세그먼트 12개월 검색량 [가정]")
    ap.add_argument("--min-demo-coverage", type=float, default=0.5, help="성·연령이 붙은 볼륨 비중 하한 [가정]")
    ap.add_argument("--suspect-min-volume", type=int, default=600,
                    help="«별칭 누락 의심»으로 볼 논브랜드 행의 12개월 볼륨 하한 (기본 600 = 2단 게이트와 같음) [가정]")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--include-last-month", action="store_true")
    ap.add_argument("--outdir", default="out")
    a = ap.parse_args()
    cfg = load_config(a.config)
    out = pathlib.Path(a.outdir); out.mkdir(parents=True, exist_ok=True)
    cat = a.category

    rows = load_rows(a.stage2)
    n_raw = len(rows)
    rows, merged, conflicts = collapse_identical(rows)
    report_collapse(merged, conflicts, n_raw)
    rows = [r for r in rows if r.get("monthly_volume")]
    if not rows:
        sys.exit("monthly_volume 이 있는 행이 없다 — 2단은 data_type=all 로 조회할 것")

    # ── T1 기준월 · 분석 월
    ref = max(max(m["month"] for m in r["monthly_volume"] if isinstance(m, dict) and m.get("month")) for r in rows)
    lag = (int(a.queried_at[:4]) - int(ref[:4])) * 12 + int(a.queried_at[5:7]) - int(ref[5:7])
    end = ref if a.include_last_month else shift(ref, 1)
    earliest = min(m["month"] for r in rows for m in r["monthly_volume"] if isinstance(m, dict) and m.get("month"))
    suspect, checked = last_month_suspects(rows, ref)
    Y0, Y1, Y2 = window(end, 12), window(shift(end, 12), 12), window(shift(end, 24), 12)
    L3, P3 = window(end, 3), window(shift(end, 12), 3)
    wl = lambda w: f"{w[0]}~{w[-1]}"
    has_3y = earliest <= Y2[0]

    # ── 분류 · 같은 검색의 중복 · 묶음(T5)
    info = {}
    for r in rows:
        b, k = classify(r["keyword"], cfg)
        info[r["keyword"]] = {"row": r, "bucket": b, "kind": k, "s": series_of(r), "canon": canon(r["keyword"], cfg["rules"])}
    counted = [kw for kw, v in info.items() if v["kind"] in COUNTED]
    dm = dedup_map([info[k]["row"] for k in counted], cfg["rules"])
    parent = {k: k for k in counted}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)
    by_canon = {}
    for k in counted:
        by_canon.setdefault(info[k]["canon"], []).append(k)
        if dm[k]["rep"] != k:
            union(k, dm[k]["rep"])
    for ks in by_canon.values():
        for k in ks[1:]:
            union(ks[0], k)
    groups = {}
    for k in counted:
        groups.setdefault(find(k), []).append(k)

    alias_suspect, G = [], []
    for gi, (root, mem) in enumerate(sorted(groups.items(), key=lambda x: x[0])):
        buckets = {info[k]["bucket"] for k in mem}
        brand_b = sorted(b for b in buckets if b not in (NONBRAND, COMPARE))
        bucket = brand_b[0] if brand_b else (COMPARE if COMPARE in buckets else NONBRAND)
        if len(buckets) > 1:
            alias_suspect.append(" = ".join(sorted(mem)))
        distinct = [k for k in mem if dm[k]["rep"] == k]          # 같은 검색의 중복은 한 번만
        y0 = {k: wsum(info[k]["s"], Y0) for k in distinct}
        rep = sorted(distinct, key=lambda k: (-(y0[k] if y0[k] is not None else
                                                sum(info[k]["s"].get(m, 0) for m in Y0)), k))[0]
        s = info[rep]["s"]
        g = {"id": f"G{gi + 1}", "bucket": bucket, "rep": rep, "members": mem, "distinct": distinct, "s": s,
             "y0": y0[rep], "y0_hi": (sum(v for v in y0.values() if v is not None) if y0[rep] is not None else None),
             "y1": wsum(s, Y1), "y2": wsum(s, Y2), "l3": wsum(s, L3), "p3": wsum(s, P3)}
        G.append(g)
        for k in mem:
            info[k]["group"] = g

    # ── 1단: 브랜드명 단독 · 커버리지
    alone, stage1_by_b, stage1_rows = {}, {}, []
    if a.stage1:
        s1 = load_rows(a.stage1)
        s1, m1, c1 = collapse_identical(s1)
        dm1 = dedup_map(s1, cfg["rules"])
        for r in s1:
            if dm1[r["keyword"]]["rep"] != r["keyword"]:
                continue
            b, k = classify(r["keyword"], cfg)
            if k == "alone":
                alone[b] = alone.get(b, 0) + vol_total(r)
            elif k in COUNTED:
                stage1_by_b[b] = stage1_by_b.get(b, 0) + vol_total(r)
        stage1_rows = s1
    s2_by_b = {}
    s1vol = {r["keyword"]: vol_total(r) for r in stage1_rows}
    for g in G:
        for k in g["distinct"]:
            s2_by_b[g["bucket"]] = s2_by_b.get(g["bucket"], 0) + s1vol.get(k, vol_total(info[k]["row"]))
    unreturned = []
    if a.requested:
        got = {v["canon"] for v in info.values()}
        unreturned = [q for q in json.load(open(a.requested, encoding="utf-8")) if canon(q, cfg["rules"]) not in got]

    # ── 별칭 누락 의심 — 사전 브랜드와 닮은 논브랜드 행(1단 볼륨 우선 · 없으면 2단 최근 12개월)
    vol_items = [(r["keyword"], vol_total(r)) for r in stage1_rows] +         [(kw, s1vol.get(kw) or wsum(v["s"], Y0) or vol_total(v["row"])) for kw, v in info.items()]
    nb_suspect = alias_gap_suspects(vol_items, cfg, a.suspect_min_volume)
    suspect_of = {canon(x["keyword"], cfg["rules"]): x["near_brand"] for x in nb_suspect}

    # ── 버킷 집계
    buckets = cfg["brands"] + [COMPARE, NONBRAND]
    B = {}
    gate12, gate3 = 12 * a.gate_monthly, 3 * a.gate_monthly
    for b in buckets:
        gs = [g for g in G if g["bucket"] == b]
        ok12 = [g for g in gs if g["y0"] is not None]
        yo = [g for g in gs if g["y0"] is not None and g["y1"] is not None]
        q3 = [g for g in gs if g["l3"] is not None and g["p3"] is not None]
        f3 = [g for g in gs if None not in (g["y0"], g["y1"], g["y2"])] if has_3y else []
        base12, comp12 = sum(g["y1"] for g in yo), sum(g["y0"] for g in yo)
        base3, comp3 = sum(g["p3"] for g in q3), sum(g["l3"] for g in q3)
        B[b] = {"gs": gs, "lo": sum(g["y0"] for g in ok12), "hi": sum(g["y0_hi"] for g in ok12),
                "incomplete": len(gs) - len(ok12), "yo_n": len(yo), "base12": base12 if yo else None,
                "comp12": comp12 if yo else None, "yoy": pct(comp12, base12) if yo and base12 >= gate12 else None,
                "yoy_gated": 1 if (not yo or base12 < gate12) else 0,
                "yoy3": pct(comp3, base3) if q3 and base3 >= gate3 else None,
                "f3": f3, "y2": sum(g["y2"] for g in f3) if f3 else None, "y1": sum(g["y1"] for g in f3) if f3 else None,
                "y0": sum(g["y0"] for g in f3) if f3 else None}
    brands_present = [b for b in cfg["brands"] if B[b]["lo"] > 0]
    tot_brand = sum(B[b]["lo"] for b in cfg["brands"])
    tot_genre = sum(B[b]["lo"] for b in buckets)
    tot_genre_hi = sum(B[b]["hi"] for b in buckets)
    order_lo = sorted(brands_present, key=lambda b: (-B[b]["lo"], b))
    order_hi = sorted(brands_present, key=lambda b: (-B[b]["hi"], b))
    rank_lo = {b: i + 1 for i, b in enumerate(order_lo)}
    rank_hi = {b: i + 1 for i, b in enumerate(order_hi)}
    yoy_rank = {b: i + 1 for i, b in enumerate(sorted([b for b in brands_present if B[b]["yoy"] is not None],
                                                       key=lambda b: (-B[b]["yoy"], b)))}

    def r4(x):
        return round(x, 4) if x is not None else ""

    def blank(x):
        return "" if x is None else x
    rank_rows = []
    for b in order_lo + [x for x in cfg["brands"] if x not in brands_present] + [COMPARE, NONBRAND]:
        d = B[b]
        is_brand = b in cfg["brands"]
        rank_rows.append({
            "rank_lo": rank_lo.get(b, ""), "rank_hi": rank_hi.get(b, ""),
            "rank_flip": ("yes" if rank_lo.get(b) != rank_hi.get(b) else "no") if b in rank_lo else "",
            "in_top": 1 if rank_lo.get(b, 10 ** 9) <= a.top else 0, "brand": b,
            "bucket_type": "brand" if is_brand else ("compare" if b == COMPARE else "nonbrand"),
            "groups_n": len(d["gs"]), "volume_12m_lo": d["lo"], "volume_12m_hi": d["hi"],
            "incomplete_groups_12m": d["incomplete"],
            "share_brandset_lo": r4(d["lo"] / tot_brand) if is_brand and tot_brand else "",
            "share_genre_lo": r4(d["lo"] / tot_genre) if tot_genre else "",
            "share_genre_hi": r4(d["hi"] / tot_genre_hi) if tot_genre_hi else "",
            "yoy_base_12m": blank(d["base12"]), "yoy_compare_12m": blank(d["comp12"]),
            "yoy_abs_12m": "" if d["base12"] is None else d["comp12"] - d["base12"],
            "yoy_12m": blank(d["yoy"]), "yoy_groups_n": d["yo_n"], "yoy_3m": blank(d["yoy3"]),
            "yoy_gated_out": d["yoy_gated"], "rank_yoy": yoy_rank.get(b, ""),
            "brand_alone_12m": alone.get(b, "") if is_brand and a.stage1 else "",
            "stage2_coverage": r4(s2_by_b.get(b, 0) / stage1_by_b[b]) if stage1_by_b.get(b) else "",
            "data_ref_month": ref, "analysis_end_month": end,
            "last_month_included": "yes(잠정)" if a.include_last_month else "no",
            "label": "실제 데이터(검색량·변화율) · 추정(하한·상한·귀속)"})
    write(out / f"brand_rank_{cat}.csv", RANK_COLS, rank_rows)

    # ── 3년 흐름(T6 고정 모집단)
    trend_rows = []
    for b in buckets:
        d = B[b]
        if not d["gs"]:
            continue
        if not has_3y:
            status = f"계산 불가(응답 시계열이 {earliest}부터 — 36개월 창 부족)"
        elif not d["f3"]:
            status = "계산 불가(모든 묶음이 3년 창 중 결측 — 신규 브랜드·결측 월)"
        else:
            status = "고정 모집단" + (f" · {len(d['gs']) - len(d['f3'])}묶음 제외" if len(d["f3"]) < len(d["gs"]) else "")
        gated = d["y2"] is None or d["y2"] < gate12
        trend_rows.append({
            "brand": b, "bucket_type": "brand" if b in cfg["brands"] else ("compare" if b == COMPARE else "nonbrand"),
            "groups_fixed_3y": len(d["f3"]), "groups_excluded_3y": len(d["gs"]) - len(d["f3"]),
            "window_y2": wl(Y2), "window_y1": wl(Y1), "window_y0": wl(Y0),
            "volume_y2": blank(d["y2"]), "volume_y1": blank(d["y1"]), "volume_y0": blank(d["y0"]),
            "abs_chg_3y": "" if d["y2"] is None else d["y0"] - d["y2"],
            "chg_3y": "" if gated else pct(d["y0"], d["y2"]), "chg_3y_gated_out": 1 if gated else 0,
            "trend_status": status, "label": "실제 데이터(연도 창 합) · 가정(고정 모집단 · 게이트)"})
    write(out / f"brand_trend_{cat}.csv", TREND_COLS, trend_rows)

    # ── 성별 · 연령대 순위 (kr 만 demography 를 돌려준다 — 그 밖은 사양)
    seg_rows, demo = [], {}
    for b in cfg["brands"]:
        w_all = w_demo = 0.0; f = 0.0; ages = {}
        for g in B[b]["gs"]:
            if g["y0"] is None:
                continue
            w = g["y0"]; w_all += w
            d = info[g["rep"]]["row"].get("demography") or {}
            fr, mr = d.get("f_gender_ratio"), d.get("m_gender_ratio")
            ak = {k[:-6]: v for k, v in d.items() if k.startswith("a") and k.endswith("_ratio") and v is not None}
            if fr is None or not ak or not sum(ak.values()):
                continue
            w_demo += w
            f += w * (fr / ((fr + mr) if mr is not None and fr + mr else 100))
            s_a = sum(ak.values())
            for k, v in ak.items():
                ages[k] = ages.get(k, 0) + w * v / s_a
        if w_demo:
            demo[b] = {"cov": w_demo / w_all, "ratio": {"gender:F": f / w_demo, "gender:M": 1 - f / w_demo,
                                                        **{f"age:{k}": v / w_demo for k, v in ages.items()}}}
    segs = sorted({s for d in demo.values() for s in d["ratio"]}, key=lambda s: (s[:3] != "gen", s))
    for seg in segs:
        vals = {b: B[b]["lo"] * demo[b]["ratio"].get(seg, 0) for b in demo}
        tot_seg = sum(vals.values()); tot_lo = sum(B[b]["lo"] for b in demo)
        ok = [b for b in demo if vals[b] >= a.seg_gate and demo[b]["cov"] >= a.min_demo_coverage]
        rk = {b: i + 1 for i, b in enumerate(sorted(ok, key=lambda b: (-vals[b], b)))}
        typ, key = seg.split(":")
        for b in sorted(demo, key=lambda b: (-vals[b], b)):
            share = vals[b] / tot_seg if tot_seg else None
            base = B[b]["lo"] / tot_lo if tot_lo else None
            why = [] if b in ok else ([f"세그먼트 12개월 {a.seg_gate:,} 미만"] if vals[b] < a.seg_gate else []) + \
                ([f"성·연령 커버리지 {a.min_demo_coverage:.0%} 미만"] if demo[b]["cov"] < a.min_demo_coverage else [])
            seg_rows.append({
                "segment_type": "gender" if typ == "gender" else "age", "segment": key,
                "segment_label": {"F": "여성", "M": "남성"}.get(key) or AGE_LABEL.get(key, key), "brand": b,
                "demo_coverage": r4(demo[b]["cov"]), "segment_ratio": r4(demo[b]["ratio"].get(seg, 0)),
                "segment_volume_12m": round(vals[b]), "share_in_segment": r4(share),
                "index_vs_all": round(share / base * 100, 1) if share is not None and base else "",
                "rank_in_segment": rk.get(b, ""), "seg_gated_out": 0 if b in ok else 1, "gate_reason": " · ".join(why),
                "label": "추정(검색량 × 성·연령 비율 — 비율은 대표 검색어의 볼륨 가중)"})
    write(out / f"segment_rank_{cat}.csv", SEG_COLS, seg_rows)
    if (cfg["gl"] or "").lower() == "kr":
        seg_status = "반환" if demo else "미반환(kr 인데 demography 없음 — 데이터 공백)"
    else:
        seg_status = "해당 없음(gl 사양 — demography 는 kr 만 반환)" if not demo else "반환"

    # ── 키워드 대조표
    map_rows = []
    seen = set()
    for kw, v in info.items():
        seen.add(v["canon"])
        g = v.get("group")
        map_rows.append({
            "keyword": kw, "canonical": v["canon"], "bucket": v["bucket"], "kind": v["kind"], "stage": "2단",
            "group_id": g["id"] if g else "", "group_rep": g["rep"] if g else "",
            "dup_of": dm[kw]["rep"] if kw in dm and dm[kw]["rep"] != kw else "",
            "dup_basis": dm[kw]["basis"] if kw in dm and dm[kw]["rep"] != kw else "",
            "volume_total_stage1": s1vol.get(kw, ""), "volume_y0": blank(wsum(v["s"], Y0)),
            "complete_y0": 1 if wsum(v["s"], Y0) is not None else 0,
            "complete_3y": 1 if has_3y and all(wsum(v["s"], w) is not None for w in (Y0, Y1, Y2)) else 0,
            "has_demography": 1 if v["row"].get("demography") else 0,
            "excluded_reason": REASON.get(v["kind"], ""),
            "alias_suspect_brand": suspect_of.get(v["canon"], ""),
            "api_volume_trend_do_not_use": (v["row"].get("ads_metrics") or {}).get("volume_trend", "")})
    for r in stage1_rows:
        c = canon(r["keyword"], cfg["rules"])
        if c in seen:
            continue
        seen.add(c)
        b, k = classify(r["keyword"], cfg)
        map_rows.append({"keyword": r["keyword"], "canonical": c, "bucket": b, "kind": k, "stage": "1단",
                         "group_id": "", "group_rep": "", "dup_of": "", "dup_basis": "",
                         "volume_total_stage1": vol_total(r), "volume_y0": "", "complete_y0": "", "complete_3y": "",
                         "has_demography": "",
                         "excluded_reason": REASON.get(k, "2단 게이트 밖(1단 12개월만)" if k in COUNTED else ""),
                         "alias_suspect_brand": suspect_of.get(c, ""),
                         "api_volume_trend_do_not_use": (r.get("ads_metrics") or {}).get("volume_trend", "")})
    write(out / f"keyword_map_{cat}.csv", MAP_COLS, map_rows)

    # ── 요약(리포트 사실 목록의 원천)
    summ = {
        "category": cat, "genre": cfg["genre"], "gl": cfg["gl"], "data_ref_month": ref, "queried_at": a.queried_at,
        "lag_months": lag, "analysis_end_month": end, "last_month_included": a.include_last_month,
        "last_month_suspect": suspect, "last_month_checked": checked, "series_from": earliest,
        "windows": {"y0": wl(Y0), "y1": wl(Y1), "y2": wl(Y2), "l3": wl(L3), "p3": wl(P3)}, "has_3y": has_3y,
        "gates": {"gate_monthly": a.gate_monthly, "gate_12m": gate12, "gate_3m": gate3, "seg_gate": a.seg_gate,
                  "suspect_min_volume": a.suspect_min_volume,
                  "min_demo_coverage": a.min_demo_coverage, "top": a.top},
        "totals": {"brands_lo": tot_brand, "genre_lo": tot_genre, "genre_hi": tot_genre_hi,
                   "nonbrand_lo": B[NONBRAND]["lo"], "compare_lo": B[COMPARE]["lo"]},
        "rows_in": n_raw, "rows_merged": merged, "groups": len(G),
        "dup_rows": sum(1 for k in counted if dm[k]["rep"] != k),
        "rank_flips": [b for b in order_lo if rank_lo[b] != rank_hi[b]],
        "yoy_gated_out": [b for b in brands_present if B[b]["yoy_gated"]],
        "chg3y_unavailable": [t["brand"] for t in trend_rows if t["bucket_type"] == "brand" and t["volume_y2"] == ""],
        "groups_excluded_3y": sum(t["groups_excluded_3y"] for t in trend_rows),
        "incomplete_groups_12m": sum(B[b]["incomplete"] for b in buckets),
        "brands_zero": [b for b in cfg["brands"] if b not in brands_present],
        "alias_suspect": alias_suspect, "alias_suspect_nonbrand": nb_suspect,
        "alias_suspect_nonbrand_volume": sum(x["volume_12m"] for x in nb_suspect),
        "alias_suspect_n": len(alias_suspect) + len(nb_suspect),
        "unreturned": unreturned, "segment_status": seg_status,
        "files": {k: f"{k}_{cat}.csv" for k in ("brand_rank", "brand_trend", "segment_rank", "keyword_map")},
    }
    json.dump(summ, open(out / f"rank_summary_{cat}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_md(out / f"rank_{cat}.md", summ, rank_rows, trend_rows, seg_rows, a)

    print(f"데이터 기준월 {ref} / 조회일 {a.queried_at} / 지연 {lag}개월 · 분석 월 {end}"
          + (" (마지막 달 포함 — 잠정)" if a.include_last_month else " (마지막 달 제외)"))
    print(f"마지막 달 이상 의심 {suspect}/{checked}행 · 묶음 {len(G)} · 같은 검색의 중복 {summ['dup_rows']}행 [추정]")
    print(f"순위(하한) 상위: " + " > ".join(order_lo[: a.top]))
    if summ["rank_flips"]:
        print(f"⚠️ 순위 뒤집힘(하한 ≠ 상한): {', '.join(summ['rank_flips'])}")
    if summ["yoy_gated_out"]:
        print(f"게이트 미만(전년 12개월 {gate12:,} 미만) — 변화율 비움: {', '.join(summ['yoy_gated_out'])}")
    if summ["chg3y_unavailable"]:
        print(f"3년 흐름 계산 불가: {', '.join(summ['chg3y_unavailable'])}")
    if alias_suspect:
        print(f"⚠️ 별칭 누락 의심(같은 검색이 다른 버킷에): {alias_suspect[:3]}")
    if nb_suspect:
        print(f"⚠️ 별칭 누락 의심(사전 브랜드와 닮은 논브랜드 행) {len(nb_suspect)}행 · 12개월 "
              f"{summ['alias_suspect_nonbrand_volume']:,}건 — 논브랜드 몫 판정 보류, 별칭 · variants 보강 후 다시 돌릴 것: "
              + ", ".join(f"{x['keyword']}→{x['near_brand']}" for x in nb_suspect[:5]))
    print(f"성별·연령대: {seg_status} → {out}/")


def write(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def write_md(path, s, rank_rows, trend_rows, seg_rows, a):
    L = [f"# {s['genre'] or s['category']} 브랜드 검색 순위 ({s['gl']})", "",
         f"데이터 기준월 {s['data_ref_month']} / 조회일 {s['queried_at']} / 지연 {s['lag_months']}개월 · "
         f"분석 월 {s['analysis_end_month']}" + (" (마지막 달 포함 · 잠정)" if s["last_month_included"] else " (마지막 달 제외)"),
         f"마지막 달 이상 의심 {s['last_month_suspect']}/{s['last_month_checked']}행 [실제 데이터 점검] · "
         f"최근 12개월 {s['windows']['y0']} · 전년 {s['windows']['y1']} · 2년 전 {s['windows']['y2']}", "",
         "## 순위 (12개월 검색량 하한 기준) [추정 — 표기변형 하한·상한]", "",
         "| 순위 하한/상한 | 브랜드 | 12개월 하한 ~ 상한 | 브랜드 합 대비 | 분야 전체 대비 | 전년 12개월 → 최근 12개월 | 전년 동기 |",
         "|---|---|---|---|---|---|---|"]
    for r in rank_rows:
        if r["bucket_type"] != "brand" or not r["in_top"]:
            continue
        yo = f"{r['yoy_12m']:+.1f}%" if r["yoy_12m"] != "" else ("게이트 미만 [데이터 공백]" if r["yoy_gated_out"] else "계산 불가")
        L.append(f"| {r['rank_lo']}/{r['rank_hi']}{' ⚠️' if r['rank_flip'] == 'yes' else ''} | {r['brand']} | "
                 f"{r['volume_12m_lo']:,} ~ {r['volume_12m_hi']:,} | {float(r['share_brandset_lo'] or 0):.1%} | "
                 f"{float(r['share_genre_lo'] or 0):.1%} | {r['yoy_base_12m'] or '—'} → {r['yoy_compare_12m'] or '—'} | {yo} |")
    nb = next(r for r in rank_rows if r["bucket_type"] == "nonbrand")
    sus = s["alias_suspect_nonbrand"]
    L += ["", f"논브랜드 분야 검색 {nb['volume_12m_lo']:,}건 · 분야 전체 대비 {float(nb['share_genre_lo'] or 0):.1%} "
              "[실제 데이터 합 · 분모 정의는 가정] — 점유는 검색량 기준 근사이며 판매 점유가 아니다"]
    if sus:
        L += ["", f"⚠️ **별칭 누락 의심 {len(sus)}행 · 12개월 {s['alias_suspect_nonbrand_volume']:,}건** — 논브랜드로 잡혔지만 "
                  "앞부분이 사전 브랜드와 닮았다. 논브랜드 몫은 판정 보류 · 사전(별칭 · variants)을 보강하고 다시 돌린다 [데이터 공백]", "",
              "| 검색어 | 12개월 | 닮은 브랜드 |", "|---|---|---|"]
        L += [f"| {x['keyword']} | {x['volume_12m']:,} | {x['near_brand']} |" for x in sus[:15]]
    L += ["",
          "## 3년 흐름 (고정 모집단 — 세 창 모두 값이 있는 묶음만) [실제 데이터 · 가정]", "",
          "| 브랜드 | 2년 전 | 전년 | 최근 | 3년 변화 | 상태 |", "|---|---|---|---|---|---|"]
    for t in trend_rows:
        L.append(f"| {t['brand']} | {t['volume_y2'] or '—'} | {t['volume_y1'] or '—'} | {t['volume_y0'] or '—'} | "
                 f"{(str(t['chg_3y']) + '%') if t['chg_3y'] != '' else ('게이트 미만' if t['volume_y2'] != '' else '—')} | {t['trend_status']} |")
    L += ["", f"## 성별·연령대 순위 — {s['segment_status']} [추정]", ""]
    for seg in dict.fromkeys((r["segment_type"], r["segment"], r["segment_label"]) for r in seg_rows):
        top = [r for r in seg_rows if (r["segment_type"], r["segment"]) == seg[:2] and r["rank_in_segment"] != ""]
        top.sort(key=lambda r: r["rank_in_segment"])
        L.append(f"- {seg[2]}: " + (" > ".join(r["brand"] for r in top[:5]) or "게이트 통과 브랜드 없음 [데이터 공백]"))
    L += ["", "## 데이터 공백 · 판정 보류", "",
          f"- 전년 동기 게이트 미만(변화율 비움): {', '.join(s['yoy_gated_out']) or '없음'} [데이터 공백]",
          f"- 3년 흐름 계산 불가: {', '.join(s['chg3y_unavailable']) or '없음'} · 3년 창에서 뺀 묶음 {s['groups_excluded_3y']}개 [데이터 공백]",
          f"- 12개월 창에 결측이 있어 순위 합계에서 뺀 묶음 {s['incomplete_groups_12m']}개 [데이터 공백]",
          f"- 순위 뒤집힘(하한 ≠ 상한): {', '.join(s['rank_flips']) or '없음'} [추정]",
          f"- 2단 검색량 0 브랜드: {', '.join(s['brands_zero']) or '없음'} [데이터 공백]",
          f"- 미반환 키워드 {len(s['unreturned'])}개 · 별칭 누락 의심 {s['alias_suspect_n']}건"
          f"(묶음 버킷 불일치 {len(s['alias_suspect'])} · 브랜드와 닮은 논브랜드 행 {len(s['alias_suspect_nonbrand'])}) [데이터 공백]", "",
          "## 해석 한계", "",
          "- 월 그레인 · 기준월은 조회일보다 늦다. 검색은 관심 신호이며 판매·사용이 아니다. 단위는 건(사람 수 아님)",
          "- 브랜드명 단독 검색은 다른 품목 수요가 섞여 순위·점유에서 뺐다(brand_alone_12m 참고)",
          "- 원인(광고·출시·이슈)은 이 산출물이 답하지 않는다"]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
