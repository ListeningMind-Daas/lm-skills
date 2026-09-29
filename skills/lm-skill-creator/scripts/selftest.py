#!/usr/bin/env python3
"""크리에이터 자가검증 — 골격 생성기와 감사기가 실제로 도는지 확인한다.

이 스킬의 산출물은 문서가 아니라 «다른 스킬»이므로, 검증도 «골격을 만들어 감사에 걸어보는»
형태여야 한다. 임시 폴더에서만 동작하며 API도 크레딧도 쓰지 않는다.
"""
import json, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
KIT = HERE.parents[2]

if not (KIT / "shared" / "lm-data-contract.md").is_file():
    sys.exit("lm-skill-creator는 저장소 전체(클론)에서 실행한다 — 루트의 shared/ 정본을 찾지 못했다.\n"
             f"  찾은 위치: {KIT / 'shared'}\n  git clone 한 저장소 루트에서 다시 실행할 것.")


def run(args, cwd=None):
    p = subprocess.run([sys.executable] + [str(a) for a in args], cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def main():
    fails = []
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td)

        # 1. 골격 생성 — 플래그가 함정 의무와 재사용 스크립트로 이어지는가
        rc, o = run([HERE/"new_skill.py", "probe", "--job", "검증용", "--connectors",
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

        # 3. 공유 자산이 없으면 «조용히 빈 스킬»이 아니라 즉시 실패해야 한다
        import os, shutil as sh
        stash = out / "_stash"
        sh.copytree(KIT / "shared" / "scripts", stash)
        try:
            for f in (KIT / "shared" / "scripts").glob("*.py"):
                os.remove(f)
            rc, o = run([HERE/"new_skill.py", "broken", "--job", "x", "--connectors",
                         "keyword_info", "--monthly", "--out", out])
            if rc == 0:
                fails.append(("new_skill", "공유 자산이 없는데 생성에 성공했다 — 빈 스크립트 스킬이 나간다", o[-300:]))
            elif (out / "broken").exists():
                fails.append(("new_skill", "실패했는데 반쪽 폴더가 남았다", ""))
        finally:
            for f in stash.glob("*.py"):
                sh.copyfile(f, KIT / "shared" / "scripts" / f.name)

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
    print("통과 — 골격 생성 · 의무 선언 · 공유 자산 부재 시 중단 · 감사기(막음/면제) · 사본 동기")
    return 0


if __name__ == "__main__":
    sys.exit(main())
