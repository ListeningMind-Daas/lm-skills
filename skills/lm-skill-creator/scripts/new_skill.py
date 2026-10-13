#!/usr/bin/env python3
"""태스크 스킬 골격 생성 — 계약·재사용 스크립트·검증 자산을 배치한다.

빈 폴더에서 시작하면 매번 계약을 다시 쓰게 되고, 다시 쓸 때마다 함정 처리가 하나씩 빠진다.
그래서 골격은 사람이 아니라 이 스크립트가 만든다. 내용(잡·시드축·특화 지표)만 채우면 된다.

    python3 new_skill.py <스킬명> --job "한 줄 정의" \\
        --connectors keyword_info,intent_finder [--monthly] [--expand] [--share] [--report] \\
        --out ../../skills

플래그는 어떤 함정 처리와 재사용 스크립트가 필요한지를 결정한다:
  --monthly  시계열을 쓴다      → T1·T2·T3·T4·T6 + check_refmonth·trend_yoy
  --expand   반복 확장을 한다    → T7 + coverage_curve
  --share    점유·기회를 논한다  → T8
  --report   HTML 리포트를 낸다  → 렌더링 계약(report-contract.md) + 공통 렌더러(render_report.py) + 견본 입력
  --context  우리 회사 맥락으로 돈다 → 맥락 파일(context.json · validation-table.json) 견본 + 스키마 + STEP 0 맥락 읽기
"""
import argparse, json, os, pathlib, shutil, sys

KIT = pathlib.Path(__file__).resolve().parents[3]          # 저장소 루트
# 정본 위치는 --shared 로 바꿀 수 있다 — selftest 가 저장소 정본을 지우지 않고 «공유 자산 없음»을
# 시험하려고 쓴다(이전 판 selftest 는 저장소 shared/scripts 를 지웠다가 복원해, 중간에 끊기면 정본이 사라졌다).
SHARED = KIT / "shared"
# 이전 판은 특정 태스크 스킬(map-your-market)에서 스크립트를 복사했다. 그 폴더가 사라지면
# 조용히 아무것도 복사하지 않고, 생성된 SKILL.md는 "배치돼 있다"고 말했다 — 그래서 정본을
# shared로 올리고, 없으면 여기서 즉시 실패시킨다.

REUSE = {
    "monthly": ["check_refmonth.py", "trend_yoy.py"],
    "expand": ["coverage_curve.py"],
    "report": ["render_report.py", "selftest_render.py"],
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


REPORT_SECTION = """
## 리포트

이 스킬의 HTML 리포트는 `references/report-contract.md`(렌더링 계약 정본 사본)를 따르고,
`scripts/render_report.py`(공통 렌더러)로만 만든다. 스킬은 **무엇을 넣을지**를 `report.json`으로 정하고,
뼈대 순서 · 차트 메타 · 카드 필드 · 숫자 추적 · 호출 기록은 렌더러가 검사한다.
`assets/report.example.json`은 이 스킬의 견본이다 — 공유 견본(`report.sample.json`)을 복사해 두었으니 이 스킬의 결과 모양으로 바꾼다.
렌더하면 `report.html` 옆에 호출 로그(`report.calls.csv`)와 비용 보고서(`report.cost.json`)가 함께 나온다.

첫 화면 경계 문장(`boundary`): ⬜ 이 스킬이 관측한 것과 아직 관측하지 못한 것을 한 문장으로

| 섹션(`kind`) | 이 스킬이 넣는 것 | 쓰는 지표 · 차트 |
|---|---|---|
| `summary` 분석 요약 | ⬜ 핵심 발견 · 반대 근거 · 가능한 결정 | ⬜ |
| `trend` 트렌드 분석 | ⬜ 절대 규모 → 최근 변화 → 과거 방향 → 계절성 (시계열을 안 쓰면 «해당 없음») | ⬜ |
| `structure` 관심 구조 | ⬜ 하위 묶음의 차이 (없으면 «해당 없음») | ⬜ |
| `action` 기회와 실행 | ⬜ 실행 카드 — 가설 · 결과물 · 담당 · 내부 데이터 · 확대/수정/중단 조건 | ⬜ |
| `appendix` 근거 부록 | ⬜ 전체 수치 · 계산 정의 · 출처 · 비용 | ⬜ |

`report.json`의 `facts`(사실 목록)는 **스크립트의 저장 계산 결과**에서 만든다 — 문구의 숫자는 `{{ID}}`로만 쓴다.
`provenance.calls`(호출 순서 · 조건 · 반환 수 · 비용 · 원본 파일)는 **수집 스크립트의 원장**에서 만든다 — 손으로 적지 않는다.

호출 원장을 남기는 단계: ⬜ 어느 STEP 에서 원본 응답과 cost_detail 을 원장에 쓰는지

```bash
python3 scripts/render_report.py report.json -o report.html     # 계약 위반이면 렌더하지 않는다
```
"""


CONTEXT_SECTION = """
## 우리 맥락

이 스킬은 회사마다 다른 것을 `assets/`의 맥락 파일에서 읽는다 — 스키마는 `references/context-schema.md`.
스킬 본문에는 회사 이름 · 브랜드 · 용어를 적지 않는다. 다른 회사에 쓰려면 맥락 파일만 바꾼다.

| 파일 | 상태 |
|---|---|
| `assets/context.json` | ⬜ 결정 · 국가 · 카테고리 경계 · 브랜드 · 용어 · 가드레일 · 요청 예시를 채울 것 |
| `assets/validation-table.json` | ⬜ 질문 ID별 검증표(완료 기준 · 관측 사실 · 원인 가설)를 채울 것 |
| `assets/themes.json` | ⬜ 분류 사전(브랜드 · 테마 · 표기 변형) — `themes.template.json`을 복사해 채울 것 |

### STEP 0 · 맥락 읽기와 실행 기록

⬜ 실행 순서 맨 앞에 넣을 것 — 맥락 파일 셋을 읽고, 요청을 검증표 질문 ID에 대응시키고,
국가 · 카테고리 경계 밖 요청은 진행 전에 묻고, 실행 폴더 `work/<스킬>/<실행 ID>/`에 요청 기록을 남기는 절차
"""


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
""" + (CONTEXT_SECTION if "context" in flags else "") + (REPORT_SECTION if "report" in flags else "")

# 변경 이력은 SKILL.md 가 아니라 같은 폴더의 CHANGELOG.md 에 쓴다 — SKILL.md 는 에이전트가 매번 읽는 절차서라
# 지난 판의 결함 서술이 섞이면 지금 절차로 오독된다. 판 번호는 frontmatter metadata.version 이 정본이다.
CHANGELOG = """# {name} — 변경 이력

판 번호의 정본은 SKILL.md frontmatter `metadata.version` 이다. 새 판을 낼 때 두 곳을 같이 고친다.
결함은 «무엇이 몇 % 틀렸다»처럼 근거 수치와 함께 적는다.

## v0.1

- 골격 생성 (`new_skill.py`)
"""


def utf8_console():
    """Windows cp949 콘솔에서 ⬜·✗ 를 찍다 UnicodeEncodeError 로 죽지 않게 한다."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main():
    utf8_console()
    ap = argparse.ArgumentParser(
        description="LM 태스크 스킬 골격을 만든다 — 계약 사본·재사용 스크립트·함정 의무 표·evals 를 배치한다.",
        epilog="플래그는 함정 처리 의무와 배치할 재사용 스크립트를 정한다. 셋 다 생략하면 T5(표기변형 정규화)만 의무가 된다.\n"
               "예: python3 new_skill.py tablet-usage-demand --job \"…\" --connectors keyword_info,intent_finder "
               "--monthly --expand --share",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="스킬 폴더 이름(영문 소문자·하이픈). frontmatter name 이 된다")
    ap.add_argument("--job", required=True, help="한 줄 정의 (description 첫 문단이 된다)")
    ap.add_argument("--connectors", required=True,
                    help="쉼표 구분 — keyword_info,intent_finder,cluster_finder,path_finder,serp,trend_finder")
    ap.add_argument("--monthly", action="store_true",
                    help="월 시계열(monthly_volume)을 쓴다 → 함정 T1·T2·T3·T4·T6 의무 + check_refmonth.py·trend_yoy.py 배치")
    ap.add_argument("--expand", action="store_true",
                    help="연관어를 반복 확장한다(intent_finder·cluster_finder 라운드) → 함정 T7(절단 검사) 의무 + coverage_curve.py·themes 템플릿 배치")
    ap.add_argument("--share", action="store_true",
                    help="점유·기회·브랜드/논브랜드를 논한다 → 함정 T8(논브랜드 분리) 의무 + themes 템플릿 배치")
    ap.add_argument("--report", action="store_true",
                    help="HTML 리포트를 낸다 → 렌더링 계약 · 공통 렌더러(render_report.py) · 견본 입력 배치 + SKILL.md 리포트 섹션")
    ap.add_argument("--context", action="store_true",
                    help="우리 회사 맥락으로 돈다 → assets/context.json · validation-table.json 견본 + references/context-schema.md + SKILL.md 맥락 섹션")
    ap.add_argument("--out", default=str(KIT / "skills"), help="스킬 폴더를 만들 상위 폴더 (기본: 저장소 skills/)")
    ap.add_argument("--shared", default=str(SHARED), help=argparse.SUPPRESS)
    a = ap.parse_args()
    shared = pathlib.Path(a.shared)
    CONTRACT = shared / "lm-data-contract.md"
    SHARED_SCRIPTS = shared / "scripts"                     # 재사용 스크립트 정본
    SHARED_ASSETS = shared / "assets"
    if not CONTRACT.is_file():
        sys.exit("lm-skill-creator는 저장소 전체(클론)에서 실행한다 — 루트의 shared/ 정본을 찾지 못했다.\n"
                 f"  기대 위치: {os.path.relpath(CONTRACT.parent)}")

    dest = pathlib.Path(a.out).resolve() / a.name
    if dest.exists():
        sys.exit(f"이미 있다: {dest}")
    for sub in ("references", "scripts", "assets", "evals"):
        (dest / sub).mkdir(parents=True)

    flags = ["always"] + [f for f in ("monthly", "expand", "share", "report", "context") if getattr(a, f)]
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
                 f"  기대 위치: {os.path.relpath(SHARED_SCRIPTS)}\n"
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
        schema = shared / "references" / "themes-schema.md"
        if schema.is_file():
            shutil.copyfile(schema, dest / "references" / "themes-schema.md")
            copied.append("themes-schema.md")
    if "report" in flags:                                   # 렌더링 계약 사본 · 견본 입력
        for src, to in ((shared / "references" / "report-contract.md", dest / "references" / "report-contract.md"),
                        (SHARED_ASSETS / "report.sample.json", dest / "assets" / "report.sample.json"),
                        (SHARED_ASSETS / "report.sample.json", dest / "assets" / "report.example.json")):
            if not src.is_file():
                shutil.rmtree(dest, ignore_errors=True)
                sys.exit(f"공유 자산이 없다 — 생성을 중단한다: {os.path.relpath(src)}")
            shutil.copyfile(src, to)
            copied.append(to.name)
    if "context" in flags:                                  # 맥락 파일 견본 · 스키마 사본 · 분류 사전 견본
        pairs = [(SHARED_ASSETS / "context.template.json", dest / "assets" / "context.json"),
                 (SHARED_ASSETS / "validation-table.template.json", dest / "assets" / "validation-table.json"),
                 (shared / "references" / "context-schema.md", dest / "references" / "context-schema.md")]
        if not (dest / "assets" / "themes.template.json").exists():
            pairs += [(SHARED_ASSETS / "themes.template.json", dest / "assets" / "themes.template.json"),
                      (shared / "references" / "themes-schema.md", dest / "references" / "themes-schema.md")]
        for src, to in pairs:
            if not src.is_file():
                shutil.rmtree(dest, ignore_errors=True)
                sys.exit(f"공유 자산이 없다 — 생성을 중단한다: {os.path.relpath(src)}")
            shutil.copyfile(src, to)
            copied.append(to.name)
    (dest / "evals" / "evals.json").write_text(json.dumps({
        "skill_name": a.name,
        "evals": [{"id": 0, "prompt": "⬜ 함정을 밟는 카테고리로 쓸 것", "expected_output": "⬜", "files": []},
                  {"id": 1, "prompt": "⬜ 입력이 불완전한 발화로 쓸 것", "expected_output": "⬜", "files": []}],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    # 출력은 로그·문서로 옮겨 적히므로 로컬 절대경로 대신 현재 폴더 기준 경로를 쓴다
    rel = lambda p: os.path.relpath(p)
    print(f"생성: {rel(dest)}")
    print(f"  계약 사본 · 재사용 스크립트 {len(copied)}종 ({', '.join(copied)})")
    print(f"  함정 의무 {len(traps)}건이 SKILL.md에 미작성(⬜)으로 박혀 있다")
    # selftest 는 «스킬 폴더에서» 돌리는 명령이다 — 저장소 루트에서 그대로 치면 scripts/selftest.py 를 못 찾는다
    # (2026-10-12 워크숍 사전 실행). 그래서 cd 를 먼저 안내하고, 감사 명령도 스킬 폴더 기준 경로로 적는다.
    here = lambda p: os.path.relpath(p, dest)
    print("\n다음: 스킬 폴더로 이동해 SKILL.md의 ⬜를 채운다")
    print(f"  cd {rel(dest)}")
    print("채운 뒤 (스킬 폴더에서):")
    print("  python3 scripts/selftest.py")
    print(f"  python3 {here(pathlib.Path(__file__).resolve().parent / 'audit_skill.py')} . --contract {here(CONTRACT)}")


if __name__ == "__main__":
    main()
