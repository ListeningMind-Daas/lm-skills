#!/usr/bin/env python3
"""마커 매칭 — 이 스킬 고유 보조 모듈 (buyer_share.py가 쓴다).

패턴은 canonical(NFKC·소문자·변형치환 후 공백 제거) 문자열에 대해 평가한다. 공백을 지우면 영어 단어 경계가
사라져 `best`가 `bestbuy`에, `or`가 `for dyson`에, `deal`이 `ideal`에 걸린다(2026-09-28 US 실측 충돌 6건 중 4건).
그래서 **ASCII로만 된 대안(alternative)은 토큰 단위로** 맞춘다:

  - 시작이 토큰 경계(원래 공백 자리 · 문자열 처음 · 영숫자↔비영숫자 전환)여야 한다
  - 끝도 토큰 경계여야 한다 — 복수형 `s` 하나는 자동 허용(`deal` → `deals`)
  - 여러 토큰에 걸친 마커는 공백 없이 쓴다(`nearme` → `near me`, `howtoclean` → `how to clean`)
  - 접두어로 쓰려면 명시한다(`compar[a-z]*` → `comparison`, `refurb[a-z]*` → `refurbished`)
  - 전후방 탐색(`battery(?!life)`, `or(?=dyson|shark)`)은 원문 전체를 보고 평가한다

한글 등 비ASCII 대안은 종전대로 canonical 부분일치다(한국어는 띄어쓰기가 흔들려 토큰 경계가 신호가 아니다).
"""
import pathlib, re, sys, unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import apply_rules, canon  # noqa: E402  변형치환은 공백을 지우기 전에 (계약 T5)


def _is_an(ch):
    return ch.isascii() and ch.isalnum()


def canon_bounds(kw, rules):
    """(canonical, 토큰 경계 위치 집합). 경계 = 지워진 공백 자리 · 양 끝 · ASCII 영숫자↔그 밖 전환."""
    s = apply_rules(unicodedata.normalize("NFKC", kw).lower(), rules)
    out, bounds = [], {0}
    for ch in s:
        if ch.isspace():
            bounds.add(len(out))
        else:
            out.append(ch)
    c = "".join(out)
    bounds.add(len(c))
    bounds |= {i for i in range(1, len(c)) if _is_an(c[i - 1]) != _is_an(c[i])}
    return c, bounds


def split_alts(pattern):
    """최상위 `|`로 대안을 나눈다(괄호·문자 클래스·이스케이프 안의 `|`는 나누지 않는다)."""
    alts, cur, depth, cls, esc = [], [], 0, False, False
    for ch in pattern:
        if esc:
            cur.append(ch); esc = False; continue
        if ch == "\\":
            cur.append(ch); esc = True; continue
        if cls:
            cls = ch != "]"; cur.append(ch); continue
        if ch == "[":
            cls = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth == 0:
            alts.append("".join(cur)); cur = []; continue
        cur.append(ch)
    alts.append("".join(cur))
    return [x for x in alts if x]


class Marker:
    """한 클래스의 패턴. hit(c, bounds) → 걸리면 True."""

    def __init__(self, pattern):
        self.pattern = pattern
        alts = split_alts(pattern)
        non = [x for x in alts if not x.isascii()]
        self.sub = re.compile("|".join(f"(?:{x})" for x in non)) if non else None
        self.tok = [(re.compile(x), "(?=" in x or "(?!" in x or "(?<" in x) for x in alts if x.isascii()]

    def hit(self, c, bounds):
        if self.sub and self.sub.search(c):
            return True
        n = len(c)
        ends = sorted(bounds)
        for rx, look in self.tok:
            for st in ends:
                if st >= n:
                    continue
                m = rx.match(c, st)
                if m and (m.end() in bounds or (c[m.end():m.end() + 1] == "s" and m.end() + 1 in bounds)) and m.end() > st:
                    return True
                if look:
                    continue
                for e in ends:
                    if e <= st:
                        continue
                    if rx.fullmatch(c, st, e) or (c[e - 1] == "s" and e - 1 > st and rx.fullmatch(c, st, e - 1)):
                        return True
        return False


def brand_marker(brands, rules):
    """브랜드 사전 → [(브랜드, Marker)] — 브랜드명도 같은 규칙(ASCII는 토큰 단위)으로 맞춘다."""
    return [(b, Marker(re.escape(canon(b, rules)))) for b in brands]
