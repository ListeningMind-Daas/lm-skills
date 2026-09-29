#!/usr/bin/env python3
"""validate-product-ideas 자가검증 — 공유 selftest + 이 스킬 고유 파이프라인.

공유 `selftest.py`(사본)는 공유 스크립트만 안다. 이 스킬 고유 스크립트 `idea_signals.py`와
SKILL.md STEP 4~6의 예시 명령 순서(check_refmonth → trend_yoy → build_universe →
coverage_curve → idea_signals)는 여기서 검사한다. 먼저 공유 selftest를 돌리고, 실패하면 멈춘다.

검사하는 것:
  · SKILL.md «산출물»에 적힌 컬럼 목록 == idea_signals.py 실제 CSV 헤더 (순서까지)
  · 표기변형 그룹은 하한(최댓값)·상한(합)을 함께 낸다 (T5) — 하한≠상한 픽스처(제로슈가/제로슈거)
  · 순위는 하한 기준, 상한에서 뒤집히면 rank_flip·카드 경고 (T5) — 뒤집힘 픽스처(다이어트아이스크림)
  · 결측 월 → 계산 불가, 기저 미달 → 게이트 제외, 2단 밖 → 별도 집계 (T6·T4)
  · 마지막 달 제외가 기본이고 카드에 표시된다 (T2) · 기준월/조회일/지연이 카드 헤더에 있다 (T1)
  · 확장 호출이 limit에 붙으면 «하한» 표시 (T7) · 브랜드 사전이 비면 경고 (T8)

합성 픽스처만 쓴다 — API 호출·크레딧 없음.

    python3 scripts/selftest_ideas.py        # 스킬 폴더에서 실행
"""
import csv, json, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL_MD = HERE.parent / "SKILL.md"
MONTHS = [f"{y}-{m:02d}" for y in (2023, 2024, 2025, 2026) for m in range(1, 13)]
LAST = "2026-07"
MONTHS = MONTHS[: MONTHS.index(LAST) + 1]


def series(base, growth=0.0, missing=(), dip_last=False):
    out = []
    for i, m in enumerate(MONTHS):
        if m in missing:
            out.append({"month": m, "gg": 10, "nv_code": 1})       # total 키 없음
            continue
        yr = int(m[:4]) - 2023
        v = int(base * (1 + growth) ** yr * (1.5 if m[5:7] in ("06", "07", "08") else 1.0))
        if dip_last and m == LAST:
            v = int(v * 0.2)
        out.append({"month": m, "gg": v // 4, "nv": v - v // 4, "total": v})
    return out


def spike_prev_last(s, value=400):
    """전년 같은 달(마지막 달 − 12)만 크게 — 마지막 달 포함 창의 전년 기저는 게이트를 넘고,
    마지막 달 제외 창(판정 창)의 전년 기저는 넘지 못한다 (P1-4 회귀)."""
    prev_last = f"{int(LAST[:4]) - 1}-{LAST[5:7]}"
    for r in s:
        if r["month"] == prev_last:
            r["total"], r["gg"], r["nv"] = value, value // 4, value - value // 4
    return s


def am(kw, vol):
    return {"keyword": kw, "ads_metrics": {"volume_total": vol, "volume_avg": vol // 12,
            "volume_trend": -0.5, "cpc": 0.3, "competition": "MEDIUM", "competition_index": 50,
            "gg_volume_total": vol // 4, "nv_volume_total": vol - vol // 4}}


def fixture(t: pathlib.Path):
    universe = [
        ("저당 아이스크림", 60000), ("제로슈가 아이스크림", 41000), ("제로슈거 아이스크림", 40000),
        ("저당 아이스크림 추천", 18000), ("알룰로스 아이스크림", 9000), ("단백질 아이스크림", 12000),
        ("다이어트 아이스크림", 30000), ("다이어트아이스크림", 15000), ("혈당스파이크 간식", 7000), ("당뇨 아이스크림", 5000),
        ("라라콘 바", 15000), ("아이스크림 칼로리", 22000), ("무설탕 아이스크림", 150),
        # 회귀 픽스처 (2026-09-28 실데이터 평가 결함)
        ("아이스크림 다이어트", 30000),      # 어순 변형 — 시계열까지 같다 → 같은 검색의 중복(T5 ①)
        ("간식 혈당스파이크", 800),          # 어순 변형 — 시계열이 다르다 → 별개 검색으로 센다
        ("당줄이기 간식", 2000),             # 게이트 창 불일치(P1-4): 마지막 달 포함 창 기저만 크다
        ("아이스크림 케이크", 5500),         # (미분류) — 2단에 넣지 않는다
        ("혈당 아이스크림", 0),              # 0건 행 — 죽은 시드
    ]
    (t / "metrics_stage1.json").write_text(json.dumps(
        {"data": [am(k, v) for k, v in universe]}, ensure_ascii=False), encoding="utf-8")
    stage2 = [
        ("저당 아이스크림", series(3000, 0.4)),
        ("제로슈가 아이스크림", series(2000, 0.3)),
        ("제로슈거 아이스크림", series(1900, 0.3)),
        ("저당 아이스크림 추천", series(900, 0.5, dip_last=True)),
        ("알룰로스 아이스크림", series(400, 0.8)),
        ("단백질 아이스크림", series(600, -0.2, missing=("2026-05",))),
        ("다이어트 아이스크림", series(1500, -0.1)),
        ("혈당스파이크 간식", series(350, 0.2)),
        ("라라콘 바", series(800, 0.1)),
        ("무설탕 아이스크림", series(3, 0.0)),
        ("아이스크림 다이어트", series(1500, -0.1)),     # 다이어트 아이스크림과 완전히 같은 시계열
        ("간식 혈당스파이크", series(90, 0.5)),
        ("당줄이기 간식", spike_prev_last(series(50, 0.0))),
    ]
    data = []
    for k, s in stage2:
        row = am(k, dict(universe)[k]); row["monthly_volume"] = s; data.append(row)
    (t / "metrics_stage2.json").write_text(json.dumps({"data": data}, ensure_ascii=False), encoding="utf-8")
    (t / "seeds.json").write_text(json.dumps(["저당 아이스크림", "다이어트 아이스크림"],
                                             ensure_ascii=False), encoding="utf-8")
    # 호출별 intent_finder 응답 — 첫 호출은 limit(5)에 붙어 절단
    (t / "if_저당아이스크림.json").write_text(json.dumps({"data": [
        "제로슈가 아이스크림", "저당 아이스크림 추천", "알룰로스 아이스크림", "단백질 아이스크림",
        "무설탕 아이스크림"]}, ensure_ascii=False), encoding="utf-8")
    (t / "if_다이어트아이스크림.json").write_text(json.dumps({"data": [
        "혈당스파이크 간식", "당뇨 아이스크림", "아이스크림 칼로리"]}, ensure_ascii=False), encoding="utf-8")
    # 1단 시드 응답: 요청 표기와 응답 표기가 다르다(띄어쓰기) · 0건 행 · 미반환 시드
    (t / "seeds_by_layer.json").write_text(json.dumps({
        "hypothesis": "당류를 줄인 아이스크림", "gl": "kr",
        "해결책": ["저당아이스크림", "제로슈가 아이스크림", "초저당 젤라또바"],
        "문제": ["혈당 아이스크림"], "목표": ["다이어트 아이스크림"]}, ensure_ascii=False), encoding="utf-8")
    (t / "stage1_seeds.json").write_text(json.dumps({"data": [
        am("저당 아이스크림", 60000), am("제로슈가 아이스크림", 41000), am("다이어트 아이스크림", 30000),
        {"keyword": "혈당 아이스크림", "ads_metrics": {"volume_total": 0}}]}, ensure_ascii=False), encoding="utf-8")
    layers = json.load(open(HERE.parent / "assets" / "layers.example.json", encoding="utf-8"))
    (t / "layers_nobrand.json").write_text(json.dumps(layers, ensure_ascii=False), encoding="utf-8")
    layers["brands"] = ["라라콘"]
    (t / "layers.json").write_text(json.dumps(layers, ensure_ascii=False), encoding="utf-8")


def run(cmd, cwd):
    p = subprocess.run([sys.executable] + cmd, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def documented_columns(block_name):
    """SKILL.md «산출물»에서 파일명 다음 들여쓴 줄들을 컬럼 목록으로 읽는다."""
    lines = SKILL_MD.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith(block_name):
            cols = []
            for nxt in lines[i + 1:]:
                if not nxt.startswith("  ") or not nxt.strip():
                    break
                cols += [c.strip() for c in nxt.split(",") if c.strip()]
            return cols
    return None


def main():
    rc, out = run([str(HERE / "selftest.py")], HERE.parent)
    print(out.strip())
    if rc:
        print("\n공유 selftest 실패 — 고유 검사로 넘어가지 않는다")
        return 1

    fails = []
    S = str(HERE)
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        fixture(t)

        # STEP 5.5 — SKILL.md 예시 그대로
        rc, o = run([f"{S}/check_refmonth.py", "metrics_stage2.json", "--queried-at", "2026-09-28"], t)
        if rc or LAST not in o or "지연          : 약 2개월" not in o:
            fails.append(("check_refmonth", "기준월·지연 미출력", o[-300:]))

        rc, o = run([f"{S}/trend_yoy.py", "metrics_stage2.json", "-o", "trend.csv", "--gate", "300"], t)
        if rc:
            fails.append(("trend_yoy", "비정상 종료", o[-300:]))

        rc, o = run([f"{S}/build_universe.py", "--metrics", "metrics_stage1.json", "--themes", "layers.json",
                     "--seeds", "seeds.json", "--round", "S1=seeds.json",
                     "--round", "R1=if_저당아이스크림.json", "--round", "R1=if_다이어트아이스크림.json",
                     "--trend", "trend.csv", "--queried-at", "2026-09-28",
                     "-o", "keyword_universe.csv"], t)
        if rc:
            fails.append(("build_universe", "층 사전으로 비정상 종료", o[-300:]))

        rc, o = run([f"{S}/coverage_curve.py", "--metrics", "metrics_stage1.json",
                     "--rules", '{"슈가":"슈거"}', "--themes", "layers.json",
                     "--round", "S1=seeds.json", "--round", "R1-a=if_저당아이스크림.json@5",
                     "--round", "R1-b=if_다이어트아이스크림.json@5"], t)
        if rc or "절단된 라운드: R1-a" not in o:
            fails.append(("coverage_curve", "호출별 절단 기록이 안 나옴", o[-300:]))

        # STEP 2 — 죽은 시드 판정은 canonical 대조 (P2: 응답 표기가 요청과 달라도 살아 있음)
        rc, o = run([f"{S}/prepare_stages.py", "seeds", "--seeds", "seeds_by_layer.json",
                     "--metrics", "stage1_seeds.json", "--layers", "layers.json", "-o", "seed_check.json"], t)
        if rc:
            fails.append(("prepare_stages seeds", "비정상 종료", o[-300:]))
        else:
            sc = json.load(open(t / "seed_check.json", encoding="utf-8"))
            dead = {d["seed"]: d["status"] for d in sc["dead"]}
            if dead != {"초저당 젤라또바": "미반환", "혈당 아이스크림": "0건"}:
                fails.append(("prepare_stages seeds", "죽은 시드 판정이 틀렸다 (미반환·0건 구분)", str(dead)))
            if "저당아이스크림" not in sc["alive"]:
                fails.append(("prepare_stages seeds", "응답 표기가 다른 시드를 죽었다고 판정 (canonical 대조 안 함)",
                              str(sc["alive"])))
            if sc.get("silent_layers") != ["문제"] or "죽은 시드도 과금" not in o:
                fails.append(("prepare_stages seeds", "시드 전멸 층·과금 안내가 없다", o[-300:]))

        # STEP 5 — 2단 대상 선정 (P2: 스크립트로)
        run([f"{S}/build_universe.py", "--metrics", "metrics_stage1.json", "--themes", "layers.json",
             "-o", "universe_pre.csv"], t)
        rc, o = run([f"{S}/prepare_stages.py", "stage2", "--universe", "universe_pre.csv",
                     "--layers", "layers.json", "--min-volume", "2500", "-o", "stage2_keywords.json"], t)
        if rc:
            fails.append(("prepare_stages stage2", "비정상 종료", o[-300:]))
        else:
            st2 = json.load(open(t / "stage2_keywords.json", encoding="utf-8"))
            bad = [k for k in ("아이스크림 케이크", "혈당 아이스크림", "아이스크림 다이어트", "제로슈거 아이스크림",
                               "라라콘 바") if k in st2]
            if bad:
                fails.append(("prepare_stages stage2", "(미분류)·죽은 행·중복·표기변형 비대표·무층 브랜드가 2단에 들어갔다",
                              str(bad)))
            # 문제 층: 게이트(2,500) 통과 2개 → 최소 3을 볼륨순으로 채운다(당줄이기 간식) · 간식 혈당스파이크는 안 든다
            if "당줄이기 간식" not in st2 or "간식 혈당스파이크" in st2 or "채움 1" not in o \
                    or "10 ×" not in o or "(미분류)" not in o:
                fails.append(("prepare_stages stage2", "층당 최소 3 채움·크레딧·(미분류) 보고가 없다", o[-400:]))

        # STEP 3.5 — 확장 반환 검토 (P1-1): 제외 패턴이 반환 대부분을 덮으면 시드를 동음이의 의심으로 표시
        lx = json.load(open(t / "layers.json", encoding="utf-8"))
        lx["exclude"] = [{"pattern": "혈당스파이크|당뇨", "reason": "테스트 — 무관 검색"}]
        lx["proxies"] = ["다이어트 아이스크림", "아이스크림 칼로리"]
        (t / "layers_review.json").write_text(json.dumps(lx, ensure_ascii=False), encoding="utf-8")
        rc, o = run([f"{S}/prepare_stages.py", "review", "--layers", "layers_review.json",
                     "--expansion", "if_저당아이스크림.json@5", "--expansion", "if_다이어트아이스크림.json@5",
                     "--metrics", "metrics_stage1.json", "-o", "proxy_review.csv"], t)
        if rc or "동음이의 의심" not in o or "확장하지 않은 프록시" not in o:
            fails.append(("prepare_stages review", "동음이의 의심 시드·미확장 프록시 표시가 없다", o[-400:]))

        cmd = [f"{S}/idea_signals.py", "--universe", "keyword_universe.csv", "--trend", "trend.csv",
               "--stage2-metrics", "metrics_stage2.json", "--layers", "layers.json",
               "--seed-check", "seed_check.json",
               "--expansion", "if_저당아이스크림.json@5", "--expansion", "if_다이어트아이스크림.json@5",
               "--queried-at", "2026-09-28", "--idea", "low-sugar-icecream-kr",
               "-o", "layer_signals.csv", "--brand-out", "brand_share.csv", "--card", "card.md"]
        rc, o = run(cmd, t)
        if rc:
            fails.append(("idea_signals", "문서 예시로 비정상 종료", o[-400:]))
        else:
            want = documented_columns("layer_signals_{idea}.csv")
            got = next(csv.reader(open(t / "layer_signals.csv", encoding="utf-8-sig")))
            if want != got:
                fails.append(("idea_signals", "SKILL.md 컬럼 목록 ≠ 실제 헤더", f"문서 {want}\n코드 {got}"))
            want_b = documented_columns("brand_share_{idea}.csv")
            got_b = next(csv.reader(open(t / "brand_share.csv", encoding="utf-8-sig")))
            if want_b != got_b:
                fails.append(("idea_signals", "브랜드 CSV 컬럼 ≠ 문서", f"문서 {want_b}\n코드 {got_b}"))
            L = {r["layer"]: r for r in csv.DictReader(open(t / "layer_signals.csv", encoding="utf-8-sig"))}
            card = (t / "card.md").read_text(encoding="utf-8")
            sol = L.get("해결책", {})
            # 제로슈가/제로슈거는 한 그룹 — 하한은 최댓값 41,000, 상한은 합 81,000 (lo≠hi 픽스처)
            if (sol.get("volume_lo") != str(60000 + 41000 + 9000 + 12000 + 150)
                    or sol.get("volume_hi") != str(60000 + 81000 + 9000 + 12000 + 150)):
                fails.append(("idea_signals", "표기변형 그룹 하한(최댓값)·상한(합)이 틀렸다 (T5)",
                              f"해결책 volume_lo={sol.get('volume_lo')} volume_hi={sol.get('volume_hi')}"))
            if sol.get("nonbrand_share_lo") != "1.0" or sol.get("nonbrand_share_hi") != "1.0":
                fails.append(("idea_signals", "논브랜드 비중 하한·상한 누락", str(sol)))
            # 다이어트 아이스크림/다이어트아이스크림: 목표 층 하한 30,000 < 제품 탐색 40,000 < 상한 45,000
            goal, explore = L.get("목표", {}), L.get("제품 탐색", {})
            if (goal.get("volume_lo"), goal.get("volume_hi")) != ("30000", "45000"):
                fails.append(("idea_signals", "목표 층 하한·상한이 틀렸다", str(goal)))
            if (goal.get("rank_lo"), goal.get("rank_hi"), goal.get("rank_flip")) != ("3", "2", "뒤집힘") \
                    or (explore.get("rank_lo"), explore.get("rank_hi"), explore.get("rank_flip")) != ("2", "3", "뒤집힘"):
                fails.append(("idea_signals", "상한에서 순위가 뒤집혔는데 rank_flip 표시가 없다 (T5)",
                              f"목표 {goal}\n제품 탐색 {explore}"))
            if sol.get("rank_flip") != "" or sol.get("rank_lo") != "1":
                fails.append(("idea_signals", "뒤집히지 않은 층에 rank_flip이 찍혔다", str(sol)))
            order = [r["layer"] for r in csv.DictReader(open(t / "layer_signals.csv", encoding="utf-8-sig"))]
            if order.index("제품 탐색") > order.index("목표"):
                fails.append(("idea_signals", "층 정렬이 하한 기준이 아니다", str(order)))
            if "하한 122,150 ~ 상한 162,150" not in card or "순위가 뒤집힌다" not in card \
                    or "볼륨(건) [추정] 하한 ~ 상한" not in card or "[파생]" in card:
                fails.append(("idea_signals", "카드에 «하한 ~ 상한» 범위·뒤집힘 경고·[추정] 라벨이 없다", card[:600]))
            B = {r["brand"]: r for r in csv.DictReader(open(t / "brand_share.csv", encoding="utf-8-sig"))}
            lara = B.get("라라콘", {})
            if (lara.get("volume_lo"), lara.get("volume_hi"), lara.get("share_of_brand_volume_lo"),
                    lara.get("share_of_brand_volume_hi")) != ("15000", "15000", "1.0", "1.0"):
                fails.append(("idea_signals", "브랜드 하한·상한 컬럼이 틀렸다", str(lara)))
            if sol.get("not_computable_groups") != "1":
                fails.append(("idea_signals", "결측 월 키워드가 «계산 불가»로 안 잡힘 (T6)", str(sol)))
            if sol.get("gated_out_groups") != "1":
                fails.append(("idea_signals", "기저 미달 키워드가 게이트에서 안 빠짐 (T4)", str(sol)))
            prob = L.get("문제", {})
            # P1-4 회귀: 게이트는 판정 창(마지막 달 제외)과 같은 창의 기저로 — «당줄이기 간식»은 게이트 미달
            if prob.get("gated_out_groups") != "1":
                fails.append(("idea_signals", "게이트 창이 판정 창과 다르다 — 마지막 달 포함 창 기저로 통과시킴 (P1-4)",
                              str(prob)))
            # P2 회귀: 시계열이 같은 어순 변형은 한 번만, 다른 어순 변형은 별개로 — 카드에 적는다
            if goal.get("same_search_dup_rows") != "1" or "어순 변형 1쌍" not in card \
                    or "같은 검색의 중복 1행" not in card:
                fails.append(("idea_signals", "어순 변형 처리(동일 시계열 1회·다르면 별개)가 카드·CSV에 없다",
                              f"{goal}\n{card[:900]}"))
            # P1-2 회귀: T2 의심 건수 · T4/T6 제외 수 · 죽은 시드 목록과 «검색 신호 없음 [데이터 공백]»
            if not re.search(r"마지막 달 미완결 의심\(T2\): 2단 조회 \d+행 중 1행", card) \
                    or "게이트 미달 2 · 계산 불가(결측 월) 1" not in card:
                fails.append(("idea_signals", "카드에 T2 의심 건수·T4/T6 제외 수가 없다 (P1-2)", card[:900]))
            if "초저당 젤라또바" not in card or "검색 신호 없음 [데이터 공백]" not in card \
                    or "문제 층: 시드 전멸" not in card:
                fails.append(("idea_signals", "카드에 죽은 시드 목록·«검색 신호 없음 [데이터 공백]»이 없다 (P1-2)", card))
            # P1-5 회귀: 논브랜드 비중의 시드 인접 편향을 적는다
            if "시드에서 확장한 키워드 공간" not in card:
                fails.append(("idea_signals", "논브랜드 비중의 시드 인접 편향 문장이 없다 (P1-5)", ""))
            if prob.get("not_in_stage2_groups") != "2":   # 당뇨 아이스크림 · 혈당 아이스크림(0건)
                fails.append(("idea_signals", "2단 조회 밖 그룹 집계 누락", str(L.get("문제"))))
            if sol.get("expansion_truncated") != "하한":
                fails.append(("idea_signals", "절단 호출이 있는데 «하한» 표시 없음 (T7)", str(sol)))
            if "지연 약 2개월" not in card or "조회일 2026-09-28" not in card:
                fails.append(("idea_signals", "카드 헤더에 기준월/조회일/지연 없음 (T1)", card[:300]))
            if "마지막 달 제외" not in card:
                fails.append(("idea_signals", "마지막 달 처리 방식이 카드에 없음 (T2)", card[:300]))
            if "라라콘" not in (t / "brand_share.csv").read_text(encoding="utf-8-sig"):
                fails.append(("idea_signals", "브랜드 집중도에 사전 브랜드가 없음 (T8)", ""))

            # 마지막 달 포함 모드는 카드에 «잠정»으로 찍혀야 한다
            rc, o = run(cmd[:-2] + ["--card", "card_incl.md", "--include-last-month"], t)
            if rc or "잠정" not in (t / "card_incl.md").read_text(encoding="utf-8"):
                fails.append(("idea_signals", "--include-last-month가 카드에 표시되지 않음", o[-200:]))

            # P1-1 회귀: exclude는 집계에서 빠지고, 미검토 프록시가 좌우하는 층은 인용 금지 표시
            rc, o = run([f"{S}/idea_signals.py", "--universe", "keyword_universe.csv", "--trend", "trend.csv",
                         "--stage2-metrics", "metrics_stage2.json", "--layers", "layers_review.json",
                         "--expansion", "if_다이어트아이스크림.json@5", "--queried-at", "2026-09-28",
                         "-o", "l_px.csv", "--brand-out", "b_px.csv", "--card", "card_px.md"], t)
            PX = {r["layer"]: r for r in csv.DictReader(open(t / "l_px.csv", encoding="utf-8-sig"))} if not rc else {}
            cpx = (t / "card_px.md").read_text(encoding="utf-8") if not rc else ""
            if rc or PX.get("문제", {}).get("excluded_keywords") != "3" \
                    or "제외한 검색 (동음이의·무관" not in cpx:
                fails.append(("idea_signals", "exclude 패턴이 집계에서 빠지지 않았다 (P1-1)", o[-300:] + str(PX.get("문제"))))
            if "미검토 프록시" not in PX.get("목표", {}).get("proxy_flag", "") or "인용하지 않는다" not in cpx:
                fails.append(("idea_signals", "미검토 프록시가 좌우하는 층을 표시하지 않았다 (P1-1)", str(PX.get("목표"))))
            lx["reviewed"] = ["다이어트 아이스크림", "아이스크림 다이어트"]
            (t / "layers_reviewed.json").write_text(json.dumps(lx, ensure_ascii=False), encoding="utf-8")
            run([f"{S}/idea_signals.py", "--universe", "keyword_universe.csv", "--trend", "trend.csv",
                 "--stage2-metrics", "metrics_stage2.json", "--layers", "layers_reviewed.json",
                 "--expansion", "if_다이어트아이스크림.json@5", "--queried-at", "2026-09-28",
                 "-o", "l_rv.csv", "--brand-out", "b_rv.csv"], t)
            RV = {r["layer"]: r for r in csv.DictReader(open(t / "l_rv.csv", encoding="utf-8-sig"))}
            if RV.get("목표", {}).get("proxy_flag"):
                fails.append(("idea_signals", "reviewed에 적은 프록시가 여전히 미검토로 표시된다", str(RV.get("목표"))))

        # P1-3 회귀: 고정 지연 서술 금지 — 기준월은 응답에서 읽는다
        doc = SKILL_MD.read_text(encoding="utf-8")
        ev = (HERE.parent / "evals" / "evals.json").read_text(encoding="utf-8")
        for bad in ("약 2개월", "9월 말 조회라면 데이터는 7월", "38~75%"):
            if bad in doc or bad in ev:
                fails.append(("SKILL.md/evals", f"고정·범위 서술이 남아 있다: «{bad}»", ""))

        # 브랜드 사전이 비면 경고해야 한다 (T8 무력화 감지)
        run([f"{S}/build_universe.py", "--metrics", "metrics_stage1.json", "--themes", "layers_nobrand.json",
             "--trend", "trend.csv", "-o", "u_nb.csv"], t)
        rc, o = run([f"{S}/idea_signals.py", "--universe", "u_nb.csv", "--trend", "trend.csv",
                     "--stage2-metrics", "metrics_stage2.json", "--queried-at", "2026-09-28", "-o", "l_nb.csv", "--brand-out", "b_nb.csv"], t)
        if rc or "브랜드 사전이 비어 있다" not in o:
            fails.append(("idea_signals", "빈 브랜드 사전 경고 없음 (T8)", o[-200:]))

    if fails:
        print(f"\n고유 검사 실패 {len(fails)}건\n")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {detail.strip()[:300]}")
        return 1
    print("통과 — idea_signals.py 계약·문서 예시 파이프라인 검증 완료 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
