#!/usr/bin/env python3
"""크리에이터 자가검증 — 골격 생성기와 감사기가 실제로 도는지 확인한다.

이 스킬의 산출물은 문서가 아니라 «다른 스킬»이므로, 검증도 «골격을 만들어 감사에 걸어보는»
형태여야 한다. 임시 폴더에서만 동작하며 API도 크레딧도 쓰지 않는다.
"""
import json, os, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parents[2]
# 2026-10-12 워크숍 사전 실행의 재현 job — 함정 단어(«전년 동기»·«논브랜드»)가 description 에 들어간다
JOB_WITH_TRAP_WORDS = ("태블릿 용도(인강·게임·업무 등) 시드를 입력받아 용도별 검색 수요의 최근 12개월 규모와 "
                       "전년 동기 대비 추세를 브랜드/논브랜드로 나눠 표로 답한다")

for _s in (sys.stdout, sys.stderr):      # Windows cp949 콘솔에서 ✗ 출력이 죽지 않게
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

if not (KIT / "shared" / "lm-data-contract.md").is_file():
    sys.exit("lm-skill-creator는 저장소 전체(클론)에서 실행한다 — 루트의 shared/ 정본을 찾지 못했다.\n"
             f"  찾은 위치: {KIT / 'shared'}\n  git clone 한 저장소 루트에서 다시 실행할 것.")


def run(args, cwd=None):
    p = subprocess.run([sys.executable] + [str(a) for a in args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return p.returncode, p.stdout + p.stderr


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td)

        # 1. 골격 생성 — 플래그가 함정 의무와 재사용 스크립트로 이어지는가
        rc, o = run([HERE/"new_skill.py", "probe", "--job", JOB_WITH_TRAP_WORDS, "--connectors",
                     "keyword_info,intent_finder", "--monthly", "--expand", "--share", "--out", out])
        skill = out / "probe"
        if rc or not (skill/"SKILL.md").is_file():
            fails.append(("new_skill", "골격 생성 실패", o[-300:]))
        else:
            md = (skill/"SKILL.md").read_text(encoding="utf-8")
            for t in ("T1", "T2", "T3", "T4", "T6", "T7", "T8", "T5"):
                if t not in md:
                    fails.append(("new_skill", f"함정 의무 {t}가 골격에 박히지 않았다", ""))
                    break
            for f in ("check_refmonth.py", "trend_yoy.py", "coverage_curve.py", "selftest.py"):
                if not (skill/"scripts"/f).is_file():
                    fails.append(("new_skill", f"재사용 스크립트 {f} 미배치", ""))
            if not (skill/"references"/"lm-data-contract.md").is_file():
                fails.append(("new_skill", "계약 사본 미배치", ""))

        # 2. 감사기 — 미작성 골격을 P0로 막는가 (선언만 있는 스킬이 통과하면 안 된다)
        if skill.is_dir():
            rc, o = run([HERE/"audit_skill.py", skill])
            if rc == 0:
                fails.append(("audit_skill", "미작성 골격이 감사를 통과했다", o[-300:]))
            elif "미작성" not in o:
                fails.append(("audit_skill", "미작성 항목을 P0로 잡지 못했다", o[-300:]))

        # 3. 공유 자산이 없으면 «조용히 빈 스킬»이 아니라 즉시 실패해야 한다.
        #    저장소 정본을 지우지 않는다 — 임시 사본에서 스크립트를 빼고 --shared 로 가리킨다.
        import shutil as sh
        fake = out / "_shared"
        sh.copytree(KIT / "shared", fake, ignore=sh.ignore_patterns("__pycache__"))
        for f in (fake / "scripts").glob("*.py"):
            f.unlink()
        rc, o = run([HERE/"new_skill.py", "broken", "--job", "x", "--connectors",
                     "keyword_info", "--monthly", "--out", out, "--shared", fake])
        if rc == 0:
            fails.append(("new_skill", "공유 자산이 없는데 생성에 성공했다 — 빈 스크립트 스킬이 나간다", o[-300:]))
        elif (out / "broken").exists():
            fails.append(("new_skill", "실패했는데 반쪽 폴더가 남았다", ""))

        # 3-1. 감사기 — 표 칸 안 ⬜ 와 빈 4-Label 을 막는가 (2026-09-30: 줄 맨 앞 ⬜ 만 세서 19개 중 3개만 잡았다)
        if skill.is_dir():
            rc, o = run([HERE/"audit_skill.py", skill, "--json"])
            try:
                rep = json.loads(o[o.index("{"):])
                p0 = {f["title"] for f in rep["P0"]}
                titles = p0 | {f["title"] for f in rep["P1"]}
            except (ValueError, KeyError):
                p0 = titles = set()
            want = "미작성 항목 19건"
            if want not in p0:
                fails.append(("audit_skill", f"골격 ⬜ 를 전부 세지 못했다 ({want} 기대)", str(sorted(p0))[:300]))
            if "4-Label 규율 없음" not in p0:
                fails.append(("audit_skill", "칸이 빈 4-Label 표가 통과했다", str(sorted(p0))[:300]))
            # 함정 단어는 공유 문서 사본(themes-schema.md)과 골격 안내문에 이미 있다 — 그것으로 충족되면 안 된다
            # 2026-10-12: job 문장(= description)의 «전년 동기»·«논브랜드»로 T3·T8 이 충족됐다 — 머리도 세면 안 된다
            leaked = [t for t in ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8")
                      if not any(x.startswith(f"함정 미처리 — {t}") for x in titles)]
            if leaked:
                fails.append(("audit_skill", f"빈 골격에서 함정 {','.join(leaked)} 가 충족됐다 — "
                              "사본·안내문·description 단어를 세고 있다", ""))
            # 2026-10-12: 골격 표 머리의 «크레딧 결정식» 글자가 식으로 세져 P0 이 안 떴다
            if "크레딧 결정식이 없다" not in p0:
                fails.append(("audit_skill", "빈 골격에서 «크레딧 결정식 없음» P0 이 안 떴다 — 표 머리 글자를 식으로 세고 있다",
                              str(sorted(p0))[:300]))

        # 3-2. 골격에 배치된 공유 selftest — SKILL.md 가 골격이면 실패해야 한다(2026-10-12: 비어 있어도 통과했다)
        if (skill / "scripts" / "selftest.py").is_file():
            rc, o = run([skill / "scripts" / "selftest.py"], cwd=skill)
            if rc == 0 or "골격 자리표시자" not in o:
                fails.append(("selftest(공유)", "골격 SKILL.md 인데 통과했다", o[-300:]))
            (skill / "SKILL.md").write_text("", encoding="utf-8")
            rc, o = run([skill / "scripts" / "selftest.py"], cwd=skill)
            if rc == 0:
                fails.append(("selftest(공유)", "빈 SKILL.md 인데 통과했다", o[-300:]))

        # 3-3. --report — 렌더링 계약 사본 · 공통 렌더러 · 견본이 배치되고, 렌더러가 빠지면 감사가 P0 로 막는가
        rc, o = run([HERE/"new_skill.py", "probe-report", "--job", "가을 보습 검색 수요를 리포트로 답한다",
                     "--connectors", "keyword_info", "--monthly", "--report", "--out", out])
        rep_skill = out / "probe-report"
        if rc:
            fails.append(("new_skill --report", "골격 생성 실패", o[-300:]))
        else:
            for f in ("scripts/render_report.py", "scripts/selftest_render.py", "references/report-contract.md", "assets/report.example.json", "assets/report.sample.json"):
                if not (rep_skill / f).is_file():
                    fails.append(("new_skill --report", f"{f} 미배치", ""))
            md = (rep_skill / "SKILL.md").read_text(encoding="utf-8")
            if "## 리포트" not in md or "report" not in re.search(r"<!--\s*lm-skill:([^>]*)-->", md).group(1):
                fails.append(("new_skill --report", "리포트 섹션 또는 의무 선언 flags=report 가 없다", ""))
            rc, o = run([rep_skill / "scripts" / "selftest.py"], cwd=rep_skill)
            if "render_report" in o and "✗ render_report" in o:
                fails.append(("selftest(공유) --report", "견본 렌더 검사가 실패했다", o[-300:]))
            (rep_skill / "scripts" / "render_report.py").unlink()
            rc, o = run([HERE/"audit_skill.py", rep_skill, "--json"])
            try:
                p0 = {f["title"] for f in json.loads(o[o.index("{"):])["P0"]}
            except (ValueError, KeyError):
                p0 = set()
            try:
                p1 = {f["title"] for f in json.loads(o[o.index("{"):])["P1"]}
            except (ValueError, KeyError):
                p1 = set()
            for want in ("리포트 견본이 공유 견본 그대로다", "호출 기록을 남기는 단계가 없다"):
                if want not in p1:
                    fails.append(("audit_skill", f"빈 --report 골격에서 «{want}» P1 이 안 떴다 — 골격 안내문을 세고 있다", str(sorted(p1))[:300]))
            if "리포트 렌더러가 없다" not in p0:
                fails.append(("audit_skill", "flags=report 인데 렌더러가 없어도 P0 이 안 떴다", str(sorted(p0))[:300]))

        # 3-4. --context — 맥락 파일 견본 · 스키마가 배치되고, 미작성이면 감사가 막고, 채우면 풀리는가
        rc, o = run([HERE/"new_skill.py", "probe-context", "--job", "가을 보습 검색 수요를 우리 맥락으로 답한다",
                     "--connectors", "keyword_info", "--monthly", "--context", "--out", out])
        cs = out / "probe-context"
        if rc:
            fails.append(("new_skill --context", "골격 생성 실패", o[-300:]))
        else:
            for f in ("assets/context.json", "assets/validation-table.json", "references/context-schema.md", "assets/themes.template.json"):
                if not (cs / f).is_file():
                    fails.append(("new_skill --context", f"{f} 미배치", ""))
            if "context" not in re.search(r"<!--\s*lm-skill:([^>]*)-->", (cs / "SKILL.md").read_text(encoding="utf-8")).group(1):
                fails.append(("new_skill --context", "의무 선언에 flags=context 가 없다", ""))
            def titles(skill):
                rc_, o_ = run([HERE/"audit_skill.py", skill, "--json"])
                try:
                    rep = json.loads(o_[o_.index("{"):])
                    return {f["title"] for f in rep["P0"]} | {f["title"] for f in rep["P1"]}
                except (ValueError, KeyError):
                    return set()
            t0 = titles(cs)
            for want in ("맥락 파일 미작성", "검증표 미작성", "STEP 0 맥락 읽기 · 실행 기록 절차가 본문에 없다"):
                if want not in t0:
                    fails.append(("audit_skill", f"빈 --context 골격에서 «{want}» 이 안 떴다 — 골격 견본 · 안내문을 세고 있다", str(sorted(t0))[:300]))
            ctx = {"schema": "lm-skill-context/v1", "skill": "probe-context",
                   "decision": {"who_decides": "콘텐츠팀", "question_example": "가을 보습 수요"},
                   "market": {"gl": "kr", "category_boundary": "얼굴 기초 스킨케어"},
                   "brands": {"ours": ["우리브랜드"], "competitors": [], "not_brands": []},
                   "internal_metrics": ["월 판매량 1,200건"], "guardrails": ["효능 단정 금지"], "request_examples": ["가을 보습 수요 봐줘"]}
            (cs / "assets" / "context.json").write_text(json.dumps(ctx, ensure_ascii=False), encoding="utf-8")
            q = {"id": "Q1", "question": "q", "data": "d", "period": "p", "control_keywords": [], "calculation": "c",
                 "done_when": "배수 ≥ 1.2", "observed": "o", "hypothesis": "h"}
            (cs / "assets" / "validation-table.json").write_text(json.dumps({"questions": [q, q]}, ensure_ascii=False), encoding="utf-8")
            t1 = titles(cs)
            if "맥락 파일 미작성" in t1:
                fails.append(("audit_skill", "채운 맥락 파일을 미작성으로 본다", ""))
            for want in ("내부 지표에 값으로 보이는 숫자가 있다", "검증표 질문 ID 가 비었거나 겹친다"):
                if want not in t1:
                    fails.append(("audit_skill", f"«{want}» 을 잡지 못했다", str(sorted(t1))[:300]))

        # 4. 감사기 — meta 면제가 동작하는가
        rc, o = run([HERE/"audit_skill.py", HERE.parent, "--kind", "meta"])
        if "4-Label" in o or "함정 미처리" in o:
            fails.append(("audit_skill", "--kind meta인데 데이터 계약 검사가 걸렸다", o[-300:]))

        # 5. 공유 자산 드리프트 검사기
        vc = KIT / "shared" / "verify_shared.py"
        if vc.is_file():
            rc, o = run([vc, "--root", KIT])
            if rc != 0:
                fails.append(("verify_contract", "정본과 사본이 어긋나 있다", o[-300:]))

    if fails:
        print(f"실패 {len(fails)}건\n")
        for n, why, d in fails:
            print(f"  ✗ {n}: {why}")
            if d: print(f"      {d.strip()[:200]}")
        return 1
    print("통과 — 골격 생성 · 의무 선언 · 공유 자산 부재 시 중단(정본 무손상) · "
          "감사기(막음/칸 안 ⬜/빈 4-Label/사본·description 단어/표 머리 결정식/면제) · 골격 selftest 실패 · 사본 동기")
    return 0


if __name__ == "__main__":
    sys.exit(main())
