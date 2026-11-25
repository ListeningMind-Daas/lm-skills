#!/usr/bin/env python3
"""find-journey-leaks 공용 모듈 — 분류 사전 읽기 · 노드 분류 · 호출 원장 읽기 (이 스킬 고유).

다른 스크립트(make_leak_dict · record_call · leak_budget · journey_leaks · build_leak_report)가 import 한다.
API를 부르지 않는다.

노드 분류는 **규칙**이다(LLM 없음). 같은 사전 · 같은 응답이면 같은 분류가 나온다.
평가 순서 (먼저 걸린 것이 이긴다):
  1. seed        — canonical이 시드와 같다
  2. 브랜드      — 별칭이 걸린 브랜드 수로 my_brand / competitor / brand_vs(둘 이상)
  3. themes 배열 — 채널 → 커뮤니티 → 비교 → 일반(카테고리어) 순서(사전 배열 순서가 곧 우선순위)
  4. unmatched   — 아무 데도 안 걸림(카테고리 밖일 수도, 사전이 모자란 것일 수도 있다 — 판정하지 않는다)
"""
import json, pathlib, re, sys, unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import apply_rules, canon as _canon  # noqa: E402  (계약 T5 — 치환은 공백 제거 전에)

NONBRAND = ("generic", "comparison", "community", "channel", "unmatched")
BRAND_CLASSES = ("my_brand", "competitor", "brand_vs")
LATIN = re.compile(r"[a-z0-9]")


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def load_json_arg(v):
    """인라인 JSON 또는 파일 경로."""
    v = str(v).strip()
    if v.startswith("{") or v.startswith("["):
        return json.loads(v)
    return json.loads(pathlib.Path(v).read_text(encoding="utf-8"))


def spaced(kw, rules):
    """NFKC · 소문자 · 변형치환 · 공백 하나로 — 라틴 별칭의 단어 경계 판정용."""
    s = unicodedata.normalize("NFKC", str(kw)).lower()
    s = apply_rules(s, rules)
    return " ".join(s.split())


def _is_latin(ch):
    return bool(ch) and bool(LATIN.match(ch))


def _canon_with_gaps(sp):
    """공백 유지 문자열 → (공백 제거 문자열, 각 글자 앞에 공백이 있었는지 목록)."""
    chars, gap, pending = [], [], False
    for ch in sp:
        if ch.isspace():
            pending = True
            continue
        chars.append(ch)
        gap.append(pending)
        pending = False
    return "".join(chars), gap


def alias_hit(sp, a_cn):
    """별칭 canonical `a_cn`이 키워드(공백 유지 정규형 `sp`)에 걸리는가.

    규칙(0.1.1): 공백을 지운 canonical 위에서 찾고, **별칭의 라틴 쪽 끝에서만** 경계를 본다.
      - 별칭 첫 글자가 라틴(a-z0-9)이면 바로 앞이 문자열 시작 · 원래 공백 · 비라틴(한글 등) 중 하나여야 한다
      - 별칭 끝 글자가 라틴이면 바로 뒤가 문자열 끝 · 원래 공백 · 비라틴 중 하나여야 한다
      - 한글 쪽 끝은 경계를 보지 않는다(한국어 복합어 `코웨이정수기` — 0.1.0과 같다)
    그래서 `sk매직`은 `sk 매직 얼음정수기` · `sk매직` · `SK 매직`에 걸리고(공백 변형), `lg`는 `lg전자` · `lg 정수기`에
    걸리되 `algae`에는 안 걸리며, `on`은 `running` · `salomon`에 안 걸린다. 순수 한글 별칭은 0.1.0처럼 canonical 부분 일치.
    붙여 쓴 5자 이상 라틴 별칭(`adidas`↔`adidasshoes`)의 부분 일치도 0.1.0처럼 허용한다.
    """
    if not a_cn:
        return False
    cn, gap = _canon_with_gaps(sp)
    lat_l, lat_r = _is_latin(a_cn[0]), _is_latin(a_cn[-1])
    if not (lat_l or lat_r):
        return a_cn in cn
    if lat_l and lat_r and not any(not _is_latin(c) for c in a_cn) and len(a_cn) >= 5 and a_cn in cn:
        return True
    i = cn.find(a_cn)
    while i >= 0:
        j = i + len(a_cn)
        ok_l = (not lat_l) or i == 0 or gap[i] or not _is_latin(cn[i - 1])
        ok_r = (not lat_r) or j == len(cn) or gap[j] or not _is_latin(cn[j])
        if ok_l and ok_r:
            return True
        i = cn.find(a_cn, i + 1)
    return False


class LeakDict:
    """make_leak_dict.py가 만든 leak_dict.json 하나를 읽는다."""

    def __init__(self, d):
        self.raw = d
        self.rules = (d.get("rules") or {}).get("변형치환") or {}
        self.gl = d.get("gl") or ""
        self.seeds = [s for s in d.get("seeds") or [] if s]
        self.seed_canons = {self.canon(s): s for s in self.seeds}
        bs = d.get("brand_sets") or {}
        my = bs.get("my") or None
        self.my = None
        if my and (my.get("name") or "").strip():
            self.my = {"name": my["name"].strip(), "aliases": self._aliases(my)}
        self.competitors = [{"name": c["name"].strip(), "aliases": self._aliases(c)}
                            for c in bs.get("competitors") or [] if (c.get("name") or "").strip()]
        self.themes = []
        for t in d.get("themes") or []:
            pat = t.get("pattern") or ""
            if pat:
                self.themes.append((t.get("name"), t.get("class") or "generic", re.compile(pat)))

    def canon(self, kw):
        return _canon(str(kw), self.rules)

    def _aliases(self, b):
        out = []
        for a in [b.get("name")] + list(b.get("aliases") or []):
            a = (a or "").strip()
            if a:
                out.append((spaced(a, self.rules), self.canon(a)))
        return list(dict.fromkeys(out))

    def _hit(self, sp, cn, aliases):
        return any(alias_hit(sp, a_cn) for _, a_cn in aliases)

    def alias_misses(self, kw, assigned):
        """별칭 canonical이 노드 canonical 안에 글자 그대로 들어 있는데 그 브랜드로 분류되지 않은 경우 → [(브랜드, 별칭)].

        경계 규칙으로 일부러 뺀 것(`lg`↔`algae`)도 걸린다 — 사람이 보고 판단하라고 내놓는 목록이다(0.1.1 P0 재발 방지 검사).
        """
        cn = self.canon(kw)
        got = set(assigned or [])
        out = []
        for b in ([self.my] if self.my else []) + self.competitors:
            if b["name"] in got:
                continue
            for _, a_cn in b["aliases"]:
                if a_cn and a_cn in cn:
                    out.append((b["name"], a_cn))
                    break
        return out

    def brands_in(self, kw):
        """(내 브랜드 걸림 여부, 걸린 경쟁 브랜드 이름 목록)."""
        sp, cn = spaced(kw, self.rules), self.canon(kw)
        my = bool(self.my and self._hit(sp, cn, self.my["aliases"]))
        comps = [c["name"] for c in self.competitors if self._hit(sp, cn, c["aliases"])]
        return my, comps

    def classify(self, kw):
        """→ dict(node_class, brand, facets). brand: 단일 브랜드면 이름, brand_vs면 '/'로 이은 이름."""
        cn = self.canon(kw)
        facets = [name for name, _, rx in self.themes if rx.search(cn)]
        if cn in self.seed_canons:
            my, comps = self.brands_in(kw)
            b = (self.my["name"] if my else (comps[0] if len(comps) == 1 else ""))
            return {"node_class": "seed", "brand": b, "facets": facets}
        my, comps = self.brands_in(kw)
        names = ([self.my["name"]] if my else []) + comps
        if len(names) >= 2:
            return {"node_class": "brand_vs", "brand": "/".join(names), "facets": facets}
        if my:
            return {"node_class": "my_brand", "brand": self.my["name"], "facets": facets}
        if comps:
            return {"node_class": "competitor", "brand": comps[0], "facets": facets}
        for name, cls, rx in self.themes:
            if rx.search(cn):
                return {"node_class": cls, "brand": "", "facets": facets}
        return {"node_class": "unmatched", "brand": "", "facets": facets}

    def stop_tokens(self):
        """미등록 브랜드 후보 탐색에서 뺄 말 — 시드 · 카테고리어 · 단계어 · 등록 별칭."""
        out = set()
        for s in self.seeds:
            out.add(self.canon(s))
            out |= {self.canon(t) for t in spaced(s, self.rules).split()}
        for t in self.raw.get("category_terms") or []:
            out.add(self.canon(t))
        for terms in (self.raw.get("stage_terms") or {}).values():
            out |= {self.canon(t) for t in terms}
        for b in ([self.my] if self.my else []) + self.competitors:
            out |= {cn for _, cn in b["aliases"]}
        return {t for t in out if t}


def load_dict(path_or_json):
    return LeakDict(load_json_arg(path_or_json))


# ───────────────────────── 호출 원장 (calls.jsonl)
def read_ledger(path):
    """한 줄에 호출 하나. raw 경로는 원장 파일 기준 상대경로로 푼다."""
    p = pathlib.Path(path)
    rows = []
    for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except ValueError as e:
            sys.exit(f"원장 {p}:{i} JSON 오류 — {e}")
        r["_raw_path"] = str((p.parent / r["raw"]).resolve()) if r.get("raw") else ""
        rows.append(r)
    return rows


def response_paths(resp):
    """path_finder 응답 → 경로 목록(공백만 정리, 표기는 그대로). 2개 미만 시퀀스는 버린다."""
    out = []
    for p in (resp or {}).get("data") or []:
        if isinstance(p, dict):                     # {"path": [...]} 모양도 받는다
            p = p.get("path") or p.get("keywords") or []
        if not isinstance(p, list):
            continue
        seq = [" ".join(str(k).split()) for k in p if str(k).strip()]
        if len(seq) >= 2:
            out.append(seq)
    return out


def total_cost(resp):
    cd = (resp or {}).get("cost_detail") or {}
    v = cd.get("total_cost")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    parts = cd.get("parts") or []
    if parts:
        return sum((x or {}).get("total_cost") or 0 for x in parts)
    return None
