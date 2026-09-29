#!/usr/bin/env python3
# shared-asset: lm-skills/shared — 이 파일은 사본이다. 고칠 때는 정본을 고치고 재배포할 것.
"""커버리지 = 라운드별 신규 발견 기여 (SKILL.md STEP 3).

절대 분모(시장 전체 추정 volume)는 쓰지 않는다. 카테고리 규모를 모르면 분모도
모르고, 틀린 분모는 300% 같은 값을 만든다. 대신 라운드를 거듭할수록 새 키워드가
얼마나 덜 나오는지(한계기여)만 본다 — 분모 없이 수렴만 보므로 규모와 무관하다.

사용법:
  python3 coverage_curve.py --metrics <ads_info.json> \
      --round "S1:시드=seeds.json" --round "R1:확장1=intent_r1.json" ...

--round 값은 `라벨=파일[@limit][,파일[@limit]…]` 형식. 한 라운드가 시드별 호출 여러 건이면
쉼표로 나열한다. 파일은 키워드 배열 JSON이거나 intent_finder 응답. 키워드는 **먼저 선언된
라운드**에 귀속된다(시간순으로 넘길 것).

@limit을 주면 **호출 단위로 절단 여부**를 검사한다(라운드 합집합 길이가 아니라). 반환이 limit에
붙은 호출이 **어느 라운드에든** 있으면 수렴이 아니라 절단일 수 있으므로 "도달"로 판정하지 않는다
— 판정은 --round 순서와 무관하다.

--rules는 인라인 JSON · 치환 사전 파일 · themes.json(rules.변형치환) 모두 받는다.
같은 검색의 중복 행(시계열·지표 일치, series_dedup.py)은 볼륨을 한 번만 센다.

--themes를 주면 브랜드 사전을 읽어 **논브랜드 커버리지를 따로** 낸다. 브랜드 키워드는
이미 존재하는 수요이고, 신규 진입자가 쓸 수 있는 공간은 논브랜드 쪽이다. 전체가 수렴해도
논브랜드가 안 닫혔으면 지도는 완성된 게 아니다.
"""
import argparse, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, dedup_map, load_rules  # noqa: E402  (계약 T5)


def load_keywords(path):
    obj = json.load(open(path, encoding="utf-8"))
    if isinstance(obj, dict):
        obj = obj.get("data", [])
    return [x if isinstance(x, str) else x.get("keyword") for x in obj]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", required=True, help="유니버스 keyword_info 응답(볼륨 소스)")
    ap.add_argument("--round", action="append", required=True, help="라벨=파일 (시간순)")
    ap.add_argument("--rules", help='표기변형 치환 — 인라인 JSON(\'{"썬":"선"}\') 또는 JSON 파일 경로')
    ap.add_argument("--threshold", type=float, default=0.20, help="수렴 판정 임계 (기본 0.20)")
    ap.add_argument("--themes", help="테마·브랜드 사전 JSON — 논브랜드 커버리지 산출용")
    a = ap.parse_args()

    # 인라인 JSON과 파일 경로를 모두 받는다 — 예시와 구현이 어긋나면 예시를 믿은 쪽이 실패한다.
    rules = load_rules(a.rules)
    m = json.load(open(a.metrics, encoding="utf-8"))
    m = m.get("data", m) if isinstance(m, dict) else m
    # 표기변형 그룹 볼륨: 상한(그룹 내 합)과 하한(그룹 내 최댓값) — 계약 T5.
    # 기여율 판정은 두 기준 모두에서 임계 아래일 때만 '도달'로 본다(보수적).
    vol, vol_lo = {}, {}
    dups = {k for k, v in dedup_map(m, rules).items() if v["rep"] != k}
    for d in m:
        if d.get("keyword") in dups:      # 같은 검색의 중복 — 한 번만 센다
            continue
        v = (d.get("ads_metrics") or {}).get("volume_total") or 0
        c = canon(d["keyword"], rules)
        vol[c] = vol.get(c, 0) + v
        vol_lo[c] = max(vol_lo.get(c, 0), v)

    brands = []
    if a.themes:
        cfg = json.load(open(a.themes, encoding="utf-8"))
        brands = [canon(b, rules) for b in cfg.get("brands", [])]
    is_brand = lambda c: any(b in c for b in brands)

    seen, curve = set(), []
    cum_kw = cum_v = cum_v_lo = cum_nb_kw = cum_nb_v = cum_nb_v_lo = 0
    truncated = []
    for spec in a.round:
        label, paths = spec.split("=", 1)
        returned, cut = [], False
        for path in paths.split(","):
            limit = None
            if "@" in path:
                path, lim = path.rsplit("@", 1)
                limit = int(lim)
            got = load_keywords(path)
            cut = cut or (limit is not None and len(got) >= limit)   # 호출 단위 판정
            returned += got
        if cut:
            truncated.append(label)
        fresh = []
        for kw in returned:
            c = canon(kw, rules)
            if c not in seen:
                seen.add(c); fresh.append(c)
        v = sum(vol.get(c, 0) for c in fresh)
        v_lo = sum(vol_lo.get(c, 0) for c in fresh)
        nb = [c for c in fresh if not is_brand(c)]
        nbv = sum(vol.get(c, 0) for c in nb)
        nbv_lo = sum(vol_lo.get(c, 0) for c in nb)
        cum_kw += len(fresh); cum_v += v; cum_v_lo += v_lo
        cum_nb_kw += len(nb); cum_nb_v += nbv; cum_nb_v_lo += nbv_lo
        curve.append({"label": label, "n": len(fresh), "v": v, "cum_kw": cum_kw, "cum_v": cum_v,
                      "share": max(v / cum_v if cum_v else 0, v_lo / cum_v_lo if cum_v_lo else 0),
                      "share_hi": v / cum_v if cum_v else 0, "share_lo": v_lo / cum_v_lo if cum_v_lo else 0,
                      "cum_v_lo": cum_v_lo, "nb_n": len(nb), "nb_v": nbv,
                      "nb_share": max(nbv / cum_nb_v if cum_nb_v else 0, nbv_lo / cum_nb_v_lo if cum_nb_v_lo else 0), "cut": cut})

    head = f"{'라운드':<20}{'신규':>6}{'신규 볼륨':>13}{'누적 볼륨':>13}{'기여율':>8}"
    if brands:
        head += f"{'논브랜드 기여':>14}"
    print(head + "   절단")
    for c in curve:
        line = (f"{c['label']:<20}{c['n']:>6}{c['v']:>13,}{c['cum_v']:>13,}{c['share']:>7.1%}")
        if brands:
            line += f"{c['nb_share']:>13.1%}"
        print(line + ("   ⚠️ limit" if c["cut"] else ""))

    last = curve[-1]
    print(f"\n누적 볼륨: 하한 {last['cum_v_lo']:,} ~ 상한 {last['cum_v']:,} (표기변형 그룹 최댓값 ~ 합, 계약 T5) · "
          f"마지막 라운드 기여율 하한 기준 {last['share_lo']:.1%} / 상한 기준 {last['share_hi']:.1%} — 판정은 큰 쪽")
    print()
    if truncated:
        print(f"판정: **보류** — limit에 붙어 잘린 호출이 있다({', '.join(truncated)}).")
        print(f"      마지막 라운드 기여율 {last['share']:.1%}는 수렴이 아니라 절단의 결과일 수 있다.")
        print("      잘린 호출의 limit을 올려 재확장한 뒤 다시 판정할 것. 잘린 채로 '도달'이라 쓰지 않는다.")
    elif last["share"] < a.threshold:
        print(f"판정: 마지막 라운드 기여율 {last['share']:.1%} < 임계 {a.threshold:.0%} → **커버리지 도달**")
        print("      확장을 멈추고 Blind Spot(B형 축 미확장) 점검으로 넘어간다.")
    else:
        print(f"판정: 마지막 라운드 기여율 {last['share']:.1%} ≥ 임계 {a.threshold:.0%} → **한 라운드 더**")
        print("      아직 새 키워드가 볼륨을 유의미하게 늘리고 있다.")

    if truncated:
        print(f"\n⚠️ 절단된 라운드: {', '.join(truncated)} — 반환 수가 limit에 도달했다.")
    if brands:
        nb_ratio = cum_nb_v / cum_v if cum_v else 0
        nb_ratio_lo = cum_nb_v_lo / cum_v_lo if cum_v_lo else 0
        print(f"\n논브랜드 공간: {cum_nb_kw}키워드 / 하한 {cum_nb_v_lo:,} ~ 상한 {cum_nb_v:,} "
              f"(전체 대비 하한 기준 {nb_ratio_lo:.1%} · 상한 기준 {nb_ratio:.1%}) — 논브랜드 기여율 판정도 큰 쪽")
        if last["nb_share"] >= a.threshold and not truncated:
            print(f"  ⚠️ 전체는 수렴했지만 **논브랜드 기여율이 {last['nb_share']:.1%}로 아직 임계 위**다.")
            print("     신규 진입 검토라면 논브랜드 축으로 한 라운드 더 확장할 것.")
        else:
            print(f"  논브랜드 기여율 {last['nb_share']:.1%} — 전체 곡선과 함께 수렴.")
        print("  브랜드 사전 누락은 논브랜드를 부풀린다. 상위 볼륨 키워드에서 사전을 채웠는지 확인할 것.")
    print("\n[가정] 임계값은 카테고리마다 보정이 필요하다. 리포트에 임계값을 함께 적을 것.")


if __name__ == "__main__":
    main()
