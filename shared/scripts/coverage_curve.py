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

**판정은 라운드 단위다(호출 단위가 아니다).** 시드별 호출을 --round 여러 개로 나눠 넘기면
«마지막 라운드»가 마지막 호출 한 건이 되어 판정이 호출 순서에 달린다. 그래서:
  - 라벨이 `R1-무기자차`, `R1-톤업`처럼 **첫 `-` 앞이 같은 연속 라운드는 한 판정 라운드로 묶는다.**
    묶음의 신규 발견 합은 묶음 안 호출 순서와 무관하다. 표에는 호출별 행과 묶음 소계를 함께 낸다.
  - 파일이 intent_finder 응답이면 request_detail.keywords(그 호출의 시드)로 확장 단계를 추정한다.
    마지막 두 판정 라운드가 **같은 단계**(시드가 같은 라운드에서 나옴)인데 따로 넘겨졌으면
    판정을 «보류»로 두고 묶어 다시 돌리라고 알린다. 단계를 알 수 없고 마지막 판정 라운드가
    호출 1건이면 순서 의존 가능성을 경고한다.

@limit을 주면 **호출 단위로 절단 여부**를 검사한다(라운드 합집합 길이가 아니라). 반환이 limit에
붙은 호출이 **어느 라운드에든** 있으면 수렴이 아니라 절단일 수 있으므로 "도달"로 판정하지 않는다
— 판정은 --round 순서와 무관하다.

--rules는 인라인 JSON · 치환 사전 파일 · themes.json(rules.변형치환) 모두 받는다.
같은 검색의 중복 행(시계열·지표 일치, series_dedup.py)은 볼륨을 한 번만 센다.

누적 볼륨은 **라운드 파일에 나온 키워드의 canonical 그룹만** 센다. keyword_info가 요청하지 않고
함께 돌려준 변형 행 중 어느 라운드 키워드와도 canonical이 같지 않은 것은 빠지므로, 유니버스
합(build_universe.py)보다 작을 수 있다. 출력에 그 차이와 이유를 함께 적는다.

--themes를 주면 브랜드 사전을 읽어 **논브랜드 커버리지를 따로** 낸다. 브랜드 키워드는
이미 존재하는 수요이고, 신규 진입자가 쓸 수 있는 공간은 논브랜드 쪽이다. 전체가 수렴해도
논브랜드가 안 닫혔으면 지도는 완성된 게 아니다.
"""
import argparse, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon, collapse_identical, dedup_map, load_rules, report_collapse  # noqa: E402  (계약 T5)


def load_call(path):
    """(키워드 목록, 그 호출의 시드 목록 또는 None). 시드는 intent_finder 응답의 request_detail에서 읽는다."""
    obj = json.load(open(path, encoding="utf-8"))
    seeds = None
    if isinstance(obj, dict):
        rd = obj.get("request_detail") or {}
        if isinstance(rd.get("keywords"), list):
            seeds = [k for k in rd["keywords"] if isinstance(k, str)]
        obj = obj.get("data", [])
    return [x if isinstance(x, str) else x.get("keyword") for x in obj], seeds


def load_keywords(path):
    return load_call(path)[0]


def group_key(label):
    """판정 라운드 키 — 첫 `-` 앞. `R1-무기자차`·`R1-톤업` → `R1`."""
    return label.split("-", 1)[0].strip() or label


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metrics", required=True, help="유니버스 keyword_info 응답(볼륨 소스)")
    ap.add_argument("--round", action="append", required=True,
                    help="라벨=파일[@limit][,파일[@limit]…] (시간순). 첫 '-' 앞이 같은 연속 라벨은 한 판정 라운드로 묶는다")
    ap.add_argument("--rules", help='표기변형 치환 — 인라인 JSON(\'{"썬":"선"}\') 또는 JSON 파일 경로')
    ap.add_argument("--threshold", type=float, default=0.20, help="수렴 판정 임계 (기본 0.20)")
    ap.add_argument("--themes", help="테마·브랜드 사전 JSON — 논브랜드 커버리지 산출용")
    a = ap.parse_args()

    # 인라인 JSON과 파일 경로를 모두 받는다 — 예시와 구현이 어긋나면 예시를 믿은 쪽이 실패한다.
    rules = load_rules(a.rules)
    m = json.load(open(a.metrics, encoding="utf-8"))
    requested = None                     # keyword_info에 요청한 키워드(canonical) — 차이 설명용
    if isinstance(m, dict) and isinstance((m.get("request_detail") or {}).get("keywords"), list):
        requested = {canon(k, rules) for k in m["request_detail"]["keywords"] if isinstance(k, str)}
    m = m.get("data", m) if isinstance(m, dict) else m
    n_raw = len(m)
    m, n_merged, conflicts = collapse_identical(m)   # 같은 keyword의 완전 중복 행은 한 행으로
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

    # ── 호출 단위 집계 (--round 항목별) ─────────────────────────────
    seen, found_in = set(), {}          # canonical → 발견된 판정 라운드 번호
    entries, truncated = [], []
    gkeys = []                           # 판정 라운드 키 (연속 같은 키만 묶는다)
    for spec in a.round:
        label, paths = spec.split("=", 1)
        gk = group_key(label)
        if not gkeys or gkeys[-1] != gk:
            gkeys.append(gk)
        gi = len(gkeys) - 1
        returned, cut, call_seeds, n_calls = [], False, [], 0
        for path in paths.split(","):
            limit = None
            if "@" in path:
                path, lim = path.rsplit("@", 1)
                limit = int(lim)
            got, seeds = load_call(path)
            n_calls += 1
            cut = cut or (limit is not None and len(got) >= limit)   # 호출 단위 판정
            returned += got
            call_seeds.append(seeds)
        if cut:
            truncated.append(label)
        fresh = []
        for kw in returned:
            c = canon(kw, rules)
            if c not in seen:
                seen.add(c); fresh.append(c); found_in[c] = gi
        nb = [c for c in fresh if not is_brand(c)]
        entries.append({"label": label, "gi": gi, "calls": n_calls, "cut": cut, "seeds": call_seeds,
                        "n": len(fresh), "v": sum(vol.get(c, 0) for c in fresh),
                        "v_lo": sum(vol_lo.get(c, 0) for c in fresh),
                        "nb_n": len(nb), "nb_v": sum(vol.get(c, 0) for c in nb),
                        "nb_v_lo": sum(vol_lo.get(c, 0) for c in nb)})

    # ── 판정 라운드 단위 집계 ────────────────────────────────────────
    rounds = []
    cum = dict(kw=0, v=0, v_lo=0, nb_kw=0, nb_v=0, nb_v_lo=0)
    for gi, gk in enumerate(gkeys):
        mem = [e for e in entries if e["gi"] == gi]
        g = {k: sum(e[k] for e in mem) for k in ("n", "v", "v_lo", "nb_n", "nb_v", "nb_v_lo")}
        cum["kw"] += g["n"]; cum["v"] += g["v"]; cum["v_lo"] += g["v_lo"]
        cum["nb_kw"] += g["nb_n"]; cum["nb_v"] += g["nb_v"]; cum["nb_v_lo"] += g["nb_v_lo"]
        share_hi = g["v"] / cum["v"] if cum["v"] else 0
        share_lo = g["v_lo"] / cum["v_lo"] if cum["v_lo"] else 0
        nb_hi = g["nb_v"] / cum["nb_v"] if cum["nb_v"] else 0
        nb_lo = g["nb_v_lo"] / cum["nb_v_lo"] if cum["nb_v_lo"] else 0
        # 확장 단계 추정 — 모든 호출에 시드가 있고, 시드가 전부 앞 판정 라운드에서 발견됐을 때만
        seeds = [s for e in mem for s in e["seeds"]]
        depth_src = None
        if seeds and all(s is not None for s in seeds):
            src = [found_in.get(canon(k, rules)) for s in seeds for k in s]
            if src and all(x is not None and x < gi for x in src):
                depth_src = max(src)
        rounds.append({"key": gk, "members": mem, "calls": sum(e["calls"] for e in mem),
                       "cum_v": cum["v"], "cum_v_lo": cum["v_lo"], **g,
                       "share": max(share_hi, share_lo), "share_hi": share_hi, "share_lo": share_lo,
                       "nb_share": max(nb_hi, nb_lo), "cut": any(e["cut"] for e in mem),
                       "src": depth_src})

    head = f"{'라운드':<20}{'신규':>6}{'신규 볼륨':>13}{'누적 볼륨':>13}{'기여율':>8}"
    if brands:
        head += f"{'논브랜드 기여':>14}"
    print(head + "   절단")
    for r in rounds:
        grouped = len(r["members"]) > 1
        if grouped:   # 묶음 안 호출별 신규 수는 순서에 달려 있다 — 참고용으로만 보이고 기여율은 묶음에만 낸다
            for e in r["members"]:
                print(f"  {e['label']:<18}{e['n']:>6}{e['v']:>13,}{'':>13}{'':>8}"
                      + (f"{'':>14}" if brands else "") + ("   ⚠️ limit" if e["cut"] else ""))
        name = f"{r['key']} (호출 {r['calls']}건 묶음)" if grouped else r["members"][0]["label"]
        line = f"{name:<20}{r['n']:>6}{r['v']:>13,}{r['cum_v']:>13,}{r['share']:>7.1%}"
        if brands:
            line += f"{r['nb_share']:>13.1%}"
        print(line + ("   ⚠️ limit" if r["cut"] else ""))

    last = rounds[-1]
    prev = rounds[-2] if len(rounds) > 1 else None
    # 같은 확장 단계가 판정 라운드 둘로 갈렸는가 — 마지막 기여율이 호출 순서에 달린다
    split_stage = bool(prev and last["src"] is not None and last["src"] == prev["src"])
    order_risk = (not split_stage and last["src"] is None and last["calls"] == 1
                  and prev is not None and prev["calls"] == 1 and len(rounds) > 2)

    report_collapse(n_merged, conflicts, n_raw)
    if any(len(r["members"]) > 1 for r in rounds):
        print("\n판정 단위: " + " · ".join(
            f"{r['key']} = {' + '.join(e['label'] for e in r['members'])}" for r in rounds if len(r["members"]) > 1)
              + " (첫 '-' 앞이 같은 연속 라벨 — 묶음 기여율은 묶음 안 호출 순서와 무관)")

    uni_hi, uni_lo = sum(vol.values()), sum(vol_lo.values())
    outside = [c for c in vol if c not in seen]
    print(f"\n누적 볼륨: 하한 {last['cum_v_lo']:,} ~ 상한 {last['cum_v']:,} (표기변형 그룹 최댓값 ~ 합, 계약 T5) · "
          f"마지막 판정 라운드 기여율 하한 기준 {last['share_lo']:.1%} / 상한 기준 {last['share_hi']:.1%} — 판정은 큰 쪽")
    if outside:
        print(f"  유니버스(--metrics) 그룹 합은 하한 {uni_lo:,} ~ 상한 {uni_hi:,} — 곡선 누적과의 차이 "
              f"{uni_lo - last['cum_v_lo']:,} ~ {uni_hi - last['cum_v']:,}는 어느 라운드 파일에도 없는 "
              f"canonical 그룹 {len(outside)}개다.")
        if requested is not None:
            unreq = [c for c in outside if c not in requested]
            print(f"  그중 요청하지 않은 변형 행(keyword_info가 함께 돌려준 것) {len(unreq)}개 · "
                  f"요청했지만 라운드 파일에 없는 키워드 {len(outside) - len(unreq)}개.")
        else:
            print("  (keyword_info가 요청하지 않고 함께 돌려준 변형 행, 또는 라운드 밖에서 추가 요청한 키워드)")
        print("  곡선은 확장으로 발견된 키워드만 센다 — 곡선 누적을 유니버스 규모로 인용하지 말 것.")
    print()
    if truncated:
        print(f"판정: **보류** — limit에 붙어 잘린 호출이 있다({', '.join(truncated)}).")
        print(f"      마지막 판정 라운드 기여율 {last['share']:.1%}는 수렴이 아니라 절단의 결과일 수 있다.")
        print("      잘린 호출의 limit을 올려 재확장한 뒤 다시 판정할 것. 잘린 채로 '도달'이라 쓰지 않는다.")
    elif split_stage:
        src_key = rounds[last["src"]]["key"]
        print(f"판정: **보류** — 판정 라운드 «{prev['key']}»·«{last['key']}»의 시드가 모두 «{src_key}»에서 나왔다(같은 확장 단계).")
        print(f"      마지막 판정 라운드 기여율 {last['share']:.1%}는 호출 순서에 달린 값이다.")
        print("      같은 단계 호출은 한 --round에 쉼표로 묶거나, 라벨의 첫 '-' 앞을 같게(예: R1-시드A, R1-시드B) 맞춰 다시 판정할 것.")
    elif last["share"] < a.threshold:
        print(f"판정: 마지막 판정 라운드 기여율 {last['share']:.1%} < 임계 {a.threshold:.0%} → **커버리지 도달**")
        print("      확장을 멈추고 Blind Spot(B형 축 미확장) 점검으로 넘어간다.")
    else:
        print(f"판정: 마지막 판정 라운드 기여율 {last['share']:.1%} ≥ 임계 {a.threshold:.0%} → **한 라운드 더**")
        print("      아직 새 키워드가 볼륨을 유의미하게 늘리고 있다.")
    if order_risk:
        print("  ⚠️ 판정 라운드가 호출 1건씩 이어져 있고 시드 출처를 확인할 수 없다(키워드 배열 파일).")
        print("     시드별 호출을 라운드로 나눠 넘긴 것이면 판정이 호출 순서에 달린다 — 쉼표로 묶거나 라벨을 'R1-…'로 맞출 것.")

    if truncated:
        print(f"\n⚠️ 절단된 라운드: {', '.join(truncated)} — 반환 수가 limit에 도달했다.")
    if brands:
        nb_ratio = cum["nb_v"] / cum["v"] if cum["v"] else 0
        nb_ratio_lo = cum["nb_v_lo"] / cum["v_lo"] if cum["v_lo"] else 0
        print(f"\n논브랜드 공간: {cum['nb_kw']}키워드 / 하한 {cum['nb_v_lo']:,} ~ 상한 {cum['nb_v']:,} "
              f"(전체 대비 하한 기준 {nb_ratio_lo:.1%} · 상한 기준 {nb_ratio:.1%}) — 논브랜드 기여율 판정도 큰 쪽")
        nb_below = last["nb_share"] < a.threshold
        all_below = last["share"] < a.threshold
        if truncated or split_stage:
            # 전체 판정이 보류면 논브랜드도 수렴이라 쓰지 않는다
            print(f"  논브랜드 기여율 {last['nb_share']:.1%} — 전체 판정이 보류라 논브랜드도 판정하지 않는다.")
        elif all_below and not nb_below:
            print(f"  ⚠️ 전체는 수렴했지만 **논브랜드 기여율이 {last['nb_share']:.1%}로 아직 임계 위**다.")
            print("     신규 진입 검토라면 논브랜드 축으로 한 라운드 더 확장할 것.")
        elif all_below:
            print(f"  논브랜드 기여율 {last['nb_share']:.1%} < 임계 {a.threshold:.0%} — 전체 곡선과 함께 수렴.")
        elif nb_below:
            print(f"  논브랜드 기여율 {last['nb_share']:.1%}는 임계 아래지만 전체가 아직 임계 위라 수렴으로 보지 않는다.")
        else:
            print(f"  논브랜드 기여율 {last['nb_share']:.1%} ≥ 임계 {a.threshold:.0%} — 전체와 함께 아직 열려 있다.")
        print("  브랜드 사전 누락은 논브랜드를 부풀린다. 상위 볼륨 키워드에서 사전을 채웠는지 확인할 것.")
    print("\n[가정] 임계값은 카테고리마다 보정이 필요하다. 리포트에 임계값을 함께 적을 것.")


if __name__ == "__main__":
    main()
