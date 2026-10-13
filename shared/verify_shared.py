#!/usr/bin/env python3
"""공유 자산 드리프트 검사 — 계약 · 재사용 스크립트 · 템플릿.

LM 스킬은 각자 폴더에 `references/lm-data-contract.md` 복사본을 둔다. Claude Code가
스킬 폴더 밖 파일을 읽지 않기 때문인데, 복사본은 반드시 낡는다.

그래서 복사본을 **해시로 검사**한다. 정본이 바뀌면 복사본이 전부 걸린다.

    python3 shared/verify_shared.py            # 검사만
    python3 shared/verify_shared.py --fix      # 정본으로 덮어써서 동기화

exit 0 = 전부 동기 / 1 = 드리프트 있음
"""
import argparse, hashlib, pathlib, re, shutil, sys

CONTRACT = "lm-data-contract.md"
# 스킬 폴더는 자기완결이어야 해서(Claude Code가 폴더 밖을 안 읽는다) 공유 자산은 복사된다.
# 복사본은 반드시 낡으므로 해시로 검사한다 — 계약뿐 아니라 스크립트·템플릿도 같다.
SHARED_DIRS = {"scripts": "*.py", "assets": "*.json", "references": "*.md"}
# 사본 판정은 파일명이 아니라 «출처 표식»으로 한다. 스킬이 같은 이름의 자기 스크립트를
# 가질 수 있기 때문이다(크리에이터의 selftest.py가 실제로 그랬다).
MARKER = "shared-asset: lm-skills/shared"


def digest(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def version_of(p: pathlib.Path) -> str:
    m = re.search(r"^version:\s*(\S+)", p.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent),
                    help="저장소 루트 (기본: 이 스크립트의 상위)")
    ap.add_argument("--fix", action="store_true", help="드리프트 난 복사본을 정본으로 덮어쓴다")
    a = ap.parse_args()

    root = pathlib.Path(a.root).resolve()
    canon = root / "shared" / CONTRACT
    if not canon.is_file():
        sys.exit(f"정본을 찾을 수 없다: {canon}")

    want, ver = digest(canon), version_of(canon)
    targets = [(canon, [p for p in root.rglob(CONTRACT) if p != canon])]
    for sub, pat in SHARED_DIRS.items():
        for src in sorted((root / "shared" / sub).glob(pat)):
            copies = []
            for p in root.rglob(src.name):
                if p == src or "shared" in p.parts:
                    continue
                try:
                    body = p.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if MARKER in body or p.suffix in (".json", ".md"):
                    copies.append(p)
            targets.append((src, copies))
    if not any(c for _, c in targets):
        print(f"정본 v{ver} ({want}) · 복사본 없음 — 스킬에 아직 배포되지 않았다")
        return 0

    drift, fixed = [], []
    for canon, copies in targets:
      want = digest(canon)
      for c in copies:
        rel = c.relative_to(root)
        if digest(c) == want:
            print(f"  ✓ {rel}")
        elif a.fix:
            shutil.copyfile(canon, c); fixed.append(rel)
            print(f"  ↻ {rel}  ({canon.name} 동기화)")
        else:
            drift.append(rel)
            print(f"  ✗ {rel}  (정본 {digest(canon)} ≠ 사본 {digest(c)} · {canon.name})")

    total = sum(len(c) for _, c in targets)
    print(f"\n계약 v{ver} · 공유 자산 {len(targets)}종 · 사본 {total}개")
    if fixed:
        print(f"{len(fixed)}개 동기화함.")
    if drift:
        print(f"드리프트 {len(drift)}개 — `--fix`로 동기화하거나, 복사본 쪽 변경이 의도였다면")
        print("정본에 반영한 뒤 다시 배포할 것. 복사본만 고친 변경은 다음 배포에서 사라진다.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
