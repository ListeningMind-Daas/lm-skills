#!/usr/bin/env python3
"""rank-category-brands 공통 규칙 — 브랜드 사전 읽기 · 키워드 귀속 · 기간 창 (이 스킬 고유).

plan_queries.py · rank_brands.py 가 같은 규칙으로 키워드를 판정하도록 한 곳에 둔다.
정규화(canonical)는 공유 사본 series_dedup.canon 을 그대로 쓴다 — NFKC → 소문자 → 변형치환(공백 유지) → 공백 제거.

귀속 규칙(결정론적, canonical 기준 · v0.1.1):
  0. 귀속 키 = 브랜드 표시명 + `brand_aliases` + `variants`. canonical 부분 일치로 찾고, 다른 브랜드의 더 긴 키 안에
     통째로 들어가는 일치는 버린다(«웰스»가 «교원웰스» 안에서 걸리는 것). 별칭이 없어도 variants 가 걸리면 그 브랜드다
  1. `exclude` 정규식에 걸리면 excluded (가장 먼저)
  2. 브랜드 2개 이상 걸리면 `_비교(복수 브랜드)` — 배분하지 않는다
  3. 1개 브랜드: 분야어가 없는 별칭과 완전히 같으면 alone(브랜드명 단독) · `variants` 가 들어 있으면 model ·
     분야어가 들어 있으면 genre(분야어가 든 별칭 «코지마 안마의자»와 완전히 같은 검색 포함) ·
     그 밖은 off_genre(분야어 없는 브랜드 검색 — 다른 품목 수요가 섞인다)
  4. 브랜드 0개: 분야어가 들어 있거나 `category_terms` 와 같으면 `_논브랜드`(category) · 그 밖은 off_genre
순위 · 점유에 들어가는 kind 는 genre · model(브랜드)과 compare · category(분모)다. alone · off_genre 는 따로 보고한다.
브랜드 0개로 `_논브랜드`가 된 행 중 앞부분이 사전 브랜드와 닮은 행은 `alias_gap_suspects` 가 «별칭 누락 의심»으로 찍는다.
"""
import json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon  # noqa: E402  공유 사본 — 고치지 않는다

NONBRAND = "_논브랜드"
COMPARE = "_비교(복수 브랜드)"
COUNTED_BRAND = ("genre", "model")
COUNTED = ("genre", "model", "compare", "category")


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def load_rows(path):
    o = json.load(open(path, encoding="utf-8"))
    rows = o.get("data", []) if isinstance(o, dict) else o
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("keyword"), str)]


def load_config(path):
    c = json.load(open(path, encoding="utf-8"))
    rules = (c.get("rules") or {}).get("변형치환", {}) or {}
    brands = [b for b in c.get("brands") or [] if isinstance(b, str) and b.strip()]
    if not brands:
        sys.exit("brands 가 비었다 — 브랜드 사전(STEP 1)을 먼저 채울 것")
    al = c.get("brand_aliases") or {}
    aliases = {b: list(dict.fromkeys([b] + [a for a in al.get(b, []) if a])) for b in brands}
    terms = [t for t in c.get("genre_terms") or [] if t]
    if not terms:
        sys.exit("genre_terms 가 비었다 — 분야어(«러닝화» 등) 없이 브랜드 검색을 분야로 한정할 수 없다")
    cfg = {
        "genre": c.get("genre") or "", "gl": c.get("gl") or "", "rules": rules, "brands": brands,
        "aliases": aliases, "genre_terms": terms,
        "category_terms": [t for t in c.get("category_terms") or [] if t],
        "variants": {b: [v for v in (c.get("variants") or {}).get(b, []) if v] for b in brands},
        "exclude": c.get("exclude") or "", "probe_alone": bool(c.get("probe_alone", True)),
    }
    cfg["_alias_canon"] = {b: [canon(a, rules) for a in aliases[b]] for b in brands}
    cfg["_term_canon"] = [t for t in (canon(t, rules) for t in terms) if t]
    # 분야어가 이미 든 별칭(«코지마 안마의자» · 표시명 «세스코 정수기») — 단독이 아니라 분야 한정 검색이다.
    # 1단에서 이미 든 분야어를 다시 붙이지 않고(«코지마 안마의자 안마의자» 금지), 완전 일치 검색은 genre 로 센다.
    # 분야어가 별칭 안의 **독립 토큰**이거나 별칭 끝에 붙을 때만 — «현대렌탈케어»처럼 회사명 가운데 든 «렌탈»은 아니다.
    cfg["alias_terms"] = {b: {a: _alias_terms(a, ac, terms, rules) for a, ac in zip(aliases[b], cfg["_alias_canon"][b])}
                          for b in brands}
    cfg["genre_qualified"] = {b: [a for a in aliases[b] if cfg["alias_terms"][b][a]] for b in brands}
    cfg["_cat_canon"] = {canon(t, rules) for t in cfg["category_terms"]}
    cfg["_var_canon"] = {b: [canon(v, rules) for v in cfg["variants"][b]] for b in brands}
    cfg["_alias_genre"] = {b: {canon(a, rules) for a in cfg["genre_qualified"][b]} for b in brands}
    cfg["_keys"] = {b: [k for k in dict.fromkeys(cfg["_alias_canon"][b] + cfg["_var_canon"][b]) if k] for b in brands}
    cfg["_exclude"] = re.compile(cfg["exclude"]) if cfg["exclude"] else None
    return cfg


def _alias_terms(alias, alias_canon, terms, rules):
    """별칭에 든 분야어 — 띄어 쓴 독립 낱말이거나 별칭 끝에 붙은 것만(«현대렌탈케어»의 «렌탈»은 아니다)."""
    toks = {canon(t, rules) for t in alias.split()}
    return [t for t in terms if canon(t, rules) and (canon(t, rules) in toks or alias_canon.endswith(canon(t, rules)))]


def brand_hits(c, cfg):
    """canonical 문자열에 걸리는 브랜드(사전 순서). 귀속 키 = 별칭(표시명 포함) + variants.

    다른 브랜드의 더 긴 키 안에 통째로 들어가는 일치만 있는 브랜드는 뺀다 — «교원웰스 정수기»에서 «웰스»(다른 브랜드 별칭)가
    따로 걸려 비교 검색이 되지 않게 한다."""
    spans = {}
    for b in cfg["brands"]:
        for k in cfg["_keys"][b]:
            i = c.find(k)
            while i >= 0:
                spans.setdefault(b, []).append((i, i + len(k)))
                i = c.find(k, i + 1)
    hits = []
    for b in cfg["brands"]:
        if b not in spans:
            continue
        other = [s for o, ss in spans.items() if o != b for s in ss]
        if other and all(any(o0 <= s0 and e0 <= o1 and o1 - o0 > e0 - s0 for o0, o1 in other) for s0, e0 in spans[b]):
            continue
        hits.append(b)
    return hits


def classify(kw, cfg):
    """→ (bucket, kind). bucket 은 브랜드명 · _비교 · _논브랜드 · ''(분모 밖)."""
    c = canon(kw, cfg["rules"])
    if cfg["_exclude"] and cfg["_exclude"].search(c):
        return "", "excluded"
    hits = brand_hits(c, cfg)
    has_term = any(t in c for t in cfg["_term_canon"])
    if len(hits) >= 2:
        return (COMPARE, "compare") if has_term else ("", "off_genre")
    if len(hits) == 1:
        b = hits[0]
        if c in cfg["_alias_canon"][b] and c not in cfg["_alias_genre"][b]:
            return b, "alone"
        if any(v in c for v in cfg["_var_canon"][b] if v):
            return b, "model"
        return (b, "genre") if has_term else (b, "off_genre")
    if has_term or c in cfg["_cat_canon"]:
        return NONBRAND, "category"
    return "", "off_genre"


def _head(c, cfg):
    """분야어 앞부분(canonical) — «쿠쿠정수기렌탈» → «쿠쿠». 분야어로 시작하거나 분야어가 없으면 ''."""
    pos = [c.find(t) for t in cfg["_term_canon"] if t in c]
    return c[:min(pos)] if pos and min(pos) > 0 else ""


def near_brand(head, cfg):
    """분야어 앞부분이 사전 브랜드와 닮았으면 그 브랜드 — 키(표시명 · 별칭 · variants) 안에 들어 있거나,
    키에서 분야어를 뺀 줄기가 앞부분 안에 있거나, 앞 2글자 이상이 같다. 2글자 미만은 보지 않는다."""
    if len(head) < 2:
        return ""
    for b in cfg["brands"]:
        for k in cfg["_keys"][b]:
            stem = k
            for t in cfg["_term_canon"]:
                stem = stem.replace(t, "")
            n = 0
            while n < min(len(head), len(stem)) and head[n] == stem[n]:
                n += 1
            if head in k or (len(stem) >= 2 and stem in head) or n >= 2:
                return b
    return ""


def alias_gap_suspects(items, cfg, min_volume):
    """«별칭 누락 의심» — `_논브랜드`로 판정된 행 중 12개월 볼륨이 min_volume 이상이고 분야어 앞부분이 사전 브랜드와
    닮은 행. items = (keyword, 12개월 볼륨) 반복. 볼륨 내림차순 dict 목록을 돌려준다.

    v0.1.0 은 «같은 묶음 안 버킷 불일치»만 봐서, «쿠쿠 정수기»처럼 묶음 상대가 없는 패밀리 검색이 통째로 논브랜드로 가도
    경고가 없었다(실데이터 재생에서 패밀리 검색이 이렇게 샜다)."""
    out, seen = [], set()
    for kw, vol in items:
        c = canon(kw, cfg["rules"])
        if c in seen or not vol or vol < min_volume:
            continue
        seen.add(c)
        b, _ = classify(kw, cfg)
        if b != NONBRAND:
            continue
        head = _head(c, cfg)
        nb = near_brand(head, cfg)
        if nb:
            out.append({"keyword": kw, "volume_12m": vol, "head": head, "near_brand": nb})
    return sorted(out, key=lambda x: (-x["volume_12m"], x["keyword"]))


def vol_total(r):
    return (r.get("ads_metrics") or {}).get("volume_total") or 0


# ── 기간 창 — 월 문자열 'YYYY-MM'
def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def window(end, size):
    return [shift(end, i) for i in range(size - 1, -1, -1)]


def series_of(r):
    """월 → total. 결측 월(nv_code — total 키 없음)은 넣지 않는다 — 0 이 아니라 결측(계약 §2)."""
    out = {}
    for m in r.get("monthly_volume") or []:
        if isinstance(m, dict) and m.get("month") and "total" in m and m["total"] is not None:
            out[m["month"]] = m["total"]
    return out


def wsum(series, months):
    """창 합계 — 한 달이라도 결측이면 None(계산 불가)."""
    if any(m not in series for m in months):
        return None
    return sum(series[m] for m in months)
