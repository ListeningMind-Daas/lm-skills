#!/usr/bin/env python3
"""trace-search-journey 자가검증 — SKILL.md의 예시 명령이 실제로 도는지, 문서의 컬럼 목록이
스크립트 실제 출력과 같은지 확인한다.

이 파일은 공유 사본이 아니라 **이 스킬 고유의 selftest**다. 이 스킬은 공유 재사용 스크립트
(check_refmonth·trend_yoy·build_universe·coverage_curve)를 쓰지 않으므로 그 검사는 없고,
고유 스크립트 둘(path_budget·journey_paths)을 검사한다.

합성 픽스처만 쓴다 — API 호출도 크레딧도 필요 없다.

    python3 scripts/selftest.py        # 스킬 폴더에서 실행
"""
import csv, json, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent / "SKILL.md"
STAGES = HERE.parent / "assets" / "stages.camping-chair-kr.example.json"


def fixture(tmp: pathlib.Path):
    # 출발 후보 ads_metrics — 하나는 볼륨 게이트 미달, 하나는 볼륨 없음
    metrics = {"data": [
        {"keyword": "캠핑의자", "ads_metrics": {"volume_total": 480000, "volume_avg": 38000}},
        {"keyword": "릴렉스체어", "ads_metrics": {"volume_total": 120000, "volume_avg": 9000}},
        {"keyword": "캠핑 체어 추천", "ads_metrics": {"volume_total": 60000, "volume_avg": 4000}},
        {"keyword": "하이백 캠핑의자 수리", "ads_metrics": {"volume_total": 3000, "volume_avg": 200}},
        {"keyword": "캠핑의자 커버 세탁", "ads_metrics": {}},
    ]}
    (tmp / "start_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False), encoding="utf-8")

    # 파일럿 — 경로 4개 · 고유 키워드 7개 → r = 1.75
    pilot_paths = [["캠핑의자", "캠핑의자 추천", "헬리녹스 체어원"],
                   ["캠핑의자", "캠핑의자 최저가"],
                   ["백패킹 의자", "캠핑의자", "코베아 캠핑의자"],
                   ["캠핑의자 후기", "캠핑의자", "캠핑의자 추천"]]
    pu = {k for p in pilot_paths for k in p}
    (tmp / "resp_pilot.json").write_text(json.dumps({
        "result": "OK",
        "request_detail": {"keyword": "캠핑의자", "gl": "kr", "time_point": "curr", "limit": 20},
        "cost_detail": {"total_cost": 50 + 40 * len(pu)}, "data": pilot_paths,
    }, ensure_ascii=False), encoding="utf-8")

    # 본 호출 — limit 6에 경로 6개(절단) · 표기변형 인접(캠핑 의자 → 캠핑의자) · 1단계 경로 1개
    paths = [["캠핑 의자", "캠핑의자", "캠핑의자 추천", "헬리녹스 체어원"],
             ["캠핑의자", "캠핑의자 추천", "캠핑의자 최저가"],
             ["차박 준비물", "캠핑의자", "캠핑의자 추천", "캠핑의자 비교"],
             ["캠핑의자 추천", "코베아 캠핑의자", "코베아 캠핑의자 후기"],
             ["릴렉스체어", "릴랙스체어", "캠핑의자"],
             ["캠핑의자"]]
    u = {k for p in paths for k in p}
    (tmp / "resp_camping_chair.json").write_text(json.dumps({
        "result": "OK",
        "request_detail": {"keyword": "캠핑의자", "gl": "kr", "time_point": "curr", "limit": 6},
        "cost_detail": {"total_cost": 50 + 40 * len(u)}, "data": paths,
    }, ensure_ascii=False), encoding="utf-8")
    (tmp / "resp_empty.json").write_text(json.dumps({
        "result": "OK", "request_detail": {"keyword": "캠핑의자 커버 세탁", "gl": "kr", "limit": 50},
        "cost_detail": {"total_cost": 50}, "data": [],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp / "stages.json").write_text(STAGES.read_text(encoding="utf-8"), encoding="utf-8")


def run(cmd, tmp):
    p = subprocess.run([sys.executable] + cmd, cwd=tmp, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def header(path):
    return next(csv.reader(open(path, encoding="utf-8-sig")))


def rows(path):
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def doc_columns(name):
    """SKILL.md 산출물 블록에서 파일명 다음 들여쓴 줄들을 컬럼 목록으로 읽는다."""
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith(name):
            cols = []
            for nxt in lines[i + 1:]:
                if not re.match(r"^\s{2}[a-z][a-z0-9_,\s]+$", nxt):
                    break
                cols += [c.strip() for c in nxt.split(",") if c.strip()]
            return cols
    return None


def main():
    fails = []
    S = str(HERE)
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        fixture(tmp)

        # ── STEP 2 예시 명령 그대로
        rc, out = run([f"{S}/path_budget.py", "--metrics", "start_metrics.json", "--limit", "50",
                       "--budget", "6000", "--pilot", "resp_pilot.json", "-o", "path_plan.csv"], tmp)
        if rc:
            fails.append(("path_budget", "문서 예시 명령이 비정상 종료", out[-300:]))
        else:
            got = header(tmp / "path_plan.csv")
            want = doc_columns("path_plan.csv")
            if want != got:
                fails.append(("path_budget", f"문서 컬럼 ≠ 실제 출력: 문서 {want} / 실제 {got}", ""))
            d = {r["keyword"]: r for r in rows(tmp / "path_plan.csv")}
            # 이미 소진 = 5(ads_metrics) + 파일럿 50+40×7=330 → 335. r=1.75 → 계획 50+40×ceil(87.5)=3,570
            if d["캠핑의자"]["decision"] != "pilot_done":
                fails.append(("path_budget", "파일럿 시드를 다시 호출 대상으로 잡았다", ""))
            if d["릴렉스체어"]["decision"] != "call" or d["릴렉스체어"]["planned_cost"] != "3570":
                fails.append(("path_budget", "결정식 50+40×ceil(limit×r) 계산이 틀리다",
                              json.dumps(d["릴렉스체어"], ensure_ascii=False)))
            if d["캠핑 체어 추천"]["decision"] != "hold_budget":
                fails.append(("path_budget", "누적 계획이 예산을 넘는데 보류하지 않았다", ""))
            if d["하이백 캠핑의자 수리"]["decision"] != "hold_volume":
                fails.append(("path_budget", "볼륨 게이트 미달을 보류하지 않았다", ""))
            if d["캠핑의자 커버 세탁"]["decision"] != "hold_no_volume":
                fails.append(("path_budget", "볼륨 결측을 0으로 취급했다", ""))
            if "= 335" not in out:
                fails.append(("path_budget", "이미 소진(ads_metrics+파일럿) 크레딧이 출력에 없다", out[-300:]))

        # 파일럿 없이 — 가정 r
        rc, out = run([f"{S}/path_budget.py", "--metrics", "start_metrics.json", "--limit", "30",
                       "--budget", "3000", "-o", "plan2.csv"], tmp)
        if rc or "assumed" not in out:
            fails.append(("path_budget", "파일럿 없는 경우 r 출처(assumed)를 표시하지 않는다", out[-300:]))

        # T5 범위 — 볼륨 게이트는 키워드 단위다. 표기변형 후보(하한 1,500 ≠ 상한 2,300)를 그룹으로
        # 묶어 게이트에 쓰면 800짜리 변형도 통과해 버린다. 각자 자기 volume_avg로 판정돼야 한다.
        (tmp / "variant_metrics.json").write_text(json.dumps({"data": [
            {"keyword": "캠핑 매트", "ads_metrics": {"volume_avg": 1500}},
            {"keyword": "캠핑매트", "ads_metrics": {"volume_avg": 800}}]}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([f"{S}/path_budget.py", "--metrics", "variant_metrics.json", "--limit", "10",
                       "--budget", "100000", "-o", "plan_variant.csv"], tmp)
        vr = {r["keyword"]: r for r in rows(tmp / "plan_variant.csv")} if not rc else {}
        if rc or (vr.get("캠핑 매트", {}).get("volume_avg"), vr.get("캠핑매트", {}).get("volume_avg")) != ("1500", "800") \
                or vr["캠핑매트"]["decision"] != "hold_volume" or vr["캠핑 매트"]["decision"] != "call":
            fails.append(("path_budget", "표기변형 후보의 볼륨을 그룹으로 합치거나 최댓값으로 바꿨다 "
                                         "— 게이트는 키워드 단위여야 한다 (T5 경계 미적용 범위)", out[-300:]))

        # ── 회귀(2026-09-28 실데이터 평가) — keyword_info가 표기를 바꾸고(캠핑의자 → 캠핑 의자)
        #    표기변형 후보를 한 행으로 합친다(무선 청소기 + 무선청소기 요청 → 1행, 과금 4)
        (tmp / "real_metrics.json").write_text(json.dumps({
            "request_detail": {"keywords": ["캠핑의자", "무선 청소기", "무선청소기", "릴렉스체어", "없는후보"]},
            "cost_detail": {"total_cost": 5},
            "data": [{"keyword": "캠핑 의자", "ads_metrics": {"volume_avg": 69366}},
                     {"keyword": "무선청소기", "ads_metrics": {"volume_avg": 121800}},
                     {"keyword": "릴렉스 체어", "ads_metrics": {"volume_avg": 5873}}]}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([f"{S}/path_budget.py", "--metrics", "real_metrics.json", "--limit", "35",
                       "--budget", "5000", "--pilot", "resp_pilot.json", "-o", "plan_real.csv"], tmp)
        rr = {r["keyword"]: r for r in rows(tmp / "plan_real.csv")} if not rc else {}
        if rc:
            fails.append(("path_budget", "실응답 모양 픽스처에서 비정상 종료", out[-300:]))
        else:
            if rr.get("캠핑 의자", {}).get("decision") != "pilot_done":
                fails.append(("P1-1", "파일럿 시드(캠핑의자)를 응답 표기(캠핑 의자)와 canonical로 맞추지 못해 다시 호출로 잡았다",
                              json.dumps(rr.get("캠핑 의자"), ensure_ascii=False)))
            elif rr["캠핑 의자"]["limit"] != "20":
                fails.append(("P2", "pilot_done 행의 limit이 파일럿 limit(20)이 아니다", rr["캠핑 의자"]["limit"]))
            if rr.get("무선 청소기", {}).get("decision") != "hold_merged":
                fails.append(("P1-2", "응답에서 합쳐진 표기변형 후보가 plan에서 조용히 사라졌다(hold_merged 아님)", ""))
            if rr.get("없는후보", {}).get("decision") != "hold_not_returned":
                fails.append(("P1-2", "요청했으나 응답에 없는 후보를 미반환으로 남기지 않았다", ""))
            if "keyword_info 5 " not in out:
                fails.append(("P1-2", "keyword_info 비용을 반환 행 수로 셌다(과금은 요청 수·cost_detail)", out[:200]))
            if "파일럿 limit 20 ≠ 본 limit 35" not in out:
                fails.append(("P2", "파일럿 limit과 본 limit이 다른데 경고가 없다", out[-300:]))
            if rr.get("릴렉스 체어", {}).get("decision") == "hold_budget" and "예산 안 최대 limit" not in out:
                fails.append(("P2", "hold_budget인데 예산 안 최대 limit 제안이 없다", out[-300:]))
        rc, out = run([f"{S}/path_budget.py", "--metrics", "start_metrics.json", "--limit", "300",
                       "--budget", "5000", "--pilot", "resp_pilot.json", "-o", "plan300.csv"], tmp)
        if rc or "예산 안 최대 limit" not in out or "1시드 limit 65" not in out:
            fails.append(("P2", "전부 hold_budget인데 최대 limit 제안(1시드: (5000-335-50)//40/1.75 → 65)이 없거나 틀리다", out[-300:]))
        txt = SKILL.read_text(encoding="utf-8") + (HERE.parent / "scripts" / "path_budget.py").read_text(encoding="utf-8")
        if "안전한 방향" in txt or "안전한 쪽" in txt:
            fails.append(("P2", "«파일럿 r은 늘 안전한 쪽» 주장이 남았다 — 실측 반례(과소 40)", ""))
        st = json.loads(STAGES.read_text(encoding="utf-8"))
        sit = next(t["pattern"] for t in st["themes"] if t["name"] == "상황·조건").split("|")
        if any(w in sit for w in ("아이", "하이", "로우", "릴랙스")):
            fails.append(("P2", "예시 사전 상황·조건에 넓은 부분 문자열(아이·하이·로우·릴랙스)이 남았다", ""))

        # ── STEP 4 예시 명령 그대로 (단계 사전은 assets 예시 파일)
        rc, out = run([f"{S}/journey_paths.py", "--run", "캠핑의자=resp_camping_chair.json@6",
                       "--run", "캠핑의자 커버 세탁=resp_empty.json",
                       "--stages", "stages.json", "--category", "camping-chair-kr",
                       "--out-dir", "out", "--queried-at", "2026-09-28", "--plan", "path_plan.csv"], tmp)
        if rc:
            fails.append(("journey_paths", "문서 예시 명령이 비정상 종료", out[-300:]))
        else:
            o = tmp / "out"
            for fname in ("journey_edges_camping-chair-kr.csv", "journey_nodes_camping-chair-kr.csv"):
                got = header(o / fname)
                want = doc_columns(fname.replace("camping-chair-kr", "{category}"))
                if want != got:
                    fails.append(("journey_paths", f"{fname}: 문서 컬럼 ≠ 실제 출력: 문서 {want} / 실제 {got}", ""))
            edges = rows(o / "journey_edges_camping-chair-kr.csv")
            if any(e["from_canonical"] == e["to_canonical"] for e in edges):
                fails.append(("journey_paths", "표기변형 인접(자기 전이)이 전이로 남았다 (T5)", ""))
            if any(e["from_canonical"] == "캠핑의자" and e["to_canonical"] == "캠핑의자" for e in edges):
                fails.append(("journey_paths", "canonical 그룹 미적용", ""))
            top = [e for e in edges if e["from_canonical"] == "캠핑의자" and e["to_canonical"] == "캠핑의자추천"]
            if not top or top[0]["path_count"] != "3" or top[0]["position"] != "post_seed":
                fails.append(("journey_paths", "시드 다음 전이 집계·방향 판정이 틀리다",
                              json.dumps(top, ensure_ascii=False)))
            pre = [e for e in edges if e["to_canonical"] == "캠핑의자" and e["from_canonical"] == "차박준비물"]
            if not pre or pre[0]["position"] != "pre_seed":
                fails.append(("journey_paths", "시드 앞 전이를 pre_seed로 판정하지 못했다", ""))
            nodes = {n["canonical"]: n for n in rows(o / "journey_nodes_camping-chair-kr.csv")}
            if nodes.get("캠핑의자최저가", {}).get("stage") != "구매·가격":
                fails.append(("journey_paths", "단계 사전 순서(먼저 선언된 단계 우선)가 적용되지 않았다", ""))
            if nodes.get("헬리녹스의자원", {}).get("is_brand") != "1":
                fails.append(("journey_paths", "브랜드 표시가 canonical에 적용되지 않았다", ""))
            summ = (o / "journey_summary_camping-chair-kr.md").read_text(encoding="utf-8")
            for must, why in (("반환분 기준", "절단(반환=limit)인데 «반환분 기준» 경고가 없다 (T7)"),
                              ("[데이터 공백]", "빈 응답을 데이터 공백으로 표기하지 않았다"),
                              ("세션 인접", "세션 인접 해석 한계가 요약 헤더에 없다"),
                              ("표기변형 인접(전이에서 제외) 2건", "표기변형 인접 수가 틀리다 (T5)"),
                              ("50+40×U", "크레딧 식 재계산 열이 없다"),
                              ("`time_point` = curr", "헤더에 time_point 실제 값이 없다 (P2)"),
                              ("시드가 낀 인접", "단계 간 전이에서 시드가 낀 인접을 빼지 않았다 (P2)"),
                              ("시드 제외", "단계 분포에서 시드 자신을 빼지 않았다 (P2)"),
                              ("요청 limit이 시드마다 다르다", "limit이 다른 시드를 경고 없이 나란히 두었다 (P2)"),
                              ("보류한 출발 후보", "path_plan 보류 후보가 요약에 없다 (P2)"),
                              ("hold_volume", "보류 후보 표에 판정이 없다 (P2)"),
                              ("[실제 데이터] · 식 재계산 합", "크레딧 소진 줄에 라벨이 없다 (P2)")):
                if must not in summ:
                    fails.append(("journey_paths", why, ""))
            if "구매" in summ and "전환율" in summ:
                fails.append(("journey_paths", "요약이 전환율을 말한다 — 이 데이터로는 못 한다", ""))

        # 인라인 JSON 단계 사전도 받는가 (문서가 인라인도 허용한다고 적었다)
        rc, out = run([f"{S}/journey_paths.py", "--run", "캠핑의자=resp_camping_chair.json",
                       "--stages", '{"themes":[{"name":"구매·가격","pattern":"최저가"}],"brands":[]}',
                       "--category", "inline", "--out-dir", "out2"], tmp)
        if rc:
            fails.append(("journey_paths", "인라인 --stages JSON으로 비정상 종료", out[-300:]))
        elif "· 절단 ·" not in out:
            fails.append(("journey_paths", "@limit 생략 시 request_detail.limit으로 절단 판정하지 못했다", out[-300:]))

    if fails:
        print(f"실패 {len(fails)}건\n")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {detail.strip()[:200]}")
        print("\n문서의 예시와 스크립트가 어긋나 있다. 배포 전에 고칠 것.")
        return 1
    print("통과 — 고유 스크립트 2종(path_budget·journey_paths) 계약 검증 완료 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
