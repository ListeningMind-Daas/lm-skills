#!/usr/bin/env python3
"""감각어·감정어 추출 + 검색량 가중 + 함께 쓰임(공기) 묶음 — find-taste-words STEP 6.

입력은 모두 **저장된 응답 파일**이다. API를 부르지 않는다.

  --universe   build_universe.py 산출 CSV (keyword · canonical · dup_of · volume_total · brand)
  --lexicon    감각어·감정어 사전 (themes 스키마 + negation · category_terms · exclude)
  --serp       serp 응답 JSON (호출 1건 = 파일 1개, 여러 번)
  --intent-call  `라운드=파일@limit` — intent_finder 호출 1건마다 (절단 검사 · 계약 T7)
  --coverage   coverage_curve.py 판정 문구 (절단이 있으면 무시하고 «보류»로 덮어쓴다)

분석 단위(unit)는 두 가지다.
  ① 검색어 단위 — 유니버스의 canonical 그룹 1개(같은 검색 중복 `dup_of` 행 제외). 무게 = 그룹 검색량
     하한(그룹 최댓값) · 상한(그룹 합) [추정] (계약 T5)
  ② 화면 문장 단위 — serp 블록 문구를 문장으로 자른 것. canonical이 같은 문장은 여러 SERP에 나와도
     **한 단위**로 센다(같은 PAA 질문이 SERP 열 개에 뜨면 열 번이 아니다). 검색어 단위와 canonical이 같은
     문장(연관 검색 블록 등)은 이미 ①에서 셌으므로 뺀다. 노출 무게 = 그 문장이 뜬 SERP 검색어들의 검색량 하한 합.

낱말 매칭은 canonical(공백 제거) 문자열에 패턴을 돌리되, 한국어 · 영어는 **원문 어절 경계**에 맞는 매칭만 센다(v0.1.1 —
«대신 나노»→신나 · «있고요»→고요 같은 경계 넘는 오탐 차단, `scan_detail` 참고). 그 위에서 **최장일치**로 고른다 — 겹치면 긴 매칭이 이기고(저소음 > 소음),
길이가 같으면 사전 배열 순서가 이긴다. 카테고리어(--keyword · category_terms)는 매칭 전에 가린다
(다크초콜릿 카테고리에서 `초콜릿`이 향 낱말로 세어지지 않게). 낱말 뒤·앞의 부정 표지(`없는`·`덜한`·`안`)가 있으면
그 출현은 «회피»(avoid)로 따로 센다.

묶음(트라이브): 단위 수가 --min-units 이상인 낱말만 쓴다. 낱말을 단위 수 순으로 하나씩 보며, 이미 있는
묶음 구성원과의 평균 자카드(함께 나온 단위 ÷ 둘 중 하나라도 나온 단위, 함께 나온 단위 < --min-co 이면 0)가
--link 이상인 묶음 중 가장 높은 곳에 넣고, 없으면 새 묶음을 연다. 같은 입력이면 같은 묶음이 나온다(결정적).

    python3 scripts/taste_words.py --universe keyword_universe.csv --lexicon themes.커피원두.json --keyword 커피원두 --gl kr --serp serp_커피원두.json --serp serp_고소한원두.json --intent-call "R1=intent_r1_커피원두.json@20" --intent-call "R1=intent_r1_커피원두맛.json@50" --coverage "수렴(R1 기여 12%)" --queried-at 2026-11-19
"""
import argparse, csv, json, pathlib, re, sys, unicodedata
from collections import Counter, defaultdict

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from series_dedup import apply_rules, canon as _canon  # noqa: E402  (계약 T5 — 공유 정규화)

WORD_COLS = ["word", "axis", "variants", "units", "want_units", "avoid_units", "avoid_share",
             "query_hits", "query_volume_lo", "query_volume_hi", "serp_units", "serp_keywords",
             "serp_exposure_lo", "serp_blocks", "tribe", "top_queries", "source_urls",
             "boundary_suspect", "label_class", "label_volume"]
TRIBE_COLS = ["tribe", "status", "anchor", "words", "axes", "n_words", "units", "query_volume_lo",
              "query_volume_hi", "serp_exposure_lo", "avoid_words", "cohesion", "top_pairs", "label"]
COOC_COLS = ["word_a", "word_b", "co_units", "units_a", "units_b", "jaccard"]
EVID_COLS = ["word", "polarity", "source", "serp_keyword", "block", "text", "url"]
COLL_COLS = ["round", "file", "seeds", "limit", "returned", "truncated"]

# serp 블록 중 문구를 읽는 유형. 이미지·동영상·뉴스는 문구가 짧고 제목 위주라 기본에서 뺀다.
DEFAULT_BLOCKS = ("ai_overview", "people_also_ask_for", "featured_snippet", "organic_results",
                  "related_searches", "people_also_search_for", "knowledge_panel")
TEXT_KEYS = ("snippet", "title", "question", "answer", "text", "description", "query", "keyword")
LINK_KEYS = ("link", "url", "source_link")
SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|[\n\r]+|\s[·•|]\s")
# 표면형에 붙여 보일 어미(낱말 수준 변형: 고소한·고소함·고소하고 → 고소). 다음 낱말(원두)까지 먹지 않게 어미만 본다.
HANGUL_TAIL = re.compile(r"(?:하고|하게|해서|하다|하며|러운|러워|럽고|스러|한|함|해|하|운|움|워|고|게|은|는|이|가|의|임|감)?")


def utf8_console():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def slug(kw, subs):
    return re.sub(r"[^\w가-힣ぁ-んァ-ン一-龥-]", "", _canon(kw, subs)) or "keyword"


def load_lexicon(path):
    cfg = json.load(open(path, encoding="utf-8"))
    subs = (cfg.get("rules") or {}).get("변형치환", {})
    words = []
    for i, t in enumerate(cfg["themes"]):
        name = t["name"]
        axis, _, word = name.partition(":")
        if not word:
            axis, word = "기타", name
        words.append({"word": word, "axis": axis, "order": i, "re": re.compile(t["pattern"]),
                      "unit_ex": re.compile(t["exclude"]) if t.get("exclude") else None,
                      "neg": t.get("negatable", axis != "감정")})
    neg = cfg.get("negation") or {}
    return {
        "subs": subs, "words": words,
        "neg_after": re.compile(neg["after"]) if neg.get("after") else None,
        "neg_before": re.compile(neg["before"]) if neg.get("before") else None,
        "exclude": re.compile(cfg["exclude"]) if cfg.get("exclude") else None,
        "category_terms": [x for x in cfg.get("category_terms") or [] if x],
        "brands": cfg.get("brands") or [],
    }


JOINERS = "/·&+"   # 어절 안 연결 부호 — «냉/온» · «냉·온수»의 «온»은 어절 첫머리가 아니다
# 어절 끝에 붙어도 «다른 낱말»이 아닌 조사 · 어미(한국어)와 굴절 접미(영어). 공백 넘기 · 경계 의심 판정에 쓴다.
END_TAIL = re.compile(r"(?:스러운|스러워|스럽게|스럽고|스럽다|스러|입니다|습니다|합니다|니다|하고|하게|해서|하다|하며|하는|하면|"
                      r"해요|했다|했어|했던|이다|이고|이며|이라|에서|으로|부터|까지|처럼|보다|지만|어요|아요|어서|아서|네요|"
                      r"러운|러워|럽고|럽게|럽다|[은는이가을를의도만에와과로랑나요다어아고게지음임함해한운움워던겠었았며면니]|"
                      r"ness|less|ing|ed|es|ly|er|est|s|y)*")
NEG_PARALLEL = re.compile(r"(?:과|와|이나|랑|이랑|및)")   # «소음과 진동이 없는» — 병렬 명사 하나를 건너뛰고 부정을 본다


def _script(ch):
    """h 한글 · l 로마자/숫자 · c 그 밖의 글자(가나 · 한자 — 띄어 쓰지 않는 문자) · '' 부호."""
    if "가" <= ch <= "힣" or "ㄱ" <= ch <= "ㆎ":
        return "h"
    if ch.isascii() and ch.isalnum():
        return "l"
    return "c" if ch.isalnum() else ""


def norm_spaced(text, subs):
    """canonical과 같은 정규화(NFKC · 소문자 · 변형치환)를 하되 **공백은 남긴다** — 어절 경계를 보려고."""
    t = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"\s+", " ", apply_rules(t, subs)).strip()


def scan_detail(text, lex, heads):
    """원문(띄어쓰기가 남은 문자열) 하나 → (적중 목록, 통계 {rejected, excluded}).

    패턴은 스키마대로 canonical(공백 제거) 문자열에 평가하되, 한국어 · 영어 매칭은 **원문 어절 경계**에 맞아야 한다.
      ① 시작: 어절 첫머리(문자열 처음 · 공백 뒤 · 문장부호 뒤 — `/` `·`는 어절 안 연결이라 경계가 아니다) ·
         가린 카테고리어 바로 뒤 · 앞선 적중(과 그 어미) 바로 뒤. «대신 나노»의 `신나` · «엔씨소프트»의 `소프트`는 못 걸린다.
      ② 공백 넘기: 사전이 일부러 쓴 여러 어절 패턴(`걱정없` · `안밀리`)은 받는다. 단 마지막 어절 쪽 조각이 1자이면
         그 어절의 나머지가 조사 · 어미여야 한다 — «최신 기술»(시작 위반) · «온 기능»의 `온기`(나머지 `능`)는 버린다.
      ③ 테마 `exclude`: 그 정규식이 canonical에서 맞은 구간과 **겹치는 출현만** 버린다(`매트리스` 속 `매트`).
    가나 · 한자처럼 띄어 쓰지 않는 문자로 시작하는 매칭에는 어절 규칙을 적용하지 않는다(v0.1.0 동작).
    적중마다 «경계 의심»(suspect)을 단다 — 매칭 뒤 같은 어절에 조사 · 어미가 아닌 글자가 이어지면(`카카오`+`톡`)
    더 긴 낱말의 앞부분일 수 있다. 회피는 «같은 어절의 나머지 + 다음 어절»(뒤) · «이전 어절»(앞)에서 본다 —
    «소음/진동이 없어» · «소음과 진동이 없는»처럼 병렬 명사 하나를 사이에 둔 부정도 회피다.
    """
    t = norm_spaced(text, lex["subs"])
    chars, sb, pend = [], [], False
    for ch in t:
        if ch == " ":
            pend = True
            continue
        chars.append(ch); sb.append(pend); pend = False
    c = "".join(chars)                       # == canon(text)
    n = len(c)
    s = c
    for h in heads:
        if h:
            s = s.replace(h, "\x00" * len(h))

    def is_start(i):
        if i == 0 or sb[i] or s[i - 1] == "\x00":
            return True
        p = s[i - 1]
        if not (p.isalnum() or p in JOINERS):
            return True
        return {_script(p), _script(s[i])} == {"h", "l"}

    def eo_end(i, full=False):
        """i부터 그 어절 끝. full이면 공백(또는 가린 말)까지, 아니면 문장부호 · 연결 부호에서도 멈춘다."""
        if i < n and sb[i] and i > 0:
            return i
        k = i
        while k < n:
            if k > i and sb[k]:
                break
            if (s[k] == "\x00") if full else (not s[k].isalnum()):
                break
            k += 1
        return k

    def next_eo(i):
        """i(어절 첫머리)에서 시작하는 다음 어절 전체 끝."""
        k = i + 1
        while k < n and not sb[k]:
            k += 1
        return k

    def eo_start(i):
        k = i
        while k > 0 and not sb[k]:
            k -= 1
        return k

    def crossing_ok(st, en):
        ks = [k for k in range(st + 1, en) if sb[k]]
        if not ks:
            return True
        return len(s[ks[-1]:en]) >= 2 or bool(END_TAIL.fullmatch(s[en:eo_end(en)]))

    ex_spans = {w["word"]: [(m.start(), m.end()) for m in w["unit_ex"].finditer(c) if m.end() > m.start()]
                for w in lex["words"] if w["unit_ex"]}
    starts = {i for i in range(n) if is_start(i)}
    extra, rejected, excluded = set(), set(), set()

    def candidates(positions, first):
        out = []
        for w in lex["words"]:
            found = set()
            if first:
                found |= {(m.start(), m.end()) for m in w["re"].finditer(s)}
            for pos in positions:
                m = w["re"].match(s, pos)
                if m:
                    found.add((m.start(), m.end()))
            for st, en in sorted(found):
                if en <= st or "\x00" in s[st:en]:
                    continue
                if _script(s[st]) in ("h", "l"):
                    if not ((st in starts or st in extra) and crossing_ok(st, en)):
                        rejected.add((st, en, w["word"]))
                        continue
                if any(a < en and st < b for a, b in ex_spans.get(w["word"], ())):
                    excluded.add((st, en, w["word"]))
                    continue
                out.append((st, en, w))
        return out

    taken = [False] * n
    hits = []
    pool = candidates(starts, True)
    for _ in range(4):        # 앞선 적중 바로 뒤에서 시작하는 매칭(«달콤고소»)을 위해 몇 번 더 돈다
        pool.sort(key=lambda x: (-(x[1] - x[0]), x[2]["order"], x[0]))
        new = []
        for st, en, w in pool:
            if any(taken[st:en]):
                continue
            for i in range(st, en):
                taken[i] = True
            new.append((st, en, w))
        hits += new
        add = set()
        for st, en, w in new:
            add |= {en, en + len(END_TAIL.match(s, en).group(0))}
        add = {p for p in add if p < n and p not in starts and p not in extra and not taken[p]}
        if not add:
            break
        extra |= add
        pool = candidates(add, False)

    hit_starts = {st for st, _, _ in hits}
    res = []
    for st, en, w in sorted(hits, key=lambda x: x[0]):
        spaced = _script(s[st]) in ("h", "l")
        if spaced:
            w1_end = eo_end(en, full=True)
            w1 = s[en:w1_end]
            w2_end = next_eo(w1_end) if w1_end < n else n
            w2 = s[w1_end:w2_end]
            w3 = s[w2_end:next_eo(w2_end)] if w2_end < n else ""
            es = eo_start(st)
            before = s[eo_start(st - 1):st] if (es == st and st > 0) else s[es:st]
            before = re.sub(r"\W+$", "", before.replace("\x00", ""))
            rest = s[en:eo_end(en)]
            for k in range(len(rest)):
                if en + k in hit_starts:
                    rest = rest[:k]
                    break
        else:
            w1, w2, w3, before, rest = s[en:en + 6], "", "", s[max(0, st - 3):st], ""
        negated = False
        if w["neg"]:
            na, nb = lex["neg_after"], lex["neg_before"]
            if na:
                negated = bool(na.match(w1 + w2))
                if not negated and spaced and w1[:1] in "/·,":
                    negated = any(na.match(w1[j:] + w2) for j in range(2, min(len(w1), 7) + 1))
                if not negated and spaced and NEG_PARALLEL.fullmatch(w1):
                    negated = any(na.match(w2[j:] + w3) for j in range(1, min(len(w2), 6) + 1))
            if not negated and nb:
                negated = bool(nb.search(before))
        tail = ""
        if re.match(r"[가-힣]", s[st:en][-1:] or ""):
            tail = HANGUL_TAIL.match(rest if spaced else s[en:]).group(0)
        res.append({"w": w, "negated": negated, "surface": s[st:en] + tail, "token": s[st:en] + rest,
                    "suspect": bool(spaced and rest and not END_TAIL.fullmatch(rest) and not negated)})
    rejected = {r for r in rejected if not any(taken[r[0]:r[1]])}
    return res, {"rejected": len(rejected), "excluded": len(excluded)}


def scan(text, lex, heads):
    """원문 하나 → (낱말, 회피 여부, 표면형) 목록 — scan_detail의 짧은 형(serp_gate.py가 쓴다)."""
    return [(h["w"], h["negated"], h["surface"]) for h in scan_detail(text, lex, heads)[0]]


def walk_texts(obj, out, links=()):
    """(문구, 그 문구의 출처 주소들) 목록. 출처는 가장 가까운 항목(dict)의 link · references 를 쓰고,
    없으면 바깥(블록)의 것을 물려받는다 — 같은 블록의 다른 결과 주소가 섞이지 않게 한다."""
    if isinstance(obj, dict):
        own = [v for k, v in obj.items() if k in LINK_KEYS and isinstance(v, str) and v.startswith("http")]
        for ref in obj.get("references") or []:
            if isinstance(ref, dict):
                own += [v for k, v in ref.items() if k in LINK_KEYS and isinstance(v, str) and v.startswith("http")]
        links = tuple(own) or tuple(links)
        for k, v in obj.items():
            if k == "references":
                continue
            if k in TEXT_KEYS and isinstance(v, str):
                out.append((v, links))
            elif isinstance(v, (dict, list)):
                walk_texts(v, out, links)
    elif isinstance(obj, list):
        for x in obj:
            walk_texts(x, out, links)


def load_serp(path):
    obj = json.load(open(path, encoding="utf-8"))
    rd = obj.get("request_detail") or {}
    kw = rd.get("keyword") or (rd.get("keywords") or [""])[0]
    return kw, obj.get("collected_at") or "", obj.get("data") or []


def main():
    utf8_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True)
    ap.add_argument("--lexicon", required=True)
    ap.add_argument("--keyword", required=True, help="분석 키워드(카테고리·제품·브랜드) — 매칭 전에 가린다")
    ap.add_argument("--gl", required=True, choices=("kr", "us", "jp"))
    ap.add_argument("--serp", action="append", default=[])
    ap.add_argument("--intent-call", action="append", default=[], help="라운드=파일@limit")
    ap.add_argument("--coverage", default="", help="coverage_curve.py 판정 문구")
    ap.add_argument("--blocks", default=",".join(DEFAULT_BLOCKS))
    ap.add_argument("--min-units", type=int, default=2, help="묶음에 넣을 낱말의 최소 단위 수 [가정]")
    ap.add_argument("--min-co", type=int, default=2, help="연결로 인정할 최소 함께 나온 단위 수 [가정]")
    ap.add_argument("--link", type=float, default=0.2, help="묶음 합류 평균 자카드 임계 [가정]")
    ap.add_argument("--sparse-density", type=float, default=0.05, help="감각어 밀도 하한 [가정]")
    ap.add_argument("--sparse-words", type=int, default=4, help="묶음 후보 낱말 최소 개수 [가정]")
    ap.add_argument("--evidence-per-word", type=int, default=5)
    ap.add_argument("--suspect-warn", type=float, default=0.2, help="경계 의심 적중 비중 경고 임계 [가정]")
    ap.add_argument("--queried-at", default="")
    ap.add_argument("--outdir", default=".")
    a = ap.parse_args()

    lex = load_lexicon(a.lexicon)
    subs = lex["subs"]
    canon = lambda s: _canon(s, subs)   # noqa: E731
    heads = sorted({canon(a.keyword)} | {canon(x) for x in lex["category_terms"]}, key=lambda h: (-len(h), h))
    allowed = {b.strip() for b in a.blocks.split(",") if b.strip()}
    out = pathlib.Path(a.outdir); out.mkdir(parents=True, exist_ok=True)
    sl = slug(a.keyword, subs)

    # ── ① 검색어 단위 — canonical 그룹 (계약 T5: 같은 검색 중복 제외 · 하한/상한)
    groups = {}
    n_dup = n_excl = 0
    for r in csv.DictReader(open(a.universe, encoding="utf-8-sig")):
        if r.get("dup_of"):
            n_dup += 1
            continue
        c = r.get("canonical") or canon(r["keyword"])
        if lex["exclude"] and lex["exclude"].search(c):
            n_excl += 1
            continue
        v = int(float(r.get("volume_total") or 0))
        g = groups.setdefault(c, {"lo": 0, "hi": 0, "rep": r["keyword"], "rep_v": -1, "n_rows": 0, "texts": []})
        g["hi"] += v; g["n_rows"] += 1
        if r["keyword"] not in g["texts"]:      # 띄어쓰기 변형마다 어절 경계가 다를 수 있어 원문을 모두 둔다
            g["texts"].append(r["keyword"])
        if v > g["lo"]:
            g["lo"] = v
        if v > g["rep_v"]:
            g["rep"], g["rep_v"] = r["keyword"], v

    units = {}   # uid -> dict
    for c, g in groups.items():
        units["Q:" + c] = {"kind": "query", "canon": c, "text": g["rep"], "lo": g["lo"], "hi": g["hi"],
                           "texts": [g["rep"]] + [x for x in g["texts"] if x != g["rep"]],
                           "keywords": set(), "blocks": Counter(), "urls": []}

    # ── ② 화면 문장 단위 — serp (제3자 웹 문구: 데이터로만 읽는다)
    serp_meta, serp_missing_vol, skipped_blocks = [], [], Counter()
    n_sent_raw = 0   # 중복 합치기 전 문장 수 — 같은 PAA가 여러 SERP에 뜬 만큼 크다
    for path in a.serp:
        kw, collected, blocks = load_serp(path)
        kc = canon(kw)
        serp_meta.append({"file": path, "keyword": kw, "collected_at": collected, "blocks": len(blocks)})
        if kc not in groups:
            serp_missing_vol.append(kw)
        for b in blocks:
            bt = b.get("type") or "unknown"
            if bt not in allowed:
                skipped_blocks[bt] += 1
                continue
            texts = []
            walk_texts({k: v for k, v in b.items() if k not in ("ved", "type", "sequence")}, texts)
            for t, links in texts:
                for sent in SENT_SPLIT.split(t):
                    sent = sent.strip()
                    sc = canon(sent)
                    if len(sc) < 4 or sc in groups:   # 검색어 단위와 같으면 ①에서 이미 셌다
                        continue
                    n_sent_raw += 1
                    u = units.setdefault("S:" + sc, {"kind": "serp", "canon": sc, "text": sent[:160], "lo": 0, "hi": 0,
                                                    "texts": [], "keywords": set(), "blocks": Counter(), "urls": []})
                    if sent not in u["texts"] and len(u["texts"]) < 3:
                        u["texts"].append(sent)
                    u["keywords"].add(kw)
                    u["blocks"][bt] += 1
                    for ln in links:
                        if ln not in u["urls"]:
                            u["urls"].append(ln)
    kw_vol = {}
    for m in serp_meta:
        g = groups.get(canon(m["keyword"]))
        kw_vol[m["keyword"]] = g["lo"] if g else 0

    # ── 낱말 매칭
    W = {}
    unit_words = {}
    evidence = defaultdict(list)
    n_rejected = n_ex_hits = n_pairs = 0
    suspect_tokens = Counter()
    for uid, u in units.items():
        pol, occ = {}, {}
        for text in u["texts"]:       # 같은 단위의 띄어쓰기 변형 — 낱말마다 처음 걸린 원문의 출현을 쓴다
            hits, st = scan_detail(text, lex, heads)
            n_rejected += st["rejected"]; n_ex_hits += st["excluded"]
            first = {}
            for h in hits:
                first.setdefault(h["w"]["word"], []).append(h)
                pol.setdefault(h["w"]["word"], set()).add("avoid" if h["negated"] else "want")
            for word, hs in first.items():
                occ.setdefault(word, hs)
        if not pol:
            continue
        for word, hs in occ.items():
            w = hs[0]["w"]
            d = W.setdefault(word, {"axis": w["axis"], "order": w["order"], "variants": Counter(), "units": set(),
                                    "want": set(), "avoid": set(), "q": set(), "s": set(), "skw": set(),
                                    "blocks": Counter(), "urls": [], "suspect": set()})
            for h in hs:
                d["variants"][h["surface"]] += 1
            n_pairs += 1
            if all(h["suspect"] for h in hs):     # 이 단위에서 이 낱말이 «더 긴 어절의 앞부분»으로만 나왔다
                d["suspect"].add(uid)
                suspect_tokens[f"{hs[0]['token']}→{word}"] += 1
        unit_words[uid] = set(pol)
        for word, ps in pol.items():
            d = W[word]
            d["units"].add(uid)
            if "want" in ps:
                d["want"].add(uid)
            if "avoid" in ps:
                d["avoid"].add(uid)
            if u["kind"] == "query":
                d["q"].add(uid)
            else:
                d["s"].add(uid); d["skw"] |= u["keywords"]; d["blocks"].update(u["blocks"])
                for ln in u["urls"]:
                    if ln not in d["urls"]:
                        d["urls"].append(ln)
            if len(evidence[word]) < a.evidence_per_word:
                evidence[word].append({"word": word, "polarity": "회피" if ps == {"avoid"} else ("혼재" if len(ps) > 1 else "선호"),
                                       "source": "검색어" if u["kind"] == "query" else "SERP 문장",
                                       "serp_keyword": " | ".join(sorted(u["keywords"])),
                                       "block": " | ".join(sorted(u["blocks"])), "text": u["text"],
                                       "url": u["urls"][0] if u["urls"] else ""})

    def qsum(uids, key):
        return sum(units[x][key] for x in uids)

    # ── 공기(co-occurrence) · 묶음
    elig = sorted((w for w, d in W.items() if len(d["units"]) >= a.min_units),
                  key=lambda w: (-len(W[w]["units"]), -qsum(W[w]["q"], "lo"), W[w]["order"]))
    eset = set(elig)
    co = Counter()
    for uid, ws in unit_words.items():
        ws = sorted(ws & eset)
        for i in range(len(ws)):
            for j in range(i + 1, len(ws)):
                co[(ws[i], ws[j])] += 1

    def jac(x, y):
        k = (x, y) if x < y else (y, x)
        n = co.get(k, 0)
        if n < a.min_co:
            return 0.0
        return n / (len(W[x]["units"]) + len(W[y]["units"]) - n)

    tribes = []
    for w in elig:
        best, best_v = None, 0.0
        for t in tribes:
            v = sum(jac(w, m) for m in t) / len(t)
            if v > best_v + 1e-12:
                best, best_v = t, v
        if best is not None and best_v >= a.link:
            best.append(w)
        else:
            tribes.append([w])

    def tribe_row(t):
        uids = set().union(*(W[w]["units"] for w in t))
        q = {x for x in uids if units[x]["kind"] == "query"}
        skw = set().union(*(W[w]["skw"] for w in t))
        pairs = sorted(((co.get((x, y) if x < y else (y, x), 0), x, y) for i, x in enumerate(t) for y in t[i + 1:]),
                       key=lambda p: (-p[0], p[1], p[2]))
        coh = [jac(x, y) for i, x in enumerate(t) for y in t[i + 1:]]
        return {"status": "묶음" if len(t) >= 2 else "단독", "anchor": t[0], "words": " · ".join(t),
                "axes": " · ".join(f"{k} {v}" for k, v in Counter(W[w]["axis"] for w in t).most_common()),
                "n_words": len(t), "units": len(uids), "query_volume_lo": qsum(q, "lo"), "query_volume_hi": qsum(q, "hi"),
                "serp_exposure_lo": sum(kw_vol.get(k, 0) for k in skw),
                "avoid_words": " · ".join(w for w in t if W[w]["avoid"] and len(W[w]["avoid"]) >= len(W[w]["want"])),
                "cohesion": round(sum(coh) / len(coh), 3) if coh else "",
                "top_pairs": " · ".join(f"{x}+{y}({n})" for n, x, y in pairs[:3] if n),
                "label": "[추정] 공기 묶음 · 검색량 [실제 데이터] 합산 [추정]", "_members": t}

    trows = sorted((tribe_row(t) for t in tribes),
                   key=lambda r: (r["status"] != "묶음", -r["query_volume_lo"], -r["units"], r["anchor"]))
    tribe_of = {}
    for i, r in enumerate(trows, 1):
        r["tribe"] = f"T{i}"
        for w in r["_members"]:
            tribe_of[w] = r["tribe"]

    # ── 낱말 표
    wrows = []
    for word, d in W.items():
        n = len(d["units"])
        top_q = sorted(d["q"], key=lambda x: -units[x]["lo"])[:3]
        wrows.append({
            "word": word, "axis": d["axis"],
            "variants": "|".join(v for v, _ in d["variants"].most_common(6)),
            "units": n, "want_units": len(d["want"]), "avoid_units": len(d["avoid"]),
            "avoid_share": round(len(d["avoid"]) / n, 3) if n else "",
            "query_hits": len(d["q"]), "query_volume_lo": qsum(d["q"], "lo"), "query_volume_hi": qsum(d["q"], "hi"),
            "serp_units": len(d["s"]), "serp_keywords": len(d["skw"]),
            "serp_exposure_lo": sum(kw_vol.get(k, 0) for k in d["skw"]),
            "serp_blocks": " · ".join(f"{k} {v}" for k, v in d["blocks"].most_common()),
            "tribe": tribe_of.get(word, "희소(단위<%d)" % a.min_units),
            "top_queries": " | ".join(units[x]["text"] for x in top_q),
            "source_urls": " | ".join(d["urls"][:3]),
            "boundary_suspect": len(d["suspect"]),
            "label_class": "추정", "label_volume": "실제 데이터(합산은 추정)",
        })
    wrows.sort(key=lambda r: (-r["query_volume_lo"], -r["serp_exposure_lo"], -r["units"], r["word"]))

    # ── 수집 범위 · 절단 (계약 T7) — 호출마다 요청 limit vs 반환 수
    coll = []
    for spec in a.intent_call:
        label, _, rest = spec.partition("=")
        f, _, lim = rest.rpartition("@")
        if not f:
            f, lim = rest, ""
        obj = json.load(open(f, encoding="utf-8"))
        data = obj.get("data", []) if isinstance(obj, dict) else obj
        seeds = (obj.get("request_detail") or {}).get("keywords", []) if isinstance(obj, dict) else []
        lim_n = int(lim) if lim.strip().isdigit() else None
        coll.append({"round": label, "file": f, "seeds": " | ".join(map(str, seeds)), "limit": lim_n if lim_n else "",
                     "returned": len(data), "truncated": "Y" if lim_n and len(data) >= lim_n else ("?" if not lim_n else "")})
    trunc = [c for c in coll if c["truncated"] == "Y"]
    unknown = [c for c in coll if c["truncated"] == "?"]
    if trunc:
        cov = f"보류(절단 {len(trunc)}건: " + ", ".join(pathlib.Path(c['file']).name for c in trunc) + ")"
        if "수렴" in a.coverage:
            print(f"⚠ --coverage «{a.coverage}»를 무시한다 — 반환이 limit에 붙은 호출이 있어 수렴이 아니라 절단일 수 있다")
    elif unknown:
        cov = f"보류(limit 미기재 {len(unknown)}건 — 절단 여부를 알 수 없다)"
    elif a.coverage:
        cov = a.coverage
    else:
        cov = "미판정(--coverage 없음)"
    cov_word = "보류" if cov.startswith("보류") else ("수렴" if "수렴" in cov else "미판정")

    # ── 감각어 밀도 · 판정
    n_units = len(units)
    n_taste_units = len(unit_words)
    density = n_taste_units / n_units if n_units else 0.0
    n_tribes = sum(1 for r in trows if r["status"] == "묶음")
    n_suspect = sum(len(d["suspect"]) for d in W.values())
    suspect_share = n_suspect / n_pairs if n_pairs else 0.0
    sparse = n_units == 0 or density < a.sparse_density or len(elig) < a.sparse_words
    verdict = "감각어 희소 — 취향 묶음 판정 보류" if sparse else "취향 묶음 판정"

    # ── 쓰기
    def write(name, cols, rows):
        p = out / name
        with open(p, "w", newline="", encoding="utf-8-sig") as fh:
            wr = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); wr.writeheader(); wr.writerows(rows)
        return p.name

    files = {
        "words": write(f"taste_words_{sl}.csv", WORD_COLS, wrows),
        "tribes": write(f"taste_tribes_{sl}.csv", TRIBE_COLS, trows),
        "cooc": write(f"taste_cooc_{sl}.csv", COOC_COLS, [
            {"word_a": x, "word_b": y, "co_units": n, "units_a": len(W[x]["units"]), "units_b": len(W[y]["units"]),
             "jaccard": round(jac(x, y), 3)} for (x, y), n in sorted(co.items(), key=lambda kv: (-kv[1], kv[0]))]),
        "evidence": write(f"taste_evidence_{sl}.csv", EVID_COLS, [e for w in sorted(evidence) for e in evidence[w]]),
        "collection": write(f"taste_collection_{sl}.csv", COLL_COLS, coll),
    }
    summary = {
        "keyword": a.keyword, "slug": sl, "gl": a.gl, "queried_at": a.queried_at,
        "serp_collected_at": sorted({m["collected_at"] for m in serp_meta if m["collected_at"]}),
        "serp_keywords": [m["keyword"] for m in serp_meta], "serp_keywords_without_volume": serp_missing_vol,
        "serp_blocks_skipped": dict(skipped_blocks),
        "n_query_groups": len(groups), "n_dup_rows": n_dup, "n_excluded_rows": n_excl,
        "n_serp_units": sum(1 for u in units.values() if u["kind"] == "serp"),
        "n_serp_sentences_raw": n_sent_raw, "n_units": n_units, "n_taste_units": n_taste_units, "taste_density": round(density, 4),
        "matching": "어절 경계(v0.1.1) — 한국어 · 영어 매칭은 원문 어절 첫머리에서 시작해야 센다",
        "n_word_hits": n_pairs, "n_boundary_suspect": n_suspect, "boundary_suspect_share": round(suspect_share, 4),
        "boundary_suspect_tokens": [f"{k} {v}" for k, v in suspect_tokens.most_common(10)],
        "n_boundary_rejected": n_rejected, "n_theme_excluded": n_ex_hits,
        "n_words": len(W), "n_words_eligible": len(elig), "n_tribes": n_tribes,
        "verdict": verdict, "coverage": cov, "coverage_status": cov_word,
        "n_intent_calls": len(coll), "n_truncated_calls": len(trunc),
        "params": {"min_units": a.min_units, "min_co": a.min_co, "link": a.link, "sparse_density": a.sparse_density,
                   "sparse_words": a.sparse_words, "suspect_warn": a.suspect_warn, "blocks": sorted(allowed), "heads": heads},
        "outputs": files,
    }
    (out / f"taste_summary_{sl}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"■ {a.keyword} ({a.gl}) — 판정: {verdict}")
    print(f"단위 {n_units} = 검색어 그룹 {len(groups)} (같은 검색 중복 {n_dup}행 · 경계 밖 {n_excl}행 제외) "
          f"+ SERP 문장 {summary['n_serp_units']} (출현 {n_sent_raw} — 같은 문장은 SERP 여러 곳에 나와도 한 번)")
    print(f"감각어·감정어가 든 단위 {n_taste_units} · 밀도 {density:.1%} [추정] · 낱말 {len(W)} · 묶음 후보 {len(elig)} · 묶음 {n_tribes}")
    print(f"수집 범위: {cov} · intent_finder 호출 {len(coll)}건 중 절단 {len(trunc)}건")
    print(f"경계 점검: 어절 경계 밖 후보 {n_rejected}건 · 테마 exclude로 뺀 출현 {n_ex_hits}건을 버렸다 · "
          f"남은 단위×낱말 적중 {n_pairs} 중 경계 의심(어절 속 앞부분 일치) {n_suspect} = {suspect_share:.1%} [추정]")
    if n_pairs and suspect_share >= a.suspect_warn:
        print(f"⚠ 경계 의심 적중이 {suspect_share:.0%} — 고유명사 · 합성어 속 일치일 수 있다: "
              + ", ".join(summary["boundary_suspect_tokens"][:5]) + " → 테마 exclude를 더하거나 패턴을 고친다")
    if serp_missing_vol:
        print(f"[데이터 공백] 검색량이 없는 SERP 검색어 {len(serp_missing_vol)}개 — 노출 무게 0: {', '.join(serp_missing_vol)}")
    if skipped_blocks:
        print("읽지 않은 블록: " + " · ".join(f"{k} {v}" for k, v in skipped_blocks.most_common()))
    for r in trows[:5]:
        print(f"  {r['tribe']} {r['status']} {r['words']} — 검색량 하한 {r['query_volume_lo']:,} ~ 상한 {r['query_volume_hi']:,}")
    print("→ " + " · ".join(files.values()) + f" · taste_summary_{sl}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
