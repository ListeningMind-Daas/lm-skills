#!/usr/bin/env python3
"""브랜드 월간 벤치마크 — 월별 검색량·검색 점유율(검색량 기준 근사)·메시지 구성·갭 경고.

입력은 2단(keyword_info data_type=all) 응답이다. `all`은 최장 48개월 monthly_volume을 주므로
첫 실행에서 과거 월 비교가 바로 나온다 — 매달 쌓을 필요가 없다. 매달 다시 돌리는 이유는
새 기준월 1개를 더하는 것뿐이다.

지키는 것:
  · 중복 묶음(bench_common.components — 같은 검색의 중복 · 표기변형 · 구글 지표 공유)마다 12개월 볼륨의
    하한(묶음 최댓값)과 상한(구글 공유 1회 + 네이버 합)을 낸다(계약 T5 · 확장). 순위는 하한 기준,
    상한에서 뒤집히면 rank_flip · 경고로 표시한다 → bounds_{category}.csv
  · 월 패널(점유율·yoy·경고)은 묶음 대표 1행 시계열 = 하한 기준이다. 비대표 변형(오타·구글 공유 행)은
    월 패널에 들어가지 않는다 — 결측 이력이 있는 오타 변형 하나가 브랜드 전체 yoy를 지우지 않는다.
    단 네이버 분리 변형(구글만 공유하고 네이버 볼륨이 다른 행 · bench_common ④)이 2단에 있으면 그 월별 네이버
    볼륨(`nv`)을 더한다 — 네이버에서는 다른 검색이다. 2단에 없으면 과소집계를 gap_alert에 적는다
  · 결측 월(nv_code, total 키 없음)은 0이 아니라 결측 — 그 달의 점유율은 «계산 불가»
  · 마지막 달은 기본 제외 (미완결) — --include-last-month로 포함하면 리포트에 표기된다
  · 전년 동기 비교(yoy_3m)는 기저 게이트 미달이면 비운다 · API volume_trend는 쓰지 않는다
  · 브랜드 점유율은 두 분모로 낸다 — 브랜드 합(sov_brandset) · 논브랜드 포함 카테고리(sov_category)
  · 브랜드명 단독 검색 비중(brand_alone_share)을 낸다 — 높으면 점유율은 카테고리 점유가 아니라 브랜드 인지도 쪽이다
  · 메시지(수식어 테마) 칸이 2단에 없을 때 0으로 단정하지 않는다 — 1단 응답으로 «게이트 밖 · 실제 0 · 미수집»을
    가른다(theme_coverage_{category}.csv). 테마 커버리지가 기준 미만인 테마는 메시지 경고를 보류한다

    python3 brand_benchmark.py stage2_all.json --config benchmark.json --category running-shoes-kr \\
        --stage1 stage1_ads_metrics.json --queried-at 2026-09-28 --gate 3000 --sov-pp 2.0 --yoy-pct 0.20 --months 24 --outdir out
"""
import argparse, csv, pathlib, sys
from collections import defaultdict
from datetime import date

sys.dont_write_bytecode = True   # 스킬 폴더에 __pycache__를 남기지 않는다
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bench_common import Config, alias_suspects, components, load, shift, sorted_series, NONBRAND, MULTI  # noqa: E402

BENCH_COLS = ["month", "brand", "bucket_type", "is_focal", "keywords_n", "volume", "missing_keywords",
              "month_complete", "series_basis", "sov_brandset", "sov_category", "sov_brandset_delta_prev_month_pp",
              "sov_brandset_delta_prev_year_pp", "yoy_3m", "yoy_gated_out", "brand_alone_share",
              "data_ref_month", "analysis_end_month", "last_month_included"]
MSG_COLS = ["month", "theme", "brand", "volume", "share_within_theme", "share_within_brand",
            "data_ref_month"]
MAP_COLS = ["keyword", "canonical", "bucket", "theme_primary", "stage", "representative", "component_rep",
            "dup_basis", "brand_alone", "volume_total", "group_rows", "group_volume_lo", "group_volume_hi",
            "excluded", "api_volume_trend_do_not_use"]
BOUND_COLS = ["brand", "bucket_type", "is_focal", "groups_n", "rows_n", "volume_12m_lo", "volume_12m_hi",
              "sov_brandset_12m_lo", "sov_brandset_12m_hi", "sov_category_12m_lo", "sov_category_12m_hi",
              "brand_alone_12m_lo", "brand_alone_share_lo",
              "rank_lo", "rank_hi", "rank_flip", "bounds_source", "label"]
THEME_COLS = ["theme", "brand", "stage1_groups", "stage1_volume_12m_lo", "stage2_groups", "stage2_volume_12m_lo",
              "theme_coverage", "cell_status", "label"]
SERIES_BASIS = "대표 행 + 네이버 분리 변형(하한 기준)"
ALONE_WARN = 0.5   # [가정] 브랜드명 단독 비중이 이 이상이면 점유율을 브랜드 인지도 쪽으로 읽으라고 경고


def rnd(x, n=4):
    return "" if x is None else round(x, n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="2단 keyword_info(data_type=all) 응답 JSON")
    ap.add_argument("--config", required=True)
    ap.add_argument("--stage1", action="append", default=[],
                    help="1단 keyword_info(ads_metrics) 응답 — 비대표 변형의 12개월 볼륨으로 상한을 내고, "
                         "2단에 없는 메시지 칸이 게이트 밖인지 실제 0인지 가른다. 없으면 둘 다 «미확인»")
    ap.add_argument("--category", required=True, help="산출 파일명 접미사")
    ap.add_argument("--queried-at", default=date.today().isoformat())
    ap.add_argument("--gate", type=int, default=3000, help="yoy_3m 기저 게이트: 전년 같은 3개월 합 최소값")
    ap.add_argument("--sov-pp", type=float, default=2.0, help="점유율 변화 경고 임계 (%%p)")
    ap.add_argument("--yoy-pct", type=float, default=0.20, help="전년 동기 증감 경고 임계 (비율)")
    ap.add_argument("--min-theme-coverage", type=float, default=0.5,
                    help="테마 커버리지(2단 ÷ 1단, 12개월 하한)가 이 미만인 테마는 메시지 경고를 보류 (기본 0.5)")
    ap.add_argument("--months", type=int, default=24, help="산출 월 수 (분석 종료 월부터 거슬러)")
    ap.add_argument("--include-last-month", action="store_true", help="미완결 가능성이 있는 마지막 달 포함")
    ap.add_argument("--pre-history-zero", action="store_true",
                    help="키워드 이력 시작 이전 달을 0으로 본다 (기본: 결측) — [가정]으로 표기된다")
    ap.add_argument("--outdir", default=".")
    a = ap.parse_args()

    cfg = Config(a.config)
    data = [d for d in load(a.path) if d.get("monthly_volume")]
    if not data:
        sys.exit("monthly_volume이 있는 행이 없다 — 2단은 data_type=all로 조회해야 한다")
    for d in data:
        d["monthly_volume"] = sorted_series(d)          # 배열 순서를 가정하지 않는다
    out = pathlib.Path(a.outdir); out.mkdir(parents=True, exist_ok=True)

    data_ref = max(d["monthly_volume"][-1]["month"] for d in data)
    end = data_ref if a.include_last_month else shift(data_ref, 1)
    qy, qm = int(a.queried_at[:4]), int(a.queried_at[5:7])
    lag = (qy - int(data_ref[:4])) * 12 + (qm - int(data_ref[5:7]))

    # ── 중복 묶음: 2단 행 + (있으면) 1단 행, 키워드당 한 번 (2단 행 우선 — 시계열로 중복을 더 정확히 가린다)
    s2kw = {d["keyword"] for d in data}
    rows, seen = list(data), set(s2kw)
    for path in a.stage1:
        for d in load(path):
            if d.get("keyword") and d["keyword"] not in seen:
                rows.append(d); seen.add(d["keyword"])
    info, comps = components(cfg, rows)
    live = {cid: c for cid, c in comps.items() if not c["excluded"]}
    for c in live.values():
        c["theme"] = cfg.theme(c["rep"], c["bucket"])
        c["alone"] = cfg.is_alone(c["canonical"])
        # 월 패널 대표: 묶음 안에서 2단에 있는 가장 큰 행 (보통 묶음 대표와 같다)
        c["panel"] = next((k for k in c["members"] if k in s2kw), None)
        # 네이버 분리 변형(bench_common ④): 2단에 있으면 월 패널에 네이버 볼륨만 더한다(구글은 대표에서 한 번)
        c["panel_nv"] = [k for k in c.get("nv_split", []) if k in s2kw and k != c["panel"]] if c["panel"] else []
        c["panel_nv_missing"] = [k for k in c.get("nv_split", []) if k not in s2kw]

    by_kw = {d["keyword"]: d for d in data}
    used = [by_kw[c["panel"]] for c in sorted(live.values(), key=lambda c: c["rep"]) if c["panel"]]
    panel_of = {c["panel"]: c for c in live.values() if c["panel"]}

    map_rows = []
    for kw, i in sorted(info.items(), key=lambda x: (-x[1]["vol"], x[0])):
        c = comps[i["comp"]]
        d = i["row"]
        map_rows.append({
            "keyword": kw, "canonical": i["canonical"], "bucket": i["bucket"],
            "theme_primary": "" if i["excluded"] else cfg.theme(kw, i["bucket"]),
            "stage": "2단" if kw in s2kw else "1단",
            "representative": int(kw in panel_of), "component_rep": c["rep"],
            "dup_basis": i["dup_basis"], "brand_alone": int(cfg.is_alone(i["canonical"])),
            "volume_total": i["vol"], "group_rows": len(c["members"]),
            "group_volume_lo": c["lo"], "group_volume_hi": c["hi"], "excluded": i["excluded"],
            "api_volume_trend_do_not_use": (d.get("ads_metrics") or {}).get("volume_trend")})

    # ── 월 시리즈: 버킷·테마별 합, 결측은 None
    need = [shift(end, i) for i in range(a.months + 14, -1, -1)]   # 전년 비교·3개월 창 여유분
    vol = defaultdict(dict)       # vol[bucket][month] = 합 또는 None
    alone = defaultdict(lambda: defaultdict(int))
    miss = defaultdict(lambda: defaultdict(int))
    miss_kw = defaultdict(set)
    tvol = defaultdict(dict)      # tvol[(theme, bucket)][month]
    kn = defaultdict(int)
    def month_value(d, m, key="total"):
        """d의 m월 값 — 결측(nv_code · 키 없음)은 None, 이력 시작 이전은 --pre-history-zero면 0."""
        r = {x["month"]: x for x in d["monthly_volume"]}.get(m)
        if r is not None and key in r:
            return r[key]
        if r is None and m < d["monthly_volume"][0]["month"] and a.pre_history_zero:
            return 0
        return None

    for d in used:
        c = panel_of[d["keyword"]]
        b, t = c["bucket"], c["theme"]; kn[b] += 1 + len(c["panel_nv"])
        parts = [(d, "total", c["alone"])] + [(by_kw[k], "nv", cfg.is_alone(info[k]["canonical"])) for k in c["panel_nv"]]
        for m in need:
            v = 0
            for dd, key, is_alone in parts:
                x = month_value(dd, m, key)
                if x is None:
                    miss_kw[dd["keyword"]].add(m); v = None; break
                v += x
                if is_alone:
                    alone[b][m] += x
            for key, store in ((b, vol), ((t, b), tvol)):
                prev = store[key].get(m, 0)
                store[key][m] = None if (prev is None or v is None) else prev + v
            if v is None:
                miss[b][m] += 1

    buckets = [b for b in cfg.brands] + [MULTI, NONBRAND]
    brands = cfg.brands

    def complete(m):
        return all(vol[b].get(m, 0) is not None for b in buckets)

    def sov(b, m, brandset=True):
        if not complete(m):
            return None
        den = sum(vol[x].get(m, 0) for x in (brands if brandset else buckets))
        return vol[b].get(m, 0) / den if den else None

    def yoy3(b, m):
        cur = [vol[b].get(shift(m, i), 0) for i in range(3)]
        prv = [vol[b].get(shift(m, i + 12), 0) for i in range(3)]
        if None in cur or None in prv:
            return None, 0
        if sum(prv) < a.gate:
            return None, 1
        return sum(cur) / sum(prv) - 1, 0

    months = need[-a.months:]
    rows_out = []
    for m in months:
        for b in buckets:
            s = sov(b, m) if b in brands else None
            sp, sy = (sov(b, shift(m, 1)) if b in brands else None), (sov(b, shift(m, 12)) if b in brands else None)
            y, gated = yoy3(b, m)
            bv = vol[b].get(m, 0)
            rows_out.append({
                "month": m, "brand": b,
                "bucket_type": "brand" if b in brands else ("multi" if b == MULTI else "nonbrand"),
                "is_focal": int(b == cfg.focal), "keywords_n": kn.get(b, 0),
                "volume": "" if bv is None else bv,
                "missing_keywords": miss[b].get(m, 0), "month_complete": int(complete(m)),
                "series_basis": SERIES_BASIS,
                "sov_brandset": rnd(s), "sov_category": rnd(sov(b, m, brandset=False)),
                "sov_brandset_delta_prev_month_pp": rnd((s - sp) * 100 if s is not None and sp is not None else None, 2),
                "sov_brandset_delta_prev_year_pp": rnd((s - sy) * 100 if s is not None and sy is not None else None, 2),
                "yoy_3m": rnd(y), "yoy_gated_out": gated,
                "brand_alone_share": rnd(alone[b][m] / bv if (b in brands and bv) else None),
                "data_ref_month": data_ref, "analysis_end_month": end,
                "last_month_included": int(a.include_last_month),
            })

    theme_names = [n for n, _ in cfg.themes]
    msg = []
    for m in months:
        for t in theme_names:
            tot = [tvol[(t, b)].get(m, 0) for b in buckets]
            tt = None if None in tot else sum(tot)
            for b in buckets:
                v = tvol[(t, b)].get(m, 0)
                bv = vol[b].get(m, 0)
                msg.append({"month": m, "theme": t, "brand": b, "volume": "" if v is None else v,
                            "share_within_theme": rnd(v / tt if (v is not None and tt) else None),
                            "share_within_brand": rnd(v / bv if (v is not None and bv) else None),
                            "data_ref_month": data_ref})

    # ── 테마 커버리지 · 메시지 칸 상태 (P0: 2단에 없는 칸을 0으로 단정하지 않는다)
    has_s1 = bool(a.stage1)
    tcov, cell = [], {}
    for t in theme_names:
        for b in buckets:
            cs = [c for c in live.values() if c["theme"] == t and c["bucket"] == b]
            s1 = cs                                   # 1단·2단 응답 전체 (묶음 기준)
            s2 = [c for c in cs if c["panel"]]
            v1, v2 = sum(c["lo"] for c in s1), sum(c["lo"] for c in s2)
            if s2:
                st = "2단 조회됨"
            elif not has_s1:
                st = "미확인(--stage1 없음) [데이터 공백]"
            elif not cs:
                st = "1단 응답 없음(미요청·미반환) [데이터 공백]"
            elif v1 > 0:
                st = f"게이트 밖(1단 12개월 {v1:,}건 · 2단 미조회) [데이터 공백]"
            else:
                st = "실제 0(1단 12개월 0건) [실제 데이터]"
            cell[(t, b)] = (st, v1, v2)
            tcov.append({"theme": t, "brand": b, "stage1_groups": len(s1) if has_s1 else "",
                         "stage1_volume_12m_lo": v1 if has_s1 else "", "stage2_groups": len(s2),
                         "stage2_volume_12m_lo": v2,
                         "theme_coverage": rnd(v2 / v1 if (has_s1 and v1) else None, 3),
                         "cell_status": st, "label": "추정"})
    theme_cov = {}
    for t in theme_names:
        v1 = sum(cell[(t, b)][1] for b in buckets); v2 = sum(cell[(t, b)][2] for b in buckets)
        theme_cov[t] = (v2 / v1) if (has_s1 and v1) else None
    weak = [t for t in theme_names if theme_cov[t] is not None and theme_cov[t] < a.min_theme_coverage]

    def write(name, cols, rs):
        p = out / name
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rs)
        return p

    p1 = write(f"benchmark_{a.category}.csv", BENCH_COLS, rows_out)
    p2 = write(f"message_share_{a.category}.csv", MSG_COLS, msg)
    p3 = write(f"keyword_map_{a.category}.csv", MAP_COLS, map_rows)
    p6 = write(f"theme_coverage_{a.category}.csv", THEME_COLS, tcov)

    # ── 12개월 하한·상한 버킷 집계 · 순위 (판정은 하한, 상한에서 뒤집히면 표시)
    BL, BH, BG, BR, BA = (defaultdict(int) for _ in range(5))
    for c in live.values():
        b = c["bucket"]
        BL[b] += c["lo"]; BH[b] += c["hi"]; BG[b] += 1; BR[b] += len(c["members"])
        if c["alone"]:
            BA[b] += c["lo"]
    den = {"lo": (sum(BL[b] for b in brands), sum(BL[b] for b in buckets)),
           "hi": (sum(BH[b] for b in brands), sum(BH[b] for b in buckets))}
    rank = {k: {b: 1 + sum(1 for x in brands if V[x] > V[b]) for b in brands}
            for k, V in (("lo", BL), ("hi", BH))}
    flips = [b for b in brands if rank["lo"][b] != rank["hi"][b]]
    src = "1단 ads_metrics + 2단 all" if a.stage1 else "2단 all 응답의 행만"
    bounds = []
    for b in buckets:
        isb = b in brands
        bounds.append({
            "brand": b, "bucket_type": "brand" if isb else ("multi" if b == MULTI else "nonbrand"),
            "is_focal": int(b == cfg.focal), "groups_n": BG[b], "rows_n": BR[b],
            "volume_12m_lo": BL[b], "volume_12m_hi": BH[b],
            "sov_brandset_12m_lo": rnd(BL[b] / den["lo"][0] if isb and den["lo"][0] else None),
            "sov_brandset_12m_hi": rnd(BH[b] / den["hi"][0] if isb and den["hi"][0] else None),
            "sov_category_12m_lo": rnd(BL[b] / den["lo"][1] if den["lo"][1] else None),
            "sov_category_12m_hi": rnd(BH[b] / den["hi"][1] if den["hi"][1] else None),
            "brand_alone_12m_lo": BA[b] if isb else "",
            "brand_alone_share_lo": rnd(BA[b] / BL[b] if isb and BL[b] else None),
            "rank_lo": rank["lo"][b] if isb else "", "rank_hi": rank["hi"][b] if isb else "",
            "rank_flip": ("yes" if b in flips else "no") if isb else "",
            "bounds_source": src, "label": "추정",
        })
    p5 = write(f"bounds_{a.category}.csv", BOUND_COLS, bounds)

    # ── 갭 경고
    at = {r["brand"]: r for r in rows_out if r["month"] == end}
    L = [f"# 경쟁 벤치마크 갭 경고 — {a.category} · 분석 월 {end}", "",
         f"- 데이터 기준월 {data_ref} / 조회일 {a.queried_at} / 지연 {lag}개월 [실제 데이터] "
         "— 기준월은 응답에서 읽는다(고정값이 아니다)",
         f"- 마지막 달({data_ref}): " + ("포함 — 미완결 가능성 있음, 잠정 [가정]" if a.include_last_month
                                      else "제외 — 미완결 가능성 때문에 분석 월을 한 달 앞당김 [가정]"),
         f"- 기저 게이트: 전년 같은 3개월 합 {a.gate:,}건 미만 브랜드는 yoy_3m 비움 [가정]",
         f"- 경고 임계: 점유율 ±{a.sov_pp}%p · 전년 동기 ±{a.yoy_pct:.0%} · 테마 커버리지 {a.min_theme_coverage:.0%} [가정]",
         f"- 이력 시작 이전 달: {'0으로 간주 [가정]' if a.pre_history_zero else '결측(계산 불가) [데이터 공백]'}",
         f"- 집계 행: 2단 응답 {len(data)}행 → 월 패널 대표 {len(used)}행 (같은 검색의 중복·표기변형·구글 지표 공유 "
         "묶음마다 1행 · 제외 규칙 반영) [추정]",
         "- 점유율은 검색량 기준 근사이며 판매 점유율이 아니다. 검색량 단위는 건(사람 수 아님)", ""]
    if not complete(end):
        L += [f"**{end}은 결측 월이 있어 점유율 계산 불가 [데이터 공백]** — "
              "missing_keywords 컬럼으로 원인 키워드를 확인할 것", ""]
    L += ["## 브랜드 점유율 (분석 월)", "",
          "표의 수치는 monthly_volume 직접 계산이다 [실제 데이터]. 비어 있으면 결측·게이트 미달 [데이터 공백]. "
          "브랜드명 단독 비중은 브랜드명·별칭만 검색한 비중이다 [추정 — 별칭 사전 기반].", "",
          "| 브랜드 | 검색량(건) | 브랜드 합 대비 | 카테고리 대비 | 전월 대비 %p | 전년 동월 대비 %p | 전년 동기 3개월 | 브랜드명 단독 비중 |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    fmt = lambda x, pct=True: "계산 불가" if x == "" else (f"{x:.1%}" if pct else f"{x:+.2f}")
    for b in buckets:
        r = at[b]
        y = "게이트 미달" if r["yoy_gated_out"] else fmt(r["yoy_3m"])
        name = f"**{b}** (관점)" if r["is_focal"] else b
        L.append(f"| {name} | {r['volume'] if r['volume'] != '' else '결측'} | "
                 f"{fmt(r['sov_brandset']) if b in brands else '—'} | {fmt(r['sov_category'])} | "
                 f"{fmt(r['sov_brandset_delta_prev_month_pp'], False) if b in brands else '—'} | "
                 f"{fmt(r['sov_brandset_delta_prev_year_pp'], False) if b in brands else '—'} | {y} | "
                 f"{fmt(r['brand_alone_share']) if b in brands else '—'} |")
    nb = at[NONBRAND]["sov_category"]
    heavy = [b for b in brands if isinstance(at[b]["brand_alone_share"], float) and at[b]["brand_alone_share"] >= ALONE_WARN]
    L += ["", f"- 논브랜드 검색 비중(카테고리 대비): {fmt(nb)} [실제 데이터] — "
          "브랜드 합 대비 점유율만 보면 카테고리 일반 수요가 빠진다"]
    if heavy:
        L.append(f"- ⚠️ 브랜드명 단독 비중이 {ALONE_WARN:.0%} 이상인 브랜드: "
                 + ", ".join(f"{b} {at[b]['brand_alone_share']:.1%}" for b in heavy)
                 + f" [추정] — 이 브랜드들의 점유율은 «{a.category} 점유율»이 아니라 브랜드명 검색(인지도·다른 카테고리 수요 포함) "
                 "점유에 가깝다. 카테고리 안의 경쟁은 수식어·모델 검색(message_share)으로 본다")
    L += [f"- 위 표와 아래 경고는 묶음 대표 시계열({SERIES_BASIS})이다 [추정] — 2단은 대표 행(과 네이버 분리 변형)만 "
          "조회하므로 상한 기준 월 시계열은 없다 [데이터 공백]"]
    sp_in = sorted(k for c in live.values() for k in c["panel_nv"])
    sp_out = sorted(((k, info[k]["nv"] or 0, c["bucket"]) for c in live.values() for k in c["panel_nv_missing"]),
                    key=lambda x: -x[1])
    if sp_in:
        L.append(f"- 네이버 분리 변형 {len(sp_in)}행(구글 지표는 대표와 같고 네이버 볼륨이 다름 — 네이버에서는 다른 검색)은 "
                 "월 패널에 네이버 볼륨만 더했다 [추정]: " + ", ".join(sp_in[:10]))
    if sp_out:
        L.append(f"- ⚠️ 네이버 분리 변형 {len(sp_out)}행이 2단에 없어 월 패널이 과소집계된다 [데이터 공백]: "
                 + ", ".join(f"{k}({b} 네이버 12개월 {v:,})" for k, v, b in sp_out[:10])
                 + " — plan_queries stage2를 다시 돌려 2단에 넣을 것")
    L.append("")
    rng = lambda lo, hi: "계산 불가" if lo == "" else f"하한 기준 {lo:.1%} / 상한 기준 {hi:.1%}"
    L += ["## 12개월 검색량·점유율 범위 (중복 묶음 하한~상한)", "",
          "하한 = 묶음 최댓값 + 네이버 분리 변형의 네이버 볼륨(변형이 같은 검색의 중복일 때) · "
          "상한 = 구글 공유 1회 + 네이버 합(서로 다른 검색일 때). "
          "시계열·지표가 같은 행과 구글 지표가 같은 행은 같은 검색으로 보고 한 번만 셌다(계약 T5 · 확장). "
          "점유율은 하한·상한 각자의 분모로 계산해서 «하한 기준 / 상한 기준»으로 적는다 — 범위가 아니다 [추정]. "
          f"출처: {src}", "",
          "| 브랜드 | 12개월 검색량(건) | 브랜드 합 대비 | 카테고리 대비 | 브랜드명 단독 비중(하한) | 순위(하한/상한) |",
          "|---|---:|---:|---:|---:|---:|"]
    for r in bounds:
        isb = r["bucket_type"] == "brand"
        rk = f"{r['rank_lo']} / {r['rank_hi']}" + (" ⚠️ 뒤집힘" if r["rank_flip"] == "yes" else "") if isb else "—"
        L.append(f"| {r['brand']} | 하한 {r['volume_12m_lo']:,} ~ 상한 {r['volume_12m_hi']:,} | "
                 f"{rng(r['sov_brandset_12m_lo'], r['sov_brandset_12m_hi']) if isb else '—'} | "
                 f"{rng(r['sov_category_12m_lo'], r['sov_category_12m_hi'])} | "
                 f"{fmt(r['brand_alone_share_lo']) if isb else '—'} | {rk} |")
    L += ["", "- 순위는 하한 기준이다 [가정]. " + (
        "상한에서 순위가 뒤집히는 브랜드가 있다 — 순위를 단정하지 말고 두 값을 함께 적는다" if flips
        else "하한·상한에서 브랜드 순위가 같다"), ""]

    L += ["## 메시지 분석 범위 (테마 커버리지)", "",
          "테마 커버리지 = 그 수식어 테마에 배정된 묶음 중 2단(월 시계열)에 들어간 12개월 하한 ÷ 1단 전체 [추정]. "
          "2단 게이트는 볼륨으로 자르므로 수식어 검색이 빠지기 쉽다 — 낮으면 메시지 구성은 [데이터 공백]이다.", "",
          f"| 테마 | 커버리지 | 관점 브랜드({cfg.focal or '미지정'}) 칸 |", "|---|---:|---|"]
    for t in theme_names:
        cv = theme_cov[t]
        fst = cell[(t, cfg.focal)][0] if cfg.focal else "—"
        L.append(f"| {t} | {'미확인' if cv is None else f'{cv:.1%}'}"
                 f"{' ⚠️ 기준 미만' if t in weak else ''} | {fst} |")
    L.append("")
    if not has_s1:
        L += ["- `--stage1`이 없어 테마 커버리지와 «게이트 밖 vs 실제 0»을 가를 수 없다 — 메시지 공백 경고를 내지 않는다 "
              "[데이터 공백]", ""]

    alerts, held = [], []
    for b in brands:
        r = at[b]
        for col, lab in (("sov_brandset_delta_prev_year_pp", "전년 동월 대비"),
                         ("sov_brandset_delta_prev_month_pp", "전월 대비")):
            if r[col] != "" and abs(r[col]) >= a.sov_pp:
                alerts.append(f"- {b}: 점유율 {lab} {r[col]:+.2f}%p [실제 데이터 · 임계는 가정]")
        if r["yoy_3m"] != "" and abs(r["yoy_3m"]) >= a.yoy_pct:
            alerts.append(f"- {b}: 전년 동기 3개월 검색량 {r['yoy_3m']:+.1%} [실제 데이터]")
    mm = [x for x in msg if x["month"] == end]
    if cfg.focal:
        for t in theme_names:
            comp_ = [x for x in mm if x["theme"] == t and x["brand"] in brands and x["brand"] != cfg.focal
                     and x["volume"] not in ("", 0)]
            if not comp_:
                continue
            top = max(comp_, key=lambda x: x["volume"])
            st, v1, _ = cell[(t, cfg.focal)]
            fv = next(x["volume"] for x in mm if x["theme"] == t and x["brand"] == cfg.focal)
            if st == "2단 조회됨":
                if fv == 0:
                    alerts.append(f"- 메시지 공백: «{t}» 수식어 검색에 {cfg.focal} {end} 0건, "
                                  f"{top['brand']} {top['volume']}건 [실제 데이터 · 수식어 분류는 추정]")
            elif st.startswith("실제 0"):
                alerts.append(f"- 메시지 공백: «{t}» 수식어 검색에 {cfg.focal} 1단 12개월 0건, "
                              f"{top['brand']} {end} {top['volume']}건 [실제 데이터 · 수식어 분류는 추정]")
            else:
                held.append(f"- «{t}» × {cfg.focal}: {st} — 공백 여부 판정 불가, 경고하지 않는다")
    prev_y = shift(end, 12)
    for t in theme_names:
        for b in brands:
            cur = next(x for x in mm if x["theme"] == t and x["brand"] == b)["share_within_theme"]
            old = next((x for x in msg if x["month"] == prev_y and x["theme"] == t and x["brand"] == b), None)
            old = old["share_within_theme"] if old else ""
            if cur != "" and old != "" and abs(cur - old) * 100 >= a.sov_pp and b != cfg.focal:
                line = (f"- 메시지 이동: {b}의 «{t}» 내 점유 {old:.1%} → {cur:.1%} (전년 동월 대비) "
                        "[실제 데이터 · 수식어 분류는 추정]")
                if t in weak:
                    held.append("- (보류 — 테마 커버리지 미달) " + line[2:])
                else:
                    alerts.append(line)
    for b in flips:
        alerts.append(f"- 순위 뒤집힘: {b} 12개월 검색량 순위 하한 {rank['lo'][b]}위 → 상한 {rank['hi'][b]}위 "
                      "— 변형이 같은 검색인지에 따라 순위가 달라진다 [추정]")
    L += ["## 경고", "", f"점유율·수요 경고는 월 패널({SERIES_BASIS}) 기준이다 — 상한 기준으로는 판정할 수 없다 "
          "[데이터 공백]. 순위 뒤집힘은 12개월 하한·상한 대조다. 메시지 공백은 관점 브랜드 칸이 2단에 있거나 "
          "1단에서 실제 0일 때만 낸다.", ""] + (alerts or ["- 임계를 넘은 변화 없음"])
    if held:
        L += ["", "### 판정 보류 (데이터 공백 — 경고 아님)", ""] + held
    inc = sum(1 for m in months if not complete(m))
    mk = sorted(((k, len(ms & set(months))) for k, ms in miss_kw.items() if ms & set(months)), key=lambda x: -x[1])
    L += ["", "## 데이터 공백", "",
          f"- 산출 {len(months)}개월 중 결측으로 점유율 계산 불가: {inc}개월 [데이터 공백]"]
    if mk:
        L.append("- 결측 월 원인 키워드(산출 기간 결측 월 수): "
                 + ", ".join(f"{k}({n})" for k, n in mk[:8])
                 + " — 묶음 대표 행의 결측이다. 비대표 변형(오타·구글 공유 행)은 월 패널에 넣지 않았다 [데이터 공백]")
    for kws in alias_suspects(info):
        L.append("- 별칭 누락 의심(구글 지표가 같은데 버킷이 다름 · 합치지 않음): "
                 + " · ".join(f"{k}({info[k]['bucket']} {info[k]['vol']:,})" for k in kws)
                 + " — brand_aliases 보강 후 다시 돌릴 것 [추정]")
    L += [f"- 비교(복수 브랜드) 키워드는 어느 브랜드에도 배분하지 않고 별도 버킷으로 둔다 "
          f"(분석 월 {at[MULTI]['volume'] or 0}건) [가정]",
          f"- 게이트 미달로 yoy_3m을 비운 브랜드: "
          f"{', '.join(b for b in brands if at[b]['yoy_gated_out']) or '없음'} [데이터 공백]",
          f"- 테마 커버리지 미달 테마: {', '.join(weak) or '없음'} [데이터 공백]",
          "- API volume_trend는 판정에 쓰지 않았다 (keyword_map의 참고 컬럼으로만 보존)", "",
          "## 해석 한계", "",
          "- 월 그레인·기준월이 조회일보다 늦다(지연은 응답에서 읽는다) — 주간·실시간 모니터링이 아니다",
          "- 검색은 관심 신호이지 구매가 아니다. 점유율 변화의 원인(광고·출시·이슈)은 이 산출물이 말하지 않는다",
          "- 인과 단정 없이 동향으로만 서술한다"]
    p4 = out / f"gap_alert_{a.category}.md"
    p4.write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"데이터 기준월 {data_ref} · 조회일 {a.queried_at} · 지연 {lag}개월")
    print(f"마지막 달 {'포함(잠정)' if a.include_last_month else '제외'} → 분석 월 {end}")
    print(f"2단 응답 {len(data)}행 → 월 패널 대표 {len(used)}행 · 결측으로 계산 불가 {inc}/{len(months)}개월")
    print(f"12개월 브랜드 합: 하한 {den['lo'][0]:,} ~ 상한 {den['hi'][0]:,}건 [추정] · 순위 뒤집힘 "
          + (", ".join(f"{b} {rank['lo'][b]}→{rank['hi'][b]}위" for b in flips) if flips else "없음"))
    print("브랜드명 단독 비중(하한): " + " · ".join(
        f"{r['brand']} {r['brand_alone_share_lo']:.1%}" for r in bounds if r["brand_alone_share_lo"] != ""))
    print("테마 커버리지: " + " · ".join(
        f"{t} {'미확인' if theme_cov[t] is None else f'{theme_cov[t]:.1%}'}" for t in theme_names))
    print(f"월 패널은 {SERIES_BASIS} — 상한 기준 월 시계열 없음")
    print(f"경고 {len(alerts)}건 · 판정 보류 {len(held)}건")
    for p in (p1, p2, p3, p5, p6, p4):
        print(f"→ {p}")


if __name__ == "__main__":
    main()
