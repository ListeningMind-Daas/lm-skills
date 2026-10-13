#!/usr/bin/env python3
"""이 스킬 고유 스크립트(`lm_tools.py`) 계약 검증 — SKILL.md의 예시 명령을 그대로 돌린다.

공유 `selftest.py`는 공유 스크립트(check_refmonth·trend_yoy)만 안다. 도구 정의·예산 가드·원장은
여기서 본다. 모의 응답만 쓰므로 API 호출도 크레딧도 없다.

    python3 scripts/selftest_tools.py        # 스킬 폴더에서 실행
"""
import csv, json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
DRY = SKILL / "assets" / "dryrun"
sys.dont_write_bytecode = True          # 스킬 폴더에 캐시를 남기지 않는다
sys.path.insert(0, str(HERE))
import lm_tools  # noqa: E402


def run(args, cwd):
    p = subprocess.run([sys.executable] + args, cwd=cwd, capture_output=True, text=True,
                       env={k: v for k, v in os.environ.items() if k not in ("LM_API_BASE", "LM_API_KEY")})
    return p.returncode, p.stdout + p.stderr


def documented_ledger_fields():
    """SKILL.md 산출물 절의 ledger 필드 목록 — 원장 실제 키와 같아야 한다."""
    md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"ledger\.jsonl\n((?:  [a-z_, ]+\n)+)", md)
    return [f.strip() for f in m.group(1).replace("\n", ",").split(",") if f.strip()] if m else []


def documented_variant_group_fields():
    """SKILL.md 산출물 절의 variant_groups 그룹 필드 목록 — annotate_keyword_info 실제 키와 같아야 한다."""
    md = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"variant_groups\.<canonical>\n((?:  [a-z_, ]+\n)+)", md)
    return [f.strip() for f in m.group(1).replace("\n", ",").split(",") if f.strip()] if m else []


def main():
    fails = []
    T = str(HERE / "lm_tools.py")
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        # 1) 도구 정의 — 두 형식
        for fmt, key in (("claude", "input_schema"), ("openai", "function")):
            rc, out = run([T, "schema", "--format", fmt, "-o", f"tools.{fmt}.json"], tmp)
            if rc:
                fails.append((f"schema {fmt}", out[-200:])); continue
            defs = json.loads((tmp / f"tools.{fmt}.json").read_text(encoding="utf-8"))
            names = [d.get("name") or d["function"]["name"] for d in defs]
            if sorted(names) != sorted(lm_tools.REG.default_names()) or any(key not in d for d in defs):
                fails.append((f"schema {fmt}", f"도구 이름·형식 불일치: {names}"))
        kid = lm_tools.REG.get("lm_keyword_info")
        if "ads_info" in json.dumps(kid.schema):
            fails.append(("schema", "ads_info가 노출돼 있다 — all의 하위집합(계약 §1)"))
        # 1.5) 잠금 파일 = 운영 명세 — 엔드포인트가 전부 도구가 되고, 판단 파일과 어긋나지 않는다
        lock = json.loads(lm_tools.LOCK_PATH.read_text(encoding="utf-8"))
        if sorted(t.path for t in lm_tools.REG.tools.values()) != sorted(lock["endpoints"]):
            fails.append(("명세", "잠금 파일의 엔드포인트 중 도구가 되지 않은 것이 있다"))
        if lm_tools.REG.orphans:
            fails.append(("명세", f"판단 파일에만 있는 경로(명세에서 사라짐): {lm_tools.REG.orphans}"))
        unrev = [n for n, t in lm_tools.REG.tools.items() if not t.reviewed]
        if unrev:
            fails.append(("명세", f"검토 전 도구가 번들에 있다 — tool-policy.json에 항목을 넣을 것: {unrev}"))
        for t in lm_tools.REG.tools.values():
            leaked = set(lm_tools.json.loads(lm_tools.POLICY_PATH.read_text(encoding="utf-8"))["hidden_params"]) & set(t.schema["properties"])
            if leaked:
                fails.append(("숨김 인자", f"{t.name}: {sorted(leaked)} 노출"))
            if t.cost is None and t.estimate_kind != "unpriced":
                fails.append(("단가", f"{t.name}: 명세에 단가가 없는데 추정 방식이 unpriced가 아니다"))
            if str(lock["spec_version"]) not in t.description() and t.cost:
                fails.append(("도구 설명", f"{t.name}: 단가 줄에 명세 버전이 없다"))
        for name, lim in (("lm_trend_finder", 50), ("lm_cluster_finder", 100), ("lm_path_finder", 20)):
            if lm_tools.REG.get(name).defaults.get("limit") != lim:
                fails.append(("기본값", f"{name} limit 기본값이 {lim}이 아니다 — API 기본값으로 나가면 추정이 커진다"))

        # 2) 문서 예시 dry-run
        rc, out = run([T, "dry-run", "--scenario", str(DRY / "scenario.sunscreen-kr.json"),
                       "--fixtures", str(DRY / "fixtures"), "--budget", "1500",
                       "--ledger", "ledger.jsonl", "--raw-dir", "raw", "--queried-at", "2026-09-28"], tmp)
        if rc or "dry-run 통과" not in out:
            fails.append(("dry-run", out[-400:]))
        else:
            rows = [json.loads(l) for l in (tmp / "ledger.jsonl").read_text(encoding="utf-8").splitlines()]
            doc = documented_ledger_fields()
            if set(doc) != set(lm_tools.LEDGER_FIELDS) or set(rows[0]) != set(lm_tools.LEDGER_FIELDS):
                fails.append(("ledger", f"문서 필드 ≠ 원장 필드: 문서만 {sorted(set(doc)-set(rows[0]))} "
                                        f"· 원장만 {sorted(set(rows[0])-set(doc))}"))
            if not any(r["truncated"] for r in rows):
                fails.append(("T7", "limit에 닿은 intent_finder를 절단으로 표시하지 않았다"))
            if not any(r["attempts"] and r["attempts"] > 1 for r in rows):
                fails.append(("429", "429 뒤 재시도가 원장에 남지 않았다"))

            # 3) keyword_info 주석 — 함정 처리 필드
            alls = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((tmp / "raw").glob("*_lm_keyword_info.json"))]
            raw = next((b for b in alls if b.get("lm_meta", {}).get("data_ref_month")), None)
            if not raw:
                fails.append(("T1", "all 응답에 data_ref_month가 없다"))
            else:
                meta = raw["lm_meta"]
                for k in ("data_ref_month", "queried_at", "lag_months", "last_month_suspect"):
                    if k not in meta:
                        fails.append(("lm_meta", f"{k} 없음"))
                need = {"canonical", "yoy_ex_last_month", "yoy_status", "yoy_base_3m",
                        "below_base_gate", "last_month_suspect", "missing_months"}
                for r in raw["data"]:
                    if need - set(r):
                        fails.append(("주석", f"{r['keyword']}: {sorted(need - set(r))} 없음"))
                    if "volume_trend" in (r.get("ads_metrics") or {}):
                        fails.append(("T3", "volume_trend가 판정 가능한 이름으로 남아 있다"))
                if not meta["last_month_suspect"]:
                    fails.append(("T2", "마지막 달 급락 픽스처를 의심하지 않았다"))
                if not any(r["yoy_status"].startswith("계산 불가") for r in raw["data"]):
                    fails.append(("T6", "결측 월 픽스처가 계산 불가로 남지 않았다"))
                if not any(r["below_base_gate"] and r["yoy_status"] == "ok" for r in raw["data"]):
                    fails.append(("T4", "기저 미달 픽스처가 게이트에 걸리지 않았다"))
                # 4) 공유 스크립트와 교차확인 — 같은 창, 같은 값
                p = tmp / "raw_all.json"
                p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
                if (HERE / "check_refmonth.py").is_file():
                    rc2, o2 = run([str(HERE / "check_refmonth.py"), str(p), "--queried-at", "2026-09-28", "--json"], tmp)
                    if rc2 or json.loads(o2)["data_ref_month"] != meta["data_ref_month"]:
                        fails.append(("교차확인", "check_refmonth와 기준월이 다르다"))
                if (HERE / "trend_yoy.py").is_file():
                    rc3, o3 = run([str(HERE / "trend_yoy.py"), str(p), "-o", "t.csv"], tmp)
                    ref = {r["keyword"]: r["yoy_ex_last_month"] for r in csv.DictReader(open(tmp / "t.csv", encoding="utf-8-sig"))}
                    for r in raw["data"]:
                        a, b = r["yoy_ex_last_month"], ref.get(r["keyword"], "")
                        if (a is None) != (b == "") or (a is not None and abs(a - float(b)) > 1e-3):
                            fails.append(("교차확인", f"{r['keyword']} yoy {a} ≠ trend_yoy {b}"))

            # 5) 요약 — used_credits를 잔여로 읽지 않는가
            rc, out = run([T, "summary", "ledger.jsonl", "-o", "usage.md", "--period", "2026-09"], tmp)
            md = (tmp / "usage.md").read_text(encoding="utf-8") if not rc else ""
            if rc or "월간 누적" not in md or "cost_detail.total_cost 합산" not in md:
                fails.append(("summary", "소진=cost_detail 합산 · used_credits=월간 누적 표기가 없다"))

        # 5.5) T5 — 표기변형 그룹 볼륨 하한(최댓값)·상한(합) 병기. lo≠hi 픽스처 + 볼륨 없는 행
        vb = {"data": [
            {"keyword": "선크림", "ads_metrics": {"volume_avg": 40000}},
            {"keyword": "선 크림", "ads_metrics": {"volume_avg": 6000}},
            {"keyword": "선크림 ", "ads_metrics": {}},
            {"keyword": "선스틱", "ads_metrics": {"volume_avg": 12500}}]}
        vm = lm_tools.annotate_keyword_info(vb, "ads_metrics", "2026-09-28", 3000)["lm_meta"]
        g = vm["variant_groups"].get("선크림") or {}
        doc_vg = documented_variant_group_fields()
        if set(g) != set(doc_vg) or not doc_vg:
            fails.append(("T5", f"문서 variant_groups 필드 ≠ 실제: 문서 {doc_vg} · 실제 {sorted(g)}"))
        if (g.get("volume_lo"), g.get("volume_hi")) != (40000, 46000):
            fails.append(("T5", f"하한 40000·상한 46000 기대 — {g.get('volume_lo')}·{g.get('volume_hi')}"))
        if g.get("volume_missing") != ["선크림 "]:
            fails.append(("T5", f"볼륨 없는 변형을 0으로 쳤거나 누락: {g.get('volume_missing')}"))
        if "선스틱" in vm["variant_groups"]:
            fails.append(("T5", "변형 없는 키워드가 variant_groups에 들어갔다"))
        if vm["labels"].get("volume_lo") != "추정" or vm["labels"].get("volume_hi") != "추정":
            fails.append(("T5", "volume_lo·volume_hi 라벨이 [추정]이 아니다(계약은 4-Label뿐)"))
        # 5.6) T5 ① — 시계열이 같은 행은 canonical이 달라도 같은 검색의 중복 · 한 번만 센다
        mv = [{"month": f"2025-{m:02d}", "gg": 1000 * m, "nv": 2000 * m, "total": 3000 * m} for m in range(1, 13)]
        dd = {"data": [
            {"keyword": "무선청소기 추천", "ads_metrics": {"volume_avg": 30000, "volume_total": 234000}, "monthly_volume": mv},
            {"keyword": "추천 무선청소기", "ads_metrics": {"volume_avg": 30000, "volume_total": 234000},
             "monthly_volume": [dict(x) for x in mv]},
            {"keyword": "무선 청소기 추천", "ads_metrics": {"volume_avg": 9000, "volume_total": 90000}}]}
        dm = lm_tools.annotate_keyword_info(dd, "all", "2026-09-28", 3000)
        rows = {r["keyword"]: r for r in dm["data"]}
        g = dm["lm_meta"]["variant_groups"].get("무선청소기추천") or {}
        if rows["추천 무선청소기"]["dup_of"] != "무선청소기 추천" or rows["추천 무선청소기"]["dup_basis"] != "시계열 일치":
            fails.append(("T5①", f"시계열 일치 행을 중복으로 잡지 못했다: {rows['추천 무선청소기'].get('dup_of')!r}"))
        if sorted(g.get("keywords") or []) != sorted(rows) or (g.get("volume_lo"), g.get("volume_hi")) != (30000, 39000):
            fails.append(("T5①", f"중복은 한 번만·canonical 넘어 한 그룹 기대(30000·39000) — {g}"))
        # 5.7) T5 치환 규칙 — 실측 응답(선크림 요청에 썬크림 동반)은 공백 규칙만으로 묶이지 않는다
        ki_real = json.loads((DRY / "real-shapes" / "keyword_info_ads6_sunscreen-kr.json").read_text(encoding="utf-8"))
        plain = lm_tools.annotate_keyword_info(json.loads(json.dumps(ki_real["body"])), "ads_metrics", "2026-09-28", 3000)
        ruled = lm_tools.annotate_keyword_info(json.loads(json.dumps(ki_real["body"])), "ads_metrics", "2026-09-28", 3000,
                                               lm_tools.load_rules('{"썬": "선"}'), ki_real["args"]["keywords"])
        gs = ruled["lm_meta"]["variant_groups"].get("선크림") or {}
        if plain["lm_meta"]["variant_groups"] or sorted(gs.get("keywords") or []) != ["선크림", "썬크림"] \
                or (gs.get("volume_lo"), gs.get("volume_hi")) != (68973, 68973 + 65186):
            fails.append(("T5 규칙", f"썬→선 규칙으로 실측 변형이 묶이지 않았다: {gs}"))
        if "variant_bounds_note" not in vm or "volume_lo" not in lm_tools.SYSTEM_RULES:
            fails.append(("T5", "하한·상한 병기 주석 또는 시스템 규칙이 없다"))

        # 5.8) 실측 응답 모양 회귀 — cluster 두 모양(communities만 · rels 쌍 목록) · path · keyword_info.
        #      결정식 = cost_detail, 노드 = output_count, 절단 판정, 호출 전 추정 ≥ 실제
        for f in sorted((DRY / "real-shapes").glob("*.json")):
            it = json.loads(f.read_text(encoding="utf-8"))
            one = type("One", (), {"calls": 0, "post": lambda self, t, p, b=it["body"]: (200, json.loads(json.dumps(b)))})()
            rt = lm_tools.Runtime(one, 10 ** 6, tmp / f"rs_{f.stem}.jsonl", queried_at="2026-09-28",
                                  strict_two_stage=False, sleep=lambda s: None)
            out = rt.dispatch(it["tool"], dict(it["args"]))
            row = json.loads((tmp / f"rs_{f.stem}.jsonl").read_text(encoding="utf-8").splitlines()[-1])
            cd = it["body"]["cost_detail"]
            if row["formula_cost"] != cd["total_cost"] or row["estimate"] < cd["total_cost"]:
                fails.append(("실측 모양", f"{f.name}: 추정 {row['estimate']} · 식 {row['formula_cost']} · 실제 {cd['total_cost']}"))
            if it["tool"] == "lm_cluster_finder":
                if row["returned"] != cd["output_count"] or row["truncated"] is not True:
                    fails.append(("실측 모양", f"{f.name}: 노드 {row['returned']} ≠ output_count {cd['output_count']} "
                                              f"또는 절단 {row['truncated']}"))
                if lm_tools.estimate(it["tool"], dict(it["args"]), cluster_bound="structural")[0] < row["estimate"]:
                    fails.append(("실측 모양", "structural 상한이 observed보다 작다"))
            if it["tool"] == "lm_path_finder" and out["lm_meta"]["unique_keywords"] != cd["output_count"]:
                fails.append(("실측 모양", f"{f.name}: 경로 고유 키워드 ≠ output_count"))
        # 관측 기록(평가 2026-09-28 · 공개 번들 밖 응답 포함)도 기본 상한이 덮는가 — (limit, 실제 과금 단위)
        for lim, n in ((10, 11), (50, 44), (50, 50), (100, 88), (500, 501), (500, 482)):
            if lm_tools.estimate("lm_cluster_finder", {"limit": lim})[0] < 150 + 50 * n:
                fails.append(("상한", f"cluster limit {lim} 노드 {n}을 기본 추정이 덮지 못한다"))
        for lim, n in ((3, 7), (20, 25), (30, 36), (35, 45), (35, 32)):
            if lm_tools.estimate("lm_path_finder", {"limit": lim})[0] < 50 + 40 * n:
                fails.append(("상한", f"path limit {lim} 키워드 {n}을 기본 추정이 덮지 못한다"))

        # 5.9) 도구 JSON에서 뺀 커넥터는 런타임도 막는다 · 생략된 limit은 스키마 기본값으로 채워 보낸다
        rc, out = run([T, "schema", "--format", "claude", "--tools", "lm_keyword_info,lm_intent_finder",
                       "-o", "tools.two.json"], tmp)
        two = lm_tools.tools_from_definitions(tmp / "tools.two.json") if not rc else []
        if sorted(two) != ["lm_intent_finder", "lm_keyword_info"]:
            fails.append(("도구 가지치기", f"schema --tools 결과 {two}"))
        sent = []
        cap = type("Cap", (), {"calls": 0, "post": lambda self, t, p: (sent.append(p), (200, {"data": {}}))[1]})()
        rt = lm_tools.Runtime(cap, 10 ** 6, tmp / "l_tools.jsonl", tools=two, sleep=lambda s: None)
        o = rt.dispatch("lm_cluster_finder", {"keyword": "선크림", "gl": "kr"})
        if o.get("error") != "tool_not_enabled" or sent:
            fails.append(("도구 가지치기", f"뺀 도구가 전송됐다: {o.get('error')} · 전송 {len(sent)}"))
        rt = lm_tools.Runtime(cap, 10 ** 6, tmp / "l_def.jsonl", sleep=lambda s: None)
        rt.dispatch("lm_cluster_finder", {"keyword": "선크림", "gl": "kr"})
        if not sent or sent[-1].get("limit") != 100 or sent[-1].get("data_type") != "communities":
            fails.append(("기본값", f"생략된 limit·data_type이 채워지지 않았다: {sent[-1] if sent else None}"))

        # 5.10) 2단 허용 집합 — ads_metrics 응답에 함께 온 변형은 all을 허용 · 픽스처는 인자로 골라 순서 무관
        fx2 = tmp / "fx2"; fx2.mkdir()
        (fx2 / "lm_keyword_info.json").write_text(json.dumps([
            {"match": {"data_type": "all"}, "status": 200,
             "body": {"data": [], "request_detail": {"keywords": ["톤업 썬크림"], "data_type": "all"},
                      "cost_detail": {"total_cost": 10}}},
            {"match": {"data_type": "ads_metrics"}, "status": 200, "body": ki_real["body"]}], ensure_ascii=False),
            encoding="utf-8")
        rt = lm_tools.Runtime(lm_tools.MockTransport(fx2), 1000, tmp / "l_ts.jsonl", sleep=lambda s: None)
        rt.dispatch("lm_keyword_info", {"keywords": ki_real["args"]["keywords"], "gl": "kr", "data_type": "ads_metrics"})
        o = rt.dispatch("lm_keyword_info", {"keywords": ["톤업 썬크림"], "gl": "kr", "data_type": "all"})
        led = [json.loads(l) for l in (tmp / "l_ts.jsonl").read_text(encoding="utf-8").splitlines()]
        if o.get("error") or any(r["formula_match"] is False for r in led):
            fails.append(("2단·픽스처", f"함께 온 변형의 all이 막혔거나 결정식 불일치: {o.get('error')} · "
                                       f"{[r['formula_match'] for r in led]}"))

        # 5.11) 새 도구 주석 — serp 수집 시각 · trend 빈 결과 0크레딧·절단 · keyword_list 절단 규칙 · usage 무단가
        one = lambda b: type("One", (), {"calls": 0, "post": lambda self, t, p: (200, json.loads(json.dumps(b)))})()
        fx_dir = DRY / "fixtures"
        serp = json.loads((fx_dir / "lm_serp.json").read_text(encoding="utf-8"))[0]["body"]
        o = lm_tools.Runtime(one(serp), 1000, tmp / "l_serp.jsonl", sleep=lambda s: None).dispatch(
            "lm_serp", {"keyword": "선크림", "gl": "kr"})
        if o["lm_meta"].get("collected_at") != serp["collected_at"] or "실시간" not in o["lm_meta"]["snapshot_note"]:
            fails.append(("serp", "collected_at·스냅샷 주석이 없다"))
        empty = {"cost_detail": {"input_cost": 50, "input_count": 0, "input_total_cost": 0, "output_cost": 2,
                                 "output_count": 0, "output_total_cost": 0, "total_cost": 0}, "total": 0, "data": []}
        rt = lm_tools.Runtime(one(empty), 1000, tmp / "l_te.jsonl", sleep=lambda s: None)
        o = rt.dispatch("lm_trend_finder", {"keyword": "캠핑 의자", "gl": "kr", "period_days": 14})
        row = json.loads((tmp / "l_te.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        if row["formula_cost"] != 0 or row["formula_match"] is not True or not o["lm_meta"].get("empty_note") \
                or row["estimate"] != 50 + 2 * 50:
            fails.append(("trend", f"빈 결과 0크레딧·추정 150·빈 결과 주석 기대 — 식 {row['formula_cost']} · "
                                   f"추정 {row['estimate']} · {o['lm_meta'].get('empty_note')}"))
        pk = json.loads((fx_dir / "lm_path_keyword_list.json").read_text(encoding="utf-8"))[0]["body"]
        o = lm_tools.Runtime(one(pk), 1000, tmp / "l_pk.jsonl", sleep=lambda s: None).dispatch(
            "lm_path_keyword_list", {"keyword": "선크림", "gl": "kr", "limit": 3})
        if o["lm_meta"]["truncated"] is not None:
            fails.append(("path_keyword_list", "경로가 없는 응답인데 절단을 판정했다"))
        us = json.loads((fx_dir / "lm_usage_summary.json").read_text(encoding="utf-8"))[0]["body"]
        rt = lm_tools.Runtime(one(us), 0, tmp / "l_us.jsonl", sleep=lambda s: None)
        o = rt.dispatch("lm_usage_summary", {"from": "2026-09-01", "to": "2026-09-29"})
        if o.get("error") or rt.spent != 0 or "잔여량" not in o["lm_meta"]["scope_note"]:
            fails.append(("usage", f"예산 0에서도 무단가 조회가 돼야 하고 잔여 아님 주석이 있어야 한다: {o.get('error')}"))
        o = lm_tools.Runtime(one(us), 1000, tmp / "l_uq.jsonl", sleep=lambda s: None).dispatch(
            "lm_keyword_info", {"keywords": ["선크림"], "gl": "kr", "data_type": "ads_metrics", "user_query": "x"})
        if o.get("error") != "invalid_args":
            fails.append(("숨김 인자", "user_query를 열지 않았는데 전송됐다"))

        # 5.12) 명세 추적 — 새 엔드포인트 자동 추가(검토 전 · 기본 꺼짐) · 단가 변경 · 삭제
        spec = {"info": {"title": "t", "version": "9.9.9"}, "paths": {
            "/new_finder/list": {"post": {"operationId": "new_finder_list", "summary": "새 도구",
                "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/NP"}}}},
                "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/NR"}}}}}}},
            "/flat": {"post": {"operationId": "flat",
                "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/FP"}}}},
                "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/FR"}}}}}}}},
            "components": {"schemas": {
                "NP": {"type": "object", "required": ["keyword", "gl"], "properties": {
                    "keyword": {"type": "string"}, "gl": {"type": "string", "enum": ["kr"]},
                    "limit": {"type": "integer", "default": 100, "maximum": 500},
                    "user_query": {"anyOf": [{"type": "string"}, {"type": "null"}]}}},
                "NR": {"properties": {"cost_detail": {"anyOf": [{"$ref": "#/components/schemas/NC"}, {"$ref": "#/components/schemas/BasicCost"}]}}},
                "NC": {"properties": {"input_cost": {"default": 20}, "output_cost": {"default": 3}}},
                "FP": {"type": "object", "required": ["keyword"], "properties": {"keyword": {"type": "string"}}},
                "FR": {"properties": {"cost_detail": {"$ref": "#/components/schemas/FC"}}},
                "FC": {"properties": {"input_cost": {"default": 5}, "output_cost": {"default": 1}}},
                "BasicCost": {"properties": {"total_cost": {"default": 1}}}}}}
        grown = json.loads(json.dumps(lock))
        grown["endpoints"].update(lm_tools.normalize_spec(spec)["endpoints"])
        pol = json.loads(lm_tools.POLICY_PATH.read_text(encoding="utf-8"))
        reg = lm_tools.Registry(grown, pol)
        nt, ft = reg.get("lm_new_finder_list"), reg.get("lm_flat")
        if not nt or nt.reviewed or nt.name in reg.default_names() or "user_query" in nt.schema["properties"]:
            fails.append(("자동 추가", "새 엔드포인트가 검토 전·기본 꺼짐·숨김 인자 제외로 추가되지 않았다"))
        elif lm_tools.estimate(nt, {"keyword": "a", "gl": "kr", "limit": 10})[0] != 20 + 3 * 500:
            fails.append(("자동 추가", "검토 전 도구의 추정이 명세 최대 limit 기준(최악)이 아니다"))
        if ft and lm_tools.estimate(ft, {"keyword": "a"})[0] is not None:
            fails.append(("자동 추가", "출력 과금이 있는데 limit이 없는 검토 전 도구를 추정했다"))
        cap2 = type("Cap2", (), {"calls": 0, "post": lambda self, t, p: (200, {"data": ["a"], "cost_detail": {"total_cost": 23, "output_count": 1}})})()
        rt = lm_tools.Runtime(cap2, 10 ** 6, tmp / "l_new.jsonl", registry=reg, sleep=lambda s: None)
        if rt.dispatch("lm_new_finder_list", {"keyword": "a", "gl": "kr"}).get("error") != "tool_not_enabled":
            fails.append(("자동 추가", "검토 전 도구가 이름 지정 없이 열렸다"))
        rt = lm_tools.Runtime(cap2, 10 ** 6, tmp / "l_new2.jsonl", registry=reg, tools=["lm_new_finder_list"], sleep=lambda s: None)
        o = rt.dispatch("lm_new_finder_list", {"keyword": "a", "gl": "kr", "limit": 5})
        row = json.loads((tmp / "l_new2.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        if o.get("error") or row["formula_match"] is not True or "검토 전" not in o["lm_meta"].get("review", ""):
            fails.append(("자동 추가", f"이름으로 연 검토 전 도구가 돌지 않았다: {o.get('error')} · {row['formula_match']}"))
        pricier = json.loads(json.dumps(lock))
        pricier["endpoints"]["/serp"]["cost"]["*"]["input"] = 40
        _, code = lm_tools.sync_report(lock, pricier, pol)
        _, code_add = lm_tools.sync_report(lock, grown, pol)
        _, code_same = lm_tools.sync_report(lock, lock, pol)
        # 잠금 파일 오프라인 점검 — 번들은 0, 깨진 잠금은 1
        if lm_tools.check_lock(lock, pol):
            fails.append(("check-lock", f"번들 잠금 파일에 문제: {lm_tools.check_lock(lock, pol)[:3]}"))
        broken = json.loads(json.dumps(lock)); broken["endpoints"]["/serp"]["required"].append("nope")
        bl = tmp / "broken_lock.json"; bl.write_text(json.dumps(broken), encoding="utf-8")
        rc_ok, _ = run([T, "sync", "--check-lock"], tmp)
        rc_bad, out_bad = run([T, "sync", "--check-lock", "--lock", str(bl)], tmp)
        if rc_ok != 0 or rc_bad != 1 or "nope" not in out_bad:
            fails.append(("check-lock", f"번들 {rc_ok} · 깨진 잠금 {rc_bad} (0·1 기대)"))
        if "실측 대조 전" in lm_tools.REG.get("lm_intent_finder").description():
            fails.append(("단가 근거", "intent_finder 단가가 실측과 일치하는데 «실측 대조 전»으로 적혀 있다"))
        if (code, code_add, code_same) != (3, 2, 0):
            fails.append(("sync", f"종료 코드 단가 3·추가 2·같음 0 기대 — {code}·{code_add}·{code_same}"))
        if lm_tools.Registry(pricier, pol).get("lm_serp").cost_line().find("40×") < 0:
            fails.append(("sync", "바뀐 단가가 도구 설명에 반영되지 않았다"))
        shrunk = json.loads(json.dumps(lock)); shrunk["endpoints"].pop("/serp")
        if lm_tools.Registry(shrunk, pol).orphans != ["/serp"]:
            fails.append(("sync", "명세에서 사라진 경로를 orphans로 알리지 않았다"))
        # CLI — 파일 명세로 sync --write → 잠금 파일에 새 엔드포인트
        (tmp / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        tl = tmp / "lock.json"; tl.write_text(json.dumps(lock), encoding="utf-8")
        rc, out = run([T, "sync", "--spec", "spec.json", "--lock", str(tl)], tmp)
        rc2, out2 = run([T, "sync", "--spec", "spec.json", "--lock", str(tl), "--write"], tmp)
        rc3, out3 = run([T, "list", "--lock", str(tl)], tmp)
        if rc != 2 or rc2 != 0 or "lm_new_finder_list" not in out3 or "검토 전" not in out3:
            fails.append(("sync CLI", f"대조 {rc} · 갱신 {rc2} · 목록 {out3[-300:]}"))

        # 6) 추정을 넘은 실제 비용 → 이후 호출 정지 · 5xx 재시도 후 오류
        fx = tmp / "fx"; fx.mkdir()
        body = json.loads((DRY / "fixtures" / "lm_path_finder.json").read_text(encoding="utf-8"))
        (fx / "lm_path_finder.json").write_text(json.dumps(body), encoding="utf-8")
        (fx / "lm_intent_finder.json").write_text(json.dumps([{"status": 503, "body": {}}]), encoding="utf-8")
        rt = lm_tools.Runtime(lm_tools.MockTransport(fx), 200, tmp / "l2.jsonl", path_kw_mult=0.1, sleep=lambda s: None)
        rt.dispatch("lm_path_finder", {"keyword": "선크림", "gl": "kr", "limit": 5})
        o = rt.dispatch("lm_path_finder", {"keyword": "선크림", "gl": "kr", "limit": 5})
        if o.get("error") != "halted_after_overrun":
            fails.append(("가드", f"실제 비용이 예산을 넘었는데 다음 호출이 막히지 않았다: {o.get('error')}"))
        rt2 = lm_tools.Runtime(lm_tools.MockTransport(fx), 1000, tmp / "l3.jsonl", sleep=lambda s: None)
        o = rt2.dispatch("lm_intent_finder", {"keywords": ["선크림"], "gl": "kr", "limit": 10})
        last = json.loads((tmp / "l3.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        if o.get("error") != "http" or last["attempts"] != 4 or rt2.spent != 0:
            fails.append(("5xx", f"재시도 3회 후 오류·과금 0 기대 — attempts {last['attempts']}, spent {rt2.spent}"))

        # 6.1) 호출 1건 상한(per_call_cap) — 기본 limit의 cluster 5,200 · path 2,450을 예산 가드와 같은 추정으로 막는다
        sent = []
        cap = type("Cap", (), {"calls": 0, "post": lambda self, t, p: (sent.append((t, p)), (200, {"data": {}}))[1]})()
        rt = lm_tools.Runtime(cap, 10 ** 6, tmp / "l_cap.jsonl", per_call_cap=1000, sleep=lambda s: None)
        oc = rt.dispatch("lm_cluster_finder", {"keyword": "선크림", "gl": "kr"})
        op = rt.dispatch("lm_path_finder", {"keyword": "선크림", "gl": "kr"})
        ok_small = rt.dispatch("lm_cluster_finder", {"keyword": "선크림", "gl": "kr", "limit": oc.get("max_limit") or 0})
        led = [json.loads(l) for l in (tmp / "l_cap.jsonl").read_text(encoding="utf-8").splitlines()]
        if (oc.get("error"), oc.get("estimate"), oc.get("max_limit")) != ("per_call_cap", 5200, 16) \
                or (op.get("error"), op.get("estimate"), op.get("max_limit")) != ("per_call_cap", 2450, 7):
            fails.append(("1회 상한", f"cluster {oc.get('error')}·{oc.get('estimate')}·{oc.get('max_limit')} "
                                     f"path {op.get('error')}·{op.get('estimate')}·{op.get('max_limit')} — "
                                     "per_call_cap·5200·16 / per_call_cap·2450·7 기대"))
        if [r["block_reason"] for r in led[:2]] != ["per_call_cap", "per_call_cap"] or len(sent) != 1 \
                or ok_small.get("error"):
            fails.append(("1회 상한", f"원장 사유 {[r['block_reason'] for r in led]} · 전송 {len(sent)} · "
                                     f"max_limit 재요청 {ok_small.get('error')}"))
        rt = lm_tools.Runtime(cap, 10 ** 6, tmp / "l_nocap.jsonl", sleep=lambda s: None)
        if rt.dispatch("lm_cluster_finder", {"keyword": "선크림", "gl": "kr"}).get("error"):
            fails.append(("1회 상한", "기본값(None)인데 호출이 막혔다 — 이전 동작이 바뀌었다"))
        # 6.2) 시장 고정(market_lock) — 값 고정 · auto(첫 전송 호출의 gl) · gl 없는 도구는 통과
        sent.clear()
        rt = lm_tools.Runtime(cap, 10 ** 6, tmp / "l_mk.jsonl", market="kr", sleep=lambda s: None)
        o = rt.dispatch("lm_keyword_info", {"keywords": ["sunscreen"], "gl": "us", "data_type": "ads_metrics"})
        row = json.loads((tmp / "l_mk.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        if o.get("error") != "market_lock" or sent or row["block_reason"] != "market_lock" or row["gl"] != "us":
            fails.append(("시장 고정", f"gl=us가 막히지 않았다: {o.get('error')} · 전송 {len(sent)}"))
        rt = lm_tools.Runtime(cap, 10 ** 6, tmp / "l_mk2.jsonl", market="auto", sleep=lambda s: None)
        a1 = rt.dispatch("lm_keyword_info", {"keywords": ["선크림"], "gl": "kr", "data_type": "ads_metrics"})
        a2 = rt.dispatch("lm_keyword_info", {"keywords": ["sunscreen"], "gl": "jp", "data_type": "ads_metrics"})
        a3 = rt.dispatch("lm_usage_summary", {"from": "2026-09-01", "to": "2026-09-29"})
        if a1.get("error") or a2.get("error") != "market_lock" or a2.get("market") != "kr" or a3.get("error"):
            fails.append(("시장 고정", f"auto: 첫 호출 {a1.get('error')} · 다른 gl {a2.get('error')} · "
                                     f"gl 없는 도구 {a3.get('error')}"))
        # 6.3) CLI — 시나리오 config와 --per-call-cap·--market
        sc = tmp / "sc_guard.json"
        sc.write_text(json.dumps({"config": {"per_call_cap": 1000, "market": "kr"}, "steps": [
            {"tool": "lm_cluster_finder", "args": {"keyword": "선크림", "gl": "kr"}, "expect": "blocked",
             "expect_reason": "per_call_cap"},
            {"tool": "lm_cluster_finder", "args": {"keyword": "선크림", "gl": "kr", "limit": 10}, "expect": "ok"},
            {"tool": "lm_keyword_info", "args": {"keywords": ["sunscreen"], "gl": "us", "data_type": "ads_metrics"},
             "expect": "blocked", "expect_reason": "market_lock"}]}, ensure_ascii=False), encoding="utf-8")
        rc, out = run([T, "dry-run", "--scenario", str(sc), "--fixtures", str(DRY / "fixtures"), "--budget", "5000",
                       "--ledger", "l_sc.jsonl", "--queried-at", "2026-09-28"], tmp)
        rc2, out2 = run([T, "dry-run", "--scenario", str(sc), "--fixtures", str(DRY / "fixtures"), "--budget", "5000",
                         "--ledger", "l_sc2.jsonl", "--queried-at", "2026-09-28", "--per-call-cap", "100000"], tmp)
        if rc or "1회 상한 1,000" not in out or rc2 == 0:
            fails.append(("가드 CLI", f"config 적용 {rc} · CLI 우선 {rc2} · {out[-200:]}"))

        # 6.4) trend 절단 원인 — limit에 닿음과 매칭 total > 반환을 가른다 (관측: limit 20 · 매칭 9 · 반환 7)
        tr = json.loads((fx_dir / "lm_trend_finder.json").read_text(encoding="utf-8"))[0]["body"]
        o = lm_tools.Runtime(one(tr), 1000, tmp / "l_tr.jsonl", sleep=lambda s: None).dispatch(
            "lm_trend_finder", {"keyword": "선크림", "gl": "kr", "period_days": 90, "limit": 20})
        m = o["lm_meta"]
        if m["truncated"] is not True or m.get("truncation_cause") != "total" or "limit에 닿음" in m["convergence"] \
                or f"매칭 {tr['total']}건 중 {len(tr['data'])}건" not in m["convergence"]:
            fails.append(("trend 절단", f"매칭 > 반환인데 문구가 원인을 틀리게 적었다: {m.get('convergence')}"))
        tr2 = dict(tr, total=len(tr["data"]))
        o = lm_tools.Runtime(one(tr2), 1000, tmp / "l_tr2.jsonl", sleep=lambda s: None).dispatch(
            "lm_trend_finder", {"keyword": "선크림", "gl": "kr", "period_days": 90, "limit": len(tr["data"])})
        if o["lm_meta"].get("truncation_cause") != "limit" or o["lm_meta"]["convergence"] != "보류 — limit에 닿음":
            fails.append(("trend 절단", f"반환 = limit인데 «limit에 닿음»이 아니다: {o['lm_meta'].get('convergence')}"))

        # 6.5) integer 인자의 경계·기본값은 정수로 낸다 (잠금 파일의 1.0 → 1)
        floats = [f"{t.name}.{k}.{f}" for t in lm_tools.REG.tools.values()
                  for k, v in t.schema["properties"].items() if v.get("type") == "integer"
                  for f in ("minimum", "maximum", "default") if isinstance(v.get(f), float)]
        if floats:
            fails.append(("정수 경계", f"integer 인자에 실수 경계: {floats[:5]}"))
        if lm_tools._param({"type": "integer", "minimum": 1.0, "maximum": 3.0})["maximum"].__class__ is not int:
            fails.append(("정수 경계", "명세 정규화가 integer 경계를 정수로 바꾸지 않는다"))

        # 6.6) 합성 keyword_info 픽스처는 요청 키워드를 따른다 (echo_keywords) · 실측 원문은 그대로
        rt = lm_tools.Runtime(lm_tools.MockTransport(fx_dir), 1000, tmp / "l_echo.jsonl", sleep=lambda s: None)
        o = rt.dispatch("lm_keyword_info", {"keywords": ["선크림", "선케어"], "gl": "kr", "data_type": "ads_metrics"})
        row = json.loads((tmp / "l_echo.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        if [r["keyword"] for r in o.get("data", [])] != ["선크림", "선케어"] or row["actual_cost"] != 2 \
                or row["formula_match"] is not True or o["lm_meta"]["not_returned"] or o["lm_meta"]["unrequested"]:
            fails.append(("모의 키워드", f"요청 키워드를 따르지 않았다: {[r['keyword'] for r in o.get('data', [])]} · "
                                       f"비용 {row['actual_cost']} · 대조 {row['formula_match']}"))
        # 7) 실제 전송은 환경변수 없이는 만들어지지 않는다 · 주소가 코드에 박혀 있지 않다
        saved = {k: os.environ.pop(k, None) for k in ("LM_API_BASE", "LM_API_KEY")}
        try:
            lm_tools.HttpTransport()
            fails.append(("보안", "LM_API_BASE 없이 전송이 만들어졌다"))
        except RuntimeError as e:
            if "LM_API_BASE" not in str(e):
                fails.append(("보안", str(e)))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v
        if re.search(r"https?://", (HERE / "lm_tools.py").read_text(encoding="utf-8")):
            fails.append(("보안", "lm_tools.py에 URL이 박혀 있다 — 주소는 LM_API_BASE로만"))

    if fails:
        print(f"실패 {len(fails)}건")
        for n, d in fails:
            print(f"  ✗ {n}: {str(d).strip()[:300]}")
        return 1
    print(f"통과 — lm_tools 명세 추적·도구 정의·예산 가드·원장·주석 검증 완료 (도구 {len(lm_tools.REG.tools)}종 · 명세 {lm_tools.REG.spec_version} · API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
