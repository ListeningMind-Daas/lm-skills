#!/usr/bin/env python3
"""태스크 스킬 골격 생성 — 계약·재사용 스크립트·검증 자산을 배치한다.

빈 폴더에서 시작하면 매번 계약을 다시 쓰게 되고, 다시 쓸 때마다 함정 처리가 하나씩 빠진다.
그래서 골격은 사람이 아니라 이 스크립트가 만든다. 내용(잡·시드축·특화 지표)만 채우면 된다.

    python3 new_skill.py <스킬명> --job "한 줄 정의" \\
        --connectors keyword_info,intent_finder [--monthly] [--expand] [--share] \\
        --out ../../skills

플래그는 어떤 함정 처리와 재사용 스크립트가 필요한지를 결정한다:
  --monthly  시계열을 쓴다      → T1·T2·T3·T4·T6 + check_refmonth·trend_yoy
  --expand   반복 확장을 한다    → T7 + coverage_curve
  --share    점유·기회를 논한다  → T8
"""
import argparse, json, pathlib, shutil, sys

KIT = pathlib.Path(__file__).resolve().parents[3]          # 저장소 루트
CONTRACT = KIT / "shared" / "lm-data-contract.md"
SHARED_SCRIPTS = KIT / "shared" / "scripts"                 # 재사용 스크립트 정본
SHARED_ASSETS = KIT / "shared" / "assets"
# 이전 판은 특정 태스크 스킬(map-your-market)에서 스크립트를 복사했다. 그 폴더가 사라지면
# 조용히 아무것도 복사하지 않고, 생성된 SKILL.md는 "배치돼 있다"고 말했다 — 그래서 정본을
# shared로 올리고, 없으면 여기서 즉시 실패시킨다.

REUSE = {
    "monthly": ["check_refmonth.py", "trend_yoy.py"],
    "expand": ["coverage_curve.py"],
    "always": ["build_universe.py", "series_dedup.py", "selftest.py"],
}
TRAPS = {
    "monthly": ["T1 기준월·지연 3종 표기", "T2 마지막 달 완결성 점검",
                "T3 volume_trend 대신 전년 동기 비교", "T4 기저 볼륨 게이트", "T6 결측 월 처리"],
    "expand": ["T7 절단(limit) 검사 — 절단이면 수렴 판정 보류"],
    "share": ["T8 논브랜드 분리 산출"],
    "always": ["T5 표기변형 canonical 정규화"],
}


def declaration(name, connectors, flags, traps):
    """감사기가 읽는 기계 판독 선언. 추론(문서에 단어가 있나)은 «쓰지 마라»는 문장까지
    잡아내므로, 스킬이 스스로 의무를 선언하고 감사기는 그것을 기준으로 검사한다."""
    ids = sorted({t.split()[0] for t in traps})
    return (f"<!-- lm-skill: kind=task; connectors={','.join(connectors)}; "
            f"flags={','.join(f for f in flags if f != 'always')}; traps={','.join(ids)} -->")


def skeleton(name, job, connectors, traps, flags):
    trap_rows = "\n".join(f"| {t} | STEP ? | ⬜ 미작성 |" for t in traps)
    decl = declaration(name, connectors, flags, traps)
    conn_rows = "\n".join(f"| `{c}` | (이 스킬에서의 용도를 쓸 것) | (결정식) |" for c in connectors)
    return f"""---
name: {name}
description: |
  {job}

  "(트리거 발화 1)" · "(트리거 발화 2)" · "(트리거 발화 3)"
  같은 요청이면 반드시 이 스킬을 쓴다.

  (사용자가 스킬 이름을 말하지 않아도 켜야 하는 상황을 여기에 쓴다.)
  (이 스킬이 아니라 다른 스킬로 가야 하는 경계도 쓴다.)
license: MIT
metadata:
  version: "0.1"
---

{decl}

# {name}

> 골격은 `new_skill.py`가 생성했다. 아래 ⬜ 표시를 채우면 감사를 통과한다. 판 번호는 frontmatter `metadata.version`, 변경 이력은 같은 폴더 `CHANGELOG.md`에 쓴다.

## 데이터 계약

이 스킬은 `references/lm-data-contract.md`(정본 사본)를 따른다. 계약은 확장할 수 있지만
뒤집을 수 없다. 아래는 이 스킬에 해당하는 **함정 처리 의무**다 — 각 항목을 어느 STEP에서
어떻게 처리하는지 채워야 한다.

| 함정 | 처리 위치 | 상태 |
|---|---|---|
{trap_rows}

> 이 표는 **선언**이다. 감사기는 표를 빼고 본문만 보므로, 각 함정을 **실제 STEP 본문에서**
> 어떻게 처리하는지 써야 통과한다. 표만 채우면 «함정 미처리»로 걸린다.

## 커넥터와 크레딧

| 커넥터 | 용도 | 크레딧 결정식 |
|---|---|---|
{conn_rows}

⬜ 예산표를 쓸 것 — `keyword_info`를 쓴다면 **2단 조회**(전량 `ads_metrics` 1 → 게이트 통과분만
`all` 10)를 기본으로 하고, 2단 대상 수가 총액을 지배한다는 점을 명시한다.

## 번들 자산

| 파일 | 하는 일 | 쓰는 곳 |
|---|---|---|
⬜ 채울 것 (재사용 스크립트가 이미 배치돼 있다 — `scripts/` 확인)

분류·정규화를 쓰는 스킬이면 `assets/themes.template.json`을 복사해 채운다.
스키마는 `references/themes-schema.md` 참조 — `theme_primary`는 **배열 순서**로 정해진다.

## 사전 확인

⬜ 채울 것 — gl 단일 확정 / 카테고리 경계 / 입력 요건

## 실행 순서

⬜ STEP 0부터 채울 것. 시계열을 쓴다면 **STEP n.5 데이터 기준월 확인**을 반드시 넣는다.

## 산출물

⬜ 파일명과 컬럼 목록을 쓸 것. **컬럼 목록은 스크립트 실제 출력과 일치해야 한다** —
어긋나면 문서를 믿은 쪽이 틀린 산출물을 만든다. `scripts/selftest.py`가 이를 검사한다.

## 4-Label

| 라벨 | 이 스킬에서의 적용 |
|---|---|
| `[실제 데이터]` | ⬜ |
| `[추정]` | ⬜ |
| `[가정]` | ⬜ |
| `[데이터 공백]` | ⬜ |

## 한계

⬜ 채울 것 — 월 그레인·지연, 검색≠구매, 커넥터별 한계
"""

# 변경 이력은 SKILL.md 가 아니라 같은 폴더의 CHANGELOG.md 에 쓴다 — SKILL.md 는 에이전트가 매번 읽는 절차서라
# 지난 판의 결함 서술이 섞이면 지금 절차로 오독된다. 판 번호는 frontmatter metadata.version 이 정본이다.
CHANGELOG = """# {name} — 변경 이력

판 번호의 정본은 SKILL.md frontmatter `metadata.version` 이다. 새 판을 낼 때 두 곳을 같이 고친다.
결함은 «무엇이 몇 % 틀렸다»처럼 근거 수치와 함께 적는다.

## v0.1

- 골격 생성 (`new_skill.py`)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--job", required=True, help="한 줄 정의 (description 첫 문단이 된다)")
    ap.add_argument("--connectors", required=True, help="쉼표 구분")
    ap.add_argument("--monthly", action="store_true")
    ap.add_argument("--expand", action="store_true")
    ap.add_argument("--share", action="store_true")
    ap.add_argument("--out", default=str(KIT / "skills"))
    a = ap.parse_args()
    if not CONTRACT.is_file():
        sys.exit("lm-skill-creator는 저장소 전체(클론)에서 실행한다 — 루트의 shared/ 정본을 찾지 못했다.\n"
                 f"  기대 위치: {CONTRACT.parent}")

    dest = pathlib.Path(a.out).resolve() / a.name
    if dest.exists():
        sys.exit(f"이미 있다: {dest}")
    for sub in ("references", "scripts", "assets", "evals"):
        (dest / sub).mkdir(parents=True)

    flags = ["always"] + [f for f in ("monthly", "expand", "share") if getattr(a, f)]
    traps = [t for f in flags for t in TRAPS.get(f, [])]
    connectors = [c.strip() for c in a.connectors.split(",") if c.strip()]

    (dest / "SKILL.md").write_text(skeleton(a.name, a.job, connectors, traps, flags), encoding="utf-8")
    (dest / "CHANGELOG.md").write_text(CHANGELOG.format(name=a.name), encoding="utf-8")
    shutil.copyfile(CONTRACT, dest / "references" / "lm-data-contract.md")
    need = [s for f in flags for s in REUSE.get(f, [])]
    missing = [s for s in need if not (SHARED_SCRIPTS / s).is_file()]
    if missing or not CONTRACT.is_file():
        shutil.rmtree(dest, ignore_errors=True)
        sys.exit("공유 자산이 없다 — 생성을 중단한다(빈 스크립트로 스킬이 나가는 것을 막는다).\n"
                 f"  기대 위치: {SHARED_SCRIPTS}\n"
                 f"  없는 것: {', '.join(missing) or 'lm-data-contract.md'}")
    copied = []
    for script in need:
        if not (dest / "scripts" / script).exists():
            shutil.copyfile(SHARED_SCRIPTS / script, dest / "scripts" / script)
            copied.append(script)
    tmpl = SHARED_ASSETS / "themes.template.json"
    if tmpl.is_file() and ("expand" in flags or "share" in flags):
        shutil.copyfile(tmpl, dest / "assets" / "themes.template.json")
        copied.append("themes.template.json")
        schema = KIT / "shared" / "references" / "themes-schema.md"
        if schema.is_file():
            shutil.copyfile(schema, dest / "references" / "themes-schema.md")
            copied.append("themes-schema.md")
    (dest / "evals" / "evals.json").write_text(json.dumps({
        "skill_name": a.name,
        "evals": [{"id": 0, "prompt": "⬜ 함정을 밟는 카테고리로 쓸 것", "expected_output": "⬜", "files": []},
                  {"id": 1, "prompt": "⬜ 입력이 불완전한 발화로 쓸 것", "expected_output": "⬜", "files": []}],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"생성: {dest}")
    print(f"  계약 사본 · 재사용 스크립트 {len(copied)}종 ({', '.join(copied)})")
    print(f"  함정 의무 {len(traps)}건이 SKILL.md에 미작성(⬜)으로 박혀 있다")
    print("\n다음: SKILL.md의 ⬜를 채우고 →")
    print(f"  python3 scripts/selftest.py")
    print(f"  python3 {pathlib.Path(__file__).parent}/audit_skill.py {dest} --contract {CONTRACT}")


if __name__ == "__main__":
    main()
