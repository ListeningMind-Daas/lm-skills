#!/usr/bin/env python3
"""benchmark-competitors 공용 함수 — 설정 로드·canonical 정규화·브랜드 귀속·테마 판정·중복 묶음.

plan_queries.py와 brand_benchmark.py가 같은 규칙으로 키워드를 분류해야 1단(스크리닝)과
2단(시계열)의 브랜드 귀속이 어긋나지 않는다. 그래서 규칙을 이 파일 한 곳에 둔다.

중복 묶음(component) — 계약 T5를 이 스킬에서 확장한 것:
  ① `series_dedup.dedup_map`이 같은 검색의 중복(시계열 일치·지표 일치)으로 판정한 행은 한 번만 센다
  ② 같은 canonical 행은 표기변형 그룹이다(하한 = 최댓값 · 상한 = 합)
  ③ 같은 버킷 안에서 **구글 지표(gg_volume_total·cpc·competition_index)가 모두 같은 행**은 구글이
     근접 변형을 합쳐 같은 값을 돌려준 것으로 보고 구글 볼륨을 한 번만 센다. canonical이 달라도
     적용한다(nike·nikep·nikes, 아디다스·아디도스, asics·acisc — 2026-09-28 실측 [추정]).
  ①~③을 이은 연결 요소가 한 묶음이다. 하한 = 묶음 내 최댓값 행 · 상한 = 구글(공유 묶음당 1회) + 네이버 합.
  ④ **네이버 분리 변형** — ③(구글 공유)으로만 이어졌고 네이버 볼륨이 대표와 다른 행은 네이버에서는 서로 다른
     검색이다(네이버는 표기마다 따로 센다). 이 행의 네이버 볼륨은 같은 검색으로 볼 근거가 없으므로 하한에도 더한다
     (하한 = 대표 행 + 네이버 분리 변형의 네이버 볼륨). 2단도 따로 조회한다(plan_queries stage2). 2026-09-30 실측:
     «헤라 선크림»(네이버 123,860)이 «헤라 썬크림»(231,490)과 구글 25,400을 공유해 묶이며 하한에서 빠져
     헤라 순위가 하한 5위 ↔ 상한 2위로 뒤집혔고, 논브랜드 «썬크림» 묶음이 하한 583,810 ~ 상한 922,120으로 벌어졌다.
"""
import json, pathlib, re, sys, unicodedata

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import apply_rules, canon as _canon, dedup_map  # noqa: E402  (계약 T5)

NONBRAND = "_논브랜드"
MULTI = "_비교(복수 브랜드)"
GG_SHARED_MIN = 1000   # [가정] 이보다 작은 구글 볼륨은 우연히 같을 수 있어 공유 판정에 쓰지 않는다
NV_SPLIT = "네이버 분리(구글 공유 · 네이버 볼륨 다름)"   # ④ dup_basis 라벨
MASK = " · "           # 테마 판정 때 브랜드 토큰 자리를 가리는 구분자 — 정규식이 이것을 넘어 매칭하지 않는다


def load(path):
    o = json.load(open(path, encoding="utf-8"))
    return o["data"] if isinstance(o, dict) and "data" in o else o


def shift(month, n):
    """month에서 n개월 이전 (n<0이면 이후)."""
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def sorted_series(d):
    """monthly_volume을 month 기준으로 정렬한다 — 배열 순서를 가정하지 않는다(계약 §1)."""
    mv = [r for r in (d.get("monthly_volume") or []) if isinstance(r, dict) and r.get("month")]
    return sorted(mv, key=lambda r: r["month"])


class Config:
    """벤치마크 설정 — themes-schema(`rules`·`themes`·`brands`·`no_match`)의 상위집합.

    추가 키: brand_aliases · variants · focal_brand · exclude · category_terms · probe_modifiers
    """

    def __init__(self, path):
        cfg = json.load(open(path, encoding="utf-8"))
        self.raw = cfg
        self.subs = {k: v for k, v in cfg.get("rules", {}).get("변형치환", {}).items() if not k.startswith("_")}
        self.brands = list(cfg.get("brands", []))
        if not self.brands:
            raise SystemExit("설정에 brands가 비어 있다 — 벤치마크할 브랜드가 없다 (T8 무력화)")
        self.focal = cfg.get("focal_brand", "")
        if self.focal and self.focal not in self.brands:
            raise SystemExit(f"focal_brand «{self.focal}»가 brands 목록에 없다")
        self.aliases = cfg.get("brand_aliases", {})
        # 브랜드 이름 자신 + 별칭을 canonical로 바꿔 매칭 토큰으로 쓴다
        self.tokens = {b: sorted({self.canon(x) for x in [b] + list(self.aliases.get(b, []))}, key=len)
                       for b in self.brands}
        self.alone = {t for ts in self.tokens.values() for t in ts}      # 브랜드명·별칭 단독 검색
        forms = set()
        for b in self.brands:
            for x in [b] + list(self.aliases.get(b, [])):
                forms |= {self.spaced(x), self.canon(x)}
        self.mask_forms = sorted((f for f in forms if f), key=len, reverse=True)
        self.variants = cfg.get("variants", {})
        self.exclude = [re.compile(p) for p in cfg.get("exclude", [])]
        self.themes = [(t["name"], re.compile(t["pattern"])) for t in cfg.get("themes", [])]
        nm = cfg.get("no_match", {})
        self.no_theme = nm.get("with_brand", "브랜드·제품명"), nm.get("without_brand", "코어·일반")
        self.category_terms = cfg.get("category_terms", [])
        self.probe_modifiers = cfg.get("probe_modifiers", [])

    def canon(self, kw):
        """NFKC · 소문자 · 변형치환(공백 유지 상태) · 공백 제거 — series_dedup.canon과 같은 규칙."""
        return _canon(kw, self.subs)

    def spaced(self, kw):
        """테마 판정용 — NFKC · 소문자 · 변형치환, 띄어쓰기는 유지."""
        s = apply_rules(unicodedata.normalize("NFKC", kw).lower(), self.subs)
        return re.sub(r"\s+", " ", s).strip()

    def excluded(self, c):
        return next((p.pattern for p in self.exclude if p.search(c)), "")

    def bucket(self, c):
        """브랜드 귀속 — 1개면 그 브랜드, 2개 이상이면 비교 버킷, 0개면 논브랜드."""
        hit = [b for b in self.brands if any(t in c for t in self.tokens[b])]
        if len(hit) == 1:
            return hit[0]
        return MULTI if hit else NONBRAND

    def is_alone(self, c):
        """브랜드명(또는 별칭) 단독 검색인가 — 수식어·모델이 붙지 않은 검색."""
        return c in self.alone

    def theme(self, kw, bucket):
        """주 테마 — themes 배열의 «먼저 선언된» 것. 어느 것도 안 걸리면 no_match 라벨.

        정규식은 **띄어쓰기를 유지한 문자열**의 토큰 경계 안에서 판정한다. 공백을 지운 canonical에 걸면
        «호카 본디»가 «호카본디»가 되어 «카본»에 걸린다(2026-09-28 실측). 그래서 ① 브랜드·별칭 토큰을
        구분자로 가리고 ② 전체 문자열 · 각 토큰 · 이웃한 두 토큰의 붙임(«무기 자차»→«무기자차»)에만 건다.
        """
        s = self.spaced(kw)
        for f in self.mask_forms:
            s = s.replace(f, MASK)
        toks = [t for t in s.split() if t != "·"]
        words = s.split()
        joins = [a + b for a, b in zip(words, words[1:]) if "·" not in (a, b)]
        cands = [s] + toks + joins
        for name, p in self.themes:
            if any(p.search(x) for x in cands):
                return name
        return self.no_theme[1] if bucket == NONBRAND else self.no_theme[0]


def _metrics(d):
    m = d.get("ads_metrics") or {}
    v = m.get("volume_total")
    if v is None and d.get("monthly_volume"):
        v = sum(r.get("total", 0) for r in sorted_series(d)[-12:])
    return m, v or 0


def components(cfg, rows):
    """중복 묶음을 만든다 (모듈 docstring ①~③). rows: keyword_info 응답 행(1단·2단 섞여도 된다, 키워드당 1행).

    반환: (info, comps)
      info[keyword] = {"comp": id, "canonical", "bucket", "excluded", "vol", "dup_basis"}
      comps[id] = {"rep": 대표 키워드, "members": [...], "lo", "hi", "bucket", "canonical", "excluded",
                   "nv_split": [네이버 분리 변형 키워드 ...] (④ — 하한에 네이버 볼륨을 더한 행)}
    """
    rows = [d for d in rows if isinstance(d, dict) and isinstance(d.get("keyword"), str)]
    info, parent, same = {}, {}, {}     # parent: ①~③ 묶음 · same: ①② 같은 검색(표기변형·중복)만

    def find(x, par=parent):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x

    def union(a, b, same_search=True):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
        if same_search:
            sa, sb = find(a, same), find(b, same)
            if sa != sb:
                same[sb] = sa

    for d in rows:
        kw = d["keyword"]; c = cfg.canon(kw); m, v = _metrics(d)
        ex = cfg.excluded(c)
        gg, nv = m.get("gg_volume_total"), m.get("nv_volume_total")
        info[kw] = {"canonical": c, "bucket": "" if ex else cfg.bucket(c), "excluded": ex, "vol": v,
                    "gg": gg, "nv": nv, "cpc": m.get("cpc"), "ci": m.get("competition_index"),
                    "dup_basis": "", "row": d}
        parent[kw] = kw; same[kw] = kw

    # ① 같은 검색의 중복 (series_dedup) — 같은 버킷 안에서만 받아들인다
    dm = dedup_map(rows, cfg.subs)
    for kw, v in dm.items():
        if v["rep"] != kw and kw in info and v["rep"] in info and \
                info[kw]["bucket"] == info[v["rep"]]["bucket"] and not info[kw]["excluded"]:
            union(v["rep"], kw); info[kw]["_dm"] = v["basis"]
    # ② 같은 canonical (표기변형)
    by_c = {}
    for kw, i in info.items():
        if i["excluded"]:
            continue
        if i["canonical"] in by_c:
            union(by_c[i["canonical"]], kw)
        else:
            by_c[i["canonical"]] = kw
    # ③ 같은 버킷 · 같은 구글 지표 (구글 근접 변형 공유)
    gsig = {}
    for kw, i in info.items():
        if i["excluded"] or i["gg"] is None or (i["gg"] or 0) < GG_SHARED_MIN or i["cpc"] is None or i["ci"] is None:
            continue
        key = (i["bucket"], i["gg"], i["cpc"], i["ci"])
        i["gkey"] = key
        if key in gsig:
            union(gsig[key], kw, same_search=False)
        else:
            gsig[key] = kw

    comps = {}
    for kw, i in info.items():
        if i["excluded"]:
            cid = "x:" + kw
        else:
            cid = find(kw)
        i["comp"] = cid
        comps.setdefault(cid, {"members": []})["members"].append(kw)
    for cid, c in comps.items():
        mem = sorted(c["members"], key=lambda k: (-info[k]["vol"], k))
        rep = mem[0]
        lo = info[rep]["vol"]
        # ④ 네이버 분리 변형: 대표와 같은 검색(①②)이 아닌 하위 묶음마다 가장 큰 행 — 네이버 볼륨이 대표와 다르면
        #    네이버에서는 다른 검색이므로 그 네이버 볼륨을 하한에도 더한다(구글은 대표에서 이미 한 번 셌다)
        split, seen_s = [], {find(rep, same)}
        rep_nv = info[rep]["nv"]
        for k in mem[1:]:
            sk = find(k, same)
            if sk in seen_s or info[k]["excluded"]:
                continue
            seen_s.add(sk)
            nv = info[k]["nv"]
            if nv and rep_nv is not None and nv != rep_nv:
                split.append(k); lo += nv
        seen_g, hi = set(), 0
        for k in mem:
            i = info[k]
            if i["gg"] is None or i["nv"] is None:
                hi += i["vol"]; continue
            key = i.get("gkey") or ("row", k)
            if key not in seen_g:
                seen_g.add(key); hi += i["gg"] or 0
            hi += i["nv"] or 0
        c.update({"rep": rep, "members": mem, "lo": lo, "hi": max(hi, lo), "bucket": info[rep]["bucket"],
                  "canonical": info[rep]["canonical"], "excluded": info[rep]["excluded"], "nv_split": split})
    # 묶음에 들어온 이유 — 대표 행은 비우고, 나머지는 가장 직접적인 근거 하나
    for c in comps.values():
        for k in c["members"]:
            i = info[k]
            if i["excluded"] or k == c["rep"]:
                i["dup_basis"] = ""
                continue
            if k in c["nv_split"]:
                i["dup_basis"] = NV_SPLIT
                continue
            others = [info[x] for x in c["members"] if x != k]
            if i.get("_dm"):
                i["dup_basis"] = i["_dm"]
            elif any(o["canonical"] == i["canonical"] for o in others):
                i["dup_basis"] = "표기변형(canonical)"
            elif i.get("gkey") and any(o.get("gkey") == i["gkey"] for o in others):
                i["dup_basis"] = "구글 지표 공유"
            else:
                i["dup_basis"] = "묶음 연결"
    return info, comps


def alias_suspects(info):
    """구글 지표가 같은데 버킷이 다른 행 묶음 — 별칭 누락 의심(예: dr.g는 닥터지, dr g는 논브랜드).

    버킷이 다르면 묶음으로 합치지 않는다(귀속을 추측하지 않는다). 대신 사람이 별칭을 보강하도록 알린다.
    """
    sig = {}
    for kw, i in info.items():
        if i["excluded"] or i["gg"] is None or (i["gg"] or 0) < GG_SHARED_MIN or i["cpc"] is None or i["ci"] is None:
            continue
        sig.setdefault((i["gg"], i["cpc"], i["ci"]), []).append(kw)
    out = []
    for kws in sig.values():
        bs = {info[k]["bucket"] for k in kws}
        if len(bs) > 1:
            out.append(sorted(kws, key=lambda k: -info[k]["vol"]))
    return out
