#!/usr/bin/env python3
"""find-journey-leaks 고유 스크립트 자가검증 — SKILL.md 예시 명령 · 함정 처리 · 문서 컬럼 = 실제 출력.

공유 `selftest.py`가 이 파일도 함께 돌린다. 합성 픽스처(assets/fixtures — 런닝화 KR 모양)만 쓰므로
API 호출도 크레딧도 필요 없다. 픽스처는 일부러 함정을 밟는다:
  T5 — 시드 표기 흔들림(런닝화/러닝화/런닝 화) · 표기변형 인접 · 같은 그룹 다른 볼륨(하한≠상한) · 완전 중복 행 · 같은 검색 중복
  T7 — 반환 = limit 인 본 호출(절단)과 반환 < limit 인 본 호출 · 대체된 파일럿
  T8 — 브랜드 없이 끝나는 경로 · 미등록 브랜드(호카) · 브랜드 비교 검색
  그 밖 — 브랜드 시드 · path_finder 시드 표기 변경 · 예산 초과 시드 · [추정] 없는 메모
  별칭 대조(0.1.1 P0 회귀) — 라틴+한글 혼합 짧은 별칭의 공백 변형(`sk 매직`) · 짧은 라틴 별칭 경계(`lg`↔`lg전자`/`algae`) ·
    `on`↔`running` · 별칭 누락 의심 경고(`alias_misses`)

    python3 scripts/selftest_leaks.py      # 스킬 폴더에서
"""
import csv, json, os, pathlib, re, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SKILL = ROOT / "SKILL.md"
FIX = ROOT / "assets" / "fixtures"
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
CAT = "running-shoes-kr"


def run(args, cwd):
    p = subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=ENV)
    return p.returncode, p.stdout + p.stderr


def doc_columns(name):
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == name:
            cols = []
            for nxt in lines[i + 1:]:
                if not re.match(r"^\s{2}[a-z][a-z0-9_,\s]+$", nxt):
                    break
                cols += [c.strip() for c in nxt.split(",") if c.strip()]
            return cols
    return None


def rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def header(path):
    with open(path, encoding="utf-8-sig") as f:
        return next(csv.reader(f))


def doc_command(step_title):
    """SKILL.md 의 해당 STEP 첫 bash 블록 명령(줄 이음 합침)."""
    text = SKILL.read_text(encoding="utf-8")
    sec = text.split(step_title, 1)[1]
    block = sec.split("```bash", 1)[1].split("```", 1)[0]
    cmd = block.replace("\\\n", " ").strip().splitlines()[0]
    return cmd


def shell_split(cmd):
    import shlex
    return shlex.split(cmd)


def alias_cases():
    """별칭 대조 단위 검사 (0.1.1 P0 회귀) → 실패 목록. 공개 브랜드 문자열만 쓴다."""
    sys.path.insert(0, str(HERE))
    from leak_lib import LeakDict
    D = LeakDict({"gl": "kr", "seeds": ["정수기"],
                  "brand_sets": {"my": {"name": "LG전자", "aliases": ["LG", "엘지"]},
                                 "competitors": [{"name": "SK매직", "aliases": []}, {"name": "코웨이", "aliases": []},
                                                 {"name": "On", "aliases": ["on"]}, {"name": "아디다스", "aliases": ["adidas"]}]},
                  "themes": [{"name": "일반", "class": "generic", "pattern": "정수기|런닝|running|shoes"}]})
    want = {
        "sk 매직 얼음정수기": ("competitor", "SK매직"),    # 실데이터 P0 — 0.1.0은 generic
        "sk매직": ("competitor", "SK매직"),
        "SK 매직": ("competitor", "SK매직"),
        "SK  매직  정수기": ("competitor", "SK매직"),
        "lg전자 정수기": ("my_brand", "LG전자"),
        "lg 정수기": ("my_brand", "LG전자"),
        "LG전자": ("my_brand", "LG전자"),
        "엘지 정수기": ("my_brand", "LG전자"),
        "코웨이정수기": ("competitor", "코웨이"),          # 한글 복합어 — 0.1.0과 같다
        "코웨이 정수기": ("competitor", "코웨이"),
        "algae 정수기": ("generic", ""),                  # 짧은 라틴 별칭이 라틴 단어 안에 걸리면 안 된다
        "lgbt 정수기": ("generic", ""),
        "msk매직 정수기": ("generic", ""),                # 혼합 별칭의 라틴 쪽 끝도 경계를 본다
        "running shoes": ("generic", ""),                 # `on` ↛ running
        "salomon running": ("generic", ""),
        "on running": ("competitor", "On"),
        "onrunning": ("generic", ""),                      # 붙여 쓴 짧은 라틴 별칭은 잡지 않는다(0.1.0과 같다)
        "adidasshoes": ("competitor", "아디다스"),         # 5자 이상 라틴 별칭 부분 일치 — 0.1.0과 같다
        "sk매직 vs 코웨이": ("brand_vs", "SK매직/코웨이"),
    }
    bad = []
    for kw, (c, b) in want.items():
        r = D.classify(kw)
        if (r["node_class"], r["brand"]) != (c, b):
            bad.append(f"{kw!r} → {r['node_class']}/{r['brand']} (기대 {c}/{b})")
    miss = D.alias_misses("algae 정수기", [])
    if miss != [("LG전자", "lg")]:
        bad.append(f"별칭 누락 검사가 algae ⊃ lg 를 내놓지 않는다: {miss}")
    if D.alias_misses("sk 매직 얼음정수기", ["SK매직"]):
        bad.append("이미 그 브랜드로 분류된 노드를 별칭 누락으로 내놓는다")
    return bad


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    fails = []
    ok = lambda cond, what, detail="": None if cond else fails.append((what, str(detail)[-400:]))
    S = str(HERE)
    for b in alias_cases():
        ok(False, "별칭 대조 회귀(0.1.1 P0)", b)
    with tempfile.TemporaryDirectory() as td:
        t = pathlib.Path(td)
        (t / "raw").mkdir()
        for f in FIX.glob("*.json"):
            shutil.copy(f, t / "raw" / f.name)
        shutil.copytree(ROOT / "assets", t / "assets", dirs_exist_ok=True)
        shutil.copytree(HERE, t / "scripts", dirs_exist_ok=True)

        # ── STEP 1 문서 명령 그대로
        cmd = shell_split(doc_command("### STEP 1"))
        ok(cmd[:2] == ["python3", "scripts/make_leak_dict.py"], "STEP 1 문서 명령을 찾지 못했다", cmd)
        rc, out = run(cmd[1:], t)
        ok(rc == 0 and (t / "leak_dict.json").is_file(), "STEP 1 문서 명령이 비정상 종료", out)
        d = json.loads((t / "leak_dict.json").read_text(encoding="utf-8"))
        ok(all(th.get("pattern") for th in d["themes"]), "사전 themes 패턴이 비었다", d["themes"])
        ok(d["brand_sets"]["my"]["name"] == "아식스" and len(d["brand_sets"]["competitors"]) == 3,
           "브랜드 슬롯이 실행 입력으로 채워지지 않았다", d["brand_sets"])

        # 브랜드 시드 거부 (eval 1)
        rc, out = run([f"{S}/make_leak_dict.py", "--gl", "kr", "--seeds", "나이키 런닝화", "--my", "아식스",
                       "--competitor", "나이키;nike", "-o", "bs.json"], t)
        ok(rc == 4 and "브랜드" in out, "브랜드 시드를 거부하지 않는다(종료 코드 4)", out)
        rc, out = run([f"{S}/make_leak_dict.py", "--gl", "kr", "--seeds", "나이키 런닝화", "--competitor", "나이키;nike",
                       "--allow-brand-seed", "-o", "bs.json"], t)
        ok(rc == 0 and json.loads((t / "bs.json").read_text(encoding="utf-8")).get("brand_seed"),
           "--allow-brand-seed 로 브랜드 출발 표본을 표시하지 않는다", out)
        rc, out = run([f"{S}/make_leak_dict.py", "--gl", "kr", "--seeds", "런닝화", "--competitor", "나이키;n",
                       "-o", "x.json"], t)
        ok(rc == 2, "2글자 미만 별칭을 막지 않는다", out)
        rc, out = run([f"{S}/make_leak_dict.py", "--gl", "kr", "--seeds", "런닝화", "--my", "아식스;asics",
                       "--competitor", "오니츠카;asics", "-o", "x.json"], t)
        ok(rc == 2, "두 브랜드에 겹친 별칭을 막지 않는다", out)

        # ── STEP 2 · 3 원장 + 예산 게이트
        rec = lambda *a: run([f"{S}/record_call.py", "--ledger", "calls.jsonl", "--gl", "kr", *a], t)
        rc, out = rec("--tool", "keyword_info", "--role", "seed_volume", "--raw", "raw/ki_seeds.json", "--step", "시드 확인")
        ok(rc == 0, "record_call keyword_info 비정상 종료", out)
        rc, out = rec("--tool", "path_finder", "--role", "pilot", "--seed", "런닝화", "--limit", "5",
                      "--raw", "raw/pf_pilot_running.json", "--step", "파일럿")
        ok(rc == 0 and "절단" in out, "record_call 이 반환 = limit 을 절단으로 적지 않는다(T7)", out)
        rc, out = run([f"{S}/leak_budget.py", "--ledger", "calls.jsonl", "--seeds", "런닝화,러닝화 추천", "--limit", "12",
                       "--budget", "3000", "--volume-nodes", "7", "-o", "leak_plan.csv"], t)
        plan = rows(t / "leak_plan.csv") if not rc else []
        ok(rc == 0 and [p["decision"] for p in plan] == ["recall_pilot", "hold_budget"],
           "예산 게이트: 파일럿 시드 재호출 · 예산 초과 시드 보류가 아니다", out)
        ok("1330" in out and "예산 안 최대 limit" in out, "계획 비용 50 + 40 × ceil(12 × 2.0 × 1.3) 또는 최대 limit 제안이 없다", out)
        ok(header(t / "leak_plan.csv") == doc_columns("leak_plan.csv"), "leak_plan.csv 컬럼 ≠ SKILL.md",
           header(t / "leak_plan.csv"))
        rec("--tool", "path_finder", "--role", "main", "--seed", "런닝화", "--limit", "12", "--raw", "raw/pf_running.json", "--step", "본 호출")
        rec("--tool", "path_finder", "--role", "main", "--seed", "러닝화 추천", "--limit", "12", "--raw", "raw/pf_running_reco.json", "--step", "본 호출")
        rec("--tool", "keyword_info", "--role", "node_volume", "--raw", "raw/ki_nodes.json", "--step", "노드 볼륨")

        # ── STEP 4 문서 명령 그대로
        cmd = shell_split(doc_command("### STEP 4"))
        rc, out = run(cmd[1:], t)
        ok(rc == 0, "STEP 4 문서 명령이 비정상 종료", out)
        o = t / "out"
        for name, fn in [("leak_calls_{category}.csv", f"leak_calls_{CAT}.csv"), ("leak_share_{category}.csv", f"leak_share_{CAT}.csv"),
                         ("leak_edges_{category}.csv", f"leak_edges_{CAT}.csv"), ("leak_hubs_{category}.csv", f"leak_hubs_{CAT}.csv"),
                         ("leak_nodes_{category}.csv", f"leak_nodes_{CAT}.csv"), ("brand_candidates_{category}.csv", f"brand_candidates_{CAT}.csv")]:
            ok((o / fn).is_file() and header(o / fn) == doc_columns(name), f"{name} 컬럼 ≠ SKILL.md",
               header(o / fn) if (o / fn).is_file() else "파일 없음")
        R = json.loads((o / f"leak_result_{CAT}.json").read_text(encoding="utf-8"))
        calls = {c["id"]: c for c in rows(o / f"leak_calls_{CAT}.csv")}
        hubs = {h["canonical"]: h for h in rows(o / f"leak_hubs_{CAT}.csv")}
        nodes = {n["canonical"]: n for n in rows(o / f"leak_nodes_{CAT}.csv")}
        share = rows(o / f"leak_share_{CAT}.csv")
        edges = rows(o / f"leak_edges_{CAT}.csv")
        summary = (o / f"leak_summary_{CAT}.md").read_text(encoding="utf-8")

        # T7 — 요청 limit vs 반환, 절단이면 판정 보류
        ok(calls["C3"]["requested"] == "12" and calls["C3"]["returned"] == "12" and calls["C3"]["truncated"] == "yes",
           "T7: 절단 호출의 요청 limit · 반환 · 절단이 원장 CSV 에 없다", calls.get("C3"))
        ok(calls["C4"]["truncated"] == "no" and calls["C2"]["used_in_analysis"] == "superseded",
           "T7: 비절단 호출 · 대체된 파일럿 표시가 틀렸다", (calls.get("C4"), calls.get("C2")))
        ok("판정 보류" in hubs["러닝화순위"]["verdict_note"], "T7: 절단 시드의 내 브랜드 0 허브에 판정 보류가 없다", hubs["러닝화순위"])
        ok("반환분 기준" in summary and R["verdict_hold"], "T7: 요약에 «반환분 기준» 절단 경고가 없다")
        ok(next(s for s in share if s["seed"] == "전체")["truncated"] == "mixed", "T7: 전체 행 절단이 mixed 가 아니다")
        ok(all(c["cost_actual"] == c["cost_formula"] for c in calls.values()), "실측 비용 ≠ 식 재계산 (50 + 40 × U · 1 × N)", calls)

        # T5 — canonical · 표기변형 인접 · 볼륨 하한/상한
        ok(R["totals"]["variant_loops"] == 2, "T5: 표기변형 인접(런닝화→러닝화 · 런닝화→런닝 화)을 2건으로 세지 않는다", R["totals"])
        ok(all(e["from_canonical"] != e["to_canonical"] for e in edges), "T5: 같은 canonical 인접이 엣지에 들어갔다")
        ok({"런닝화", "러닝화", "런닝 화"} <= set(nodes["러닝화"]["variants"].split(";")), "T5: 시드 표기변형이 한 그룹으로 묶이지 않았다",
           nodes.get("러닝화"))
        h = hubs["러닝화순위"]
        ok((h["volume_lo"], h["volume_hi"]) == ("1000", "1400"), "T5: 표기변형 그룹 볼륨 하한(최댓값)·상한(합)이 틀렸다", h)
        h = hubs["쿠션좋은러닝화"]
        ok((h["volume_lo"], h["volume_hi"]) == ("720", "720"), "T5: 같은 검색의 중복(지표 일치)을 상한에 이중 계상했다", h)
        ok(nodes["디시러닝화"]["volume_status"] == "not_returned", "요청했는데 미반환인 볼륨을 데이터 공백으로 남기지 않는다")
        ok("완전 중복 행 1행 합침" in out, "keyword_info 완전 중복 행을 합치지 않는다", out)

        # T8 — 두 분모 · no_brand · 미등록 브랜드 후보 · 허브는 논브랜드
        allr = [s for s in share if s["seed"] == "전체"]
        nb = next(s for s in allr if s["outcome"] == "no_brand")
        comp = next(s for s in allr if s["brand"] == "나이키")
        ok(nb["paths"] == "3" and nb["share_branded_paths"] == "", "T8: no_brand 행이 없거나 브랜드 도달 분모에 들어갔다", nb)
        ok(comp["share_all_paths"] == "0.25" and comp["share_branded_paths"] == "0.3077",
           "T8: 진행 경로 대비 · 브랜드 도달 경로 대비 두 분모가 아니다", comp)
        ok(any(c["token"] == "호카" for c in rows(o / f"brand_candidates_{CAT}.csv")), "T8: 미등록 브랜드(호카) 후보를 못 뽑는다")
        ok(all(x["node_class"] not in ("competitor", "brand_vs", "seed") for x in hubs.values()), "T8: 허브 표에 브랜드 · 시드 노드가 들어갔다")
        ok(hubs["러닝화순위"]["group"] == "leaking" and hubs["쿠션좋은러닝화"]["group"] == "contested",
           "허브 그룹 판정이 정의와 다르다", (hubs["러닝화순위"]["group"], hubs["쿠션좋은러닝화"]["group"]))
        ok(any(e["edge_type"] == "switch" for e in edges), "내 브랜드 → 경쟁 브랜드 인접(switch)을 못 잡는다")
        ok(R["totals"]["off_seed"] == 1 and R["totals"]["seed_terminal"] == 1, "off_seed · seed_terminal 을 분모에서 빼고 세지 않는다", R["totals"])
        vr = json.loads((o / f"volume_request_{CAT}.json").read_text(encoding="utf-8"))
        ok(vr["planned_cost"] == len(vr["keywords"]) > 0, "노드 볼륨 요청 목록의 계획 비용이 1 × M 이 아니다", vr)

        # 시드 표기 변경 — path_finder 가 시드를 다른 표기로 돌려주면 멈춘다
        rc, _ = run([f"{S}/make_leak_dict.py", "--gl", "kr", "--seeds", "런닝화", "--competitor", "나이키;nike", "-o", "nv.json"], t)
        run([f"{S}/record_call.py", "--ledger", "v.jsonl", "--tool", "path_finder", "--role", "main", "--seed", "런닝화",
             "--limit", "3", "--raw", "raw/pf_seed_variant.json"], t)
        rc, out = run([f"{S}/journey_leaks.py", "--ledger", "v.jsonl", "--dict", "nv.json", "--category", "v", "--out-dir", "ov"], t)
        ok(rc == 3 and "러닝화" in out, "시드 표기 변경(0경로)을 멈추고 규칙을 제안하지 않는다(종료 코드 3)", out)

        # ── STEP 6 리포트
        cmd = shell_split(doc_command("### STEP 6"))
        rc, out = run(cmd[1:], t)
        ok(rc == 0, "STEP 6 build_leak_report 문서 명령이 비정상 종료", out)
        rep = json.loads((t / "report.json").read_text(encoding="utf-8"))
        ok([c["id"] for c in rep["provenance"]["calls"]] == ["C1", "C2", "C3", "C4", "C5"]
           and rep["provenance"]["calls"][2]["cost"] == 850, "provenance.calls 가 원장과 다르다", rep["provenance"]["calls"])
        rc, out = run([f"{S}/render_report.py", "report.json", "-o", "report.html"], t)
        ok(rc == 0, "report.json 이 렌더링 계약을 어긴다", out)
        (t / "bad_notes.json").write_text(json.dumps({"D1.interpretation": "경쟁사가 이긴다"}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([f"{S}/build_leak_report.py", f"out/leak_result_{CAT}.json", "-o", "r2.json", "--notes", "bad_notes.json"], t)
        ok(rc != 0 and "[추정]" in out, "[추정] 없는 에이전트 메모를 거부하지 않는다", out)
        (t / "notes.json").write_text(json.dumps({"D1.interpretation": "[추정] 추천 검색 뒤에 경쟁이 먼저 붙는다"}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([f"{S}/build_leak_report.py", f"out/leak_result_{CAT}.json", "-o", "r3.json", "--notes", "notes.json"], t)
        ok(rc == 0, "[추정] 메모를 받지 않는다", out)

        # 경쟁 지형 모드(내 브랜드 없음)도 리포트까지 간다
        run([f"{S}/make_leak_dict.py", "--gl", "kr", "--seeds", "런닝화,러닝화 추천", "--competitor", "나이키;nike",
             "--competitor", "아디다스;adidas", "--variant", "런닝=러닝", "-o", "land.json"], t)
        rc, out = run([f"{S}/journey_leaks.py", "--ledger", "calls.jsonl", "--dict", "land.json", "--category", "land", "--out-dir", "ol"], t)
        rc2, out2 = run([f"{S}/build_leak_report.py", "ol/leak_result_land.json", "-o", "land_r.json"], t)
        rc3, out3 = run([f"{S}/render_report.py", "land_r.json", "-o", "land.html"], t)
        ok(rc == rc2 == rc3 == 0, "경쟁 지형 모드에서 리포트까지 가지 않는다", out + out2 + out3)
        ok(all(h["group"] != "winning" for h in rows(t / "ol" / "leak_hubs_land.csv")), "내 브랜드 없이 winning 그룹이 나왔다")

        # 별칭 대조 통합 (0.1.1 P0 회귀) — 혼합 별칭 공백 변형이 경쟁 노드 · 누수 엣지가 되고, 경계로 뺀 노드는 경고 · 반대 근거에 뜬다
        (t / "raw" / "pf_alias.json").write_text(json.dumps({
            "request_detail": {"keyword": "정수기", "gl": "kr", "limit": 10, "time_point": "curr"},
            "cost_detail": {"total_cost": 370, "unit": "unique_keywords", "input_count": 8},
            "data": [["정수기", "얼음정수기 비교", "sk 매직 얼음정수기"], ["정수기", "얼음정수기 비교", "SK매직 정수기"],
                     ["정수기", "lg 정수기"], ["정수기", "algae 정수기", "코웨이 정수기"], ["정수기", "얼음정수기 비교", "lg전자 정수기"]]},
            ensure_ascii=False), encoding="utf-8")
        run([f"{S}/make_leak_dict.py", "--gl", "kr", "--seeds", "정수기", "--my", "LG전자;LG;엘지",
             "--competitor", "SK매직", "--competitor", "코웨이", "--category-terms", "정수기", "-o", "al.json"], t)
        run([f"{S}/record_call.py", "--ledger", "al.jsonl", "--tool", "path_finder", "--role", "main", "--seed", "정수기",
             "--limit", "10", "--raw", "raw/pf_alias.json"], t)
        rc, out = run([f"{S}/journey_leaks.py", "--ledger", "al.jsonl", "--dict", "al.json", "--category", "al", "--out-dir", "oa"], t)
        ok(rc == 0, "별칭 회귀 픽스처에서 journey_leaks 비정상 종료", out)
        if rc == 0:
            an = {n["canonical"]: n for n in rows(t / "oa" / "leak_nodes_al.csv")}
            ok(an["sk매직얼음정수기"]["node_class"] == "competitor" and an["sk매직얼음정수기"]["brand"] == "SK매직",
               "P0 회귀: `sk 매직 얼음정수기`가 SK매직 경쟁 노드가 아니다", an.get("sk매직얼음정수기"))
            ae = [(e["from_canonical"], e["to_canonical"], e["edge_type"], e["path_count"]) for e in rows(t / "oa" / "leak_edges_al.csv")]
            ok(("얼음정수기비교", "sk매직얼음정수기", "leak", "1") in ae and ("얼음정수기비교", "sk매직정수기", "leak", "1") in ae,
               "P0 회귀: 얼음정수기 비교 → SK매직 누수 엣지가 없다", ae)
            ah = {h["canonical"]: h for h in rows(t / "oa" / "leak_hubs_al.csv")}
            ok("sk매직얼음정수기" not in ah, "P0 회귀: 경쟁 브랜드 노드가 허브(미확보 기회)로 올라갔다", list(ah))
            AR = json.loads((t / "oa" / "leak_result_al.json").read_text(encoding="utf-8"))
            ok([(m["canonical"], m["brand"]) for m in AR["alias_misses"]] == [("algae정수기", "LG전자")],
               "별칭 누락 검사가 경계로 뺀 노드만 정확히 내놓지 않는다", AR.get("alias_misses"))
            ok(any("별칭 누락 의심" in w for w in AR["warnings"]) and "별칭 누락 의심" in out, "별칭 누락 의심 경고가 출력 · 결과에 없다", out)
            rc, out = run([f"{S}/build_leak_report.py", "oa/leak_result_al.json", "-o", "al_r.json"], t)
            rc2, out2 = run([f"{S}/render_report.py", "al_r.json", "-o", "al.html"], t)
            ok(rc == rc2 == 0 and "별칭 누락 의심" in (t / "al_r.json").read_text(encoding="utf-8"),
               "리포트 반대 근거에 별칭 누락 의심이 없다", out + out2)

    # 견본 리포트
    rc, out = run([f"{S}/render_report.py", str(ROOT / "assets" / "report.example.json"), "--check"], ROOT)
    ok(rc == 0, "assets/report.example.json 이 렌더링 계약을 어긴다", out)

    if fails:
        print(f"selftest_leaks 실패 {len(fails)}건")
        for what, detail in fails:
            print(f"  ✗ {what}")
            if detail:
                print(f"      {detail[:300]}")
        return 1
    print("selftest_leaks 통과 — 예시 명령 · T5 · T7 · T8 · 별칭 대조(0.1.1 P0) · 브랜드 시드 · 시드 표기 변경 · 예산 게이트 · 리포트 · 컬럼 대조 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
