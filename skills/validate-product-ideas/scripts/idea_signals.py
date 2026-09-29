#!/usr/bin/env python3
"""가설 수요 신호 카드 — 층별 수요 존재·전년 대비 방향·브랜드 잠김을 집계한다 (SKILL.md STEP 6).

이 스크립트는 validate-product-ideas 고유 스크립트다(공유 사본이 아니다).

입력은 공유 스크립트의 산출물과 저장된 응답이다 — API를 부르지 않는다:
  --universe       build_universe.py 산출 CSV (keyword·canonical·dup_of·brand·theme_primary·volume_total …)
                   theme_primary 가 곧 «층»이다. 층 사전은 themes.json 스키마로 쓴 layers.json.
  --trend          trend_yoy.py 산출 CSV (2단 조회 `all` 대상만 행이 있다)
  --stage2-metrics 2단 `all` 응답 원본 — 게이트 창을 판정 창과 맞추고, 시계열이 같은 행을 찾는다
  --layers         층 사전 — exclude(동음이의·무관 검색) · proxies · reviewed · 변형치환
  --seed-check     prepare_stages.py seeds 산출 — 죽은 시드 목록
  --expansion      intent_finder 호출별 응답 파일@요청limit (여러 번) — 절단 기록·프록시 반환 추적

설계 원칙 (데이터 계약 §3):
  · 층 사전 `exclude`에 걸린 키워드(동음이의·무관 검색)는 집계에서 뺀다. 뺀 건수·볼륨은 카드에 남는다.
  · 프록시 시드(`proxies`)와 그 확장 반환 중 `reviewed`에 없는 것은 «미검토 프록시»다. 층 하한 볼륨의
    절반 이상이거나 방향 판정 그룹의 과반이면 그 층에 `proxy_flag`가 찍히고 카드가 인용 금지를 경고한다.
  · 시계열이 완전히 같은 행(계약 T5 ①)은 같은 검색의 중복이다 — canonical이 달라도(어순·복수형) 한 번만
    센다(`series_dedup.dedup_map`). 1단 지표 일치(build_universe `dup_of`)도 같다.
    그 밖의 표기변형 그룹(같은 canonical)은 **하한(그룹 내 최댓값)과 상한(그룹 내 합)**을 함께 낸다(T5 ②).
    어순 변형이 시계열까지 다르면 별개 검색으로 두고 그 쌍 수를 카드에 적는다. 모두 `[추정]`.
  · 그룹의 전년 대비는 2단 조회 대표 행 하나에서 온다 — 하한 기준값이다. 상한 시계열은 만들지 않는다.
  · 전년 대비는 기본적으로 **마지막 달을 뺀 창**(yoy_ex_last_month)을 쓴다. 게이트(T4)도 **같은 창**의
    전년 동기 3개월 합으로 판정한다 — 판정 창과 게이트 창이 다르면 기저가 작은 키워드가 게이트를 통과한다.
  · 비교 창에 결측 월이 있으면 «계산 불가»다. 0으로 채우지 않는다.
  · `volume_trend` 필드는 읽지 않는다.
  · 확장 호출 중 하나라도 반환 수가 limit에 붙었으면 층 볼륨은 **하한**이다.

    python3 idea_signals.py --universe keyword_universe.csv --trend trend.csv \\
        --stage2-metrics metrics_stage2.json --layers layers.json --seed-check seed_check.json \\
        --expansion "if_저당아이스크림.json@50" --queried-at 2026-09-28 \\
        --idea low-sugar-icecream-kr -o layer_signals.csv \\
        --brand-out brand_share.csv --card idea_signal_card.md
"""
import argparse, csv, json, pathlib, statistics, sys, unicodedata
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import apply_rules, dedup_map  # noqa: E402  같은 검색의 중복 (계약 T5 ①)
from prepare_stages import Layers, expansion_map, load_data  # noqa: E402

LAYER_COLUMNS = [
    "layer", "groups", "keywords", "volume_lo", "volume_hi",
    "nonbrand_volume_lo", "nonbrand_volume_hi", "nonbrand_share_lo", "nonbrand_share_hi",
    "rank_lo", "rank_hi", "rank_flip",
    "brand_groups", "yoy_eligible_groups", "yoy_median", "rising_share",
    "gated_out_groups", "not_computable_groups", "not_in_stage2_groups",
    "excluded_keywords", "excluded_volume", "same_search_dup_rows",
    "unreviewed_proxy_volume_lo", "unreviewed_proxy_share_lo",
    "demand_status", "direction", "proxy_flag", "expansion_truncated",
]
BRAND_COLUMNS = ["brand", "groups", "volume_lo", "volume_hi",
                 "share_of_brand_volume_lo", "share_of_brand_volume_hi",
                 "share_of_total_volume_lo", "share_of_total_volume_hi",
                 "rank_lo", "rank_hi", "rank_flip"]
PROXY_SHARE = 0.5   # [가정] 층 하한 볼륨 중 미검토 프록시 비율이 이 이상이면 인용 금지 표시


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def month_lag(ref, queried):
    return (int(queried[:4]) - int(ref[:4])) * 12 + (int(queried[5:7]) - int(ref[5:7]))


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def window(end, size=3):
    return [shift(end, i) for i in range(size - 1, -1, -1)]


def gate_bases(rows, include_last):
    """키워드 → 판정 창과 **같은 창**의 전년 동기 3개월 합 (결측 월이 있으면 None). trend_yoy.py와 같은 기준월."""
    rows = [d for d in rows if isinstance(d, dict) and d.get("monthly_volume")]
    if not rows:
        return {}, ""
    ref = max(m["month"] for d in rows for m in d["monthly_volume"] if isinstance(m, dict))
    end = ref if include_last else shift(ref, 1)
    prev = [shift(x, 12) for x in window(end)]
    out = {}
    for d in rows:
        s = {m["month"]: m for m in d["monthly_volume"] if isinstance(m, dict)}
        vals = [s.get(x, {}).get("total") for x in prev]
        out[d["keyword"]] = None if any(v is None for v in vals) else sum(vals)
    return out, f"{prev[0]}~{prev[-1]}"


def order_key(kw, rules):
    """어순 변형 탐지 키 — 토큰을 정렬한다(띄어쓰기 변형은 canonical이 이미 묶는다)."""
    s = apply_rules(unicodedata.normalize("NFKC", kw).lower(), rules)
    return tuple(sorted(s.split()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True, help="build_universe.py 산출 CSV")
    ap.add_argument("--trend", required=True, help="trend_yoy.py 산출 CSV (2단 조회 대상)")
    ap.add_argument("--stage2-metrics", required=True,
                    help="2단 all 응답 원본 — 게이트 창 정렬(T4)·같은 검색 중복 판정(T5 ①)")
    ap.add_argument("--layers", help="층 사전 — exclude·proxies·reviewed (없으면 제외·프록시 검사 생략 경고)")
    ap.add_argument("--seed-check", help="prepare_stages.py seeds 산출 — 죽은 시드 목록")
    ap.add_argument("--expansion", action="append", default=[],
                    help="intent_finder 호출별 응답 파일@요청limit — 호출마다 한 번씩")
    ap.add_argument("--gate", type=int, default=300,
                    help="방향 판정 최소 전년 동기 3개월 합 (기본 300 — 가설 키워드는 작다)")
    ap.add_argument("--min-groups", type=int, default=3,
                    help="층 방향 판정에 필요한 최소 판정 가능 그룹 수 (기본 3)")
    ap.add_argument("--include-last-month", action="store_true",
                    help="마지막 달 포함 전년 대비(yoy) 사용 — 카드에 명시된다")
    ap.add_argument("--queried-at", required=True, help="조회일 YYYY-MM-DD")
    ap.add_argument("--idea", default="idea")
    ap.add_argument("-o", "--out", default="layer_signals.csv")
    ap.add_argument("--brand-out", default="brand_share.csv")
    ap.add_argument("--card", help="수요 신호 카드 마크다운 경로 (주면 쓴다)")
    a = ap.parse_args()

    L = Layers(a.layers)
    uni_all = list(csv.DictReader(open(a.universe, encoding="utf-8-sig")))
    if not uni_all:
        sys.exit("유니버스가 비어 있다")
    trend = {r["keyword"]: r for r in csv.DictReader(open(a.trend, encoding="utf-8-sig"))}
    yoy_col = "yoy" if a.include_last_month else "yoy_ex_last_month"
    s2rows = load_data(a.stage2_metrics)
    base_of, prev_win = gate_bases(s2rows, a.include_last_month)

    # ── 제외(동음이의·무관 검색) — 층 사전 exclude
    excl = defaultdict(list)
    uni = []
    for r in uni_all:
        why = L.excluded(r["keyword"])
        if why:
            excl[r.get("theme_primary") or "(미분류)"].append((r, why))
        else:
            uni.append(r)
    kept = {r["keyword"] for r in uni}

    # ── 같은 검색의 중복 (T5 ①): 1단 지표 일치(dup_of) + 2단 시계열 일치 → 한 번만 센다
    dm2 = dedup_map(s2rows, L.rules)
    dup = {}
    for r in uni:
        kw = r["keyword"]
        rep = r.get("dup_of") or ""
        if not (rep and rep in kept):
            rep = dm2.get(kw, {}).get("rep", kw)
        if rep != kw and rep in kept:
            dup[kw] = rep
    dup_by_layer = defaultdict(int)
    for r in uni:
        if r["keyword"] in dup:
            dup_by_layer[r.get("theme_primary") or "(미분류)"] += 1

    # ── T7 절단 기록: 호출별 요청 limit vs 반환 수
    calls = []
    for spec in a.expansion:
        path, _, lim = spec.rpartition("@")
        if not path:
            path, lim = spec, ""
        n = len(load_data(path))
        limit = int(lim) if lim else None
        calls.append({"file": path, "limit": limit, "returned": n,
                      "truncated": bool(limit is not None and n >= limit)})
    truncated = any(c["truncated"] for c in calls)

    # ── 프록시: 프록시 시드 + 그 확장 반환 — reviewed에 없으면 미검토
    emap = expansion_map(a.expansion, L) if a.expansion else {}
    proxied = set(L.proxies) | {c for c, seeds in emap.items() if seeds & L.proxies}

    # ── T5 ② 표기변형 그룹: 하한 = 그룹 내 최댓값 · 상한 = 그룹 내 합 (둘 다 [추정])
    groups = defaultdict(list)
    for r in uni:
        if r["keyword"] not in dup:
            groups[r["canonical"]].append(r)
    G = []
    for c, rows in groups.items():
        rep = max(rows, key=lambda r: num(r.get("volume_total")) or 0)
        lo = num(rep.get("volume_total")) or 0
        hi = sum(num(r.get("volume_total")) or 0 for r in rows)
        # 그룹의 전년 대비: 2단 조회에 들어간 행 중 대표(판정 창 기준 전년 기저가 가장 큰 행) — 하한 기준값
        tr = [r["keyword"] for r in rows if r["keyword"] in trend]
        status, yoy = "not_in_stage2", None
        if tr:
            k = max(tr, key=lambda x: base_of.get(x) or 0)
            base, y = base_of.get(k), num(trend[k].get(yoy_col))
            if y is None:
                status = "not_computable"      # 결측 월 또는 전년 0 — 0으로 채우지 않는다
            elif base is None or base < a.gate:
                status = "gated_out"           # 판정 창과 같은 창의 기저로 거른다 (T4)
            else:
                status, yoy = "eligible", y
        brand = next((r["brand"] for r in rows if r.get("brand")), "")
        unrev = any(L.canon(r["keyword"]) in proxied and L.canon(r["keyword"]) not in L.reviewed for r in rows)
        G.append({"canonical": c, "layer": rep.get("theme_primary") or "(미분류)", "rep_kw": rep["keyword"],
                  "keywords": len(rows), "lo": lo, "hi": hi, "brand": brand,
                  "status": status, "yoy": yoy, "unrev_proxy": unrev,
                  "ref": next((trend[k].get("data_ref_month") for k in tr if trend[k].get("data_ref_month")), "")})

    # 어순 변형: 시계열이 같으면 위에서 중복으로 합쳐졌다. 남은 쌍은 별개 검색으로 센다
    ok = defaultdict(list)
    for g in G:
        ok[order_key(g["rep_kw"], L.rules)].append(g["rep_kw"])
    order_pairs = [v for v in ok.values() if len(v) > 1]

    def ranks(items, key):
        """동률은 같은 순위(1부터). 하한·상한 각각 따로 매긴다."""
        vals = sorted({key(x) for x in items}, reverse=True)
        return {id(x): vals.index(key(x)) + 1 for x in items}

    def share(n, d):
        return round(n / d, 3) if d else ""

    total_lo = sum(g["lo"] for g in G)
    total_hi = sum(g["hi"] for g in G)
    names = list(dict.fromkeys([g["layer"] for g in sorted(G, key=lambda g: (-g["lo"], -g["hi"]))]
                               + L.layer_names()))
    layers = []
    for layer in names:
        gs = [g for g in G if g["layer"] == layer]
        lo = sum(g["lo"] for g in gs)
        hi = sum(g["hi"] for g in gs)
        nb_lo = sum(g["lo"] for g in gs if not g["brand"])
        nb_hi = sum(g["hi"] for g in gs if not g["brand"])
        elg = [g for g in gs if g["status"] == "eligible"]
        el = [g["yoy"] for g in elg]
        med = statistics.median(el) if el else None
        rising = sum(1 for y in el if y > 0) / len(el) if el else None
        if len(el) < a.min_groups:
            direction = "판정 불가"
        elif med > 0 and rising >= 0.6:
            direction = "상승"
        elif med < 0 and rising <= 0.4:
            direction = "하락"
        else:
            direction = "혼조"
        up_lo = sum(g["lo"] for g in gs if g["unrev_proxy"])
        flags = []
        if lo and up_lo / lo >= PROXY_SHARE:
            flags.append("볼륨 과반이 미검토 프록시")
        if el and sum(1 for g in elg if g["unrev_proxy"]) * 2 > len(elg):
            flags.append("방향 판정 그룹 과반이 미검토 프록시")
        ex = excl.get(layer, [])
        layers.append({
            "layer": layer, "groups": len(gs), "keywords": sum(g["keywords"] for g in gs),
            "volume_lo": int(lo), "volume_hi": int(hi),
            "nonbrand_volume_lo": int(nb_lo), "nonbrand_volume_hi": int(nb_hi),
            "nonbrand_share_lo": share(nb_lo, lo), "nonbrand_share_hi": share(nb_hi, hi),
            "brand_groups": sum(1 for g in gs if g["brand"]),
            "yoy_eligible_groups": len(el),
            "yoy_median": round(med, 4) if med is not None else "",
            "rising_share": round(rising, 3) if rising is not None else "",
            "gated_out_groups": sum(1 for g in gs if g["status"] == "gated_out"),
            "not_computable_groups": sum(1 for g in gs if g["status"] == "not_computable"),
            "not_in_stage2_groups": sum(1 for g in gs if g["status"] == "not_in_stage2"),
            "excluded_keywords": len(ex),
            "excluded_volume": int(sum(num(r.get("volume_total")) or 0 for r, _ in ex)),
            "same_search_dup_rows": dup_by_layer.get(layer, 0),
            "unreviewed_proxy_volume_lo": int(up_lo),
            "unreviewed_proxy_share_lo": share(up_lo, lo),
            # 하한 > 0 ⇔ 상한 > 0 (볼륨은 음수가 아니다) — 관측 판정은 두 기준에서 같다
            "demand_status": "관측됨" if lo > 0 else "데이터 공백",
            "direction": direction,
            "proxy_flag": " · ".join(flags),
            "expansion_truncated": "하한" if truncated else "",
        })
    layers.sort(key=lambda L_: (-L_["volume_lo"], -L_["volume_hi"]))   # 정렬·순위는 하한 기준
    rl, rh = ranks(layers, lambda x: x["volume_lo"]), ranks(layers, lambda x: x["volume_hi"])
    for x in layers:
        x["rank_lo"], x["rank_hi"] = rl[id(x)], rh[id(x)]
        x["rank_flip"] = "뒤집힘" if x["rank_lo"] != x["rank_hi"] else ""

    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=LAYER_COLUMNS); w.writeheader(); w.writerows(layers)

    bv = defaultdict(lambda: [0, 0, 0])
    for g in G:
        if g["brand"]:
            bv[g["brand"]][0] += 1; bv[g["brand"]][1] += g["lo"]; bv[g["brand"]][2] += g["hi"]
    b_lo = sum(v[1] for v in bv.values())
    b_hi = sum(v[2] for v in bv.values())
    brows = [{"brand": b, "groups": n, "volume_lo": int(lo), "volume_hi": int(hi),
              "share_of_brand_volume_lo": share(lo, b_lo), "share_of_brand_volume_hi": share(hi, b_hi),
              "share_of_total_volume_lo": share(lo, total_lo), "share_of_total_volume_hi": share(hi, total_hi)}
             for b, (n, lo, hi) in sorted(bv.items(), key=lambda kv: (-kv[1][1], -kv[1][2]))]
    rl, rh = ranks(brows, lambda B: B["volume_lo"]), ranks(brows, lambda B: B["volume_hi"])
    for B in brows:
        B["rank_lo"], B["rank_hi"] = rl[id(B)], rh[id(B)]
        B["rank_flip"] = "뒤집힘" if B["rank_lo"] != B["rank_hi"] else ""
    with open(a.brand_out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=BRAND_COLUMNS); w.writeheader(); w.writerows(brows)

    refs = sorted({g["ref"] for g in G if g["ref"]})
    ref = refs[-1] if refs else ""
    lag = month_lag(ref, a.queried_at) if ref else None
    nb_lo = sum(g["lo"] for g in G if not g["brand"])
    nb_hi = sum(g["hi"] for g in G if not g["brand"])
    no_brands = not any(g["brand"] for g in G)
    multi = sum(1 for g in G if g["hi"] != g["lo"])
    layer_flips = [x["layer"] for x in layers if x["rank_flip"]]
    brand_flips = [B["brand"] for B in brows if B["rank_flip"]]
    counted_kw = {r["keyword"] for r in uni if r["keyword"] not in dup}
    t2_rows = [trend[k] for k in counted_kw if k in trend]
    t2_suspect = sorted(r["keyword"] for r in t2_rows if r.get("last_month_flag"))
    n_gated = sum(x["gated_out_groups"] for x in layers)
    n_nc = sum(x["not_computable_groups"] for x in layers)
    n_ns2 = sum(x["not_in_stage2_groups"] for x in layers)
    n_dup = len(dup)
    dup_vol = sum(num(r.get("volume_total")) or 0 for r in uni if r["keyword"] in dup)
    seedchk = json.load(open(a.seed_check, encoding="utf-8")) if a.seed_check else None
    proxy_layers = [x for x in layers if x["proxy_flag"]]

    def pct(x):
        return f"{x:.0%}" if x != "" else "-"

    lines = [
        f"# 수요 신호 카드 — {a.idea}",
        "",
        f"- 데이터 기준월 {ref or '[데이터 공백]'} / 조회일 {a.queried_at} / 지연 "
        f"{f'약 {lag}개월' if lag is not None else '[데이터 공백]'} (기준월은 응답의 최신 월에서 읽었다)",
        f"- 전년 대비 창: {'마지막 달 포함 (yoy) — 잠정' if a.include_last_month else '마지막 달 제외 (yoy_ex_last_month)'}"
        f" · 게이트도 같은 창의 전년 동기({prev_win or '-'}) 3개월 합으로 판정",
        f"- 마지막 달 미완결 의심(T2): 2단 조회 {len(t2_rows)}행 중 {len(t2_suspect)}행"
        + (f" ({', '.join(t2_suspect[:5])}{' …' if len(t2_suspect) > 5 else ''})" if t2_suspect else "")
        + (" — 기본 창이 마지막 달을 빼므로 판정에는 들어가지 않는다" if not a.include_last_month else " — 포함 창이라 잠정"),
        f"- [가정] 방향 판정 게이트: 전년 동기 3개월 합 ≥ {a.gate:,}건 · 층당 판정 가능 그룹 ≥ {a.min_groups}",
        f"- 방향 판정에서 뺀 그룹 (T4·T6): 게이트 미달 {n_gated} · 계산 불가(결측 월) {n_nc} · 2단 조회 밖 {n_ns2} "
        "[데이터 공백]",
        f"- 표기변형 그룹 {len(G)}개 (행 {len(uni_all)}개 · 변형이 2개 이상 걸려 하한≠상한인 그룹 {multi}개) — "
        "볼륨은 하한(그룹 내 최댓값: 변형이 같은 검색의 중복일 때) ~ 상한(그룹 내 합: 서로 다른 검색일 때)이다. "
        "응답은 어느 쪽인지 알려주지 않는다 (계약 T5)",
        f"- [추정] 같은 검색의 중복 {n_dup}행(볼륨 {int(dup_vol):,}건)은 한 번만 셌다 — 시계열 또는 1단 지표가 "
        "완전히 같은 행(어순·복수형 변형 포함)",
        f"- [추정] 어순 변형 {len(order_pairs)}쌍은 시계열이 달라 별개 검색으로 셌다"
        + (f" (예: {' / '.join(order_pairs[0])})" if order_pairs else ""),
        f"- [추정] 그룹 기준 볼륨 합(최근 12개월) 하한 {int(total_lo):,} ~ 상한 {int(total_hi):,}건 · "
        f"논브랜드 하한 {int(nb_lo):,} ~ 상한 {int(nb_hi):,}건 "
        f"(비중 하한 기준 {nb_lo / total_lo:.1%} · 상한 기준 {nb_hi / total_hi:.1%})" if total_lo else
        "- [데이터 공백] 볼륨이 관측된 그룹이 없다 — «시장 없음»이 아니라 «검색 신호 없음»이다",
        "- [추정] 논브랜드 비중은 **가설 시드에서 확장한 키워드 공간 안**의 비중이다. 시드가 논브랜드 표현이라 "
        "브랜드 검색이 덜 들어오므로 높게 나오는 쪽으로 치우친다 — 카테고리 전체가 브랜드에 덜 잠겼다는 뜻이 아니다",
        "- 순위는 하한 기준이다. 전년 대비는 2단 조회 대표 행 하나의 값이라 하한 기준이며, 상한 시계열은 없다",
    ]
    if layer_flips or brand_flips:
        lines.append("- ⚠️ 상한 기준에서 순위가 뒤집힌다 — "
                     + " · ".join(([f"층: {', '.join(layer_flips)}"] if layer_flips else [])
                                  + ([f"브랜드: {', '.join(brand_flips)}"] if brand_flips else []))
                     + " (표기변형을 중복으로 보느냐 별개 검색으로 보느냐에 따라 순서가 달라진다 — 한쪽으로 단정하지 않는다)")
    if no_brands:
        lines.append("- ⚠️ 브랜드 사전이 비어 있다 — 논브랜드 비중이 과대평가된다. 상위 볼륨에서 채울 것")
    if not a.layers:
        lines.append("- ⚠️ 층 사전(--layers)을 주지 않았다 — 동음이의·무관 검색 제외와 프록시 검토 표시가 빠졌다")
    for x in proxy_layers:
        lines.append(f"- ⚠️ {x['layer']} 층은 미검토 프록시가 좌우한다({x['proxy_flag']} · 하한 볼륨의 "
                     f"{pct(x['unreviewed_proxy_share_lo'])}) — 이 층의 볼륨·방향은 인용하지 않는다. "
                     "확장 반환을 검토해 exclude 또는 reviewed에 적고 다시 돌린다")
    if seedchk is not None:
        dead = seedchk.get("dead", [])
        lines += ["", "## 죽은 시드 (1단 존재 확인)", ""]
        if dead:
            lines.append("| 시드 | 층 | 상태 |")
            lines.append("|---|---|---|")
            for d in dead:
                lines.append(f"| {d['seed']} | {d.get('layer', '')} | {d['status']} [데이터 공백] |")
            lines.append("")
            lines.append(f"- 죽은 시드 {len(dead)}개는 **검색 신호 없음 [데이터 공백]**이다. «시장 없음»이 아니다 — "
                         "신조어·표현 차이·B2B일 수 있다. 확장(intent_finder)에 넣지 않았다(1단에서는 과금됐다)")
        else:
            lines.append("- 죽은 시드 없음")
        for l in seedchk.get("silent_layers", []):
            lines.append(f"- {l} 층: 시드 전멸 — **검색 신호 없음 [데이터 공백]**")
        if seedchk.get("respelled"):
            lines.append("- 응답 표기가 요청과 달랐던 시드(canonical 대조로 살아 있음 판정): "
                         + " · ".join(f"{r['seed']} → {', '.join(r['returned_as'])}" for r in seedchk["respelled"]))
    exl = [(r, why) for v in excl.values() for r, why in v]
    if exl:
        lines += ["", "## 제외한 검색 (동음이의·무관 — 층 사전 exclude)", "",
                  "| 키워드 | 볼륨(건) | 사유 |", "|---|---:|---|"]
        for r, why in sorted(exl, key=lambda t: -(num(t[0].get("volume_total")) or 0))[:15]:
            lines.append(f"| {r['keyword']} | {int(num(r.get('volume_total')) or 0):,} | {why} |")
    if calls:
        lines.append("")
        lines.append("## 확장 호출 절단 기록")
        lines.append("")
        lines.append("| 호출 | 요청 limit | 반환 | 판정 |")
        lines.append("|---|---:|---:|---|")
        for c in calls:
            lines.append(f"| {c['file']} | {c['limit'] if c['limit'] is not None else '-'} | {c['returned']} | "
                         f"{'절단 — 볼륨은 하한' if c['truncated'] else '미절단'} |")
    lines += ["", "## 층별 신호", "",
              "| 층 | 그룹 | 볼륨(건) [추정] 하한 ~ 상한 | 순위 하한/상한 | 논브랜드 비중 하한 ~ 상한 | "
              "판정 가능 | 게이트 미달 | 계산 불가 | 전년 대비 중앙값 | 상승 비율 | 방향 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for x in layers:
        med = f"{x['yoy_median']:+.0%}" if x["yoy_median"] != "" else "-"
        rs = f"{x['rising_share']:.0%}" if x["rising_share"] != "" else "-"
        flip = " ⚠️" if x["rank_flip"] else ""
        vol = (f"하한 {x['volume_lo']:,} ~ 상한 {x['volume_hi']:,}" if x["volume_lo"]
               else "검색 신호 없음 [데이터 공백]")
        d = x["direction"] + (" ⚠️ 미검토 프록시 — 인용 금지" if x["proxy_flag"] else "")
        lines.append(f"| {x['layer']} | {x['groups']} | {vol} | "
                     f"{x['rank_lo']}/{x['rank_hi']}{flip} | "
                     f"{pct(x['nonbrand_share_lo'])} ~ {pct(x['nonbrand_share_hi'])} | "
                     f"{x['yoy_eligible_groups']} | {x['gated_out_groups']} | {x['not_computable_groups']} | "
                     f"{med} | {rs} | {d} |")
    if brows:
        lines += ["", "## 브랜드 집중도 (검색량 기준 근사)", "",
                  "| 브랜드 | 그룹 | 볼륨(건) [추정] 하한 ~ 상한 | 순위 하한/상한 | 브랜드 볼륨 중 비중 하한 ~ 상한 |",
                  "|---|---:|---:|---:|---:|"]
        for b in brows[:10]:
            flip = " ⚠️" if b["rank_flip"] else ""
            lines.append(f"| {b['brand']} | {b['groups']} | 하한 {b['volume_lo']:,} ~ 상한 {b['volume_hi']:,} | "
                         f"{b['rank_lo']}/{b['rank_hi']}{flip} | "
                         f"{pct(b['share_of_brand_volume_lo'])} ~ {pct(b['share_of_brand_volume_hi'])} |")
    lines += ["", "## 라벨", "",
              "- [실제 데이터] 행 단위 볼륨·전년 대비 값 (monthly_volume 직접 계산)",
              "- [추정] 층 배정(규칙 사전)·방향 판정·브랜드 집중도 · 표기변형 그룹 하한(최댓값)·상한(합) 볼륨과 "
              "그로부터의 비중·순위(규칙 기반 집계 — 비중은 각자의 분모로 나눴다) · 같은 검색의 중복 판정 · "
              "동음이의 제외(사람 검토 규칙)",
              "- [가정] 게이트·최소 그룹 수·방향 임계(중앙값 부호 + 상승 비율 60%/40%) · 미검토 프록시 임계 50%",
              "- [데이터 공백] 죽은 시드 · 판정 불가 층 · 계산 불가(결측 월) · 게이트 미달 · 2단 조회 밖 그룹",
              "",
              "검색 수요는 관심 신호다. 이 카드는 출시 여부 판정도 판매 예측도 아니다."]
    if a.card:
        open(a.card, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    print(f"기준월 {ref or '?'} · 조회일 {a.queried_at} · 지연 {lag if lag is not None else '?'}개월")
    print(f"전년 대비 창: {'마지막 달 포함' if a.include_last_month else '마지막 달 제외'} · 게이트 {a.gate:,} "
          f"(같은 창 전년 {prev_win})")
    print(f"{len(uni_all)}행 → 제외 {len(exl)} · 같은 검색 중복 {n_dup} → 표기변형 그룹 {len(G)}개 · "
          f"그룹 기준 볼륨 하한 {int(total_lo):,} ~ 상한 {int(total_hi):,} [추정]")
    print(f"마지막 달 의심 {len(t2_suspect)}/{len(t2_rows)} · 게이트 미달 {n_gated} · 계산 불가 {n_nc} · 2단 밖 {n_ns2}")
    if seedchk is not None:
        print(f"죽은 시드 {len(seedchk.get('dead', []))}개 [데이터 공백]")
    if truncated:
        print("⚠️ 확장 호출 중 절단(반환=limit) 있음 — 층 볼륨은 하한이다")
    if no_brands:
        print("⚠️ 브랜드 사전이 비어 있다 — 논브랜드 비중 과대")
    if layer_flips or brand_flips:
        print(f"⚠️ 상한 기준 순위 뒤집힘 — 층 {layer_flips} · 브랜드 {brand_flips}")
    for x in proxy_layers:
        print(f"⚠️ 미검토 프록시가 좌우하는 층: {x['layer']} ({x['proxy_flag']})")
    for x in layers:
        print(f"  {x['layer']:<14}하한 {x['volume_lo']:>9,} ~ 상한 {x['volume_hi']:>9,}  "
              f"순위 {x['rank_lo']}/{x['rank_hi']}{' 뒤집힘' if x['rank_flip'] else ''}  "
              f"판정 {x['yoy_eligible_groups']:>3}  {x['direction']}{'  ⚠️프록시' if x['proxy_flag'] else ''}")
    print(f"\n→ {a.out} · {a.brand_out}" + (f" · {a.card}" if a.card else ""))


if __name__ == "__main__":
    main()
