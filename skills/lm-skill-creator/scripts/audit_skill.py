#!/usr/bin/env python3
"""LM 태스크 스킬 정적 감사 — 데이터 계약 준수 여부를 기계적으로 검사한다.

사람이 읽어서 잡는 결함과, 읽어서는 못 잡는 결함이 있다. 이 스크립트는 후자를 맡는다:
계약이 요구하는 처리가 문서에 실제로 있는지, 크레딧이 식으로 적혔는지, 문서가 약속한
컬럼을 스크립트가 만드는지, 내부 경로가 새지 않았는지.

    python3 audit_skill.py <스킬폴더> [--contract shared/lm-data-contract.md] [--json]

결과는 P0(출시 차단) · P1(신뢰 저하) · P2(위생)로 분류된다. exit 1 = P0 있음.
"""
import argparse, hashlib, json, pathlib, re, sys

# 함정 8종 — 문서에서 이 표현들이 잡히면 처리한 것으로 본다(정밀도보다 재현율 우선).
TRAPS = [
    ("T1 기준월·지연",      r"기준월|data_ref_month|조회일과 구분|지연"),
    ("T2 마지막 달 미완결",  r"마지막 달|최신 월|미완결|완결성"),
    ("T3 volume_trend 대체", r"전년 동(기|월)|yoy|YoY"),
    ("T4 기저 게이트",       r"게이트|기저|최소 볼륨|floor"),
    ("T5 표기변형 정규화",   r"canonical|표기변형|정규화 그룹|이중 계상"),
    ("T6 결측 월",           r"결측|nv_code|계산 불가"),
    ("T7 절단 검사",         r"절단|limit에 (딱 )?붙|truncat"),
    ("T8 논브랜드 분리",     r"논브랜드|non-?brand|브랜드 분리"),
]
# 커넥터별로 해당되는 함정 (그 커넥터를 안 쓰면 면제)
TRAP_REQUIRES = {
    "T1 기준월·지연": "monthly", "T2 마지막 달 미완결": "monthly", "T3 volume_trend 대체": "monthly",
    "T4 기저 게이트": "monthly", "T6 결측 월": "monthly",
    "T5 표기변형 정규화": "any", "T7 절단 검사": "expand", "T8 논브랜드 분리": "share",
}


# 공유 문서 사본 — 모든 스킬이 같은 사본을 갖는다. 사본에 적힌 처리를 이 스킬이 한 것으로 세면
# 빈 골격도 통과한다(2026-09-30 실측: themes-schema.md 사본의 단어로 T5·T8 이 충족됐다).
# 저장소 밖에서 만든 스킬도 걸리도록 이름 목록을 기본으로 두고, 저장소 안이면 shared/references 를 더한다.
SHARED_DOCS = {"lm-data-contract.md", "themes-schema.md"}
_KIT_REFS = pathlib.Path(__file__).resolve().parents[3] / "shared" / "references"
if _KIT_REFS.is_dir():
    SHARED_DOCS |= {f.name for f in _KIT_REFS.glob("*.md")}

# 미작성 표시 — 골격이 박는 ⬜. «⬜로 박는다»처럼 기호를 설명하는 문장(조사가 바로 붙음)은 제외한다.
HOLE = re.compile(r"⬜(?![로를가는이의와과도/])")

# SKILL.md 머리(frontmatter) — description 은 `--job` 문장 그대로다. 본문 판정에 섞으면 job 문장에 «전년 동기»·
# «논브랜드»가 있다는 이유만으로 빈 골격의 T3·T8 이 «처리됨»이 됐다(2026-10-12 워크숍 사전 실행 실측).
FRONTMATTER = re.compile(r"\A---\n.*?\n---[ \t]*\n?", re.S)

# 크레딧 결정식 — «결정식»이라는 글자가 아니라 식 자체를 찾는다. 골격 표 머리의 «크레딧 결정식»에 걸려
# 빈 골격에서 «결정식 없음» P0 이 뜨지 않았다(2026-10-12 실측). 숫자에 곱하기가 붙은 항이 하나는 있어야 한다:
# `10 × K` · `30 + 2 × 반환` · 코드의 `10 * len(kws)`.
FORMULA = re.compile(r"\d\s*×\s*\S|\S\s*×\s*\d|\b\d+\s*\*\s*[A-Za-z_(]|[A-Za-z_)]\s*\*\s*\d+\b")


def utf8_console():
    """Windows cp949 콘솔에서 ✗·■ 를 찍다 UnicodeEncodeError 로 죽지 않게 한다."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def read_all(root, suffixes=(".md",), skip_shared=True):
    """공유 문서 사본은 제외하고 읽는다 — 사본에 적힌 처리를 이 스킬이 한 것으로 세면
    미처리 스킬도 전부 통과해버린다."""
    out = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix not in suffixes or "evals" in f.parts:
            continue
        if skip_shared and "references" in f.parts and f.name in SHARED_DOCS:
            continue
        out[f] = f.read_text(encoding="utf-8", errors="ignore")
    return out


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("skill")
    ap.add_argument("--contract", help="정본 경로 — 주면 사본 해시를 대조한다")
    ap.add_argument("--kind", choices=("task", "meta"), default="task",
                    help="task=데이터를 산출하는 스킬(기본) · meta=스킬을 만드는 스킬. "
                         "meta는 데이터 계약 검사(함정·4-Label·계약 사본)를 면제받는다")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = pathlib.Path(a.skill).resolve()
    if not root.is_dir():
        sys.exit(f"스킬 폴더가 아니다: {root}")
    skill_md = root / "SKILL.md"
    if not skill_md.is_file():
        sys.exit("SKILL.md가 없다 — 스킬로 인식되지 않는다")

    docs = read_all(root)
    # 본문 판정(함정·크레딧·4-Label·의무 추론)은 SKILL.md 머리를 뺀 본문만 본다. 머리 검사는 아래 `head`로 따로 한다.
    fm_text = ""
    body = dict(docs)
    if skill_md in body:
        m = FRONTMATTER.match(body[skill_md])
        if m:
            fm_text = m.group(0)
            body[skill_md] = body[skill_md][m.end():]
    raw = "\n".join(body.values())
    # 함정 «의무 표»는 선언일 뿐 구현이 아니다. 표의 행만 보고 처리했다고 세면
    # 골격 상태의 스킬이 그대로 통과한다 — 그래서 표 영역을 빼고 본문만 검사한다.
    # 미작성(⬜) 줄도 뺀다 — 골격의 안내문(«데이터 기준월 확인을 넣는다» 등)이 함정 단어를 담고 있어
    # 채우지 않은 채로 T1·T4 가 충족됐다(2026-09-30 실측).
    text = "\n".join(ln for ln in raw.splitlines()
                     if not (ln.lstrip().startswith("| T") and "|" in ln[3:]) and not HOLE.search(ln))
    code = "\n".join(read_all(root, (".py",)).values())
    head = skill_md.read_text(encoding="utf-8")[:4000]
    F = {"P0": [], "P1": [], "P2": []}
    def add(sev, title, detail): F[sev].append({"title": title, "detail": detail})

    # ── 미작성 골격 — 줄 맨 앞만 보던 이전 판은 표 칸 안의 ⬜(4-Label 4칸 · 함정 표 8칸)를 놓쳐
    # 골격 ⬜ 19개 중 3개만 잡았다(2026-09-30 실측). 위치와 무관하게 기호마다 센다.
    where = []
    for f, body in docs.items():
        for i, ln in enumerate(body.splitlines(), 1):
            n = len(HOLE.findall(ln)) + (1 if re.match(r"^\s*\w+\s*:\s*TODO", ln) else 0)
            if n:
                where.append((f.relative_to(root).as_posix(), i, n))
    holes = sum(n for *_, n in where)
    if holes:
        at = ", ".join(f"{p}:{i}" for p, i, _ in where[:5]) + (" …" if len(where) > 5 else "")
        add("P0", f"미작성 항목 {holes}건", f"골격 상태다(⬜/TODO) — {at}. 채우기 전에는 감사 대상이 아니다")
    ev = root / "evals" / "evals.json"
    if ev.is_file() and HOLE.search(ev.read_text(encoding="utf-8", errors="ignore")):
        add("P1", "eval 케이스가 미작성이다", "evals/evals.json 에 ⬜ 가 남아 있다 — 개선 루프의 기준선이 없다")

    # ── frontmatter
    fm = re.match(r"^---\n(.*?)\n---", head, re.S)
    if not fm:
        add("P0", "frontmatter 없음", "name·description이 없으면 스킬이 트리거되지 않는다")
    else:
        y = fm.group(1)
        for k in ("name", "description"):
            if not re.search(rf"^{k}\s*:", y, re.M):
                add("P0", f"frontmatter `{k}` 없음", "트리거의 유일한 신호다")
        if not re.search(r"^license\s*:", y, re.M):
            add("P2", "frontmatter `license` 없음", "배포 전 확정 필요")
        desc = y[y.find("description"):] if "description" in y else ""
        if len(desc) < 120:
            add("P1", "description이 짧다", f"{len(desc)}자 — 트리거 발화 예시를 넣어야 한다")
        if not re.search(r'"[^"]{6,}"|“[^”]{6,}”|·\s*"', desc):
            add("P1", "description에 트리거 발화 예시가 없다",
                "사용자가 실제로 칠 문장을 인용으로 넣어야 언더트리거를 막는다")

    # ── 의무 판정: 선언이 있으면 그것이 기준, 없으면 추론(레거시 스킬용)
    decl = re.search(r"<!--\s*lm-skill:([^>]*)-->", raw)
    declared = {}
    if decl:
        for part in decl.group(1).split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                declared[k.strip()] = v.strip()
    if declared:
        conn = [c for c in declared.get("connectors", "").split(",") if c]
        uses = {c: (c in conn) for c in
                ("keyword_info", "intent_finder", "cluster_finder", "path_finder", "serp", "trend_finder")}
        flags = set(f for f in declared.get("flags", "").split(",") if f)
        monthly, expand, share = "monthly" in flags, "expand" in flags, "share" in flags
        report = "report" in flags
        context = "context" in flags
        if a.kind == "task" and declared.get("kind") == "meta":
            a.kind = "meta"
        source = "선언"
    else:
        uses = {c: bool(re.search(c, text)) for c in
                ("keyword_info", "intent_finder", "cluster_finder", "path_finder", "serp", "trend_finder")}
        monthly = bool(re.search(r"monthly_volume|data_type\s*=?\s*[\"']?(all|ads_info)", text))
        expand = uses["intent_finder"] or uses["cluster_finder"]
        share = bool(re.search(r"점유|SoV|share|기회|진입", text))
        report = (root / "scripts" / "render_report.py").is_file()
        context = (root / "assets" / "context.json").is_file()
        source = "추론"
        add("P2", "의무 선언(<!-- lm-skill: … -->)이 없다",
            "커넥터·함정 의무를 문서 문구로 추론했다 — \"쓰지 마라\"는 문장까지 잡히므로 부정확하다. "
            "골격 생성기가 넣는 선언 블록을 추가할 것")

    # ── 크레딧: 범위 표기 금지, 결정식 요구
    range_hits = [ln for ln in text.splitlines()
                  if re.search(r"\d+\s*~\s*\d+\s*(credits|크레딧)", ln)
                  and not re.search(r"표기|였을 뿐|아니다|금지|대신", ln)]  # 나쁜 관행을 인용한 줄은 제외
    if range_hits:
        add("P1", "크레딧을 범위로 표기했다",
            f"관측 범위는 상한이 아니다. 결정식으로 적을 것 — 예: {range_hits[0].strip()[:70]}")
    if a.kind == "task" and not FORMULA.search(text):
        add("P0", "크레딧 결정식이 없다", "예산을 계산할 수 없으면 고객이 비용을 통제하지 못한다 — "
            "표 머리가 아니라 식(예: `1 × N + 10 × K`)이 본문에 있어야 한다")
    if re.search(r"ads_info", text) and not re.search(r"ads_info[^\n]{0,160}(cost_detail|단가|쓰지 않|10)", text):
        add("P1", "`ads_info` 사용이 단가 근거 없이 등장한다",
            "단가가 환경마다 다를 수 있다. 쓰려면 첫 호출 `cost_detail`로 단가를 확인한다고 "
            "적고, 결정식에는 확인 전 상한 10을 쓸 것 (계약 §1 v1.3)")

    # ── 함정 커버리지 (meta 스킬은 데이터를 산출하지 않으므로 면제)
    ctx = {"monthly": monthly, "expand": expand, "share": share, "any": True}
    missing = []
    for name, pat in TRAPS:
        need = ctx.get(TRAP_REQUIRES.get(name, "any"), True)
        if need and not re.search(pat, text, re.I):
            missing.append(name)
    for m in (missing if a.kind == "task" else []):
        sev = "P0" if m in ("T1 기준월·지연", "T3 volume_trend 대체", "T6 결측 월", "T7 절단 검사") else "P1"
        add(sev, f"함정 미처리 — {m}", "데이터 계약 §3이 요구하는 처리가 문서에 없다")

    # ── 4-Label — 라벨 이름만 세면 칸이 빈 표도 통과한다. 라벨마다 «이 스킬에서의 적용»이 적힌 줄이
    # 있어야 센다(라벨 이름·기호를 빼고 남는 글자가 있어야 한다). ⬜ 줄은 위에서 이미 빠졌다.
    def applied(label):
        for ln in text.splitlines():
            if label in ln:
                rest = re.sub(r"[`\[\]|*\s·:—-]", "", ln.replace(label, ""))
                if len(rest) >= 4:
                    return True
        return False
    labels = sum(1 for k in ("실제 데이터", "추정", "가정", "데이터 공백") if applied(k))
    if a.kind == "task" and labels < 3:
        add("P0", "4-Label 규율 없음", f"적용이 적힌 라벨 {labels}/4 — 측정·파생·가정·공백이 구분되지 않는다")

    # ── 정본 사본
    copy = root / "references" / "lm-data-contract.md"
    if not copy.is_file() and a.kind == "task":
        add("P1", "데이터 계약 사본이 없다", "스킬 폴더 밖 파일은 로드되지 않는다 — references/에 두어야 한다")
    elif a.contract and copy.is_file():
        canon = pathlib.Path(a.contract)
        if canon.is_file():
            h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            if h(copy) != h(canon):
                add("P1", "데이터 계약 사본이 정본과 다르다", f"{h(copy)} ≠ {h(canon)} — 재배포 필요")

    # ── 문서가 약속한 산출 컬럼을 코드가 만드는가
    promised = set(re.findall(r"\b([a-z][a-z0-9_]{4,})\b", "\n".join(
        re.findall(r"^\s{2}[a-z][a-z0-9_,\s]+$", text, re.M))))
    if promised and code:
        absent = sorted(c for c in promised if c not in code)[:6]
        if absent:
            add("P1", "문서가 약속한 컬럼이 코드에 없다", f"예: {', '.join(absent)}")

    # ── 리포트 — 렌더링 계약은 공통 자산이다. 리포트를 내는 스킬이 자기 렌더러를 쓰면 뼈대·차트 메타·숫자 추적이
    # 스킬마다 달라지고, 증감률만 있는 표나 사실 목록을 거치지 않은 숫자가 검사 없이 나간다.
    if report and a.kind == "task":
        if not (root / "scripts" / "render_report.py").is_file():
            add("P0", "리포트 렌더러가 없다", "flags=report 인데 scripts/render_report.py(공통 렌더러)가 없다 — "
                "계약 위반을 막는 수단이 없다. new_skill.py --report 로 배치되는 사본을 쓸 것")
        rc_copy = root / "references" / "report-contract.md"
        if not rc_copy.is_file():
            add("P1", "렌더링 계약 사본이 없다", "references/report-contract.md 를 두어야 에이전트가 규칙을 읽는다")
        else:
            canon_rc = pathlib.Path(__file__).resolve().parents[3] / "shared" / "references" / "report-contract.md"
            if canon_rc.is_file():
                h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                if h(rc_copy) != h(canon_rc):
                    add("P1", "렌더링 계약 사본이 정본과 다르다", f"{h(rc_copy)} ≠ {h(canon_rc)} — 재배포 필요")
        if not re.search(r"render_report", text):
            add("P1", "리포트 절차에 공통 렌더러가 없다", "본문 실행 순서에 `scripts/render_report.py report.json` 단계가 있어야 한다")
        own, sample = root / "assets" / "report.example.json", root / "assets" / "report.sample.json"
        if not own.is_file():
            add("P1", "리포트 견본 입력이 없다", "assets/report.example.json 이 없으면 selftest 가 이 스킬의 리포트를 시험하지 못한다")
        elif sample.is_file() and own.read_bytes() == sample.read_bytes():
            add("P1", "리포트 견본이 공유 견본 그대로다", "assets/report.example.json 을 이 스킬의 결과 모양(섹션 · 차트 · 사실 · 호출)으로 바꿔야 "
                "selftest 가 이 스킬의 리포트를 시험한다")
        # 골격 안내문(«…는 수집 원장에서 만든다 — 손으로 적지 않는다» · «렌더하면 … 호출 로그 …»)은 세지 않는다 — 빈 골격이 통과한다
        own_text = "\n".join(ln for ln in text.splitlines()
                              if "손으로 적지 않는다" not in ln and "렌더하면 `report.html` 옆에" not in ln
                              and "뼈대 순서 · 차트 메타" not in ln)
        if not re.search(r"provenance|호출 (원장|로그|기록)|cost_detail", own_text):
            add("P1", "호출 기록을 남기는 단계가 없다", "리포트의 호출 순서 · 로그 · 비용 보고서는 수집 원장에서 만든다 — "
                "어느 STEP 에서 원본 응답과 cost_detail 을 원장에 쓰는지 본문에 적는다(렌더링 계약 §9)")
    elif a.kind == "task" and re.search(r"\.html\b|HTML 리포트|리포트 HTML", text) and not report:
        add("P1", "HTML 산출물에 렌더링 계약이 적용되지 않았다",
            "리포트를 낸다면 의무 선언에 flags=report 를 넣고 공통 렌더러(render_report.py)로 만든다")

    # ── 우리 맥락 — 회사마다 다른 것은 assets/ 맥락 파일에만 둔다(references/context-schema.md).
    # 맥락이 대화에만 있으면 다음 요청에서 스킬이 우리 회사를 모르고, 본문에 박으면 다른 회사에 쓸 수 없다.
    if context and a.kind == "task":
        def load(name):
            f = root / "assets" / name
            if not f.is_file():
                return None, f"assets/{name} 없음"
            try:
                return json.loads(f.read_text(encoding="utf-8")), None
            except ValueError as e:
                return None, f"assets/{name} JSON 오류 — {e}"
        ctx, err = load("context.json")
        if err:
            add("P0", "맥락 파일이 없거나 깨졌다", err)
        else:
            raw_ctx = json.dumps(ctx, ensure_ascii=False)
            need = [("decision", "who_decides"), ("market", "gl"), ("market", "category_boundary"), ("brands", "ours")]
            miss = [f"{a_}.{b_}" for a_, b_ in need if not (ctx.get(a_) or {}).get(b_)]
            miss += [k for k in ("guardrails", "request_examples") if not ctx.get(k)]
            if HOLE.search(raw_ctx) or miss:
                add("P0", "맥락 파일 미작성", f"assets/context.json — ⬜ {len(HOLE.findall(raw_ctx))}칸"
                    + (f" · 빈 필수 키 {miss}" if miss else "") + " · 스키마 references/context-schema.md")
            # 값처럼 보이는 숫자만 — 콤마 · 소수 · 세 자리 이상 · 단위(% 원 억 만 건)가 붙은 수. «3개월 재구매율» 같은 이름은 통과
            VAL = re.compile(r"\d{1,3}(?:,\d{3})+|\d+\.\d+|\d{3,}|\d+\s*(?:%|원|억|만|건)")
            leak = [m for m in ctx.get("internal_metrics") or [] if VAL.search(str(m))]
            if leak:
                add("P1", "내부 지표에 값으로 보이는 숫자가 있다", f"internal_metrics 에는 이름만 둔다 — 예: {leak[0]}")
        vt, err = load("validation-table.json")
        if err:
            add("P0", "검증표가 없거나 깨졌다", err)
        else:
            qs = (vt or {}).get("questions") or []
            ids = [q.get("id") for q in qs]
            bad = [q.get("id") or "?" for q in qs if not q.get("done_when") or not q.get("observed") or "hypothesis" not in q]
            if not qs or HOLE.search(json.dumps(vt, ensure_ascii=False)):
                add("P0", "검증표 미작성", "assets/validation-table.json — 질문이 없거나 ⬜ 가 남았다")
            elif len(set(ids)) != len(ids) or not all(ids):
                add("P0", "검증표 질문 ID 가 비었거나 겹친다", f"{ids}")
            elif bad:
                add("P1", "완료 기준 · 관측 사실 · 원인 가설이 빈 질문이 있다", f"{bad}")
        if not (root / "assets" / "themes.json").is_file():
            add("P1", "분류 사전(themes.json)이 없다", "브랜드 · 표기 변형이 맥락 파일과 따로 논다 — themes.template.json 을 복사해 채운다")
        own_text = "\n".join(ln for ln in text.splitlines() if "회사 이름 · 브랜드 · 용어를 적지 않는다" not in ln)
        if not (re.search(r"context\.json", own_text) and re.search(r"request\.json|실행 폴더|work/", own_text)):
            add("P1", "STEP 0 맥락 읽기 · 실행 기록 절차가 본문에 없다",
                "실행 순서 맨 앞에 맥락 파일을 읽고 요청을 질문 ID 에 대응시키고 실행 폴더에 요청 기록을 남기는 단계를 쓴다")

    # ── 검증 자산
    if not (root / "scripts" / "selftest.py").is_file():
        add("P1", "selftest가 없다", "문서 예시가 실제로 도는지 검증할 수단이 없다")
    if not list(root.rglob("evals*")) and not (root / "evals").exists():
        add("P2", "eval 케이스가 없다", "개선 루프의 기준선이 없다")

    # ── 위생
    hygiene = "\n".join(ln for ln in text.splitlines()
                        if not re.search(r"삭제|제거|교체|대체|참조 없음|이력|→", ln))  # 익명 예시 검사용 — 조치를 서술한 줄은 제외
    # 누출은 전문(全文)에서 찾는다 — 변경 이력 줄에 적혀도 공개되면 누출이다.
    # 개인 경로 · 키 파일 이름 · 비공개 환경(dev/staging/internal) 호스트. 공개 문서 주소는 허용.
    leaks = re.findall(r"(/(?:Users|home)/[^\s`\"']+|\.[\w-]*api[-_]?key\b|"
                       r"(?:dev|staging|internal)[-.][\w.-]+\.(?:io|com|net))", fm_text + "\n" + text, re.I)
    if leaks:
        add("P2", "내부 경로·엔드포인트 노출", f"{sorted(set(leaks))[:3]}")
    if a.kind == "task" and re.search(r"brand-[abc]\b", hygiene):
        add("P2", "익명 예시(brand-a/b/c) 사용", "무엇이 좋은 입력인지 학습되지 않는다 — 실제 카테고리로 교체")

    n0, n1, n2 = len(F["P0"]), len(F["P1"]), len(F["P2"])
    if a.json:
        print(json.dumps({"skill": root.name, "P0": F["P0"], "P1": F["P1"], "P2": F["P2"]},
                         ensure_ascii=False, indent=1))
    else:
        print(f"■ {root.name} — P0 {n0} · P1 {n1} · P2 {n2}")
        print(f"  커넥터: {', '.join(k for k, v in uses.items() if v) or '없음'}"
              f" · 시계열: {'예' if monthly else '아니오'}"
              f" · 확장: {'예' if expand else '아니오'}"
              f" · 점유: {'예' if share else '아니오'}  ({source} 기준)\n")
        for sev, label in (("P0", "출시 차단"), ("P1", "신뢰 저하"), ("P2", "위생")):
            if F[sev]:
                print(f"[{sev} · {label}]")
                for f in F[sev]:
                    print(f"  ✗ {f['title']}\n      {f['detail']}")
                print()
        if not (n0 or n1 or n2):
            print("결함 없음 — 계약 준수")
    return 1 if n0 else 0


if __name__ == "__main__":
    sys.exit(main())
