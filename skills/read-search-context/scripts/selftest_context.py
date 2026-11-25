#!/usr/bin/env python3
"""이 스킬 고유 스크립트의 계약 검증 — SKILL.md 의 예시 흐름과 컬럼 · 키 목록이 실제 출력과 맞는지 본다.

합성 자료(assets/fixtures)만 쓴다. 네트워크 · API 호출 · 크레딧이 없다. 설정과 실행 기록은 임시 폴더에만 쓴다.

    python3 scripts/selftest_context.py        # 스킬 폴더에서 실행 (scripts/selftest.py 가 함께 돌린다)
"""
import csv, json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
FX = SKILL / "assets" / "fixtures"
FAKE_KEY = "SELFTEST-NOT-A-REAL-KEY"


def doc_lists():
    """SKILL.md «산출물» 코드 블록의 `파일명` + 들여쓴 컬럼 줄 → {파일명: [컬럼]}."""
    out, cur = {}, None
    for ln in (SKILL / "SKILL.md").read_text(encoding="utf-8").splitlines():
        if re.fullmatch(r"[a-z_]+\.(csv|jsonl?)", ln.strip()) and not ln.startswith(" "):
            cur = ln.strip(); out[cur] = []
        elif cur and re.fullmatch(r"\s{2}[a-z][a-z0-9_,\s]+", ln):
            out[cur] += [c.strip() for c in ln.split(",") if c.strip()]
        elif cur and not ln.strip():
            cur = None
    return out


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    fails = []
    ok = lambda cond, name, detail="": fails.append((name, str(detail)[-300:])) if not cond else None

    with tempfile.TemporaryDirectory() as td:
        ws = pathlib.Path(td)
        env = {k: v for k, v in os.environ.items() if k not in ("LM_API_BASE", "LM_API_KEY")}
        env.update(PYTHONIOENCODING="utf-8", LM_COLLECT_FIXTURES=str(FX), LM_CONFIG_DIR=str(ws / "conf"), LM_API_KEY=FAKE_KEY)

        def run(script, *args, e=None):
            p = subprocess.run([sys.executable, str(HERE / script), *args], cwd=ws, env=e or env, capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            return p.returncode, p.stdout + p.stderr

        # ── STEP 0 설정
        rc, out = run("collect.py", "status")
        ok(rc == 2 and "온보딩" in out, "status: 설정이 없으면 종료 코드 2 로 온보딩을 알린다", out)
        rc, out = run("collect.py", "setup", "--source", "api", "--base", "http://api.example.com", "--budget", "3000")
        ok(rc != 0 and "https" in out, "setup: https 가 아닌 서버 주소를 거부한다", out)
        rc, out = run("collect.py", "setup", "--source", "api", "--base", "https://api.example.com", "--gl", "kr", "--lang", "kr",
                      "--limit", "14", "--budget", "3000")
        st = ws / "work/read-search-context/settings.json"
        ok(rc == 0 and st.is_file() and "api.example.com" in out, "setup: 설정을 저장하고 호스트를 다시 보여 준다", out)
        ok(FAKE_KEY not in out and (not st.is_file() or FAKE_KEY not in st.read_text(encoding="utf-8")), "setup: 키를 화면 · 설정에 쓰지 않는다", out)
        ok((ws / "conf/api-base").is_file(), "setup: 서버 주소는 사용자 설정 폴더에 둔다(스킬 폴더 아님)")

        # 주소 · 키 찾는 순서 — 저장한 값이 먼저 · 환경변수 호스트가 다르면 멈춘다
        env2 = dict(env, LM_API_BASE="https://other-server.example.net", LM_API_KEY="SELFTEST-OTHER-KEY")
        rc, out = run("collect.py", "status", e=env2)
        ok(rc == 3 and "api.example.com" in out and "other-server.example.net" in out, "status: 저장한 주소와 환경변수 주소가 다르면 두 호스트를 보여 주고 종료 코드 3", out)
        ok("키: 있음 (사용자 설정 폴더)" not in out and "키: 있음 (환경변수)" in out, "status: 저장한 키가 없으면 환경변수 키를 쓰고 읽은 곳을 적는다", out)
        ok("다른 곳에서 읽었다" in out or "읽었다" in out, "status: 주소와 키를 읽은 곳이 다르면 알린다", out)
        rc, out = run("collect.py", "init", "--seed", "캠핑의자", e=env2)
        rx = pathlib.Path(out.strip().splitlines()[-1]) if rc == 0 else ws / "none"
        rc, out = run("collect.py", "seed", "--run", str(rx), e=env2)
        ok(rc == 5 and not (rx / "calls.jsonl").is_file(), "seed: 주소가 엇갈리면 부르기 전에 종료 코드 5 로 멈춘다", out)
        (ws / "conf/api-key").write_text("SELFTEST-SAVED-KEY\n", encoding="utf-8")
        rc, out = run("collect.py", "setup", "--source", "api", "--base", "https://api.example.com", "--gl", "kr", "--lang", "kr",
                      "--limit", "14", "--budget", "3000", "--override-env", e=env2)
        ok(rc == 0 and "사용자 설정 폴더" in out and "다른 키" in out and "SELFTEST" not in out, "setup --override-env: 확인한 환경변수 호스트를 넘기고 저장한 키를 먼저 쓴다", out)
        rc, out = run("collect.py", "seed", "--run", str(rx), e=env2)
        ok(rc == 0, "seed: 사용자가 확인한 환경변수 호스트면 진행한다", out)
        rc, out = run("collect.py", "status", e=dict(env2, LM_API_BASE="https://third.example.org"))
        ok(rc == 3 and "third.example.org" in out, "status: 환경변수 호스트가 바뀌면 다시 멈춘다", out)
        rc, out = run("collect.py", "status", e=dict(env2, LM_API_BASE="https://api.example.com/"))
        ok(rc == 0, "status: 같은 호스트면 멈추지 않는다", out)
        (ws / "conf/api-key").unlink()
        rc, out = run("collect.py", "setup", "--source", "api", "--base", "https://api.example.com", "--gl", "kr", "--lang", "kr",
                      "--limit", "14", "--budget", "3000")

        # ── STEP 1 견적 · 실행 기록 · 시드 확인
        rc, out = run("collect.py", "quote", "--limit", "100", "--serp", "18")
        ok(rc == 0 and "4702" in out and "상한이 아니다" in out and "월별 검색량" in out, "quote: 문서의 계산 예(4,702 · 월별 검색량 포함)와 같고 상한이 아님을 밝힌다", out)
        rc, out = run("collect.py", "quote", "--limit", "30", "--serp", "18", "--budget", "2000")
        ok(rc == 0 and "최대 33개" in out, "quote --budget: 관측 r 1.40 에서도 상한 안에 드는 경로 수를 낸다", out)
        rc, out = run("collect.py", "init", "--seed", "캠핑의자", "--gl", "xx", "--budget", "100")
        ok(rc != 0, "init: 모르는 국가를 거부한다", out)
        rc, out = run("collect.py", "init", "--seed", "캠핑의자", "--request", "캠핑의자 앞뒤로 뭘 검색해?")
        r = pathlib.Path(out.strip().splitlines()[-1]) if rc == 0 else ws / "none"
        ok(rc == 0 and (r / "request.json").is_file(), "init: 실행 폴더와 request.json 을 만든다", out)
        rc, out = run("collect.py", "seed", "--run", str(r))
        ok(rc == 0 and "표기가 요청과 다르다" in out, "seed: 응답 표기가 요청과 다르면 경고한다", out)

        # ── STEP 2 경로 · 검색량 · 원장
        rc, out = run("collect.py", "paths", "--run", str(r))
        ok(rc == 0 and "절단" in out and "890" in out, "paths: 받은 경로 수 = limit 이면 절단을 알린다(T7)", out)
        rc, out = run("collect.py", "volumes", "--run", str(r))
        ok(rc == 0 and (r / "raw/metrics.json").is_file(), "volumes: 검색량 원본을 한 파일로 저장한다", out)
        led = [json.loads(x) for x in (r / "calls.jsonl").read_text(encoding="utf-8").splitlines()] if (r / "calls.jsonl").is_file() else []
        ok([c["cost"] for c in led] == [1, 890, 21], "원장: 호출마다 cost_detail 실측을 적는다", led)

        # ── STEP 3 집계
        rc, out = run("context_paths.py", "--paths", str(r / "raw/path.json"), "--seed", "캠핑의자", "--metrics", str(r / "raw/metrics.json"),
                      "--limit", "14", "--out-dir", str(r / "out"))
        ok(rc == 0 and "표기변형 인접 2건" in out, "context_paths: 표기변형 인접을 세지 않고 건수를 적는다(T5)", out)
        docs = doc_lists()
        for name in ("context_edges.csv", "context_nodes.csv", "representative_paths.csv", "serp_targets.csv", "trend_targets.csv"):
            f = r / "out" / name
            head = next(csv.reader(open(f, encoding="utf-8-sig"))) if f.is_file() else []
            ok(head == docs.get(name), f"{name}: 컬럼이 SKILL.md 목록과 같다", f"문서 {docs.get(name)} · 실제 {head}")
        sm = json.loads((r / "out/context_summary.json").read_text(encoding="utf-8")) if (r / "out/context_summary.json").is_file() else {}
        ok(list(sm) == docs.get("context_summary.json"), "context_summary.json: 키가 SKILL.md 목록과 같다", list(sm))
        ok(sm.get("truncated") is True and sm.get("seed_paths") == 12 and sm.get("off_seed_paths") == 1 and sm.get("volume_missing") == 1
           and sm.get("actual_cost") == sm.get("formula_cost") == 890, "context_summary: 절단 · 시드 경로 · 공백 · 비용 대조", sm)
        nodes = {n["canonical"]: n for n in csv.DictReader(open(r / "out/context_nodes.csv", encoding="utf-8-sig"))} if (r / "out/context_nodes.csv").is_file() else {}
        ok((nodes.get("캠핑의자수리") or {}).get("volume_avg") == "" and (nodes.get("캠핑의자수리") or {}).get("volume_label") == "[데이터 공백]",
           "검색량 없는 검색어를 0 으로 바꾸지 않고 비운다", nodes.get("캠핑의자수리"))
        seed = nodes.get("캠핑의자") or {}
        ok(seed.get("volume_avg") == "74000" and seed.get("volume_avg_upper") == "83900" and seed.get("volume_label") == "[추정]",
           "표기 묶음의 검색량은 하한 · 상한을 함께 두고 [추정]으로 적는다", seed)
        ok(seed.get("google_avg") == "13320" and seed.get("naver_avg") == "60680" and seed.get("google_share") == "18.0",
           "한국 검색어 표에 하한 행의 구글 · 네이버 값과 구글 비중이 있다", {k: seed.get(k) for k in ("google_avg", "naver_avg", "google_share")})
        edges = list(csv.DictReader(open(r / "out/context_edges.csv", encoding="utf-8-sig"))) if (r / "out/context_edges.csv").is_file() else []
        top = next((e for e in edges if e["position"] == "post_seed" and e["depth"] == "1"), {})
        ok(top.get("to_canonical") == "캠핑의자추천" and top.get("path_count") == "5" and top.get("stem_count") == "3",
           "시드 바로 뒤 이음에 경로 수와 줄기 수가 함께 붙는다", top)
        ok(not any(e["from_canonical"] == e["to_canonical"] for e in edges), "같은 묶음끼리의 이음이 없다")

        # 시드 표기 변경 → 멈춤 → 규칙으로 통과
        sv = ws / "sun.json"
        sv.write_text(json.dumps({"request_detail": {"limit": 5}, "data": [["썬크림", "썬크림 추천"], ["썬크림", "썬스틱"]]}, ensure_ascii=False), encoding="utf-8")
        rc, out = run("context_paths.py", "--paths", str(sv), "--seed", "선크림", "--out-dir", str(ws / "sun"))
        ok(rc == 3 and "썬" in out and "--rules" in out, "시드 표기가 바뀌면 종료 코드 3 으로 멈추고 치환 규칙을 제안한다", out)
        rc, out = run("context_paths.py", "--paths", str(sv), "--seed", "선크림", "--rules", '{"썬":"선"}', "--out-dir", str(ws / "sun"))
        ok(rc == 0 and "절단 아님" in out, "치환 규칙(인라인 JSON)을 주면 집계한다", out)

        # 맥락 — 브랜드 표시 · 빈 칸은 건너뛴다
        ctx = ws / "context.json"
        ctx.write_text(json.dumps({"brands": {"ours": ["헬리녹스"], "competitors": ["⬜ 경쟁 브랜드", "콜맨"]}}, ensure_ascii=False), encoding="utf-8")
        rc, out = run("context_paths.py", "--paths", str(r / "raw/path.json"), "--seed", "캠핑의자", "--context", str(ctx), "--out-dir", str(ws / "ctx"))
        b = {n["canonical"]: n["brand"] for n in csv.DictReader(open(ws / "ctx/context_nodes.csv", encoding="utf-8-sig"))} if rc == 0 else {}
        ok(b.get("헬리녹스체어원") == "ours" and b.get("콜맨릴렉스체어") == "competitor" and b.get("캠핑의자") == "", "맥락 파일의 브랜드를 표시하고 빈 칸은 건너뛴다", b)
        rc, out = run("context_paths.py", "--paths", str(r / "raw/path.json"), "--seed", "캠핑의자", "--context", str(SKILL / "assets/context.starter.json"), "--out-dir", str(ws / "ctx2"))
        b2 = {n["brand"] for n in csv.DictReader(open(ws / "ctx2/context_nodes.csv", encoding="utf-8-sig"))} if rc == 0 else {"?"}
        ok(b2 == {""}, "채우지 않은 맥락 견본으로는 브랜드 표시가 생기지 않는다", b2)

        # ── STEP 3.5 작년 같은 시기와 비교
        rc, out = run("collect.py", "monthly", "--run", str(r), "--targets", str(r / "out/trend_targets.csv"))
        ok(rc == 0 and "캠핑의자 수리" in out and "캠핑 의자" in out, "monthly: 돌아오지 않은 검색어 · 요청하지 않은 표기 행을 알린다", out)
        mt = json.loads((r / "out/monthly_targets.json").read_text(encoding="utf-8")) if (r / "out/monthly_targets.json").is_file() else {}
        ok(mt and "캠핑 의자" not in {x["keyword"] for x in mt.get("data", [])} and len(mt.get("data", [])) == 8,
           "monthly: 요청한 검색어 행만 집계용 파일로 쓴다(요청하지 않은 표기 행 제외 · T5)", [x["keyword"] for x in mt.get("data", [])])
        rc, out = run("check_refmonth.py", str(r / "out/monthly_targets.json"), "--queried-at", "2026-11-25", "--json")
        (r / "out/refmonth.json").write_text(out, encoding="utf-8")
        rm = json.loads(out) if rc == 0 else {}
        ok(rm.get("data_ref_month") == "2026-10" and rm.get("lag_months") == 1 and rm.get("last_month_suspect") == 1,
           "check_refmonth: 기준월 · 지연 · 최신 달 미완결 의심을 읽는다(T1 · T2)", rm)
        rc, out = run("trend_yoy.py", str(r / "out/monthly_targets.json"), "-o", str(r / "out/trend.csv"), "--gate", "3000")
        tr = {x["keyword"]: x for x in csv.DictReader(open(r / "out/trend.csv", encoding="utf-8-sig"))} if rc == 0 else {}
        ok(tr.get("접이식 의자", {}).get("yoy") == "" and tr.get("캠핑의자", {}).get("last_month_flag") and "게이트 미만 1행" in out,
           "trend_yoy: 결측 월은 계산 불가(T6) · 최신 달 미완결 표시(T2) · 게이트 미만(T4)", out[-200:])

        # ── STEP 4 검색 결과 화면
        rc, out = run("collect.py", "serp", "--run", str(r), "--targets", str(r / "out/serp_targets.csv"))
        ok(rc == 0 and "저장본 없음" in out, "serp: 저장본 없는 대상을 공백으로 알린다", out)
        rc, out = run("serp_evidence.py", "--targets", str(r / "out/serp_targets.csv"), "--serp-dir", str(r / "raw/serp"), "--out", str(r / "out/evidence.json"))
        ev = json.loads((r / "out/evidence.json").read_text(encoding="utf-8")) if rc == 0 else []
        ok(ev and list(ev[0]) == docs.get("evidence.json"), "evidence.json: 키가 SKILL.md 목록과 같다", list(ev[0]) if ev else out)
        stc = [e["status"] for e in ev]
        ok(stc.count("ok") == 8 and stc.count("no_snapshot") == 2, "evidence: 저장본 있음 8 · 없음 2", stc)
        ok(ev and len(ev[0]["aio"]) == 4 and ev[0]["collected_at"], "AI 개요의 문단 · 목록 · 목록 안의 목록을 모두 읽고 수집일을 남긴다", ev[0] if ev else "")
        led = [json.loads(x) for x in (r / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        ok(list(led[0]) == docs.get("calls.jsonl"), "calls.jsonl: 키가 SKILL.md 목록과 같다", list(led[0]))
        ok(sum(c["cost"] for c in led) == 1161 and [c["status"] for c in led].count("no_snapshot") == 2, "원장: 누적 1,161(월별 검색량 9행 포함) · 저장본 없는 호출은 비용 0", led[-3:])
        blob = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in r.rglob("*") if p.is_file())
        ok(FAKE_KEY not in blob and "LM-API-KEY" not in blob, "실행 폴더 어디에도 키 · 요청 헤더가 없다")

        # 예산 가드 — 계획이 남은 예산을 넘으면 부르지 않는다 · 남은 예산만큼만 serp
        rc, out = run("collect.py", "init", "--seed", "캠핑의자", "--budget", "100")
        r2 = pathlib.Path(out.strip().splitlines()[-1])
        rc, out = run("collect.py", "paths", "--run", str(r2))
        ok(r2 != r, "init: 같은 초에 다시 만들어도 앞 실행 폴더를 덮지 않는다", r2)
        ok(rc == 4 and "예산 가드" in out and not (r2 / "raw/path.json").is_file(), "paths: 계획이 남은 예산을 넘으면 부르지 않는다(종료 코드 4)", out)
        rc, out = run("collect.py", "serp", "--run", str(r2), "--targets", str(r / "out/serp_targets.csv"))
        ok(rc == 0 and "3/10" in out and len(list((r2 / "raw/serp").glob("*.json"))) == 3, "serp: 남은 예산만큼만 부르고 못 부른 대상을 알린다", out)

        # 커넥터 원본 넣기
        rc, out = run("collect.py", "ingest", "--run", str(r2), "--tool", "path_finder", "--file", str(sv), "--params", "seed=선크림")
        ok(rc != 0 and "--cost" in out, "ingest: 비용을 모르면 0 으로 채우지 않고 멈춘다", out)
        rc, out = run("collect.py", "ingest", "--run", str(r2), "--tool", "path_finder", "--file", str(sv), "--params", "seed=선크림", "--cost", "1", "--expect", "9")
        ok(rc != 0 and "기대" in out, "ingest: 건수가 다르면 넣지 않는다", out)
        rc, out = run("collect.py", "ingest", "--run", str(r2), "--tool", "path_finder", "--file", str(sv), "--params", "seed=선크림", "--cost", "1", "--expect", "2")
        ok(rc == 0 and (r2 / "raw/path.json").is_file(), "ingest: 건수가 맞으면 원본과 원장에 넣는다", out)

        # ── STEP 5 · 6 해석 입력 · 조립 · 렌더 · 맵
        rc, out = run("build_report.py", "brief", "--run", str(r))
        ok(rc == 0 and all((r / "out" / f).is_file() for f in ("brief.md", "facts.json", "analysis.template.json")), "brief: 해석 입력 · 사실 목록 · 작성 틀을 낸다", out)
        an = json.loads((FX / "analysis.json").read_text(encoding="utf-8"))
        bad = json.loads(json.dumps(an))
        bad["paths"][0]["evidence"] = ["E99-1"]; bad["actions"] = bad["actions"][:2]; bad["summary"]["counter"] = ""
        (ws / "bad.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        rc, out = run("build_report.py", "assemble", "--run", str(r), "--analysis", str(ws / "bad.json"), "-o", str(r / "bad_report.json"))
        ok(rc == 1 and "E99-1" in out and "3~5" in out and "counter" in out and not (r / "bad_report.json").is_file(),
           "assemble: 없는 근거 번호 · 실행 카드 부족 · 반대 근거 없음을 거부한다", out)
        dup = json.loads(json.dumps(an)); dup["lede"] = dup["lede"].replace("{{F3}}", "{{F3}}개")
        (ws / "dup.json").write_text(json.dumps(dup, ensure_ascii=False), encoding="utf-8")
        rc, out = run("build_report.py", "assemble", "--run", str(r), "--analysis", str(ws / "dup.json"), "-o", str(r / "dup_report.json"))
        ok(rc == 1 and "단위" in out, "assemble: 사실 ID 뒤에 단위를 또 쓰면 멈춘다(«100개 개» 방지)", out)
        js = json.loads(json.dumps(an)); js["lede"] = js["lede"] + " 시드 검색량은 {{F7}}로 적다."
        (ws / "josa.json").write_text(json.dumps(js, ensure_ascii=False), encoding="utf-8")
        rc, out = run("build_report.py", "assemble", "--run", str(r), "--analysis", str(ws / "josa.json"), "-o", str(r / "josa_report.json"))
        ok(rc == 1 and "{{F7}}으로" in out, "assemble: «건» 뒤 «로»를 «으로»로 고치라고 멈춘다", out)
        ok(json.loads((r / "out/facts.json").read_text(encoding="utf-8")).get("F17", {}).get("unit") == "개", "facts: 창을 옮기면 방향이 바뀌는 검색어 수(F17)가 있다")
        ok("캠핑 의자" in (nodes.get("캠핑의자") or {}).get("variants", "") or "캠핑 의자" in seed.get("variants", ""), "검색어 표의 묶인 표기에 검색량 응답 표기도 보인다", seed.get("variants"))
        facts = json.loads((r / "out/facts.json").read_text(encoding="utf-8")) if (r / "out/facts.json").is_file() else {}
        ok(facts.get("B1V", {}).get("value") == 12100, "facts: 갈래 검색량 사실(B1V)이 있다", facts.get("B1V"))
        ok(facts.get("B1G", {}).get("value") == 25.0 and facts.get("B1G", {}).get("unit") == "%", "facts: 갈래 구글 비중 사실(B1G)이 있다", facts.get("B1G"))
        brief = (r / "out/brief.md").read_text(encoding="utf-8") if (r / "out/brief.md").is_file() else ""
        ok("더 앞:" in brief and "단위까지" in brief, "brief: 시드 앞 갈래 하위는 «더 앞:» · 단위 안내가 있다")
        rc, out = run("build_report.py", "assemble", "--run", str(r), "--analysis", str(FX / "analysis.json"), "-o", str(r / "report.json"))
        ok(rc == 0, "assemble: 견본 해석으로 report.json 을 조립한다", out)
        rc, out = run("render_report.py", str(r / "report.json"), "-o", str(r / "report.html"))
        page = (r / "report.html").read_text(encoding="utf-8") if (r / "report.html").is_file() else ""
        ok(rc == 0 and "{{" not in page and (r / "report.calls.csv").is_file() and (r / "report.cost.json").is_file(),
           "render: 조립한 리포트가 렌더링 계약을 통과하고 호출 로그 · 비용 보고서가 나온다", out)
        ok("경고" not in out.splitlines()[-1] if out.strip() else False, "render: 렌더러 경고가 없다", out)
        rep_j = json.loads((r / "report.json").read_text(encoding="utf-8")) if (r / "report.json").is_file() else {}
        kinds = [s_["kind"] for s_ in rep_j.get("sections", [])]
        trend_txt = json.dumps([s_ for s_ in rep_j.get("sections", []) if s_["kind"] == "trend"], ensure_ascii=False)
        ok(kinds[:2] == ["summary", "trend"] and rep_j.get("scope", {}).get("latest_month") == "2026-10" and "2026-10" in rep_j.get("boundary", ""),
           "report: 요약 다음에 추이 섹션 · 데이터 범위와 경계 문장에 기준월", kinds)
        ok('"role": "pct_change"' in trend_txt and '"role": "abs_change"' in trend_txt and "계산 불가" in trend_txt and "게이트 미만" in trend_txt
           and "미완결" in trend_txt, "report: 전년비 표에 절대 증감 · 계산 불가 · 게이트 미만 · 미완결 표시가 있다", trend_txt[:200])
        ok("volume_trend" not in json.dumps(rep_j.get("facts"), ensure_ascii=False), "report: volume_trend 를 사실로 쓰지 않는다(T3)")
        rc, out = run("build_map.py", "--run", str(r), "-o", str(r / "context_map.html"))
        mp = (r / "context_map.html").read_text(encoding="utf-8") if (r / "context_map.html").is_file() else ""
        ok(rc == 0 and 'id="d"' in mp and "캠핑의자" in mp and "http" not in mp.split("<script>")[-1], "build_map: 외부 자원 없는 단독 HTML 을 낸다", out)

    # 문서가 가리키는 스크립트 · 하위 명령이 실제로 있는가
    md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for script, sub in set(re.findall(r"python3 scripts/(\w+\.py)(?: (\w+))?", md)):
        f = HERE / script
        ok(f.is_file(), f"SKILL.md 가 가리키는 scripts/{script} 가 있다")
        if f.is_file() and sub and script in ("collect.py", "build_report.py"):
            ok(f'"{sub}"' in f.read_text(encoding="utf-8"), f"scripts/{script} 에 하위 명령 {sub} 가 있다")
    ex = SKILL / "assets" / "report.example.json"
    ok(ex.is_file() and json.loads(ex.read_text(encoding="utf-8")).get("meta", {}).get("skill") == "read-search-context",
       "assets/report.example.json 이 이 스킬의 견본이다")

    if fails:
        print(f"실패 {len(fails)}건\n")
        for name, detail in fails:
            print(f"  ✗ {name}" + (f"\n      {detail.strip()[:260]}" if detail.strip() else ""))
        return 1
    print("통과 — read-search-context 고유 스크립트 계약 검증 완료 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
