#!/usr/bin/env python3
"""map-entry-situations 공통 함수 (이 스킬 고유 — 공유 사본 아님).

뜻 고정 파일(sense.json)과 상황 사전(themes.json)을 읽고, 검색어 하나를 규칙으로 분류한다.
분류는 정규식 사전만 쓴다 — LLM 분류는 재현되지 않아 같은 데이터에서 다른 CEP 지도가 나온다.

  axis_of("시점:여름") -> "시점"         CEP 축 6종: 계기 · 시점 · 장소 · 사람 · 문제 · 목표
  Lexicon.classify(keyword) -> (bucket, kind, facets)
  Lexicon.match(keyword)    -> {bucket, kind, facets, term, scope}
     kind = "상황"(CEP) | "비상황"(구매 탐색 등) | "브랜드·제품명" | "미분류"

테마 패턴은 **어절 경계에 맞춰** 평가한다(v0.1.1 — v0.1.0은 공백을 지운 canonical 아무 데서나 걸었다):
  1. 원문 검색어 → NFKC · 소문자 · 변형치환(공백 유지) → 어절마다 시작 위치를 기억한 채 공백 제거
  2. 카테고리어(키워드 · anchor_terms)와 브랜드명을 «□» 한 글자로 가린다(마스킹). 가린 자리의 앞뒤도 경계다
     — `직수정수기` → `직수□` 라서 `수정`이 걸릴 수 없다. 패턴 안에 적힌 카테고리어 · 브랜드명도 같은 «□»로 바꿔 맞춘다
  3. 패턴은 경계(어절 시작 · 가린 자리 앞뒤)에서 **시작하는** 일치만 인정한다. 끝은 열려 있다 — 조사 · 어미 · 합성어가
     붙어도 된다(`이사할때` · `아기분유`). `정수 영어로`의 `수영`, `미세플라스틱`의 `스틱`처럼 낱말 가운데서 시작하는 일치는 버린다
  4. 정밀도 신호 — 주 테마 일치가 어절 전체(또는 어절 + 조사)면 scope «어절», 더 긴 어절의 앞부분이면 «어절 일부».
     «어절 일부» 비중이 높은 CEP는 패턴이 다른 낱말의 앞머리를 잡았을 수 있다(`아이` → `아이콘`)
"""
import json, pathlib, re, sys, unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import apply_rules as _apply_rules  # noqa: E402
from series_dedup import canon as _canon  # noqa: E402  (계약 T5 — NFKC·소문자·변형치환·공백 제거)

MASK = "□"
# 어절 끝에 붙어도 «어절 전체 일치»로 보는 조사 · 어미 · 접미 — 정밀도 신호용이다(일치 여부는 바꾸지 않는다)
PARTICLES = {"은", "는", "이", "가", "을", "를", "에", "에서", "의", "도", "로", "으로", "와", "과", "랑", "이랑", "용",
             "들", "에게", "한테", "만", "때", "할때", "하는", "하기", "할", "한", "전", "후", "중", "시", "하고", "해서"}

AXES = ("계기", "시점", "장소", "사람", "문제", "목표")
NON_CEP_PREFIX = "비상황"


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def load_json(path):
    return json.load(open(path, encoding="utf-8"))


def data_rows(obj):
    return obj.get("data", []) if isinstance(obj, dict) else (obj or [])


def axis_of(name):
    head = (name or "").split(":", 1)[0].strip()
    return head if head in AXES else ""


def load_sense(path, allow_unconfirmed=False):
    """뜻 고정 파일. 사용자가 확인하기 전(confirmed_by_user != true)이면 멈춘다 — 뜻이 여러 개인 키워드에서
    확인 없이 돌면 다른 뜻의 검색어가 CEP로 집계된다."""
    s = load_json(path)
    miss = [k for k in ("keyword", "gl", "anchor_terms") if not s.get(k)]
    if miss:
        sys.exit(f"sense.json 필수 키 누락: {miss}")
    if s.get("gl") not in ("kr", "jp", "us"):
        sys.exit(f"sense.json gl 은 kr / jp / us 중 하나여야 한다: {s.get('gl')}")
    if not s.get("confirmed_by_user") and not allow_unconfirmed:
        sys.exit("sense.json 이 사용자 확인 전이다(confirmed_by_user != true) — 뜻 고정(분야 · 앵커 · 제외)을 "
                 "사용자에게 보여 주고 확인받은 뒤 true 로 바꿀 것 (STEP 0)")
    s.setdefault("seeds", [])
    s.setdefault("exclude", [])
    s.setdefault("competitors", [])
    s.setdefault("focal_brand", "")
    return s


class Lexicon:
    def __init__(self, themes, sense=None):
        self.rules = (themes.get("rules") or {}).get("변형치환", {}) or {}
        nm = themes.get("no_match") or {}
        self.with_brand = nm.get("with_brand", "브랜드·제품명")
        self.without_brand = nm.get("without_brand", "미분류")
        brands = list(themes.get("brands") or [])
        sense = sense or {}
        for b in [sense.get("focal_brand")] + list(sense.get("competitors") or []):
            if b and b not in brands:
                brands.append(b)
        # 긴 브랜드명부터 — «라운드랩»과 «라운드» 같은 부분 일치에서 긴 쪽이 이긴다
        self.brands = sorted({b for b in brands if b}, key=lambda b: (-len(self.canon(b)), b))
        self.anchor = [self.canon(a) for a in (sense.get("anchor_terms") or []) if a]
        self.exclude = [re.compile(p) for p in (sense.get("exclude") or []) if p]
        self.seed = self.canon(sense.get("keyword", "")) if sense else ""
        # 마스킹 대상 — 카테고리어(키워드 · 앵커어)와 브랜드명, 긴 것부터
        terms = {t for t in [self.seed] + self.anchor + [self.canon(b) for b in self.brands] if t}
        self.mask_terms = sorted(terms, key=lambda t: (-len(t), t))
        self.themes = [(t["name"], re.compile(self._mask_pattern(t["pattern"]))) for t in themes.get("themes", [])
                       if t.get("name") and t.get("pattern")]

    def canon(self, kw):
        return _canon(kw, self.rules)

    def _mask_pattern(self, pat):
        """패턴에 문자 그대로 적힌 카테고리어 · 브랜드명도 «□»로 바꾼다 — 검색어 쪽이 가려지므로(`간이정수기` → `간이□`)."""
        for t in self.mask_terms:
            if re.fullmatch(r"[0-9a-z가-힣]+", t) and t in pat:
                pat = pat.replace(t, MASK)
        return pat

    @staticmethod
    def _latin_ok(s, i, t):
        """영문 · 숫자 마스킹 대상은 영문 · 숫자 낱말 안에서 가리지 않는다(`sk`가 `ask` 안에서)."""
        if not re.search(r"[a-z0-9]", t):
            return True
        before = s[i - 1] if i > 0 else " "
        after = s[i + len(t)] if i + len(t) < len(s) else " "
        return not (re.match(r"[a-z0-9]", before) or re.match(r"[a-z0-9]", after))

    def segment(self, kw):
        """(가린 문자열, 경계 위치 집합). 경계 = 어절 시작 · 가린 자리 앞뒤 · 문자열 끝."""
        s = _apply_rules(unicodedata.normalize("NFKC", str(kw)).lower(), self.rules)
        chars, starts = [], set()
        for tok in s.split():
            starts.add(len(chars))
            chars.extend(tok)
        flat = "".join(chars)
        out, bounds, i = [], set(), 0
        while i < len(flat):
            if i in starts:
                bounds.add(len(out))
            hit = next((t for t in self.mask_terms if flat.startswith(t, i) and self._latin_ok(flat, i, t)), None)
            if hit:
                bounds.add(len(out)); out.append(MASK); bounds.add(len(out))
                i += len(hit)
                continue
            out.append(flat[i]); i += 1
        bounds.add(len(out))
        return "".join(out), bounds

    @staticmethod
    def _hit(rx, s, bounds):
        for b in sorted(bounds):
            if b >= len(s):
                break
            m = rx.match(s, b)
            if m and m.end() > b:
                return b, m.end()
        return None

    @staticmethod
    def _scope(s, bounds, b, e):
        nxt = min((x for x in bounds if x > b), default=len(s))
        if e >= nxt or s[e:nxt] in PARTICLES:
            return "어절"
        return "어절 일부"

    def match(self, kw):
        """검색어 하나(원문 · 공백 유지)의 분류와 주 테마 일치 정보. 주 테마는 배열 순서로 정한다(themes-schema ①).
        키워드 자신 · 앵커어 그 자체(«선크림» · «선스틱»)는 상황이 아니라 «카테고리 코어»다 — 미분류에 넣으면
        미분류 비중이 코어 검색량에 묻혀 사전 빈칸을 가린다."""
        c = self.canon(kw)
        if c and (c == self.seed or c in self.anchor):
            return {"bucket": "카테고리 코어", "kind": "코어", "facets": [], "term": "", "scope": ""}
        s, bounds = self.segment(kw)
        facets, first = [], None
        for n, rx in self.themes:
            h = self._hit(rx, s, bounds)
            if h:
                facets.append(n)
                first = first or h
        if facets:
            b, e = first
            return {"bucket": facets[0], "kind": "상황" if axis_of(facets[0]) else "비상황", "facets": facets,
                    "term": s[b:e], "scope": self._scope(s, bounds, b, e)}
        if self.brands_in(c):
            return {"bucket": self.with_brand, "kind": "브랜드·제품명", "facets": [], "term": "", "scope": ""}
        return {"bucket": self.without_brand, "kind": "미분류", "facets": [], "term": "", "scope": ""}

    def classify(self, kw):
        """(bucket, kind, facets). 원문 검색어(공백 유지)를 받는다 — canonical을 넘기면 어절 경계를 잃는다."""
        m = self.match(kw)
        return m["bucket"], m["kind"], m["facets"]

    def brands_in(self, c):
        """canonical 안에 든 브랜드 전부(겹치는 짧은 이름은 긴 이름에 포함되면 뺀다)."""
        hit = []
        for b in self.brands:
            bc = self.canon(b)
            if bc and bc in c and not any(bc in self.canon(h) for h in hit):
                hit.append(b)
        return hit

    def excluded(self, c):
        for p in self.exclude:
            if p.search(c):
                return p.pattern
        return ""

    def anchor_hit(self, c):
        """카테고리와 함께 검색됐는가 — 앵커어(카테고리어)나 브랜드가 들어 있으면 Y."""
        return any(a and a in c for a in self.anchor) or bool(self.brands_in(c))


def shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def window(end, size):
    return [shift(end, i) for i in range(size - 1, -1, -1)]


def series_of(row):
    """{month: total 또는 None}. nv_code 결측 월은 total 키가 없다 — 0이 아니라 None(계약 §2 · T6)."""
    out = {}
    for m in row.get("monthly_volume") or []:
        if isinstance(m, dict) and m.get("month"):
            out[m["month"]] = m.get("total") if "total" in m else None
    return out


def paths_of(resp):
    out = []
    for p in data_rows(resp):
        if isinstance(p, list):
            seq = [" ".join(str(k).split()) for k in p if str(k).strip()]
            if seq:
                out.append(seq)
    return out


def parse_spec(spec):
    """'파일.json@limit' → (파일, limit 또는 None)."""
    m = re.match(r"^(.*)@(\d+)$", spec)
    return (m.group(1), int(m.group(2))) if m else (spec, None)


def pre_seed(path, lex, steps):
    """경로 안 시드(canonical 일치)의 첫 위치 앞 `steps`걸음의 검색어. 시드가 없으면 None."""
    cs = [lex.canon(k) for k in path]
    if lex.seed not in cs:
        return None
    i = cs.index(lex.seed)
    return path[max(0, i - steps):i]


def read_pool(path):
    import csv
    return list(csv.DictReader(open(path, encoding="utf-8-sig")))


def build_universe(pool_rows, ads_rows, lex, monthly_rows=None):
    """후보 풀 + keyword_info 응답 → 검색어 행 목록(행 보존)과 canonical 그룹.

    - 응답 행은 요청 표기와 다를 수 있다 → 풀과 canonical로 대조한다(계약 §1)
    - 요청하지 않은 변형 행은 source=«응답 변형»으로 남긴다(과금 안 됨)
    - 요청했는데 응답에 canonical이 같은 행이 없으면 «미반환» 행 [데이터 공백] — 0이 아니다
    - 같은 검색의 중복(시계열·지표 일치, series_dedup.dedup_map)은 dup_of에 대표를 적고 집계에서 뺀다(T5 ①)
    - 그룹 하한 = 그룹 내 최댓값, 상한 = 그룹 내 합(T5 ②)
    """
    from series_dedup import collapse_identical, dedup_map
    ads_rows, merged, conflicts = collapse_identical([r for r in ads_rows if isinstance(r, dict)])
    mon = {}
    for r in collapse_identical([r for r in (monthly_rows or []) if isinstance(r, dict)])[0]:
        if isinstance(r.get("keyword"), str):
            mon[r["keyword"]] = r
    by_canon = {}
    for p in pool_rows:
        by_canon.setdefault(p["canonical"], []).append(p)
    # 같은 검색 중복 — 지표 일치(ads_metrics 전량)와 시계열 일치(2단 대상) 둘 다 본다. 한쪽에만 시계열이 있으면
    # 시계열 버킷과 지표 버킷이 갈려 중복을 놓친다(selftest 픽스처 «선 크림»).
    dmap = dedup_map(ads_rows, lex.rules)
    smap = dedup_map(list(mon.values()), lex.rules)
    for k, v in smap.items():
        if v["rep"] != k and dmap.get(k, {"rep": k})["rep"] == k:
            dmap[k] = v
    rows, seen_canon = [], set()
    for r in ads_rows:
        kw = r.get("keyword")
        if not isinstance(kw, str):
            continue
        c = lex.canon(kw)
        seen_canon.add(c)
        ps = by_canon.get(c, [])
        mt = lex.match(kw)
        bucket, kind, facets = mt["bucket"], mt["kind"], mt["facets"]
        v = (r.get("ads_metrics") or {}).get("volume_total")
        d = dmap.get(kw, {"rep": kw})
        src = "|".join(sorted({s for p in ps for s in p["source"].split("|")})) or "응답 변형"
        rows.append({
            "keyword": kw, "canonical": c, "source": src,
            "path_pre_paths": sum(int(p.get("path_pre_paths") or 0) for p in ps),
            "anchor_hit": "Y" if lex.anchor_hit(c) else "N",
            "boundary": (f"경계 밖({lex.excluded(c)})" if lex.excluded(c) else ""),
            "cep": bucket, "kind": kind, "facets": "|".join(facets), "multi_cep": "Y" if len(facets) > 1 else "N",
            "brand": "|".join(lex.brands_in(c)), "volume_total": v if isinstance(v, (int, float)) else None,
            "match_term": mt["term"], "match_scope": mt["scope"],
            "dup_of": d["rep"] if d["rep"] != kw else "", "returned": "Y",
            "monthly": mon.get(kw),
        })
    for c, ps in by_canon.items():
        if c in seen_canon:
            continue
        p = ps[0]
        mt = lex.match(p["keyword"])
        bucket, kind, facets = mt["bucket"], mt["kind"], mt["facets"]
        rows.append({"keyword": p["keyword"], "canonical": c, "source": p["source"],
                     "path_pre_paths": sum(int(x.get("path_pre_paths") or 0) for x in ps),
                     "anchor_hit": p["anchor_hit"], "boundary": p["boundary"], "cep": bucket, "kind": kind,
                     "facets": "|".join(facets), "multi_cep": "Y" if len(facets) > 1 else "N",
                     "brand": p["brand"], "volume_total": None, "dup_of": "",
                     "match_term": mt["term"], "match_scope": mt["scope"],
                     "returned": "N" if not p["boundary"] else "요청 안 함", "monthly": None})
    groups = {}
    for r in rows:
        if r["boundary"] or r["dup_of"] or r["volume_total"] is None:
            continue
        groups.setdefault(r["canonical"], []).append(r)
    for c, g in groups.items():
        rep = max(g, key=lambda x: (x["volume_total"], -len(x["keyword"])))
        lo, hi = rep["volume_total"], sum(x["volume_total"] for x in g)
        for x in g:
            x["group_volume_lo"], x["group_volume_hi"], x["canonical_rep"] = lo, hi, rep["keyword"]
    # 분류가 어절 경계를 보므로 같은 canonical의 표기(`아기 분유` / `아기분유`)가 다르게 분류될 수 있다 —
    # 한 그룹이 두 CEP에 이중 집계되지 않게 그룹 대표의 분류를 같은 canonical의 모든 행에 쓴다
    reps = {c: max(g, key=lambda x: (x["volume_total"], -len(x["keyword"]))) for c, g in groups.items()}
    for r in rows:
        rp = reps.get(r["canonical"])
        if rp is not None and rp is not r:
            for k in ("cep", "kind", "facets", "multi_cep", "match_term", "match_scope"):
                r[k] = rp[k]
    for r in rows:
        r.setdefault("group_volume_lo", None); r.setdefault("group_volume_hi", None); r.setdefault("canonical_rep", "")
    return rows, groups, {"merged_identical": merged, "conflicts": conflicts}
