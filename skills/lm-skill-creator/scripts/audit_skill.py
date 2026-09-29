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


def read_all(root, suffixes=(".md",), skip_contract=True):
    """정본 사본은 제외하고 읽는다 — 계약은 모든 스킬이 갖고 있으므로, 사본에 적힌 처리를
    이 스킬이 한 것으로 세면 미처리 스킬도 전부 통과해버린다."""
    out = {}
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix not in suffixes or "evals" in f.parts:
            continue
        if skip_contract and f.name == "lm-data-contract.md":
            continue
        out[f] = f.read_text(encoding="utf-8", errors="ignore")
    return out


def main():
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
    raw = "\n".join(docs.values())
    # 함정 «의무 표»는 선언일 뿐 구현이 아니다. 표의 행만 보고 처리했다고 세면
    # 골격 상태의 스킬이 그대로 통과한다 — 그래서 표 영역을 빼고 본문만 검사한다.
    text = "\n".join(ln for ln in raw.splitlines()
                     if not (ln.lstrip().startswith("| T") and "|" in ln[3:]))
    code = "\n".join(read_all(root, (".py",)).values())
    head = skill_md.read_text(encoding="utf-8")[:4000]
    F = {"P0": [], "P1": [], "P2": []}
    def add(sev, title, detail): F[sev].append({"title": title, "detail": detail})

    # ── 미작성 골격
    holes = len([ln for ln in raw.splitlines()
                 if re.match(r"^\s*(\|\s*)?⬜\s*(채울|미작성|쓸|$)", ln)
                 or re.match(r"^\s*\w+\s*:\s*TODO", ln)])
    if holes:
        add("P0", f"미작성 항목 {holes}건", "골격 상태다(⬜/TODO). 채우기 전에는 감사 대상이 아니다")

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
        if a.kind == "task" and declared.get("kind") == "meta":
            a.kind = "meta"
        source = "선언"
    else:
        uses = {c: bool(re.search(c, text)) for c in
                ("keyword_info", "intent_finder", "cluster_finder", "path_finder", "serp", "trend_finder")}
        monthly = bool(re.search(r"monthly_volume|data_type\s*=?\s*[\"']?(all|ads_info)", text))
        expand = uses["intent_finder"] or uses["cluster_finder"]
        share = bool(re.search(r"점유|SoV|share|기회|진입", text))
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
    if not re.search(r"×|\*\s*len|공식|결정식", text):
        add("P0", "크레딧 결정식이 없다", "예산을 계산할 수 없으면 고객이 비용을 통제하지 못한다")
    if re.search(r"ads_info", text) and not re.search(r"ads_info[^\n]{0,80}(쓰지 않|10|상위집합)", text):
        add("P1", "`ads_info` 사용이 단가 근거 없이 등장한다",
            "단가가 환경마다 다르게 관측됐다(10 · 1). 상위집합인 `all`을 쓰고 «쓰지 않는다»고 명시할 것 (계약 §1)")

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

    # ── 4-Label
    labels = sum(1 for k in ("실제 데이터", "추정", "가정", "데이터 공백") if k in text)
    if a.kind == "task" and labels < 3:
        add("P0", "4-Label 규율 없음", f"발견 {labels}/4 — 측정·파생·가정·공백이 구분되지 않는다")

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
                       r"(?:dev|staging|internal)[-.][\w.-]+\.(?:io|com|net))", text, re.I)
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
