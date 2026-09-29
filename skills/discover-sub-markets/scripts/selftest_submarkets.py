#!/usr/bin/env python3
"""이 스킬 고유 스크립트(submarkets.py) 계약 검증 — SKILL.md의 예시 명령을 합성 픽스처로 돌린다.

공유 `selftest.py`는 공유 스크립트만 안다. 이 스킬의 판정은 `submarkets.py`에 있으므로
여기서 따로 검사한다. 먼저 공유 selftest를 돌리고, 이어서 아래를 확인한다.

  - SKILL.md «산출물»의 컬럼 목록 = 스크립트 실제 출력 (양방향)
  - 절단(관계 수 = limit)이면 경고와 truncated 컬럼이 나온다          (T7)
  - 표기변형 그룹 볼륨은 하한(최댓값)·상한(합)을 둘 다 내고, 판정·순위는 하한 기준 (T5)
  - 상한 기준이면 판정이 바뀌는 군집은 verdict_flip, 순위가 바뀌면 rank_flip으로 표시된다
  - 브랜드가 지배하는 군집은 하위 시장 후보에서 빠진다                (T8)
  - 결측 창은 0으로 합산하지 않고 계산 불가로 센다                    (T6)
  - 빈 응답은 [데이터 공백]으로 멈춘다
  - 문서의 파이프라인(budget → nodes → build_universe → score → trend_yoy → score --trend)이 끝까지 돈다
  회귀(2026-09-28 실데이터 평가 결함):
  - P1-1 기준월 원천(--seed-all 또는 --trend)이 없으면 경고 + 종료 코드 3, 있으면 모든 행에 data_ref_month
  - P1-2 한 허브에 간선이 몰리면 «별 모양 퇴화»를 경고하고, 절단 설명이 «먼 주변부»를 단정하지 않는다
  - P1-3 한 키워드짜리 브랜드 군집도 «브랜드 지배» · 브랜드 시드면 경고
  - P1-4 빈 시드·그래프에 없는 시드는 종료 코드 2로 거부
  - P2 역방향 중복 간선은 degree에 한 번만 · 같은 검색의 중복(dup_of)은 볼륨에 한 번만 ·
       2단 대상은 volume_avg × 3 < yoy_gate면 제외 · STEP 7은 stage2 파일을 덮어쓰지 않는다 ·
       budget은 보수·관측 상한을 둘 다 낸다

합성 픽스처만 쓰므로 API 호출도 크레딧도 필요 없다.

    python3 scripts/selftest_submarkets.py        # 스킬 폴더에서 실행
"""
import csv, json, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL_MD = HERE.parent / "SKILL.md"
S = str(HERE)

COMMS = {
    "0": ["캠핑의자", "캠핑 의자", "캠핑체어"],
    "1": ["헬리녹스", "헬리녹스 체어원", "헬리녹스 체어 원", "헬리녹스 체어투"],
    "2": ["릴렉스체어", "릴렉스 체어", "흔들의자", "접이식 흔들의자", "캠핑 흔들의자"],
    "3": ["캠핑의자 추천", "캠핑의자 가성비", "경량 캠핑의자"],
    "4": ["피크닉매트"],
    # 5: 하한 6,500(규모 미달) · 상한 11,200(통과) → 판정 뒤집힘
    "5": ["캠핑테이블", "캠핑 테이블", "캠핑테이블 추천", "캠핑 테이블 추천", "롤테이블"],
    # 6: 변형 없음 · 하한=상한 8,000 → 규모 미달 안에서 5와 순위가 뒤집힌다(하한 6>5, 상한 5>6)
    "6": ["야전침대", "캠핑 야전침대", "코트 침대", "침대 코트"],
    # 7: 한 키워드짜리 브랜드 군집 → 소형·단일이 아니라 브랜드 지배 (P1-3)
    "7": ["코베아 체어"],
}
RELS = [
    ["캠핑의자", "캠핑 의자"], ["캠핑의자", "캠핑체어"], ["캠핑의자", "헬리녹스"],
    ["헬리녹스", "헬리녹스 체어원"], ["헬리녹스", "헬리녹스 체어투"], ["헬리녹스", "헬리녹스 체어 원"],
    ["캠핑체어", "릴렉스체어"], ["릴렉스체어", "릴렉스 체어"], ["릴렉스체어", "흔들의자"],
    ["흔들의자", "접이식 흔들의자"], ["흔들의자", "캠핑 흔들의자"], ["캠핑의자", "캠핑의자 추천"],
    ["캠핑의자 추천", "캠핑의자 가성비"], ["캠핑의자", "경량 캠핑의자"], ["릴렉스체어", "피크닉매트"],
    ["릴렉스체어", "캠핑테이블"], ["캠핑테이블", "캠핑 테이블"], ["캠핑테이블", "캠핑테이블 추천"],
    ["캠핑테이블 추천", "캠핑 테이블 추천"], ["캠핑테이블", "롤테이블"],
    ["캠핑의자", "야전침대"], ["야전침대", "캠핑 야전침대"], ["야전침대", "코트 침대"],
    ["코트 침대", "침대 코트"], ["캠핑체어", "코베아 체어"],
    ["캠핑 의자", "캠핑의자"],   # 역방향 중복 — degree에 한 번만 (P2)
]
VOL = {"캠핑의자": 90000, "캠핑 의자": 30000, "캠핑체어": 25000,
       "헬리녹스": 50000, "헬리녹스 체어원": 20000, "헬리녹스 체어 원": 19000, "헬리녹스 체어투": 8000,
       "릴렉스체어": 40000, "릴렉스 체어": 39000, "흔들의자": 20000, "접이식 흔들의자": 3000,
       "캠핑의자 추천": 15000, "캠핑의자 가성비": 5000, "경량 캠핑의자": 6000, "피크닉매트": 30000,
       "캠핑테이블": 4000, "캠핑 테이블": 3800, "캠핑테이블 추천": 1000, "캠핑 테이블 추천": 900,
       "롤테이블": 1500, "야전침대": 5000, "캠핑 야전침대": 2000, "코트 침대": 1000,
       "침대 코트": 1000,   # 어순 변형 · 지표 동일 → 같은 검색의 중복 (dup_of)
       "코베아 체어": 7000}
N_NODES = 26
# "캠핑 흔들의자"는 일부러 지표에서 뺀다 → missing_metrics 1
MONTHS = [f"{y}-{m:02d}" for y in (2023, 2024, 2025, 2026) for m in range(1, 13)]
LAST = "2026-07"


def series(base, growth, missing=()):
    out = []
    for m in MONTHS[: MONTHS.index(LAST) + 1]:
        if m in missing:
            out.append({"month": m, "gg": 10, "nv_code": 1})
            continue
        v = int(base * (growth if m >= "2025-08" else 1.0))
        out.append({"month": m, "gg": v // 3, "nv": v - v // 3, "total": v})
    return out


def metrics_row(k, v, mv=None):
    d = {"keyword": k, "ads_metrics": {"volume_total": v, "volume_avg": v // 12, "volume_trend": 0.1,
                                        "cpc": 0.3, "competition": "LOW", "competition_index": 10,
                                        "gg_volume_total": v // 3, "nv_volume_total": v - v // 3}}
    if mv:
        d["monthly_volume"] = mv
    return d


def fixture(tmp):
    w = lambda n, o: (tmp / n).write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")
    w("cluster.json", {"result": "OK", "cost_detail": {"total_cost": 150 + N_NODES * 50},
                       "data": {"communities": COMMS, "rels": RELS}})
    # 별 모양 퇴화: 모든 간선이 허브 H 하나에 붙고, 시드 S는 H에만 이어진다
    star_rels = [["H", f"k{i}"] for i in range(9)] + [["S", "H"]]
    w("star.json", {"result": "OK", "data": {"communities": {"0": ["H", "S"] + [f"k{i}" for i in range(9)]},
                                             "rels": star_rels}})
    w("empty.json", {"result": "OK", "data": {"communities": {}, "rels": []}})
    w("metrics.json", {"data": [metrics_row(k, v) for k, v in VOL.items()]})
    w("seeds.json", ["캠핑의자"])
    w("themes.json", {"rules": {"변형치환": {}},
                      "themes": [{"name": "형태·용도", "pattern": "흔들|릴렉스|경량|접이식"},
                                 {"name": "구매·비교", "pattern": "추천|가성비|비교"}],
                      "brands": ["헬리녹스", "코베아"],
                      "no_match": {"with_brand": "브랜드·제품명", "without_brand": "코어·일반"}})
    growth = {"릴렉스체어": 1.6, "흔들의자": 1.3, "접이식 흔들의자": 1.2,
              "캠핑의자 추천": 0.9, "경량 캠핑의자": 1.1, "캠핑의자 가성비": 1.0}
    rows = []
    for k, g in growth.items():
        miss = ("2026-05",) if k == "접이식 흔들의자" else ()
        rows.append(metrics_row(k, VOL[k], series(max(VOL[k] // 12, 400), g, miss)))
    w("all.json", {"data": rows})
    # STEP 1 시드 all — 응답 표기가 요청과 다르다(캠핑의자 → 캠핑 의자)
    w("seed_all.json", {"data": [metrics_row("캠핑 의자", VOL["캠핑 의자"], series(2500, 1.0))]})


def run(cmd, tmp):
    p = subprocess.run([sys.executable] + cmd, cwd=tmp, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def read_csv(path):
    with open(path, encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


def doc_columns(marker):
    """SKILL.md 산출물 블록에서 marker 파일의 컬럼 목록을 읽는다."""
    lines = SKILL_MD.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == marker:
            cols = []
            for nxt in lines[i + 1:]:
                if not nxt.startswith("  ") or not re.match(r"^\s+[a-z_]", nxt):
                    break
                cols += [c.strip() for c in nxt.split(",") if c.strip()]
            return cols
    return None


def main():
    fails = []
    rc, out = run([f"{S}/selftest.py"], HERE.parent)
    print(out.strip().splitlines()[-1] if out.strip() else "(공유 selftest 출력 없음)")
    if rc:
        fails.append(("selftest.py", "공유 스크립트 검증 실패", out[-300:]))

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        fixture(tmp)

        # STEP 0 — 사전 예산: 보수 상한과 관측 상한 [가정]을 둘 다 (P2)
        rc, out = run([f"{S}/submarkets.py", "budget", "--limit", "500"], tmp)
        if rc or "51,160" not in out or "25,711" not in out or "[가정" not in out:
            fails.append(("budget", "보수 상한(10+150+51×1000)·관측 상한(10+150+51×501)·[가정] 표기 이상", out[-300:]))

        # STEP 2 — 절단 아님 / 절단
        rc, out = run([f"{S}/submarkets.py", "nodes", "cluster.json", "--limit", "500", "--seed", "캠핑의자",
                       "-o", "nodes.json"], tmp)
        if rc or "절단 아님" not in out or f"150 + 50 × {N_NODES} = {150 + 50 * N_NODES:,}" not in out:
            fails.append(("nodes", "노드 수·결정식·절단 아님 출력 이상", out[-300:]))
        if "역방향 중복 1쌍" not in out:
            fails.append(("nodes", "역방향 중복 간선을 세지 않는다 (P2)", out[-300:]))
        rc, out = run([f"{S}/submarkets.py", "nodes", "cluster.json", "--limit", "23", "-o", "nodes23.json"], tmp)
        if rc or "⚠️ 절단" not in out:
            fails.append(("nodes", "관계 수 = limit인데 절단 경고가 없다 (T7)", out[-300:]))
        elif "먼 주변부다" in out:
            fails.append(("nodes", "절단 설명이 «먼 주변부»를 단정한다 — 실측은 한 허브의 인접 목록이었다 (P1-2)", ""))
        rc, out = run([f"{S}/submarkets.py", "nodes", "star.json", "--limit", "10", "--seed", "S", "-o", "star_nodes.json"], tmp)
        if rc or "별 모양 퇴화" not in out or "직접 이웃 1개" not in out:
            fails.append(("nodes", "간선이 한 허브에 몰렸는데 별 모양 퇴화 경고가 없다 (P1-2)", out[-300:]))
        rc, out = run([f"{S}/submarkets.py", "nodes", "cluster.json", "--limit", "500", "-o", "n2.json"], tmp)
        if "별 모양" in out:
            fails.append(("nodes", "퇴화가 아닌 그래프에 별 모양 경고가 떴다", ""))
        rc, out = run([f"{S}/submarkets.py", "nodes", "empty.json", "--limit", "500", "-o", "e.json"], tmp)
        if rc != 2 or "데이터 공백" not in out:
            fails.append(("nodes", "빈 응답이 [데이터 공백]으로 멈추지 않는다", out[-300:]))

        # STEP 4 — 공유 build_universe (문서 예시 그대로)
        rc, out = run([f"{S}/build_universe.py", "--metrics", "metrics.json", "--themes", "themes.json",
                       "--seeds", "seeds.json", "--round", "CF=nodes.json", "-o", "universe.csv"], tmp)
        if rc:
            fails.append(("build_universe", "문서 예시로 비정상 종료", out[-300:]))

        # STEP 5 — 1차 판정
        base = [f"{S}/submarkets.py", "score", "cluster.json", "--universe", "universe.csv",
                "--seed", "캠핑의자", "--limit", "500", "--seed-all", "seed_all.json", "-o", "submarkets.csv",
                "--keywords-out", "submarket_keywords.csv", "--pick-out", "stage2_keywords.json"]
        LIM = base.index("--limit") + 1

        # P1-1 — 기준월 원천이 없으면 헤더를 비운 채 통과하지 않는다
        no_ref = [x for x in base if x not in ("--seed-all", "seed_all.json")]
        rc, out = run(no_ref, tmp)
        if rc != 3 or "기준월 없음" not in out:
            fails.append(("score", "기준월 원천 없이 종료 코드 0 — 헤더 기준월이 조용히 빈다 (P1-1)", f"rc={rc} {out[-200:]}"))
        # P1-4 — 빈 시드·그래프에 없는 시드 거부
        for bad in ("", "  ", "없는시드"):
            b2 = list(base); b2[b2.index("캠핑의자")] = bad
            rc, out = run(b2, tmp)
            if rc != 2:
                fails.append(("score", f"시드 «{bad}»를 거부하지 않았다 (P1-4)", f"rc={rc} {out[-200:]}"))

        rc, out = run(base, tmp)
        if rc:
            fails.append(("score", "문서 예시로 비정상 종료", out[-300:]))
        else:
            hdr, rows = read_csv(tmp / "submarkets.csv")
            doc = doc_columns("submarkets_{seed}.csv")
            if doc is None or set(doc) != set(hdr):
                fails.append(("score", "SKILL.md 컬럼 목록 ≠ 실제 출력",
                              f"문서만: {sorted(set(doc or []) - set(hdr))} · 코드만: {sorted(set(hdr) - set(doc or []))}"))
            khdr, _ = read_csv(tmp / "submarket_keywords.csv")
            kdoc = doc_columns("submarket_keywords_{seed}.csv")
            if kdoc is None or set(kdoc) != set(khdr):
                fails.append(("score", "키워드 CSV 컬럼 목록 ≠ 실제 출력", f"{kdoc} vs {khdr}"))
            by = {r["community_id"]: r for r in rows}
            expect = {"0": "시드 코어", "1": "브랜드 지배", "2": "숨은 하위 시장 후보",
                      "3": "인접 하위 시장", "4": "소형·단일", "5": "규모 미달", "6": "규모 미달",
                      "7": "브랜드 지배"}
            for cid, t in expect.items():
                if by.get(cid, {}).get("submarket_type") != t:
                    fails.append(("score", f"군집 {cid} 판정 {by.get(cid, {}).get('submarket_type')} ≠ {t}", ""))
            if any(r["data_ref_month"] != LAST for r in rows):
                fails.append(("score", "--seed-all을 줬는데 data_ref_month가 빈 행이 있다 (P1-1)", ""))
            if by.get("7", {}).get("brand_dominated") != "예" or by.get("2", {}).get("brand_dominated"):
                fails.append(("score", "brand_dominated 표시 오류 (P1-3)", ""))
            if by.get("0", {}).get("hub_degree") != "6":
                fails.append(("score", "역방향 중복 간선이 degree를 부풀렸다 (P2)", by.get("0", {}).get("hub_degree", "")))
            c6d = by.get("6", {})
            if (c6d.get("volume_lo"), c6d.get("dup_rows")) != ("8000", "1"):
                fails.append(("score", "같은 검색의 중복(어순 변형 · 지표 동일)을 두 번 셌다 (T5 ①)",
                              f"{c6d.get('volume_lo')} / dup {c6d.get('dup_rows')}"))
            if "먼 주변부" in out or "[파생]" in out:
                fails.append(("score", "옛 문구(먼 주변부·[파생]) 잔존", ""))
            c2 = by.get("2", {})
            if (c2.get("volume_lo"), c2.get("volume_hi")) != ("63000", "102000"):
                fails.append(("score", "표기변형 그룹 하한(최댓값 63,000)·상한(합 102,000)이 아니다 (T5)",
                              f"{c2.get('volume_lo')} / {c2.get('volume_hi')}"))
            c5, c6 = by.get("5", {}), by.get("6", {})
            if (c5.get("nonbrand_volume_lo"), c5.get("nonbrand_volume_hi")) != ("6500", "11200"):
                fails.append(("score", "군집 5 논브랜드 하한·상한 오류", f"{c5.get('nonbrand_volume_lo')} / {c5.get('nonbrand_volume_hi')}"))
            if c5.get("verdict_flip") != "예" or c5.get("submarket_type_hi") != "숨은 하위 시장 후보":
                fails.append(("score", "상한 기준이면 통과하는 군집에 verdict_flip이 없다",
                              f"{c5.get('verdict_flip')} · {c5.get('submarket_type_hi')}"))
            if c2.get("verdict_flip") or c2.get("rank_flip"):
                fails.append(("score", "뒤집히지 않는 군집에 flip 표시가 붙었다", ""))
            if (c6.get("rank_in_type_lo"), c6.get("rank_in_type_hi"), c6.get("rank_flip")) != ("1", "2", "예") \
                    or (c5.get("rank_in_type_lo"), c5.get("rank_in_type_hi"), c5.get("rank_flip")) != ("2", "1", "예"):
                fails.append(("score", "규모 미달 안의 하한/상한 순위 뒤집힘이 rank_flip으로 표시되지 않았다", ""))
            if "판정 뒤집힘" not in out or "순위 뒤집힘" not in out or "하한 " not in out:
                fails.append(("score", "출력에 하한~상한 범위·뒤집힘 경고가 없다", out[-300:]))
            kw = {r["keyword"]: r for r in read_csv(tmp / "submarket_keywords.csv")[1]}
            if (kw.get("릴렉스 체어", {}).get("group_volume_lo"), kw.get("릴렉스 체어", {}).get("group_volume_hi")) != ("40000", "79000"):
                fails.append(("score", "키워드 CSV의 group_volume_lo/hi 오류", ""))
            if by.get("2", {}).get("missing_metrics") != "1":
                fails.append(("score", "지표 없는 키워드를 missing_metrics로 세지 않았다", ""))
            if by.get("2", {}).get("seed_distance") != "2":
                fails.append(("score", "시드 거리 계산 오류", by.get("2", {}).get("seed_distance", "")))
            picks = json.load(open(tmp / "stage2_keywords.json", encoding="utf-8"))
            if any("헬리녹스" in p for p in picks) or not picks:
                fails.append(("score", "2단 조회 대상에 브랜드가 섞였거나 비었다 (T8)", str(picks)))
            if "접이식 흔들의자" in picks or "경량 캠핑의자" in picks:
                fails.append(("score", "volume_avg × 3 < yoy_gate인 키워드를 2단 대상에 넣었다 (P2)", str(picks)))

        # P1-3 — 브랜드 시드 경고
        b3 = list(base); b3[b3.index("캠핑의자")] = "헬리녹스"
        rc, out = run(b3 + ["--pick-out", "brand_picks.json"], tmp)
        if rc or "브랜드 시드" not in out:
            fails.append(("score", "브랜드 시드인데 경고가 없다 (P1-3)", out[-300:]))
        # P1-2 — score도 별 모양 퇴화를 표시한다
        rc, out = run([f"{S}/submarkets.py", "score", "star.json", "--universe", "universe.csv", "--seed", "S",
                       "--limit", "10", "--seed-all", "seed_all.json", "-o", "star.csv",
                       "--keywords-out", "star_kw.csv", "--pick-out", "star_picks.json"], tmp)
        if rc or "별 모양 퇴화" not in out or read_csv(tmp / "star.csv")[1][0].get("star_degenerate") != "예":
            fails.append(("score", "score 단계에서 별 모양 퇴화 표시가 없다 (P1-2)", out[-300:]))

        rc, out = run(base[:LIM] + ["23"] + base[LIM + 1:], tmp)
        if rc or "⚠️ 절단" not in out:
            fails.append(("score", "절단 시 잠정 경고가 없다 (T7)", out[-300:]))
        else:
            _, rows = read_csv(tmp / "submarkets.csv")
            if not all(r["truncated"] == "예" for r in rows):
                fails.append(("score", "절단인데 truncated 컬럼이 비었다", ""))

        # STEP 6 — 2단 조회 결과로 기준월·추세
        rc, out = run([f"{S}/check_refmonth.py", "all.json", "--queried-at", "2026-09-28"], tmp)
        if rc or LAST not in out:
            fails.append(("check_refmonth", "기준월 미출력", out[-300:]))
        rc, out = run([f"{S}/trend_yoy.py", "all.json", "-o", "trend.csv", "--gate", "3000"], tmp)
        if rc:
            fails.append(("trend_yoy", "비정상 종료", out[-300:]))

        # STEP 7 — 추세를 붙여 최종 판정 (문서 예시: --pick-out 없이)
        rc, out = run(base, tmp)            # 1차 판정 산출물로 되돌린다
        before = (tmp / "stage2_keywords.json").read_text(encoding="utf-8")
        step7 = [x for x in base if x not in ("--pick-out", "stage2_keywords.json")] + ["--trend", "trend.csv"]
        rc, out = run(step7, tmp)
        if rc:
            fails.append(("score --trend", "비정상 종료", out[-300:]))
        elif (tmp / "stage2_keywords.json").read_text(encoding="utf-8") != before or "크레딧 →" in out \
                or "10 × " in out:
            fails.append(("score --trend", "STEP 7이 2단 대상을 다시 쓰거나 크레딧을 재안내한다 (P2)", out[-300:]))
        else:
            _, rows = read_csv(tmp / "submarkets.csv")
            by = {r["community_id"]: r for r in rows}
            c2 = by.get("2", {})
            if c2.get("yoy_ex_last_median", "") == "" or c2.get("data_ref_month") != LAST:
                fails.append(("score --trend", "추세·기준월이 군집에 붙지 않았다", str(c2)[:200]))
            if c2.get("yoy_uncomputable_n") != "1":
                fails.append(("score --trend", "결측 창 키워드를 계산 불가로 세지 않았다 (T6)",
                              c2.get("yoy_uncomputable_n", "")))
            if c2.get("trend_status") != "판정" or by.get("5", {}).get("trend_status") != "추세 미조회":
                fails.append(("score --trend", "trend_status(판정·게이트 미달·계산 불가·추세 미조회) 구분 오류",
                              f"{c2.get('trend_status')} / {by.get('5', {}).get('trend_status')}"))

    if fails:
        print(f"실패 {len(fails)}건\n")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {str(detail).strip()[:200]}")
        print("\n문서의 예시와 스크립트가 어긋나 있다. 배포 전에 고칠 것.")
        return 1
    print("통과 — submarkets.py 계약 검증 완료 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
