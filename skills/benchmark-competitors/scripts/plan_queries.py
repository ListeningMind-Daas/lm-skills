#!/usr/bin/env python3
"""2단 조회 계획 — 1단(ads_metrics) 요청 목록 생성과 2단(all) 대상 선별.

비용은 2단 대상 수가 지배한다(all = 10크레딧/kw). 그래서 2단에 넘기기 전에
  ① 중복 묶음(같은 검색의 중복 · 표기변형 · 구글 지표 공유 — bench_common.components)마다 대표 1행만 남기고
  ② 버킷마다 가장 큰 묶음 · 수식어 테마 × 버킷 칸마다 가장 큰 묶음을 먼저 확보한 뒤(테마 예약)
  ③ 나머지를 12개월 볼륨 순으로 채운다(`--min-volume` 이상, `--max-k2`가 있으면 그 수까지).
볼륨만 보고 자르면 수식어 검색(«○○ 할인»·«○○ 사이즈»)이 전부 빠져 메시지 분석이 조용히 무너진다
(2026-09-28 실측: 2단 30행 중 테마 배정 2행). 그래서 테마 예약과 **테마 커버리지**를 함께 보고한다.
버킷 합계는 하한(묶음 최댓값의 합)과 상한(구글 공유 1회 + 네이버 합)을 함께 낸다(계약 T5 · 확장).

    # 1단 요청 목록 (브랜드명·별칭·모델 변형·브랜드×수식어·카테고리어×수식어)
    python3 plan_queries.py combos --config benchmark.json -o stage1_keywords.json

    # 2단 대상 선별 (1단 응답을 입력)
    python3 plan_queries.py stage2 stage1_ads_metrics.json --config benchmark.json \\
        --requested stage1_keywords.json --min-volume 120 --min-coverage 0.95 -o stage2_keywords.json

    # 점검표용 사전 — build_universe.py가 별칭까지 브랜드로 읽게 한다
    python3 plan_queries.py review-config --config benchmark.json -o review_themes.json
"""
import argparse, json, pathlib, sys

sys.dont_write_bytecode = True   # 스킬 폴더에 __pycache__를 남기지 않는다
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from bench_common import Config, alias_suspects, components, load, NONBRAND, MULTI  # noqa: E402


def combos(cfg, out):
    kws = []
    for b in cfg.brands:
        kws += [b] + list(cfg.aliases.get(b, [])) + list(cfg.variants.get(b, []))
        kws += [f"{b} {m}" for m in cfg.probe_modifiers]
    for t in cfg.category_terms:
        kws += [t] + [f"{t} {m}" for m in cfg.probe_modifiers]
    seen, uniq = set(), []
    for k in kws:
        c = cfg.canon(k)
        if c not in seen and not cfg.excluded(c):
            seen.add(c); uniq.append(k)
    json.dump(uniq, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"1단 요청 {len(uniq)}개 (canonical 중복 {len(kws) - len(uniq)}개 제거)")
    print(f"1단 크레딧 = 1 × {len(uniq)} = {len(uniq)}  (keyword_info · data_type=ads_metrics)")
    if not cfg.category_terms:
        print("⚠️ category_terms가 비었다 — 논브랜드 버킷이 비어 sov_category의 분모가 브랜드 합과 같아진다")
    print(f"→ {out}")


def review_config(cfg, out):
    """build_universe.py 점검표용 사전 — brands에 별칭을 넣는다(build_universe는 brand_aliases를 모른다)."""
    raw = {k: v for k, v in cfg.raw.items() if k in ("rules", "themes", "no_match")}
    raw["brands"] = [x for b in cfg.brands for x in [b] + list(cfg.aliases.get(b, []))]
    raw["_note"] = "plan_queries.py review-config 산출 — 점검표 전용. brand 칸에 별칭이 그대로 찍히면 그 별칭의 브랜드다"
    json.dump(raw, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"점검표 사전: 브랜드 {len(cfg.brands)}개 + 별칭 {len(raw['brands']) - len(cfg.brands)}개 → {out}")


def stage2(cfg, a):
    rows = [d for d in load(a.path) if d.get("keyword")]
    info, comps = components(cfg, rows)
    live = [c for c in comps.values() if not c["excluded"]]
    excl = sum(1 for c in comps.values() if c["excluded"])
    theme_names = [n for n, _ in cfg.themes]
    for c in live:
        c["theme"] = cfg.theme(c["rep"], c["bucket"])

    # ── 선별: 버킷 대표 → 테마 예약 → 볼륨 순
    order, why = [], {}

    def push(c, reason):
        if c["rep"] not in why:
            order.append(c); why[c["rep"]] = reason
    buckets = cfg.brands + [MULTI, NONBRAND]
    for b in buckets:
        top = sorted((c for c in live if c["bucket"] == b), key=lambda c: (-c["lo"], c["rep"]))[:1]
        for c in top:
            if c["lo"] > 0:
                push(c, "버킷 대표")
    reserve = []
    for t in theme_names:
        for b in buckets:
            cell = sorted((c for c in live if c["theme"] == t and c["bucket"] == b), key=lambda c: (-c["lo"], c["rep"]))
            reserve += [c for c in cell[: a.theme_reserve] if c["lo"] >= a.theme_min_volume]
    for c in sorted(reserve, key=lambda c: (-c["lo"], c["rep"])):
        push(c, "테마 예약")
    for c in sorted(live, key=lambda c: (-c["lo"], c["rep"])):
        if c["lo"] >= a.min_volume:
            push(c, "볼륨")
    n_reserved = sum(1 for c in order if why[c["rep"]] != "볼륨")
    picked = order[: a.max_k2] if a.max_k2 else order
    cut = order[len(picked):]
    json.dump([c["rep"] for c in picked], open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    pk = {c["rep"] for c in picked}

    print(f"1단 응답 {len(rows)}행 → 중복 묶음 {len(live)}개 (같은 검색의 중복·표기변형·구글 지표 공유 "
          f"{len(rows) - len(comps)}행은 대표 1행만 2단으로) · 제외 규칙 {excl}행")
    if a.requested:
        req = json.load(open(a.requested, encoding="utf-8"))
        got = {info[k]["canonical"] for k in info}
        missing = [k for k in req if cfg.canon(k) not in got]
        if missing:
            print(f"1단 요청 {len(req)}개 중 응답에 없는 키워드(canonical 대조) {len(missing)}개 — 미반환 "
                  f"[데이터 공백]: {', '.join(missing[:10])}")
        else:
            print(f"1단 요청 {len(req)}개 전부 응답에 있다(canonical 대조 — 응답 표기가 달라도 같은 검색으로 본다)")
    n_res = min(n_reserved, len(picked))
    print(f"2단 대상 {len(picked)}개 = 버킷 대표·테마 예약 {n_res} + 볼륨 {len(picked) - n_res} · "
          f"최소 12개월 볼륨 {a.min_volume:,} · 테마 예약 최소 {a.theme_min_volume:,}"
          + (f" · 상한 --max-k2 {a.max_k2}" if a.max_k2 else ""))
    print(f"2단 크레딧 = 10 × {len(picked)} = {10 * len(picked):,}  (keyword_info · data_type=all)")
    if cut:
        print(f"  --max-k2로 빠진 대상 {len(cut)}개 · 12개월 하한 {sum(c['lo'] for c in cut):,}건 "
              f"(테마 예약분 {sum(1 for c in cut if why[c['rep']] != '볼륨')}개)")
    print()
    print("버킷별 커버리지 (2단 대표 행 12개월 볼륨 ÷ 1단 전체) — 판정은 하한 기준 [추정]")
    print("  하한 = 묶음 최댓값의 합(변형이 같은 검색일 때) · 상한 = 구글 공유 1회 + 네이버 합(서로 다른 검색일 때)")
    low, flip = [], []
    for b in buckets:
        t = sum(c["lo"] for c in live if c["bucket"] == b)
        th = sum(c["hi"] for c in live if c["bucket"] == b)
        if not th:
            print(f"  {b:<16} 1단 볼륨 0 — [데이터 공백] 브랜드 표기·별칭을 확인할 것")
            continue
        got = sum(c["lo"] for c in live if c["bucket"] == b and c["rep"] in pk)
        cov, cov_hi = (got / t if t else 0), got / th
        mark = ""
        if cov < a.min_coverage:
            low.append(b); mark = "  ⚠️ 기준 미만"
        elif cov_hi < a.min_coverage:
            flip.append(b); mark = "  ⚠️ 상한 기준 미만(판정 뒤집힘)"
        print(f"  {b:<16} 하한 기준 {cov:6.1%} / 상한 기준 {cov_hi:6.1%}  (전체 하한 {t:,} ~ 상한 {th:,}){mark}")
    if low:
        print(f"\n⚠️ 커버리지 {a.min_coverage:.0%} 미만(하한 기준): {', '.join(low)} — --min-volume을 낮추거나 "
              "과소집계를 리포트에 [데이터 공백]으로 적을 것")
    if flip:
        print(f"⚠️ 하한 기준 통과·상한 기준 미달: {', '.join(flip)} — 비대표 변형이 서로 다른 검색이면 "
              "2단 월 패널이 그 버킷을 과소집계한다. 월 패널은 대표 행(하한 기준)임을 리포트에 적을 것")

    print("\n테마 커버리지 (메시지 분석 — 테마 배정 묶음 중 2단에 들어간 12개월 하한 ÷ 1단) [추정]")
    weak = []
    for t in theme_names:
        cell = [c for c in live if c["theme"] == t]
        s1 = sum(c["lo"] for c in cell)
        s2 = sum(c["lo"] for c in cell if c["rep"] in pk)
        foc = [c for c in cell if c["bucket"] == cfg.focal]
        fs1, fs2 = sum(c["lo"] for c in foc), sum(c["lo"] for c in foc if c["rep"] in pk)
        cov = s2 / s1 if s1 else None
        if cov is not None and cov < a.min_theme_coverage:
            weak.append(t)
        fdesc = ("1단 응답 없음 [데이터 공백]" if not foc else
                 f"1단 {fs1:,}건 → 2단 {fs2:,}건" + (" (게이트 밖 — 2단 미조회)" if fs1 and not fs2 else ""))
        print(f"  {t:<12} 2단 {len([c for c in cell if c['rep'] in pk])}/{len(cell)}묶음 · "
              f"{'—' if cov is None else f'{cov:6.1%}'}  · 관점 브랜드({cfg.focal or '미지정'}): {fdesc}")
    if weak:
        print(f"\n⚠️ 테마 커버리지 {a.min_theme_coverage:.0%} 미만: {', '.join(weak)} — 메시지 구성·메시지 경고는 "
              "이 테마에서 [데이터 공백]이다. --theme-min-volume을 낮추거나 --max-k2를 올릴 것")
    sus = alias_suspects(info)
    if sus:
        print("\n⚠️ 구글 지표가 같은데 버킷이 다른 키워드 — 별칭 누락 의심(합치지 않았다 · 사람이 brand_aliases를 보강할 것):")
        for kws in sus:
            print("  " + " · ".join(f"{k}({info[k]['bucket']} {info[k]['vol']:,})" for k in kws))
    print(f"→ {a.out}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("combos")
    c.add_argument("--config", required=True)
    c.add_argument("-o", "--out", default="stage1_keywords.json")
    r = sub.add_parser("review-config")
    r.add_argument("--config", required=True)
    r.add_argument("-o", "--out", default="review_themes.json")
    s = sub.add_parser("stage2")
    s.add_argument("path", help="1단 keyword_info(ads_metrics) 응답 JSON")
    s.add_argument("--config", required=True)
    s.add_argument("--requested", help="1단 요청 목록(combos 산출) — 응답에 없는 키워드를 canonical로 대조해 미반환으로 보고")
    s.add_argument("--min-volume", type=int, default=120, help="볼륨 순 2단 진입 최소 12개월 볼륨 (기본 120)")
    s.add_argument("--theme-min-volume", type=int, default=100,
                   help="테마 예약 최소 12개월 볼륨 — 수식어 테마 × 버킷 칸의 최대 묶음이 이 이상이면 2단에 넣는다 (기본 100)")
    s.add_argument("--theme-reserve", type=int, default=1, help="테마 × 버킷 칸마다 예약할 묶음 수 (기본 1)")
    s.add_argument("--max-k2", type=int, help="2단 대상 수 상한 — 버킷 대표 → 테마 예약 → 볼륨 순으로 채운다")
    s.add_argument("--min-coverage", type=float, default=0.95, help="버킷별 커버리지 경고 기준")
    s.add_argument("--min-theme-coverage", type=float, default=0.5, help="테마 커버리지 경고 기준 (기본 0.5)")
    s.add_argument("-o", "--out", default="stage2_keywords.json")
    a = ap.parse_args()
    cfg = Config(a.config)
    if a.cmd == "combos":
        combos(cfg, a.out)
    elif a.cmd == "review-config":
        review_config(cfg, a.out)
    else:
        stage2(cfg, a)


if __name__ == "__main__":
    main()
