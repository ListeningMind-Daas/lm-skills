#!/usr/bin/env python3
# shared-asset: lm-skills/shared — 이 파일은 사본이다. 고칠 때는 정본을 고치고 재배포할 것.
"""번들 스크립트 계약 검증 — SKILL.md의 예시 명령이 실제로 도는지 확인한다.

왜 필요한가: 이 계열 스킬은 «명세와 코드 불일치»를 두 번 냈다. 한 번은 문서가 약속한 CSV
컬럼을 스크립트가 만들지 않았고, 한 번은 문서의 `--rules '{"..."}'` 인라인 예시를 스크립트가
파일 경로로 열어 죽었다. 둘 다 사람이 읽어서는 안 잡히고, 문서를 믿고 따라간 쪽에서만 터진다.

스킬은 플래그에 따라 공유 스크립트를 **일부만** 배치받는다. 없는 것은 «건너뜀»으로 보고하되
조용히 넘어가지 않는다.

합성 픽스처만 쓰므로 API 호출도 크레딧도 필요 없다.

    python3 scripts/selftest.py        # 스킬 폴더에서 실행
"""
import csv, json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
REF_MONTHS = [f"{y}-{m:02d}" for y in (2023, 2024, 2025, 2026) for m in range(1, 13)][:48]
LAST = "2026-07"

# 골격 자리표시자 — `new_skill.py`가 박는 것들. 남아 있으면 SKILL.md가 아직 골격이다.
# 이전 판은 스크립트만 돌려 SKILL.md가 비어 있어도 «통과»를 냈다(2026-10-12 워크숍 사전 실행 실측).
# «⬜로 박는다»처럼 기호를 설명하는 문장(조사가 바로 붙음)은 제외한다 — 감사기와 같은 기준.
HOLE = re.compile(r"⬜(?![로를가는이의와과도/])")
PLACEHOLDERS = ("(트리거 발화 1)", "(이 스킬에서의 용도를 쓸 것)", "| (결정식) |",
                "(사용자가 스킬 이름을 말하지 않아도 켜야 하는 상황을 여기에 쓴다.)")
# 스크립트 출력은 UTF-8로 받는다 — Windows(cp949) 기본 인코딩이면 ⚠·— 를 찍다 하위 프로세스가 죽는다
CHILD_ENV = dict(os.environ, PYTHONIOENCODING="utf-8")


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def skeleton_left(skill_md: pathlib.Path):
    """SKILL.md가 비었거나 골격 자리표시자가 남았으면 (줄, 내용) 목록을 돌려준다."""
    body = skill_md.read_text(encoding="utf-8", errors="ignore")
    if not body.strip():
        return [(0, "SKILL.md가 비어 있다")]
    hits = []
    for i, ln in enumerate(body.splitlines(), 1):
        if HOLE.search(ln) or any(ph in ln for ph in PLACEHOLDERS):
            hits.append((i, ln.strip()[:60]))
    return hits


def fixture(tmp: pathlib.Path):
    """ads_info/all 응답 모양의 합성 데이터 — 표기변형·결측 월·마지막 달 급락을 포함한다."""
    def months(base, dip_last=False, missing=()):
        out = []
        for i, m in enumerate(REF_MONTHS[: REF_MONTHS.index(LAST) + 1]):
            if m in missing:
                out.append({"month": m, "gg": 10, "nv_code": 1})   # total 키 없음
                continue
            v = base + (i % 12) * 100
            if dip_last and m == LAST:
                v = int(v * 0.2)
            out.append({"month": m, "gg": v // 3, "nv": v - v // 3, "total": v})
        return out

    data = [
        {"keyword": "선크림", "ads_metrics": {"volume_total": 90000, "volume_avg": 7000,
         "volume_trend": -0.6, "cpc": 0.5, "competition": "HIGH", "competition_index": 90,
         "gg_volume_total": 30000, "nv_volume_total": 60000}, "monthly_volume": months(5000, dip_last=True)},
        {"keyword": "썬크림", "ads_metrics": {"volume_total": 88000, "volume_avg": 6900,
         "volume_trend": 0.1, "cpc": 0.5, "competition": "HIGH", "competition_index": 88,
         "gg_volume_total": 29000, "nv_volume_total": 59000}, "monthly_volume": months(4900)},
        {"keyword": "브랜드에이 선스틱", "ads_metrics": {"volume_total": 40000, "volume_avg": 3000,
         "volume_trend": 0.3, "cpc": 0.4, "competition": "LOW", "competition_index": 20,
         "gg_volume_total": 15000, "nv_volume_total": 25000},
         "monthly_volume": months(2500, missing=("2025-03", "2025-04"))},
    ]
    (tmp / "metrics.json").write_text(json.dumps({"data": data}, ensure_ascii=False), encoding="utf-8")
    (tmp / "seeds.json").write_text(json.dumps(["선크림"], ensure_ascii=False), encoding="utf-8")
    (tmp / "r1.json").write_text(json.dumps({"data": ["썬크림", "브랜드에이 선스틱"]},
                                            ensure_ascii=False), encoding="utf-8")
    (tmp / "rules.json").write_text('{"썬":"선"}', encoding="utf-8")
    # 응답 안에서 같은 keyword·같은 내용의 행이 반복된 경우(2026-09-30 실측 모양)
    (tmp / "metrics_dup.json").write_text(json.dumps({"data": data + [data[2]]}, ensure_ascii=False), encoding="utf-8")
    # 시드별 intent_finder 응답 — request_detail로 확장 단계를 추정할 수 있는 모양
    (tmp / "if_a.json").write_text(json.dumps({"request_detail": {"keywords": ["선크림"]},
                                               "data": ["선크림", "썬크림"]}, ensure_ascii=False), encoding="utf-8")
    (tmp / "if_b.json").write_text(json.dumps({"request_detail": {"keywords": ["선크림"]},
                                               "data": ["선크림", "브랜드에이 선스틱"]}, ensure_ascii=False), encoding="utf-8")
    (tmp / "themes.json").write_text(json.dumps({
        "rules": {"변형치환": {"썬": "선"}},
        "themes": [{"name": "제형", "pattern": "선스틱|스틱"}],
        "brands": ["브랜드에이"],
        "no_match": {"with_brand": "브랜드·제품명", "without_brand": "코어·일반"},
    }, ensure_ascii=False), encoding="utf-8")


def run(cmd, tmp):
    p = subprocess.run([sys.executable] + cmd, cwd=tmp, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=CHILD_ENV)
    return p.returncode, p.stdout + p.stderr


def header(path):
    return set(next(csv.reader(open(path, encoding="utf-8-sig"))))


def main():
    utf8_console()
    fails, skipped = [], []
    S = str(HERE)

    # 0. 문서 — 스크립트가 돌아도 SKILL.md가 골격이면 «문서의 예시 명령»이 없다. 정본 폴더(shared/)에서는 건너뛴다.
    skill_md = HERE.parent / "SKILL.md"
    if skill_md.is_file():
        left = skeleton_left(skill_md)
        if left:
            at = ", ".join(f"SKILL.md:{i}" for i, _ in left[:5]) + (" …" if len(left) > 5 else "")
            fails.append(("SKILL.md", f"골격 자리표시자 {len(left)}곳이 남아 있다(⬜·괄호 안내문) — 채운 뒤 다시 돌릴 것",
                          at))

    def has(name):
        if (HERE / name).is_file():
            return True
        skipped.append(name)
        return False

    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        fixture(tmp)

        if has("check_refmonth.py"):
            rc, out = run([f"{S}/check_refmonth.py", "metrics.json", "--queried-at", "2026-09-07"], tmp)
            if rc or LAST not in out:
                fails.append(("check_refmonth", "기준월 미출력 또는 비정상 종료", out[-300:]))
            elif "이상 의심: 0행" in out:
                fails.append(("check_refmonth", "마지막 달 급락 픽스처를 감지하지 못함", out[-300:]))

        if has("trend_yoy.py"):
            rc, out = run([f"{S}/trend_yoy.py", "metrics.json", "-o", "trend.csv"], tmp)
            if not rc and not all(k in out for k in ("계산 불가", "같은 검색의 중복", "게이트 미만")):
                fails.append(("trend_yoy", "랭킹 풀 제외 사유가 사유별로 나뉘어 출력되지 않는다", out[-300:]))
            need = {"yoy", "yoy_ex_last_month", "mom_last", "mom_last_prev_year",
                    "last_month_gap_vs_prev_year", "last_month_flag", "missing_months_in_window"}
            if rc:
                fails.append(("trend_yoy", "비정상 종료", out[-300:]))
            else:
                miss = need - header(tmp / "trend.csv")
                if miss:
                    fails.append(("trend_yoy", f"명세 컬럼 누락: {sorted(miss)}", ""))

        if has("build_universe.py"):
            args = [f"{S}/build_universe.py", "--metrics", "metrics.json", "--themes", "themes.json",
                    "--seeds", "seeds.json", "--round", "S1=seeds.json", "--round", "R1=r1.json",
                    "-o", "universe.csv"]
            if (tmp / "trend.csv").is_file():
                args += ["--trend", "trend.csv"]
            rc, out = run(args, tmp)
            if rc:
                fails.append(("build_universe", "비정상 종료", out[-300:]))
            else:
                miss = {"canonical", "brand", "theme_primary"} - header(tmp / "universe.csv")
                if miss:
                    fails.append(("build_universe", f"명세 컬럼 누락: {sorted(miss)}", ""))
            rc2, out2 = run([f"{S}/build_universe.py", "--metrics", "metrics_dup.json", "--themes", "themes.json",
                             "-o", "universe_dup.csv"], tmp)
            if rc2 or "완전 중복 행 1행 합침" not in out2:
                fails.append(("build_universe", "같은 keyword의 완전 중복 행을 합치지 않는다(상한 이중 계상)", out2[-300:]))
            elif not rc and out.split("상한 ")[1].split()[0] != out2.split("상한 ")[1].split()[0]:
                fails.append(("build_universe", "완전 중복 행이 있으면 그룹 상한이 달라진다", out2[-300:]))

        if has("coverage_curve.py"):
            # SKILL.md 예시 그대로 — 인라인 JSON
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json",
                           "--rules", '{"썬":"선"}', "--themes", "themes.json",
                           "--round", "S1=seeds.json", "--round", "R1=r1.json@2"], tmp)
            if rc:
                fails.append(("coverage_curve", "인라인 --rules JSON으로 비정상 종료 (문서 예시가 실패한다)", out[-300:]))
            elif "보류" not in out:
                fails.append(("coverage_curve", "절단(반환=limit)인데 «보류» 판정이 안 나옴", out[-300:]))
            elif "논브랜드" not in out:
                fails.append(("coverage_curve", "--themes를 줬는데 논브랜드 커버리지 미출력", out[-300:]))
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json",
                           "--rules", "rules.json", "--round", "S1=seeds.json"], tmp)
            if rc:
                fails.append(("coverage_curve", "--rules 파일 경로로 비정상 종료", out[-300:]))
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json",
                           "--rules", "themes.json", "--round", "S1=seeds.json"], tmp)
            if rc:
                fails.append(("coverage_curve", "--rules 에 themes.json 을 주면 비정상 종료", out[-300:]))
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json",
                           "--round", "R1=r1.json@2", "--round", "R2=seeds.json"], tmp)
            if rc or "보류" not in out:
                fails.append(("coverage_curve", "앞 라운드의 절단이 뒤 라운드에 가려 «보류»가 안 나온다(순서 의존)", out[-300:]))
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json", "--themes", "themes.json",
                           "--rules", '{"썬":"선"}', "--round", "S1=seeds.json", "--round", "R1=r1.json@2"], tmp)
            if "함께 수렴" in out:
                fails.append(("coverage_curve", "전체 판정이 «보류»인데 논브랜드를 «수렴»으로 적는다(문구 모순)", out[-300:]))
            verdicts = []
            for order in (("if_a.json", "if_b.json"), ("if_b.json", "if_a.json")):
                rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json", "--rules", '{"썬":"선"}',
                               "--round", "S1=seeds.json",
                               "--round", f"R1-x={order[0]}", "--round", f"R1-y={order[1]}"], tmp)
                verdicts.append(next((l for l in out.splitlines() if l.startswith("판정:")), out[-200:]))
            if rc or verdicts[0] != verdicts[1] or "묶음" not in out:
                fails.append(("coverage_curve", "같은 라운드(R1-…) 호출을 묶지 않아 판정이 호출 순서에 달린다", " / ".join(verdicts)))
            rc, out = run([f"{S}/coverage_curve.py", "--metrics", "metrics.json", "--rules", '{"썬":"선"}',
                           "--round", "S1=seeds.json", "--round", "A=if_a.json", "--round", "B=if_b.json"], tmp)
            if rc or "같은 확장 단계" not in out:
                fails.append(("coverage_curve", "같은 확장 단계가 라운드 둘로 갈렸는데 «보류»로 알리지 않는다", out[-300:]))

        if has("trend_signals.py"):
            rc, out = run([f"{S}/trend_signals.py", "metrics.json", "-o", "sig.csv",
                           "--gate", "0", "--rules", '{"썬":"선"}'], tmp)
            need = {"growth", "accel", "persistence_months", "diffusion", "signal_score",
                    "grade", "gated_out", "canonical", "data_ref_month", "window_end"}
            if rc:
                fails.append(("trend_signals", "문서 예시(인라인 --rules)로 비정상 종료", out[-300:]))
            else:
                miss = need - header(tmp / "sig.csv")
                if miss:
                    fails.append(("trend_signals", f"명세 컬럼 누락: {sorted(miss)}", ""))
                elif "마지막 달" not in out:
                    fails.append(("trend_signals", "마지막 달 처리 방식이 출력에 표시되지 않는다", out[-200:]))

        if has("series_dedup.py"):
            sys.path.insert(0, str(HERE))
            from series_dedup import apply_rules, dedup_map
            same = [{"month": m, "total": 5000} for m in REF_MONTHS[:12]]
            rows = [{"keyword": "stick vacuum cordless", "monthly_volume": same},
                    {"keyword": "cordless stick vacuum", "monthly_volume": same},
                    {"keyword": "robot mop", "monthly_volume": [{"month": m, "total": 4000} for m in REF_MONTHS[:12]]}]
            dm = dedup_map(rows)
            if dm["stick vacuum cordless"]["rep"] != dm["cordless stick vacuum"]["rep"]:
                fails.append(("series_dedup", "시계열이 같은 어순 변형을 중복으로 묶지 못한다", ""))
            try:
                from series_dedup import collapse_identical
                rows2, merged, conflicts = collapse_identical(rows + [dict(rows[2])])
            except ImportError:
                rows2, merged, conflicts = rows, 0, []
            if merged != 1 or len(rows2) != 3 or conflicts:
                fails.append(("series_dedup", "같은 keyword의 완전 중복 행을 하나로 합치지 못한다", ""))
            if dm["robot mop"]["rep"] != "robot mop":
                fails.append(("series_dedup", "시계열이 다른 키워드를 중복으로 묶었다", ""))
            if apply_rules("vacuum sale", {"vacuums": "vacuum"}) != "vacuum sale":
                fails.append(("series_dedup", "변형치환이 단어 경계를 넘는다", ""))
            if apply_rules("stickvacuum", {"stickvac": "stickvacuum"}) != "stickvacuum":
                fails.append(("series_dedup", "변형치환이 멱등이 아니다(stickvacuumuum)", ""))

        # 공통 리포트 렌더러 — 견본 입력이 렌더되고, 계약 위반(경계 문장 없음 · 증감률만 있는 표)은 거부되는가
        if has("render_report.py"):
            own = HERE.parent / "assets" / "report.example.json"         # 스킬 자기 견본 — 스킬이 고친다
            example = HERE.parent / "assets" / "report.sample.json"      # 공유 견본 — 고치지 않는다
            if own.is_file():
                rc, out = run([f"{S}/render_report.py", str(own), "-o", "own.html"], tmp)
                if rc:
                    fails.append(("render_report", "스킬 견본(assets/report.example.json)이 렌더링 계약을 어긴다", out[-400:]))
            elif HERE.name == "scripts" and (HERE.parent / "SKILL.md").is_file():
                fails.append(("render_report", "스킬 견본 assets/report.example.json 이 없다", ""))
            if not example.is_file():
                fails.append(("render_report", "공유 견본 assets/report.sample.json 이 없다 — 렌더 계약을 시험할 수 없다", ""))
            else:
                rc, out = run([f"{S}/render_report.py", str(example), "-o", "report.html"], tmp)
                page = (tmp / "report.html").read_text(encoding="utf-8") if (tmp / "report.html").is_file() else ""
                if rc or 'class="boundary"' not in page or "{{" in page:
                    fails.append(("render_report", "견본 입력이 렌더되지 않았다", out[-300:]))
                bad = json.loads(example.read_text(encoding="utf-8"))
                bad.pop("boundary", None)
                for sec in bad.get("sections", []):
                    for blk in sec.get("blocks", []):
                        if blk.get("type") == "table":
                            for col in blk.get("columns", []):
                                if col.get("role") in ("base", "abs_change"):
                                    col.pop("role")
                (tmp / "bad.json").write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
                rc, out = run([f"{S}/render_report.py", "bad.json", "--check"], tmp)
                if rc != 1 or "boundary" not in out or "증감률 열만" not in out:
                    fails.append(("render_report", "계약 위반(경계 문장 없음 · 증감률만 있는 표)을 거부하지 못했다", out[-300:]))

    # 우리 맥락 — 맥락 파일이 있으면 스키마 필수 키 · 검증표 질문 ID 를 본다(references/context-schema.md)
    ctx_f, vt_f = HERE.parent / "assets" / "context.json", HERE.parent / "assets" / "validation-table.json"
    if ctx_f.is_file():
        try:
            ctx = json.loads(ctx_f.read_text(encoding="utf-8"))
            need = [("decision", "who_decides"), ("market", "gl"), ("market", "category_boundary"), ("brands", "ours")]
            miss = [f"{a}.{b}" for a, b in need if not (ctx.get(a) or {}).get(b)] + \
                   [k for k in ("guardrails", "request_examples") if not ctx.get(k)]
            if ctx.get("schema") != "lm-skill-context/v1" or miss:
                fails.append(("context.json", "맥락 파일 스키마가 맞지 않는다", f"schema={ctx.get('schema')} · 빈 키 {miss}"))
        except ValueError as e:
            fails.append(("context.json", "JSON 오류", str(e)[:200]))
        if not vt_f.is_file():
            fails.append(("validation-table.json", "맥락 파일은 있는데 검증표가 없다", ""))
        else:
            try:
                ids = [q.get("id") for q in json.loads(vt_f.read_text(encoding="utf-8")).get("questions") or []]
                if not ids or not all(ids) or len(set(ids)) != len(ids):
                    fails.append(("validation-table.json", "질문 ID 가 없거나 비었거나 겹친다", str(ids)[:200]))
            except ValueError as e:
                fails.append(("validation-table.json", "JSON 오류", str(e)[:200]))

    # 스킬 고유 스크립트 검증 — scripts/selftest_*.py 가 있으면 함께 돌린다(중첩 실행 방지)
    if not os.environ.get("LM_SELFTEST_NESTED"):
        env = dict(CHILD_ENV, LM_SELFTEST_NESTED="1")
        for extra in sorted(HERE.glob("selftest_*.py")):
            r = subprocess.run([sys.executable, str(extra)], cwd=HERE.parent, env=env,
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode:
                fails.append((extra.name, "스킬 고유 selftest 실패", (r.stdout + r.stderr)[-300:]))

    if skipped:
        print(f"건너뜀 — 이 스킬에 배치되지 않은 스크립트: {', '.join(skipped)}")
    if fails:
        print(f"실패 {len(fails)}건\n")
        for name, why, detail in fails:
            print(f"  ✗ {name}: {why}")
            if detail:
                print(f"      {detail.strip()[:200]}")
        print("\n문서의 예시와 스크립트가 어긋나 있다. 배포 전에 고칠 것.")
        return 1
    checked = len([n for n in ("check_refmonth.py", "trend_yoy.py", "build_universe.py",
                               "coverage_curve.py", "trend_signals.py", "series_dedup.py", "render_report.py") if (HERE / n).is_file()])
    print(f"통과 — 배치된 스크립트 {checked}종 계약 검증 완료 (API 호출 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
