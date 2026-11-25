#!/usr/bin/env python3
"""CEP 지도 — 상황 사전으로 묶은 검색어에 크기 · 추세 · 계절성 · 브랜드 연결을 붙여 우선순위를 낸다
(이 스킬 고유 · API 호출 없음 · 저장한 응답만 읽는다).

| 처리 | 방법 | 함정 |
|---|---|---|
| 분류 | themes.json 정규식 — 카테고리어·브랜드명을 가린 뒤 어절 시작에서만 일치(cep_lib 머리말) · 배열 순서가 주 CEP · 안 걸리면 «미분류» · 브랜드만 걸리면 «브랜드·제품명» · 주 일치가 더 긴 어절의 앞부분이면 match_scope=어절 일부(정밀도 신호 partial_token_share) | — |
| 표기변형 | canonical 그룹 하한(최댓값)·상한(합) 병기 · 순위는 하한, 상한에서 뒤집히면 flip_under_hi | T5 |
| 같은 검색 중복 | dup_of 행은 남기되 집계에서 뺀다 | T5 ① |
| 카테고리 결속 | 앵커어·브랜드가 든 검색어만 CEP 검색량에 넣고, 시드 앞 검색 중 카테고리어 없는 것은 situation_only로 따로 | — |
| 브랜드 분리 | 우선순위는 논브랜드 검색량(하한) 순 · 브랜드 검색량은 따로 · 브랜드 연결은 cep_brand.csv | T8 |
| 순위 창 | 순위 · 게이트 볼륨은 추세와 같은 창(기본: 마지막 달 제외 12개월)의 월 합 — 시계열 없는 그룹만 volume_total(마지막 달 포함)로 대신하고 window_series_share 로 비중을 적는다 · 마지막 달 포함 12개월 순위는 priority_rank_incl_last 옆 열, 다르면 rank_shift_incl_last | T2 |
| 추세 | monthly_volume 합으로 전년 동기(12개월 · 3개월) 직접 계산. volume_trend는 읽지 않는다 | T3 |
| 마지막 달 | 기본 제외 — 창이 기준월 한 달 전에서 끝난다(--include-last-month로 포함) · 미완결 의심 건수 표기 | T2 |
| 결측 월 | 비교 창에 결측이 있는 검색어는 CEP 시계열 모집단에서 빼고 수를 적는다(0으로 채우지 않음) | T6 |
| 기저 게이트 | CEP 12개월 하한 < --gate 이면 순위 밖(gated_out) · 전년 창 합 < 추세 게이트면 추세 «게이트 미달» | T4 |
| 기준월 | 응답 monthly_volume 최댓값 = 기준월 · 조회일 · 지연을 헤더에 | T1 |
| 절단 | --calls(pool_calls.csv)의 절단 호출을 헤더에 · --coverage 판정 문구를 그대로 싣는다 | T7 |

가중 합성 점수를 만들지 않는다 — 가중치를 정당화할 근거가 없다. 순위는 논브랜드 검색량 하나로 매기고,
추세 · 계절성 · 브랜드 연결은 옆 열로 붙인다.

    python3 cep_map.py --pool pool.csv --metrics metrics_ads.json --monthly metrics_all.json \
        --sense sense.json --themes themes.json --paths raw/path_main.json@60 --calls pool_calls.csv \
        --queried-at 2026-09-28 --coverage "보류(절단 1건)" --out-dir out
"""
import argparse, csv, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cep_lib import (Lexicon, axis_of, build_universe, data_rows, load_json, load_sense, parse_spec,  # noqa: E402
                     paths_of, pre_seed, read_pool, series_of, shift, utf8_console, window)

KW_COLS = ["keyword", "canonical", "source", "path_pre_paths", "anchor_hit", "boundary", "cep", "cep_axis", "kind",
           "facets", "multi_cep", "brand", "brand_scope", "volume_total", "group_volume_lo", "group_volume_hi",
           "canonical_rep", "dup_of", "returned", "stage2", "yoy_12m", "series_status", "match_term", "match_scope",
           "window_volume"]
MAP_COLS = ["cep", "cep_axis", "kind", "keywords", "canonical_groups", "volume_12m_lo", "volume_12m_hi",
            "nonbrand_volume_lo", "nonbrand_volume_hi", "brand_volume_lo", "share_lo", "share_hi",
            "situation_only_volume_lo", "path_pre_paths", "path_pre_rate", "rank_window", "window_volume_lo",
            "window_nonbrand_lo", "window_nonbrand_hi", "window_series_share", "gated_out", "priority_rank",
            "priority_rank_hi", "flip_under_hi", "priority_rank_incl_last", "rank_shift_incl_last",
            "partial_token_share", "series_groups", "series_excluded_missing", "series_coverage",
            "yoy_window", "yoy_12m", "yoy_3m", "yoy_basis_prev_12m", "trend_status", "trend_call", "trend_conflict",
            "season_status", "season_strength", "peak_months", "focal_brand", "focal_share", "top_competitor",
            "top_competitor_share", "brand_link_call", "top_keywords", "data_ref_month"]
BRAND_COLS = ["cep", "brand", "role", "cooccur_groups", "cooccur_volume_lo", "share_of_cep_lo",
              "path_cooccur_paths", "cep_paths"]
OTHER = "기타 브랜드"


def rnd(x, n=4):
    return None if x is None else round(x, n)


def blank(x):
    return "" if x is None else x


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="CEP 지도 (0크레딧)")
    ap.add_argument("--pool", required=True); ap.add_argument("--metrics", required=True)
    ap.add_argument("--monthly", help="2단 keyword_info(all) 응답 — 없으면 추세·계절성은 [데이터 공백]")
    ap.add_argument("--sense", required=True); ap.add_argument("--themes", required=True)
    ap.add_argument("--paths", action="append", default=[], help="path_finder 응답 파일[@limit]")
    ap.add_argument("--calls", help="situation_pool.py 의 pool_calls.csv (절단 기록)")
    ap.add_argument("--stage2", help="select_stage2.py 출력 — 2단 대상 표시")
    ap.add_argument("--queried-at", required=True)
    ap.add_argument("--coverage", default="판정 안 함", help="coverage_curve.py 판정 문구")
    ap.add_argument("--gate", type=int, default=12000, help="CEP 12개월 하한 순위 게이트 [가정]")
    ap.add_argument("--trend-gate", type=int, default=12000, help="CEP 전년 12개월 창 합 게이트 [가정]")
    ap.add_argument("--gate-3m", type=int, default=3000, help="CEP 전년 3개월 창 합 게이트 [가정]")
    ap.add_argument("--kw-gate", type=int, default=3000, help="검색어 단위 전년 12개월 게이트 [가정]")
    ap.add_argument("--trend-pct", type=float, default=0.20, help="상승/하락 판정 임계 [가정]")
    ap.add_argument("--brand-min", type=float, default=0.05, help="브랜드 연결 «약함» 임계 [가정]")
    ap.add_argument("--peak", type=float, default=1.30, help="성수기 지수 임계 [가정]")
    ap.add_argument("--pre-steps", type=int, default=3)
    ap.add_argument("--include-last-month", action="store_true")
    ap.add_argument("--allow-unconfirmed", action="store_true")
    ap.add_argument("--out-dir", default=".")
    a = ap.parse_args()

    sense = load_sense(a.sense, a.allow_unconfirmed)
    lex = Lexicon(load_json(a.themes), sense)
    monthly = data_rows(load_json(a.monthly)) if a.monthly else []
    rows, groups, dd = build_universe(read_pool(a.pool), data_rows(load_json(a.metrics)), lex, monthly)
    stage2 = set(load_json(a.stage2).get("keywords", [])) if a.stage2 else set()
    focal = sense.get("focal_brand") or ""
    comps = [c for c in sense.get("competitors") or [] if c]

    # ── 기준월 · 창 (T1 · T2)
    months = [m.get("month") for r in monthly for m in (r.get("monthly_volume") or []) if isinstance(m, dict)]
    ref = max(months) if months else None
    qy, qm = int(a.queried_at[:4]), int(a.queried_at[5:7])
    lag = ((qy - int(ref[:4])) * 12 + qm - int(ref[5:7])) if ref else None
    end = (ref if a.include_last_month else shift(ref, 1)) if ref else None
    cur12 = window(end, 12) if end else []
    prev12 = [shift(m, 12) for m in cur12]
    cur3, prev3 = cur12[-3:], prev12[-3:]
    rank_window = f"{cur12[0]}~{cur12[-1]}" if cur12 else "volume_total(12개월 · 시계열 없음)"
    suspect = 0
    if ref:
        prv, ly, lprv = shift(ref, 1), shift(ref, 12), shift(ref, 13)
        for r in monthly:
            s = series_of(r)
            if all(s.get(m) for m in (ref, prv, ly, lprv)):
                mom = s[ref] / s[prv] - 1
                if mom < 0 and mom - (s[ly] / s[lprv] - 1) < -0.30:
                    suspect += 1

    # ── 그룹 대표의 시계열 상태
    def gstatus(rep):
        if not rep.get("monthly"):
            return "시계열 없음", None, None
        s = series_of(rep["monthly"])
        if any(s.get(m) is None for m in cur12 + prev12):
            return "결측 월", s, None
        p = sum(s[m] for m in prev12)
        c = sum(s[m] for m in cur12)
        if p < a.kw_gate:
            return "게이트 미달", s, None
        return "계산됨", s, c / p - 1

    rep_of = {c: next(r for r in g if r["keyword"] == r["canonical_rep"]) for c, g in groups.items()}
    gst = {c: gstatus(r) for c, r in rep_of.items()}

    # ── 순위 창(T2) — 추세와 같은 창(기본: 마지막 달 제외 12개월). 대표 시계열이 창 전부에 값이 있으면 그 월 합,
    #    없으면 volume_total(기준월 포함 12개월)로 대신한다 — 어느 쪽인지 window_series_share 로 남긴다
    def wvol(rep):
        if rep.get("monthly") and cur12:
            sr = series_of(rep["monthly"])
            if all(sr.get(m) is not None for m in cur12):
                return sum(sr[m] for m in cur12), True
        return rep["volume_total"], False

    wv = {c: wvol(r) for c, r in rep_of.items()}
    wlo = lambda c: wv[c][0]
    whi = lambda c: rep_of[c]["group_volume_hi"] - rep_of[c]["group_volume_lo"] + wv[c][0]

    # ── 경로: 시드 앞 검색(진입 계기 신호)과 브랜드 동반
    paths, found = [], 0
    for spec in a.paths:
        f, _ = parse_spec(spec)
        paths += paths_of(load_json(f))
    pre_hits, path_bucket_sets = {}, []
    for p in paths:
        cs = [lex.canon(k) for k in p]
        path_bucket_sets.append(({lex.classify(k)[0] for k, c in zip(p, cs) if c != lex.seed}, set(cs)))
        pre = pre_seed(p, lex, a.pre_steps)
        if pre is None:
            continue
        found += 1
        for b in {lex.classify(k)[0] for k in pre}:
            pre_hits[b] = pre_hits.get(b, 0) + 1

    # ── 버킷(CEP · 미분류 · 비상황 · 브랜드·제품명) 집계
    buckets = {}
    for r in rows:
        if not r["boundary"]:
            buckets.setdefault((r["cep"], r["kind"]), []).append(r)
    total_lo = sum(rep_of[c]["group_volume_lo"] for c in groups if rep_of[c]["anchor_hit"] == "Y")
    total_hi = sum(rep_of[c]["group_volume_hi"] for c in groups if rep_of[c]["anchor_hit"] == "Y")
    cep_rows, brand_rows, cep_json = [], [], []
    for (name, kind), rs in buckets.items():
        cs = sorted({r["canonical"] for r in rs if r["canonical"] in groups})
        anc = [c for c in cs if rep_of[c]["anchor_hit"] == "Y"]
        sit = [c for c in cs if rep_of[c]["anchor_hit"] != "Y"]
        lo = sum(rep_of[c]["group_volume_lo"] for c in anc)
        hi = sum(rep_of[c]["group_volume_hi"] for c in anc)
        nb = [c for c in anc if not rep_of[c]["brand"]]
        nlo = sum(rep_of[c]["group_volume_lo"] for c in nb)
        nhi = sum(rep_of[c]["group_volume_hi"] for c in nb)
        w_all = sum(wlo(c) for c in anc)
        w_nlo, w_nhi = sum(wlo(c) for c in nb), sum(whi(c) for c in nb)
        w_ser = (sum(wlo(c) for c in anc if wv[c][1]) / w_all) if w_all else None
        part = (sum(rep_of[c]["group_volume_lo"] for c in anc if rep_of[c].get("match_scope") == "어절 일부") / lo) if lo else None
        inc = [c for c in anc if gst[c][0] in ("계산됨", "게이트 미달")]
        miss = sum(1 for c in anc if gst[c][0] == "결측 월")
        span = window(end, 36) if end else []
        ser = []
        for m in span:
            vals = [gst[c][1].get(m) for c in inc]
            ser.append(sum(vals) if inc and all(v is not None for v in vals) else None)
        sm = dict(zip(span, ser))
        y12 = y3 = p12 = None
        tstat = "시계열 없음"
        if inc:
            p12 = sum(sm[m] for m in prev12)
            c12 = sum(sm[m] for m in cur12)
            p3, c3 = sum(sm[m] for m in prev3), sum(sm[m] for m in cur3)
            if p12 >= a.trend_gate:
                y12, tstat = c12 / p12 - 1, "계산됨"
            else:
                tstat = f"게이트 미달(전년 12개월 {p12:,})"
            if p3 >= a.gate_3m:
                y3 = c3 / p3 - 1
        cov = (sum(rep_of[c]["group_volume_lo"] for c in inc) / lo) if (lo and inc) else None
        if y12 is None:
            call = "판정 보류"
        else:
            call = "상승" if y12 >= a.trend_pct else ("하락" if y12 <= -a.trend_pct else "보합")
            if cov is not None and cov < 0.5:
                call += "(잠정)"
        conflict = "Y" if (y12 is not None and y3 is not None and (y12 > 0) != (y3 > 0)) else ""
        # 계절성 — 달력 월 지수 = 그 달 평균 ÷ 전체 월평균 (36개월 중 값이 있는 달, 24개월 이상 · 달마다 2회 이상)
        obs = [(m, v) for m, v in sm.items() if v is not None]
        idx, sstat, strength, peaks = {}, "시계열 없음" if not inc else "계산 불가(24개월 미만)", None, []
        if len(obs) >= 24:
            by = {}
            for m, v in obs:
                by.setdefault(int(m[5:7]), []).append(v)
            mean = sum(v for _, v in obs) / len(obs)
            if mean > 0 and all(len(by.get(k, [])) >= 2 for k in range(1, 13)):
                idx = {k: round(sum(by[k]) / len(by[k]) / mean, 2) for k in range(1, 13)}
                mn = min(idx.values())
                strength = round(max(idx.values()) / mn, 2) if mn > 0 else None
                peaks = [k for k, v in idx.items() if v >= a.peak]
                sstat = "계산됨"
        # 브랜드 연결 (T8)
        co, cog = {}, {}
        for c in anc:
            bs = [b for b in rep_of[c]["brand"].split("|") if b]
            keys = {b if b in ([focal] + comps) else OTHER for b in bs}
            for k in keys:
                co[k] = co.get(k, 0) + rep_of[c]["group_volume_lo"]
                cog[k] = cog.get(k, 0) + 1
        cep_paths = [ps for bs, ps in path_bucket_sets if name in bs]
        for b in (([focal] if focal else []) + comps + ([OTHER] if OTHER in co else [])) if kind == "상황" else []:
            bc = None if b == OTHER else lex.canon(b)
            if bc:
                pc = sum(1 for ps in cep_paths if any(bc in x for x in ps))
            else:
                others = [lex.canon(x) for x in lex.brands if x not in [focal] + comps]
                pc = sum(1 for ps in cep_paths if any(o in x for o in others for x in ps))
            brand_rows.append({"cep": name, "brand": b,
                               "role": "기준" if b == focal else ("경쟁" if b in comps else "기타"),
                               "cooccur_groups": cog.get(b, 0), "cooccur_volume_lo": co.get(b, 0),
                               "share_of_cep_lo": rnd(co.get(b, 0) / lo) if lo else "",
                               "path_cooccur_paths": pc, "cep_paths": len(cep_paths)})
        fs = (co.get(focal, 0) / lo) if (focal and lo) else None
        top_c = max(comps, key=lambda b: co.get(b, 0)) if comps else ""
        tcs = (co.get(top_c, 0) / lo) if (top_c and lo) else None
        bshare = ((lo - nlo) / lo) if lo else None
        if kind != "상황":
            link = ""
        elif w_all < a.gate:
            link = "판정 보류(게이트 미달)"
        elif bshare is not None and bshare < a.brand_min:
            link = "미점유(브랜드 연결 약함)"
        elif not focal:
            link = f"경쟁 구도만(선두 {top_c})" if top_c else "브랜드 지정 없음"
        elif (fs or 0) > 0 and (fs or 0) >= (tcs or 0):
            link = "기준 브랜드 우위"
        elif (tcs or 0) > (fs or 0):
            link = "경쟁 브랜드 우위"
        else:
            link = "기타 브랜드 연결"
        top = sorted((rep_of[c] for c in anc), key=lambda r: -r["group_volume_lo"])[:5]
        cep_rows.append({
            "cep": name, "cep_axis": axis_of(name) if kind == "상황" else "", "kind": kind,
            "keywords": len(rs), "canonical_groups": len(cs), "volume_12m_lo": lo, "volume_12m_hi": hi,
            "nonbrand_volume_lo": nlo, "nonbrand_volume_hi": nhi, "brand_volume_lo": lo - nlo,
            "share_lo": rnd(lo / total_lo) if total_lo else "", "share_hi": rnd(hi / total_hi) if total_hi else "",
            "situation_only_volume_lo": sum(rep_of[c]["group_volume_lo"] for c in sit),
            "path_pre_paths": pre_hits.get(name, 0), "path_pre_rate": rnd(pre_hits.get(name, 0) / found) if found else "",
            "rank_window": rank_window, "window_volume_lo": w_all, "window_nonbrand_lo": w_nlo,
            "window_nonbrand_hi": w_nhi, "window_series_share": blank(rnd(w_ser, 3)),
            "gated_out": "Y" if (kind == "상황" and w_all < a.gate) else ("" if kind == "상황" else "-"),
            "priority_rank": "", "priority_rank_hi": "", "flip_under_hi": "",
            "priority_rank_incl_last": "", "rank_shift_incl_last": "", "partial_token_share": blank(rnd(part, 3)),
            "series_groups": len(inc), "series_excluded_missing": miss, "series_coverage": blank(rnd(cov, 3)),
            "yoy_window": f"{cur12[0]}~{cur12[-1]} vs {prev12[0]}~{prev12[-1]}" if cur12 else "",
            "yoy_12m": blank(rnd(y12)), "yoy_3m": blank(rnd(y3)), "yoy_basis_prev_12m": blank(p12),
            "trend_status": tstat, "trend_call": call, "trend_conflict": conflict,
            "season_status": sstat, "season_strength": blank(strength),
            "peak_months": "|".join(str(k) for k in peaks), "focal_brand": focal,
            "focal_share": blank(rnd(fs, 3)), "top_competitor": top_c, "top_competitor_share": blank(rnd(tcs, 3)),
            "brand_link_call": link, "top_keywords": "|".join(r["keyword"] for r in top), "data_ref_month": ref or "",
        })
        cep_json.append({"cep": name, "series": [{"month": m, "v": v} for m, v in sm.items()],
                         "season_index": idx})

    # ── 우선순위 — 게이트 통과 상황 CEP, 순위 창 논브랜드 하한 순 (상한 순위와 다르면 적는다)
    ranked = [r for r in cep_rows if r["kind"] == "상황" and r["gated_out"] != "Y"]
    for i, r in enumerate(sorted(ranked, key=lambda r: (-r["window_nonbrand_lo"], r["cep"])), 1):
        r["priority_rank"] = i
    for i, r in enumerate(sorted(ranked, key=lambda r: (-r["window_nonbrand_hi"], r["cep"])), 1):
        r["priority_rank_hi"] = i
        if i != r["priority_rank"]:
            r["flip_under_hi"] = f"순위 {r['priority_rank']}→{i}"
    # 옆 열 — 마지막 달 포함 12개월(volume_total) 기준 순위(v0.1.0 방식). 다르면 마지막 달이 순위를 움직인 CEP다
    incl = [r for r in cep_rows if r["kind"] == "상황" and r["volume_12m_lo"] >= a.gate]
    for i, r in enumerate(sorted(incl, key=lambda r: (-r["nonbrand_volume_lo"], r["cep"])), 1):
        r["priority_rank_incl_last"] = i
    for r in cep_rows:
        if r["kind"] == "상황" and r["priority_rank"] != r["priority_rank_incl_last"]:
            r["rank_shift_incl_last"] = f"{r['priority_rank_incl_last'] or '밖'}→{r['priority_rank'] or '밖'}"
    order = {"상황": 0, "코어": 1, "비상황": 2, "브랜드·제품명": 3, "미분류": 4}
    cep_rows.sort(key=lambda r: (order.get(r["kind"], 9), r["priority_rank"] if r["priority_rank"] != "" else 999,
                                 -r["volume_12m_lo"]))

    # ── 검색어 행
    kw_rows = []
    for r in rows:
        st = gst.get(r["canonical"]) if r["canonical"] in groups and r["keyword"] == r["canonical_rep"] else None
        kw_rows.append({**{k: r.get(k, "") for k in KW_COLS},
                        "cep_axis": axis_of(r["cep"]) if r["kind"] == "상황" else "",
                        "brand_scope": "브랜드" if r["brand"] else "논브랜드",
                        "volume_total": blank(r["volume_total"]), "group_volume_lo": blank(r["group_volume_lo"]),
                        "group_volume_hi": blank(r["group_volume_hi"]),
                        "returned": r["returned"], "stage2": "Y" if r["keyword"] in stage2 else "",
                        "yoy_12m": blank(rnd(st[2])) if st else "",
                        "window_volume": (wv[r["canonical"]][0] if st and wv[r["canonical"]][1] else ""),
                        "series_status": (st[0] if st else ("미반환" if r["returned"] == "N" else
                                                            ("경계 밖" if r["boundary"] else
                                                             ("같은 검색 중복" if r["dup_of"] else "그룹 대표 아님"))))})

    # ── 머리 정보
    calls = list(csv.DictReader(open(a.calls, encoding="utf-8-sig"))) if a.calls else []
    trunc = [c for c in calls if c.get("truncated") == "Y"]
    sit_lo = sum(r["volume_12m_lo"] for r in cep_rows if r["kind"] == "상황")
    sit_nb = sum(r["nonbrand_volume_lo"] for r in cep_rows if r["kind"] == "상황")
    unc = next((r for r in cep_rows if r["kind"] == "미분류"), None)
    unc_top = sorted((rep_of[c] for c in groups if rep_of[c]["kind"] == "미분류"),
                     key=lambda r: -r["group_volume_lo"])[:10]
    flips = sum(1 for r in cep_rows if r["flip_under_hi"])
    shifts = [f"{r['cep']} {r['rank_shift_incl_last']}" for r in cep_rows if r["rank_shift_incl_last"]]
    sit_anc = [c for c in groups if rep_of[c]["anchor_hit"] == "Y" and rep_of[c]["kind"] == "상황"]
    part_lo = sum(rep_of[c]["group_volume_lo"] for c in sit_anc if rep_of[c].get("match_scope") == "어절 일부")
    w_sit = sum(wlo(c) for c in sit_anc)
    w_sit_ser = sum(wlo(c) for c in sit_anc if wv[c][1])
    meta = {
        "keyword": sense["keyword"], "gl": sense["gl"], "domain": sense.get("domain", ""),
        "focal_brand": focal, "competitors": comps,
        "data_ref_month": ref, "queried_at": a.queried_at, "lag_months": lag,
        "window_end": end, "last_month": "포함" if a.include_last_month else "제외(기본)",
        "last_month_suspect": suspect, "windows": {"cur12": f"{cur12[0]}~{cur12[-1]}" if cur12 else None,
                                                   "prev12": f"{prev12[0]}~{prev12[-1]}" if prev12 else None,
                                                   "cur3": f"{cur3[0]}~{cur3[-1]}" if cur3 else None,
                                                   "prev3": f"{prev3[0]}~{prev3[-1]}" if prev3 else None},
        "gates": {"cep_12m": a.gate, "trend_12m": a.trend_gate, "trend_3m": a.gate_3m, "keyword_12m": a.kw_gate,
                  "trend_pct": a.trend_pct, "brand_min": a.brand_min, "peak": a.peak, "pre_steps": a.pre_steps},
        "coverage": a.coverage, "truncated_calls": [f"{c['tool']} {c['seed']}" for c in trunc],
        "calls_checked": len(calls),
        "anchored_volume_lo": total_lo, "anchored_volume_hi": total_hi,
        "situation_volume_lo": sit_lo, "situation_share_lo": rnd(sit_lo / total_lo) if total_lo else None,
        "situation_nonbrand_lo": sit_nb, "situation_nonbrand_share_lo": rnd(sit_nb / sit_lo) if sit_lo else None,
        "unclassified_volume_lo": unc["volume_12m_lo"] if unc else 0,
        "unclassified_share_lo": (unc["share_lo"] if unc else 0),
        "paths": len(paths), "paths_seed_found": found,
        "excluded_boundary": [r["keyword"] for r in rows if r["boundary"]],
        "not_returned": [r["keyword"] for r in rows if r["returned"] == "N"],
        "dup_rows": sum(1 for r in rows if r["dup_of"]), "merged_identical": dd["merged_identical"],
        "flips_under_hi": flips, "ranked_ceps": len(ranked),
        "rank_window": rank_window, "rank_window_series_share": rnd(w_sit_ser / w_sit, 3) if w_sit else None,
        "rank_shifts_incl_last": shifts,
        "partial_token_volume_lo": part_lo, "partial_token_share_lo": rnd(part_lo / sit_lo) if sit_lo else None,
        "gated_ceps": [r["cep"] for r in cep_rows if r["gated_out"] == "Y"],
    }
    out = pathlib.Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    for fn, cols, data in (("cep_keywords.csv", KW_COLS, kw_rows), ("cep_map.csv", MAP_COLS, cep_rows),
                           ("cep_brand.csv", BRAND_COLS, brand_rows)):
        with open(out / fn, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(data)
    sj = {c["cep"]: c for c in cep_json}
    doc = {"meta": meta, "ceps": [{**r, **sj.get(r["cep"], {})} for r in cep_rows], "brands": brand_rows,
           "unclassified_top": [{"keyword": r["keyword"], "volume_lo": r["group_volume_lo"]} for r in unc_top]}
    json.dump(doc, open(out / "cep_map.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ── 요약 md
    L = [f"# CEP 지도 — {sense['keyword']} ({sense['gl']})", "",
         f"- 데이터 기준월 {ref or '[데이터 공백]'} / 조회일 {a.queried_at} / 지연 {lag if lag is not None else '?'}개월 [실제 데이터]",
         f"- 비교 창 {meta['windows']['cur12']} vs {meta['windows']['prev12']} · 마지막 달 {meta['last_month']} · "
         f"미완결 의심 {suspect}건 [추정]",
         f"- 수집 범위 {a.coverage} · 절단 호출 {len(trunc)}/{len(calls)}건"
         + (" — 절단된 호출은 반환분 기준이다" if trunc else ""),
         f"- 경로 {len(paths)}개 중 시드 발견 {found}개 · 시드 앞 {a.pre_steps}걸음 [가정] — 세션 인접 집계이지 개인 여정이 아니다",
         f"- 카테고리 결속 검색량(하한) {total_lo:,} ~ (상한) {total_hi:,} 중 상황 CEP {sit_lo:,}"
         f" · 미분류 {meta['unclassified_volume_lo']:,} [추정]",
         f"- 상황 CEP 중 논브랜드 {sit_nb:,} · 표기변형 상한에서 순위 뒤집힘 {flips}건 · 같은 검색 중복 {meta['dup_rows']}행",
         f"- 순위 창 {rank_window} (추세 창과 같음 · 마지막 달 {meta['last_month']}) — 순위 창 볼륨 중 시계열 합 비중 "
         f"{meta['rank_window_series_share'] if meta['rank_window_series_share'] is not None else '—'}, 나머지는 volume_total(마지막 달 포함) [추정]",
         f"- 마지막 달 포함 12개월로 매기면 순위가 바뀌는 CEP {len(shifts)}개" + (f": {' · '.join(shifts)}" if shifts else ""),
         f"- 정밀도 신호: 상황 CEP 검색량 중 주 일치가 더 긴 어절의 앞부분(«어절 일부»)인 비중 "
         f"{meta['partial_token_share_lo'] if meta['partial_token_share_lo'] is not None else '—'} — 높은 CEP는 검색어를 직접 확인 [추정]",
         f"- 게이트 {a.gate:,}(CEP 순위 창 하한) 미만 CEP {len(meta['gated_ceps'])}개는 순위 밖 [가정]", "",
         "| 순위 | CEP | 논브랜드 순위 창(하한~상한) | 논브랜드 12개월 마지막 달 포함(하한) | 포함 시 순위 | 전체(하한) | 전년 12개월 | 최근 3개월 | 추세 | 성수기 | 브랜드 연결 | 어절 일부 |",
         "|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|---:|"]
    for r in cep_rows:
        if r["kind"] != "상황":
            continue
        pct = lambda v: f"{v:+.0%}" if v != "" else "—"
        pct0 = lambda v: f"{v:.0%}" if v != "" else "—"
        L.append(f"| {r['priority_rank'] or '—'} | {r['cep']} | {r['window_nonbrand_lo']:,}~{r['window_nonbrand_hi']:,} | "
                 f"{r['nonbrand_volume_lo']:,} | {r['priority_rank_incl_last'] or '—'} | "
                 f"{r['volume_12m_lo']:,} | {pct(r['yoy_12m'])} | {pct(r['yoy_3m'])} | {r['trend_call']} | "
                 f"{r['peak_months'] or '—'} | {r['brand_link_call']} | {pct0(r['partial_token_share'])} |")
    L += ["", "## 미분류 상위 검색어 (사전 보강 후보)", ""]
    L += [f"- {r['keyword']} {r['group_volume_lo']:,}" for r in unc_top] or ["- 없음"]
    L += ["", "라벨: 검색량·월별 값 [실제 데이터] · CEP 분류·합산·비중·브랜드 연결 [추정] · 게이트·임계·사전 [가정] · "
          "미반환·결측·시계열 없음 [데이터 공백]. CEP 이름 해석·메시지 제안은 에이전트가 쓴 [추정]이다."]
    (out / "cep_summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"기준월 {ref} · 조회일 {a.queried_at} · 지연 {lag}개월 · 창 끝 {end} (마지막 달 {meta['last_month']}) · 미완결 의심 {suspect}건")
    print(f"상황 CEP {sum(1 for r in cep_rows if r['kind'] == '상황')}개 · 순위 {len(ranked)}개 · 게이트 미달 {len(meta['gated_ceps'])}개 · "
          f"미분류 비중 {meta['unclassified_share_lo']} · 어절 일부 비중 {meta['partial_token_share_lo']} [추정]")
    print(f"순위 창 {rank_window} · 마지막 달 포함 시 순위 바뀜 {len(shifts)}개" + (f" ({' · '.join(shifts[:5])})" if shifts else ""))
    print(f"→ {out / 'cep_map.csv'} · cep_keywords.csv · cep_brand.csv · cep_map.json · cep_summary.md")


if __name__ == "__main__":
    main()
