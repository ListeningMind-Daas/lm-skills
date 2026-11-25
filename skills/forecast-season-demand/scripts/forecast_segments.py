#!/usr/bin/env python3
"""STEP 6~8 · keyword_info `all` 응답 → 세그먼트 묶기 · 다음 3/6개월 예측 · 시즌 일정.

  python3 scripts/forecast_segments.py raw/kw_all.json --themes themes.json --horizon 6 \
      --queried-at 2026-11-19 --requested all_request.json --refmonth-check refmonth.json \
      --ledger ledger.json [--gate 3000] [--window 3] [--include-last-month] -o out/

출력(out/): keywords.csv · segment_forecast.csv · forecast_monthly.csv · season_calendar.csv · forecast_result.json
계산 규칙은 forecast_core.py 머리 주석과 SKILL.md STEP 7~8. 모든 예측 · 일정은 규칙 기반 `[추정]`이고 임계는 `[가정]`이다.

함정 처리
- T1 기준월 = monthly_volume 의 month 최댓값(배열 순서 가정 없음). 조회일과의 지연 · 예측 기간 중 조회일에 이미 지난 달 수를 낸다.
- T2 기본은 마지막 달(기준월)을 비율 · 프로파일에서 뺀다. 다른 모드(포함)도 계산해 비율 방향이 갈리면 «잠정».
  `--refmonth-check`(check_refmonth.py --json 출력)가 없으면 멈춘다(`--no-refmonth-check`면 «미점검 [데이터 공백]»).
- T3 `volume_trend` 는 keywords.csv `volume_trend_ref` 참고 컬럼으로만 옮기고 어떤 계산에도 쓰지 않는다.
- T4 세그먼트 비율의 전년 기저(W개월 합)가 `--gate` 미만이면 r=1 · gated=예 → 순위 문장의 주어에서 뺀다.
- T5 완전 중복 행 합치기 → 시계열이 같은 행은 한 번만(dup_of) → 같은 canonical 표기변형은 하한(대표 1개) · 상한(합) 병기.
- T6 계산 창(기준월로 끝나는 37개월)에 결측 월이 하나라도 있으면 그 검색어는 모든 합계에서 뺀다(모집단 고정) — 0으로 채우지 않는다.
"""
import argparse, csv, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, collapse_identical, dedup_map, load_rules, report_collapse  # noqa: E402
import forecast_core as FC  # noqa: E402
import ledger as L  # noqa: E402

KEYWORD_COLS = ["keyword", "canonical", "dup_of", "segment", "facets", "brand", "status", "avg_12m", "yoy_12m",
                "missing_in_window", "first_active_month", "volume_trend_ref"]
SEGMENT_COLS = ["segment", "n_keywords", "n_variants_upper", "avg_12m", "ratio_window", "recent_sum", "prior_sum",
                "r_used", "r_note", "r_other_mode", "r_12m", "next_total", "next_total_last_year", "next_yoy",
                "next_abs_change", "next_lo", "next_hi", "next_total_upper", "backtest_err", "backtest_naive_err",
                "quality", "gated", "provisional", "rank_pct", "rank_abs"]
MONTHLY_COLS = ["segment", "month", "last_year_month", "last_year", "forecast", "forecast_lo", "forecast_hi"]
CALENDAR_COLS = ["segment", "season_type", "peak_month", "peak_consistency", "peak_index", "amplitude", "ramp_month",
                 "prep", "launch", "campaign_from", "peak", "end", "status", "peak_in_horizon", "anti_phase"]
CATEGORY = "카테고리 합계"


def load_json(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8"))


def write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def rnd(x, n=4):
    return None if x is None else round(x, n)


def read_refcheck(path):
    txt = pathlib.Path(path).read_text(encoding="utf-8")
    try:
        obj = json.loads(txt)
        return {"checked": obj.get("checked"), "suspect": obj.get("last_month_suspect"),
                "flagged": [f[0] for f in obj.get("flagged") or []], "data_ref_month": obj.get("data_ref_month")}
    except ValueError:
        m = re.search(r"\((\d+)행 검사\)", txt)
        s = re.search(r"이상 의심: (\d+)행", txt)
        return {"checked": int(m.group(1)) if m else None, "suspect": int(s.group(1)) if s else None,
                "flagged": [], "data_ref_month": None}


def series_of(row):
    """{월: 값 또는 None}. nv_code 결측 월은 total 키가 없다 → None (계약 §2)."""
    return {m["month"]: m.get("total") for m in row.get("monthly_volume") or [] if isinstance(m, dict) and m.get("month")}


def segment_record(name, members, ref, end, other_end, horizon, window, gate, hmonths, queried):
    """members: [(keyword, 시계열, is_lo)] — is_lo=False 는 표기변형 상한에만 들어간다."""
    need = FC.required_months(ref, horizon, window)
    lo = {m: sum(s[m] for _, s, is_lo in members if is_lo) for m in need}
    hi = {m: sum(s[m] for _, s, _ in members) for m in need}
    r, cur, prev, note = FC.ratio(lo, end, window, gate)
    r_o, _, _, _ = FC.ratio(lo, other_end, window, gate)
    r12, _, _, _ = FC.ratio(lo, end, 12, gate * 12 // window)
    fc = FC.forecast(lo, ref, horizon, r)
    cands = [x for x in (r, r_o, r12) if x is not None]
    lo_r, hi_r = min(cands), max(cands)
    nt = sum(f for *_, f in fc)
    ly = sum(b for _, _, b, _ in fc)
    upper = sum(hi[b] * r for _, b, _, _ in fc)
    bt = FC.backtest_sums(lo, end, horizon, window, gate)
    bt_err = (bt[0] / bt[2] - 1) if bt and bt[2] > 0 else None
    bt_naive = (bt[1] / bt[2] - 1) if bt and bt[2] > 0 else None
    gated = "예" if note.startswith(("기저 게이트", "계산 불가")) else "아니오"
    prov = "잠정" if (gated == "아니오" and r_o is not None and (r - 1) * (r_o - 1) < 0) else ""
    quality = "점검 불가" if bt_err is None else ("참고" if abs(bt_err) > FC.QUALITY_ERR else "양호")
    prof = FC.profile(lo, end)
    tm = FC.timing(prof, queried, hmonths)
    last12 = FC.window_months(ref, 12)
    return {
        "segment": name, "n_keywords": sum(1 for *_, is_lo in members if is_lo),
        "n_variants_upper": sum(1 for *_, is_lo in members if not is_lo),
        "avg_12m": round(sum(lo[m] for m in last12) / 12),
        "ratio_window": f"{FC.month_add(end, -window + 1)}~{end} vs {FC.month_add(end, -window - 11)}~{FC.month_add(end, -12)}",
        "recent_sum": cur, "prior_sum": prev, "r_used": rnd(r), "r_note": note, "r_other_mode": rnd(r_o),
        "r_12m": rnd(r12), "next_total": round(nt), "next_total_last_year": round(ly),
        "next_yoy": rnd(nt / ly - 1) if ly else None, "next_abs_change": round(nt - ly),
        "next_lo": round(ly * lo_r), "next_hi": round(ly * hi_r), "next_total_upper": round(upper),
        "backtest_err": rnd(bt_err), "backtest_naive_err": rnd(bt_naive), "quality": quality,
        "gated": gated, "provisional": prov, "rank_pct": None, "rank_abs": None,
        "_bt": bt, "_lo": lo, "_fc": [(m, lym, b, f, None) for m, lym, b, f in fc],
        "_range": (lo_r, hi_r), "_profile": prof, "_timing": tm,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("metrics")
    ap.add_argument("--themes", required=True)
    ap.add_argument("--horizon", type=int, choices=(3, 6), required=True)
    ap.add_argument("--queried-at", required=True)
    ap.add_argument("--requested")
    ap.add_argument("--refmonth-check")
    ap.add_argument("--no-refmonth-check", action="store_true")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--gate", type=int, default=3000)
    ap.add_argument("--window", type=int, default=3)
    ap.add_argument("--include-last-month", action="store_true")
    ap.add_argument("-o", "--outdir", required=True)
    a = ap.parse_args()

    if not a.refmonth_check and not a.no_refmonth_check:
        print("--refmonth-check 가 없다 — STEP 5.5 `check_refmonth.py <응답> --json > refmonth.json` 을 먼저 돌린다"
              " (점검 없이 가려면 --no-refmonth-check · 리포트에 «미점검 [데이터 공백]»)")
        return 2
    out = pathlib.Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    themes = load_json(a.themes)
    rules = load_rules(a.themes)
    pats = [(t["name"], re.compile(t["pattern"])) for t in themes.get("themes") or []]
    brands = [(b, canon(b, rules)) for b in themes.get("brands") or []]
    nm = themes.get("no_match") or {"with_brand": "브랜드·제품명", "without_brand": "코어·일반"}
    ex = (themes.get("exclude") or {}).get("pattern") or ""
    ex_re = re.compile(ex) if ex else None

    obj = load_json(a.metrics)
    rows = obj.get("data", []) if isinstance(obj, dict) else obj
    n_raw = len(rows)
    rows, merged, conflicts = collapse_identical([r for r in rows if isinstance(r, dict) and r.get("keyword")])
    report_collapse(merged, conflicts, n_raw)

    # ── T1 기준월
    refs = sorted({max(m["month"] for m in r["monthly_volume"] if m.get("month"))
                   for r in rows if r.get("monthly_volume")})
    if not refs:
        print("monthly_volume 이 있는 행이 없다 — data_type=all 로 조회했는지 확인할 것 (ads_metrics 에는 시계열이 없다)")
        return 2
    ref = refs[-1]
    queried = a.queried_at[:7]
    lag = FC.month_diff(ref, queried)
    end = ref if a.include_last_month else FC.month_add(ref, -1)
    other_end = FC.month_add(ref, -1) if a.include_last_month else ref
    hmonths = [FC.month_add(ref, k) for k in range(1, a.horizon + 1)]
    elapsed = sum(1 for m in hmonths if FC.month_diff(m, queried) >= 0)
    need = FC.required_months(ref, a.horizon, a.window)

    req_c = None
    if a.requested:
        req = load_json(a.requested)
        req_c = {canon(k, rules): k for k in req}
    dm = dedup_map(rows, rules)

    kw_rows, usable = [], []
    for r in rows:
        kw = r["keyword"]
        c = canon(kw, rules)
        s = series_of(r)
        facets = [n for n, p in pats if p.search(c)]
        brand = next((b for b, bc in brands if bc and bc in c), "")
        seg = facets[0] if facets else (nm["with_brand"] if brand else nm["without_brand"])
        miss = [m for m in need if m in s and s[m] is None]
        absent = [m for m in need if m not in s]
        dup = dm.get(kw, {}).get("rep", kw)
        last12 = [s.get(m) for m in FC.window_months(ref, 12)]
        prev12 = [s.get(FC.month_add(m, -12)) for m in FC.window_months(ref, 12)]
        avg12 = round(sum(last12) / 12) if all(v is not None for v in last12) else None
        yoy = None
        if avg12 is not None and all(v is not None for v in prev12) and sum(prev12) >= a.gate:
            yoy = round(sum(last12) / sum(prev12) - 1, 4)
        first = next((m for m in sorted(s) if (s[m] or 0) > 0), "")
        if ex_re and ex_re.search(c):
            st = "범위 밖"
        elif req_c is not None and c not in req_c:
            st = "요청 외 반환"
        elif not s:
            st = "시계열 없음"
        elif dup != kw:
            st = "같은 검색의 중복"
        elif absent:
            st = f"이력 부족 — 계산 창 {len(need)}개월 중 {len(absent)}개월 없음"
        elif miss:
            st = "결측 월 — 계산 불가"
        else:
            st = "사용"
        row = {"keyword": kw, "canonical": c, "dup_of": "" if dup == kw else dup, "segment": seg,
               "facets": " / ".join(facets), "brand": brand, "status": st, "avg_12m": avg12, "yoy_12m": yoy,
               "missing_in_window": len(miss) + len(absent), "first_active_month": first,
               "volume_trend_ref": (r.get("ads_metrics") or {}).get("volume_trend")}
        kw_rows.append(row)
        if st == "사용":
            usable.append((row, s))

    # T5 ② — 같은 canonical 표기변형: 12개월 합이 가장 큰 표기 하나가 하한, 나머지는 상한에만
    groups = {}
    for row, s in usable:
        groups.setdefault(row["canonical"], []).append((row, s))
    members_by_seg = {}
    for c, g in groups.items():
        g.sort(key=lambda x: (-(x[0]["avg_12m"] or 0), x[0]["keyword"]))
        for i, (row, s) in enumerate(g):
            if i:
                row["status"] = f"표기변형 — 상한에만 (하한 대표 {g[0][0]['keyword']})"
            members_by_seg.setdefault(row["segment"], []).append((row["keyword"], s, i == 0))

    if req_c is not None:
        got = {r["canonical"] for r in kw_rows}
        for c, k in req_c.items():
            if c not in got:
                kw_rows.append({"keyword": k, "canonical": c, "dup_of": "", "segment": "", "facets": "", "brand": "",
                                "status": "미반환", "avg_12m": None, "yoy_12m": None, "missing_in_window": None,
                                "first_active_month": "", "volume_trend_ref": None})

    if not members_by_seg:
        print("계산에 쓸 검색어가 없다 — keywords.csv 의 status 를 보고 결측 · 이력 부족 · 범위 밖을 확인할 것")
        write_csv(out / "keywords.csv", KEYWORD_COLS, kw_rows)
        return 3

    order = [n for n, _ in pats] + [nm["with_brand"], nm["without_brand"]]
    segs = [segment_record(n, members_by_seg[n], ref, end, other_end, a.horizon, a.window, a.gate, hmonths, queried)
            for n in order if n in members_by_seg]

    # 카테고리 = 세그먼트 하한 합(아래에서 위로) · 예측도 세그먼트 예측의 합
    all_members = [m for n in order for m in members_by_seg.get(n, [])]
    cat = segment_record(CATEGORY, all_members, ref, end, other_end, a.horizon, a.window, a.gate, hmonths, queried)
    cat_fc = []
    for i, m in enumerate(hmonths):
        cat_fc.append((m, segs[0]["_fc"][i][1], sum(s["_fc"][i][2] for s in segs), sum(s["_fc"][i][3] for s in segs), None))
    cat["_fc"] = cat_fc
    nt, ly = sum(x[3] for x in cat_fc), sum(x[2] for x in cat_fc)
    cat.update({"next_total": round(nt), "next_total_last_year": round(ly), "next_yoy": rnd(nt / ly - 1) if ly else None,
                "next_abs_change": round(nt - ly), "next_lo": sum(s["next_lo"] for s in segs),
                "next_hi": sum(s["next_hi"] for s in segs), "next_total_upper": sum(s["next_total_upper"] for s in segs),
                "r_used": rnd(nt / ly) if ly else None, "r_note": "세그먼트 예측의 합(아래에서 위로) — r 은 합계에서 역산"})
    bts = [s["_bt"] for s in segs if s["_bt"]]
    if len(bts) == len(segs) and sum(b[2] for b in bts) > 0:
        A = sum(b[2] for b in bts)
        cat["backtest_err"] = rnd(sum(b[0] for b in bts) / A - 1)
        cat["backtest_naive_err"] = rnd(sum(b[1] for b in bts) / A - 1)
        cat["quality"] = "참고" if abs(cat["backtest_err"]) > FC.QUALITY_ERR else "양호"

    # 순위 — 게이트 통과 세그먼트만(T4). 증감률 · 절대 증감 두 기준
    elig = [s for s in segs if s["gated"] == "아니오" and s["next_yoy"] is not None]
    for i, s in enumerate(sorted(elig, key=lambda s: -s["next_yoy"]), 1):
        s["rank_pct"] = i
    for i, s in enumerate(sorted(elig, key=lambda s: -s["next_abs_change"]), 1):
        s["rank_abs"] = i

    # 역위상 — 카테고리 정점과 ANTI_PHASE_GAP 개월 이상 떨어진 계절형 세그먼트
    cp = cat["_profile"]
    warnings = []
    for s in segs:
        p = s["_profile"]
        s["anti_phase"] = ""
        if p and cp and p["season_type"] == "계절형" and cp.get("peak") and FC.circ(p["peak"], cp["peak"]) >= FC.ANTI_PHASE_GAP:
            s["anti_phase"] = "예"
            warnings.append(f"역위상: «{s['segment']}» 정점 {p['peak']}월 ↔ 카테고리 정점 {cp['peak']}월 — 카테고리 일정이 이 세그먼트를 대표하지 않는다")
    seasonal_peaks = {s["_profile"]["peak"] for s in segs if s["_profile"] and s["_profile"]["season_type"] == "계절형"}
    if cp and cp["season_type"] != "계절형" and len(seasonal_peaks) >= 2:
        warnings.append(f"카테고리 합계는 «{cp['season_type']}»이지만 계절형 세그먼트 정점이 {sorted(seasonal_peaks)}월로 갈린다 — 합계가 계절을 상쇄했다")
    cat["anti_phase"] = ""

    # ── 출력
    write_csv(out / "keywords.csv", KEYWORD_COLS, kw_rows)
    write_csv(out / "segment_forecast.csv", SEGMENT_COLS, segs + [cat])
    monthly = []
    for s in segs + [cat]:
        lo_r, hi_r = s["_range"] if s is not cat else (None, None)
        for i, (m, lym, base, f, _) in enumerate(s["_fc"]):
            if s is cat:
                flo = sum(x["_fc"][i][2] * x["_range"][0] for x in segs)
                fhi = sum(x["_fc"][i][2] * x["_range"][1] for x in segs)
            else:
                flo, fhi = base * lo_r, base * hi_r
            monthly.append({"segment": s["segment"], "month": m, "last_year_month": lym or FC.month_add(m, -12),
                            "last_year": base, "forecast": round(f), "forecast_lo": round(flo), "forecast_hi": round(fhi)})
    write_csv(out / "forecast_monthly.csv", MONTHLY_COLS, monthly)
    cal_rows = []
    for s in segs + [cat]:
        p, t = s["_profile"], s["_timing"]
        cal_rows.append({"segment": s["segment"], "season_type": p["season_type"] if p else "판정 불가",
                         "peak_month": p["peak"] if p else None, "peak_consistency": f"{p['peak_consistency']}/3" if p else "",
                         "peak_index": rnd(p["si"][p["peak"]], 3) if p and p["peak"] else None,
                         "amplitude": rnd(p["amplitude"], 2) if p and p["amplitude"] else None,
                         **{k: t[k] for k in ("ramp_month", "prep", "launch", "campaign_from", "peak", "end", "status", "peak_in_horizon")},
                         "anti_phase": s.get("anti_phase", "")})
    write_csv(out / "season_calendar.csv", CALENDAR_COLS, cal_rows)

    counts = {}
    for r in kw_rows:
        key = r["status"].split(" — ")[0] if not r["status"].startswith("표기변형") else "표기변형(상한에만)"
        counts[key] = counts.get(key, 0) + 1
    refcheck = read_refcheck(a.refmonth_check) if a.refmonth_check else None
    led = L.load(a.ledger)
    rd = (obj.get("request_detail") or {}) if isinstance(obj, dict) else {}
    n_req = len(rd.get("keywords") or (list(req_c) if req_c else rows))
    formula = 10 * n_req
    cost, src = L.cost_of(obj, formula)
    L.put(led, {"step": "STEP 5 48개월 조회", "step_order": 5, "at": L.saved_at(a.metrics, obj), "tool": "keyword_info",
                "params": f"data_type=all · 검색어 {n_req}개", "purpose": "통과분 48개월 월별 검색량",
                "returned": n_raw, "cost": cost, "cost_source": src, "formula": f"10 × {n_req} = {formula}",
                "raw": str(a.metrics).replace("\\", "/"), "data_through": ref, "status": "ok"})
    L.save(a.ledger, led)

    def pub(s):
        p = s["_profile"]
        lo = s["_lo"]
        return {**{k: s.get(k) for k in SEGMENT_COLS},
                "calendar": next(c for c in cal_rows if c["segment"] == s["segment"]),
                "history": {m: lo[m] for m in FC.window_months(ref, 24)},
                "forecast": [{"month": m, "last_year": b, "forecast": round(f)} for m, _, b, f, _ in s["_fc"]],
                "profile": None if not p else {"si": {str(c): rnd(v, 3) for c, v in p["si"].items()},
                                               "blocks": [{"label": b["label"], "values": b["values"],
                                                           "index": {str(c): rnd(v, 3) for c, v in b["index"].items()}}
                                                          for b in p["blocks"]]}}

    result = {
        "meta": {"gl": (led.get("steps", {}).get("collect") or {}).get("gl"), "data_ref_month": ref,
                 "mixed_ref_months": refs if len(refs) > 1 else None, "queried_at": a.queried_at, "lag_months": lag,
                 "horizon": a.horizon, "horizon_months": hmonths, "horizon_elapsed_at_query": elapsed,
                 "window": a.window, "gate": a.gate,
                 "last_month_mode": "포함" if a.include_last_month else "제외", "ratio_end": end,
                 "profile_window": f"{FC.month_add(end, -35)}~{end}", "calc_window": f"{need[0]}~{need[-1]}",
                 "rules": {"R_MIN": FC.R_MIN, "R_MAX": FC.R_MAX, "SEASONAL_PEAK": FC.SEASONAL_PEAK,
                           "RAMP_LEVEL": FC.RAMP_LEVEL, "PREP_LEAD": FC.PREP_LEAD, "LAUNCH_LEAD": FC.LAUNCH_LEAD,
                           "QUALITY_ERR": FC.QUALITY_ERR, "ANTI_PHASE_GAP": FC.ANTI_PHASE_GAP},
                 "refmonth_check": refcheck, "keyword_counts": counts, "rows_merged": merged,
                 "unreturned": [r["keyword"] for r in kw_rows if r["status"] == "미반환"],
                 "excluded_missing": [r["keyword"] for r in kw_rows if r["status"].startswith(("결측", "이력 부족"))]},
        "segments": [pub(s) for s in segs], "category": pub(cat), "warnings": warnings,
        "ledger_steps": led.get("steps", {}),
    }
    (out / "forecast_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"데이터 기준월 {ref} / 조회일 {a.queried_at} / 지연 {lag}개월 · 마지막 달 {result['meta']['last_month_mode']}"
          f" (비율 창 끝 {end})")
    print(f"예측 기간 {hmonths[0]}~{hmonths[-1]} — 조회일 기준 이미 지났거나 진행 중인 달 {elapsed}개")
    if refcheck is None:
        print("마지막 달 완결성: 미점검 [데이터 공백]")
    else:
        print(f"마지막 달 완결성: {refcheck['checked']}행 검사 · 의심 {refcheck['suspect']}행")
    print("검색어 " + " · ".join(f"{k} {v}" for k, v in counts.items()))
    for s in segs + [cat]:
        yoy = "—" if s["next_yoy"] is None else f"{s['next_yoy']:+.1%}"
        print(f"  {s['segment']}: 다음 {a.horizon}개월 {s['next_total']:,} (전년 {s['next_total_last_year']:,} · {yoy})"
              f" 범위 {s['next_lo']:,}~{s['next_hi']:,} · 점검 {s['quality']}"
              + (f" · {s['r_note']}" if s['r_note'] and s is not cat else "") + (" · 잠정" if s["provisional"] else "")
              + (" · 순위 제외(게이트)" if s["gated"] == "예" else ""))
    for w in warnings:
        print("⚠️ " + w)
    print(f"→ {out}/ keywords.csv · segment_forecast.csv · forecast_monthly.csv · season_calendar.csv · forecast_result.json")
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(main())
