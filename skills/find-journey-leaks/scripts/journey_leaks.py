#!/usr/bin/env python3
"""path_finder 경로 → 누수 엣지 · 허브 노드 점유 · 경로 귀속 (이 스킬 고유 · 0크레딧).

입력은 호출 원장(calls.jsonl)과 분류 사전(leak_dict.json)뿐이다. 원장의 path_finder 행이 가리키는 저장 응답을 읽고,
keyword_info 행(node_volume · seed_volume)이 있으면 노드 볼륨을 붙인다. API는 부르지 않는다.

    python3 scripts/journey_leaks.py --ledger calls.jsonl --dict leak_dict.json \\
        --category running-shoes-kr --out-dir out --queried-at 2026-11-19

정의 (전부 세션 인접 경로 위의 집계다 — 개인 여정이 아니다)
  · 경로 나누기: 시드(canonical)가 처음 나온 자리에서 앞은 유입(inbound), 뒤는 진행(outbound). 누수는 진행 쪽에서만 센다.
    시드가 경로 끝에 있어 진행이 없으면 seed_terminal, 시드가 없으면 off_seed — 둘 다 분모에서 빼고 수를 적는다
  · 경로 귀속: 진행 쪽에서 시드 다음 **처음 닿은 브랜드 노드**의 주인. my_brand / competitor / brand_vs(두 브랜드 이상이
    한 검색에) / no_brand(반환 경로 안에서 브랜드에 닿지 않음)
  · 누수 엣지: 진행 쪽 인접 a → b 에서 b 가 경쟁 브랜드이고 a 가 경쟁 브랜드 · 비교 브랜드가 아닌 것(leak). a 가 내 브랜드면 switch.
    반대 방향은 capture(→ 내 브랜드) · win_back(경쟁 → 내 브랜드), 두 브랜드 비교 검색으로 가면 vs
  · 허브 노드 점유: 논브랜드 노드 A 를 지난 진행 경로가 A 다음에 처음 닿은 브랜드. 내 브랜드 노드는 my_node 로 두고 그 뒤 경쟁 브랜드로
    넘어간 경로를 센다

함정 처리
  T5 — canonical(NFKC · 소문자 · 변형치환 · 공백 제거)로 노드를 묶고, 같은 그룹끼리 붙은 인접(표기변형 인접)은 엣지에서 빼서
       variant_loops 로 센다. 노드 볼륨은 그룹 하한(최댓값)·상한(같은 검색의 중복을 뺀 합)을 병기한다 [추정]
  T7 — 원장의 호출마다 요청 limit 과 반환 경로 수를 적고, 반환 ≥ limit 인 호출의 시드가 지나는 허브는 «반환분 기준»이다.
       «미도달 · 새지 않음» 같은 부재 판정과 «경로를 다 봤다» 같은 커버 판정은 보류(verdict_note)
  T8 — 점유는 늘 두 분모로 낸다: 진행 경로 전체(no_brand 포함) 대비와 브랜드에 닿은 경로 대비. no_brand 비중을 따로 적고,
       등록하지 않은 브랜드 후보(brand_candidates)를 뽑아 논브랜드 부풀림을 알린다
"""
import argparse, collections, csv, json, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from leak_lib import BRAND_CLASSES, NONBRAND, load_dict, read_ledger, response_paths, utf8_console  # noqa: E402
from series_dedup import collapse_identical, dedup_map, report_collapse  # noqa: E402

CALL_COLS = ["id", "step", "tool", "role", "seed", "requested", "requested_unit", "returned", "used_paths",
             "unique_keywords", "missing", "truncated", "cost_actual", "cost_formula", "used_in_analysis", "raw", "status"]
SHARE_COLS = ["seed", "outcome", "brand", "paths", "share_all_paths", "share_branded_paths", "base_paths",
              "branded_paths", "limit", "truncated", "verdict_note"]
EDGE_COLS = ["edge_type", "from_canonical", "to_canonical", "from_example", "to_example", "from_class", "to_class",
             "to_brand", "path_count", "stem_count", "seeds"]
HUB_COLS = ["canonical", "example", "node_class", "brand", "paths_through", "stems_through", "seeds", "my_paths",
            "competitor_paths", "vs_paths", "no_brand_paths", "my_share_all", "competitor_share_all", "no_brand_share",
            "my_share_branded", "competitor_share_branded", "top_competitor", "top_competitor_paths", "group",
            "verdict_note", "volume_lo", "volume_hi", "volume_status"]
NODE_COLS = ["canonical", "example", "variants", "node_class", "brand", "facets", "outbound_paths", "inbound_paths",
             "volume_lo", "volume_hi", "volume_rows", "volume_status"]
CAND_COLS = ["token", "nodes", "leading_nodes", "examples"]


def ratio(a, b):
    return round(a / b, 4) if b else None


def write_csv(path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def seed_variant_scan(seed_c, canons_by_path):
    """시드와 한 글자만 다른 canonical(같은 길이 · 치환 1). 시드 3글자 미만은 보지 않는다 [가정]."""
    if len(seed_c) < 3:
        return 0, {}, {}
    var = collections.Counter()
    only = 0
    for cs in canons_by_path:
        found = {c for c in cs if c != seed_c and len(c) == len(seed_c)
                 and sum(x != y for x, y in zip(c, seed_c)) == 1}
        var.update(found)
        if found and seed_c not in cs:
            only += 1
    # 제안 규칙은 글자 하나가 아니라 표기 전체 쌍이다 — «러→런» 같은 한 글자 치환은 무관한 말까지 바꾼다
    rules = {v: seed_c for v, _ in var.most_common()}
    return only, dict(var.most_common()), rules


def attribute(seq, cls, skip):
    """seq(canonical 목록)에서 처음 닿은 브랜드 → (outcome, brand). skip: 귀속에서 뺄 브랜드 이름 집합."""
    for c in seq:
        k = cls[c]
        if k["node_class"] == "brand_vs":
            names = set(k["brand"].split("/"))
            if names - skip:
                return "brand_vs", k["brand"]
        elif k["node_class"] in ("my_brand", "competitor", "seed") and k["brand"] and k["brand"] not in skip:
            return ("my_brand" if k["is_my"] else "competitor"), k["brand"]
    return "no_brand", ""


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="경로 → 누수 엣지 · 허브 점유 (0크레딧)")
    ap.add_argument("--ledger", required=True, help="record_call.py 가 쓴 calls.jsonl")
    ap.add_argument("--dict", required=True, help="make_leak_dict.py 가 만든 leak_dict.json (파일 또는 인라인 JSON)")
    ap.add_argument("--category", required=True, help="산출 파일명 접미사")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--queried-at", default="", help="조회일 YYYY-MM-DD")
    ap.add_argument("--min-paths", type=int, default=3, help="허브 판정 최소 경로 수 [가정] (기본 3)")
    ap.add_argument("--stem-len", type=int, default=3, help="공통 줄기로 볼 진행 앞 걸음 수 [가정] (기본 3)")
    ap.add_argument("--volume-top", type=int, default=60, help="노드 볼륨 요청 목록에 넣을 노드 수 상한 (1 × 표기 수)")
    ap.add_argument("--allow-seed-variant", action="store_true")
    a = ap.parse_args()

    D = load_dict(a.dict)
    led = read_ledger(a.ledger)
    out = pathlib.Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cat = a.category
    warnings = []

    # ── 1. 원장 → 분석에 쓸 path_finder 호출 (시드마다 limit 이 가장 큰 호출 하나 · 나머지는 superseded)
    pf = [r for r in led if r.get("tool") == "path_finder"]
    active = {}
    for r in pf:
        c = D.canon(r.get("seed", ""))
        cur = active.get(c)
        if cur is None or (r.get("limit") or 0) >= (cur.get("limit") or 0):
            active[c] = r
    if not active:
        sys.exit("원장에 path_finder 호출이 없다")
    used_ids = {r["id"] for r in active.values()}
    limits = {r.get("limit") for r in active.values()}
    main_limit = max((x for x in limits if x), default=None)
    mismatch = [r for r in active.values() if r.get("limit") != main_limit]
    for r in mismatch:
        warnings.append(f"limit 불일치 — {r['seed']} 는 limit {r.get('limit')}(본 limit {main_limit}) 경로만 있다. "
                        f"시드 간 비중을 나란히 비교하지 않는다")
    seed_brands = set()
    if D.raw.get("brand_seed"):
        for s in D.seeds:
            m, cs = D.brands_in(s)
            seed_brands |= set(([D.my["name"]] if m else []) + cs)
        warnings.append(f"브랜드 출발 표본 — 시드 브랜드 {sorted(seed_brands)}는 귀속에서 뺐다. 카테고리 진입 경로가 아니다")

    # ── 2. 경로 읽기 · canonical · 표기변형 인접 접기 (T5)
    examples = collections.defaultdict(collections.Counter)
    runs = []          # (call, seed_c, [canon seq])
    variant_loops = 0
    for c, r in active.items():
        resp = json.loads(pathlib.Path(r["_raw_path"]).read_text(encoding="utf-8"))
        seqs = []
        for p in response_paths(resp):
            cs = []
            for kw in p:
                k = D.canon(kw)
                examples[k][kw] += 1
                if cs and cs[-1] == k:
                    variant_loops += 1
                    continue
                cs.append(k)
            if len(cs) >= 2:
                seqs.append(cs)
        found = sum(1 for cs in seqs if c in cs)
        if seqs and found == 0:
            only, var, rules = seed_variant_scan(c, seqs)
            if only * 2 >= len(seqs) and not a.allow_seed_variant:
                print(f"✗ 시드 «{r['seed']}»가 {len(seqs)}경로 어디에도 없고 한 글자 다른 표기 {var} 가 {only}경로에 있다.\n"
                      f"  path_finder 가 시드 표기를 바꿔 돌려준 것이다 — 사전 rules.변형치환 에 {json.dumps(rules, ensure_ascii=False)} 를"
                      f" 넣고 다시 돌린다(0크레딧). 정말 다른 검색이면 --allow-seed-variant")
                return 3
            warnings.append(f"시드 «{r['seed']}»가 사용 경로에 없다 — 전부 off_seed 로 빠진다")
        elif seqs and found < len(seqs):
            only, var, rules = seed_variant_scan(c, seqs)
            if var:
                warnings.append(f"시드 «{r['seed']}» 한 글자 다른 표기 {var} — 같은 검색이면 변형치환 {rules} 를 넣는다")
        runs.append((r, c, seqs))

    # ── 3. 노드 분류 (규칙)
    cls = {}
    for k, ex in examples.items():
        picks = [D.classify(kw) for kw, _ in ex.most_common()]
        pick = next((x for x in picks if x["node_class"] in BRAND_CLASSES), picks[0])
        if k in D.seed_canons:
            pick = dict(pick, node_class="seed")
        pick["is_my"] = bool(D.my and pick["brand"] == D.my["name"])
        cls[k] = pick

    # ── 3b. 별칭 누락 검사 (0.1.1 — 선언 브랜드의 별칭 canonical이 글자 그대로 든 노드가 그 브랜드로 분류되지 않으면 경고)
    alias_misses = []
    for k, ex in examples.items():
        assigned = [b for b in (cls[k]["brand"] or "").split("/") if b]
        for brand, alias in D.alias_misses(ex.most_common(1)[0][0], assigned):
            alias_misses.append({"canonical": k, "example": ex.most_common(1)[0][0], "node_class": cls[k]["node_class"],
                                 "brand": brand, "alias": alias})
    if alias_misses:
        shown = ", ".join(f"{m['example']}({m['node_class']}) ⊃ {m['alias']}→{m['brand']}" for m in alias_misses[:8])
        warnings.append(f"별칭 누락 의심 {len(alias_misses)}노드 — 선언 브랜드 별칭이 글자로 들어 있는데 그 브랜드로 분류되지 않았다: {shown}"
                        + (" 외" if len(alias_misses) > 8 else "") + ". 브랜드 검색이면 별칭을 고쳐 다시 돌린다(0크레딧), 아니면 무시")

    # ── 4. 귀속 · 엣지 · 허브
    n_off = n_term = 0
    outcome_by_seed = collections.defaultdict(collections.Counter)
    brand_by_seed = collections.defaultdict(collections.Counter)
    node = collections.defaultdict(lambda: {"paths": 0, "my": 0, "comp": 0, "vs": 0, "none": 0,
                                            "comp_by": collections.Counter(), "seeds": set(), "stems": set()})
    inbound_cnt, outbound_cnt = collections.Counter(), collections.Counter()
    edges = collections.defaultdict(lambda: {"paths": 0, "seeds": set(), "stems": set()})
    stems_by_seed = collections.defaultdict(collections.Counter)
    trunc_seeds = {c for r, c, _ in runs if r.get("truncated")}
    for r, sc, seqs in runs:
        for cs in seqs:
            if sc not in cs:
                n_off += 1
                continue
            i0 = cs.index(sc)
            inb, outb = cs[: i0 + 1], cs[i0:]
            for k in set(inb[:-1]):
                inbound_cnt[k] += 1
            if len(outb) < 2:
                n_term += 1
                continue
            stem = tuple(outb[: a.stem_len])
            stems_by_seed[sc][stem] += 1
            for k in set(outb[1:]):
                outbound_cnt[k] += 1
            oc, br = attribute(outb[1:], cls, seed_brands)
            outcome_by_seed[sc][oc] += 1
            if br:
                brand_by_seed[sc][(oc, br)] += 1
            seen = set()
            for i in range(1, len(outb)):
                k = outb[i]
                if k in seen:
                    continue
                seen.add(k)
                kc = cls[k]
                if kc["node_class"] in ("competitor", "brand_vs", "seed"):
                    continue
                skip = set(seed_brands) | ({kc["brand"]} if kc["brand"] else set())
                o2, b2 = attribute(outb[i + 1:], cls, skip)
                m = node[k]
                m["paths"] += 1
                m["seeds"].add(sc)
                m["stems"].add(stem)
                m[{"my_brand": "my", "competitor": "comp", "brand_vs": "vs", "no_brand": "none"}[o2]] += 1
                if o2 == "competitor":
                    m["comp_by"][b2] += 1
            pairs = set()
            for x, y in zip(outb, outb[1:]):
                cx, cy = cls[x], cls[y]
                fx, fy = cx["node_class"], cy["node_class"]
                et = None
                if fy == "competitor" and cy["brand"] not in seed_brands:
                    et = "switch" if fx == "my_brand" else (None if fx in ("competitor", "brand_vs") else "leak")
                elif fy == "my_brand" and cy["brand"] not in seed_brands:
                    et = "win_back" if fx == "competitor" else (None if fx in ("my_brand", "brand_vs") else "capture")
                elif fy == "brand_vs" and fx != "brand_vs":
                    et = "vs"
                if et:
                    pairs.add((et, x, y))
            for p in pairs:
                edges[p]["paths"] += 1
                edges[p]["seeds"].add(sc)
                edges[p]["stems"].add(stem)

    # ── 5. 노드 볼륨 (keyword_info ads_metrics) — 그룹 하한 · 상한 (T5)
    ki = [r for r in led if r.get("tool") == "keyword_info" and r.get("_raw_path")]
    vol_rows, requested = [], set()
    for r in ki:
        resp = json.loads(pathlib.Path(r["_raw_path"]).read_text(encoding="utf-8"))
        vol_rows += [x for x in resp.get("data") or [] if isinstance(x, dict) and x.get("keyword")]
        requested |= {D.canon(k) for k in (resp.get("request_detail") or {}).get("keywords") or []}
    vol = {}
    if vol_rows:
        vol_rows, merged, conflicts = collapse_identical(vol_rows)
        report_collapse(merged, conflicts, len(vol_rows))
        dm = dedup_map(vol_rows, D.rules)
        grp = collections.defaultdict(dict)
        for x in vol_rows:
            v = (x.get("ads_metrics") or {}).get("volume_avg")
            if isinstance(v, (int, float)):
                grp[D.canon(x["keyword"])][dm[x["keyword"]]["rep"]] = max(v, grp[D.canon(x["keyword"])].get(dm[x["keyword"]]["rep"], 0))
        for k, reps in grp.items():
            vol[k] = {"lo": max(reps.values()), "hi": sum(reps.values()), "rows": len(reps)}

    def vstat(k):
        if k in vol:
            return "returned"
        return "not_returned" if k in requested else "not_requested"

    def ex(k):
        return examples[k].most_common(1)[0][0]

    # ── 6. 산출 — 호출 원장 (T7: 요청 limit vs 반환)
    call_rows = []
    for r in led:
        pfr = r.get("tool") == "path_finder"
        call_rows.append({"id": r["id"], "step": r.get("step"), "tool": r.get("tool"), "role": r.get("role"),
                          "seed": r.get("seed"), "requested": r.get("limit") if pfr else r.get("requested"),
                          "requested_unit": "paths(limit)" if pfr else "keywords",
                          "returned": r.get("returned"), "used_paths": r.get("used_paths"),
                          "unique_keywords": r.get("unique_keywords"), "missing": r.get("missing"),
                          "truncated": ("unknown" if pfr and r.get("truncated") is None else
                                        ("yes" if r.get("truncated") else ("no" if pfr else ""))),
                          "cost_actual": r.get("cost"), "cost_formula": r.get("cost_formula"),
                          "used_in_analysis": ("yes" if r["id"] in used_ids else "superseded") if pfr else "yes",
                          "raw": r.get("raw"), "status": r.get("status")})
        if pfr and r.get("cost") is not None and r.get("cost_formula") is not None and r["cost"] != r["cost_formula"] \
                and not r.get("reused_from"):
            warnings.append(f"{r['id']} 실측 비용 {r['cost']} ≠ 식 50 + 40 × {r.get('unique_keywords')} = {r['cost_formula']}")
    write_csv(out / f"leak_calls_{cat}.csv", CALL_COLS, call_rows)

    # ── 점유 (T8: 두 분모)
    share_rows = []
    seed_name = {c: r["seed"] for r, c, _ in runs}
    seed_limit = {c: r.get("limit") for r, c, _ in runs}
    total_oc, total_br = collections.Counter(), collections.Counter()
    for sc in [c for _, c, _ in runs] + ["__all__"]:
        oc = outcome_by_seed[sc] if sc != "__all__" else total_oc
        br = brand_by_seed[sc] if sc != "__all__" else total_br
        if sc != "__all__":
            total_oc.update(oc)
            total_br.update(br)
        base = sum(oc.values())
        branded = base - oc["no_brand"]
        tr = ("yes" if sc in trunc_seeds else "no") if sc != "__all__" else \
            ("yes" if trunc_seeds == set(seed_name) else ("mixed" if trunc_seeds else "no"))
        note = "반환분 기준(절단)" if tr in ("yes", "mixed") else ""
        lim = seed_limit.get(sc) if sc != "__all__" else ("mixed" if len(limits) > 1 else main_limit)
        rows = [("my_brand", D.my["name"] if D.my else "", sum(v for (o, b), v in br.items() if o == "my_brand"))] if D.my else []
        rows += [("competitor", b, v) for (o, b), v in sorted(br.items(), key=lambda x: -x[1]) if o == "competitor"]
        rows += [("brand_vs", b, v) for (o, b), v in sorted(br.items(), key=lambda x: -x[1]) if o == "brand_vs"]
        rows += [("no_brand", "", oc["no_brand"])]
        for o, b, v in rows:
            n2 = note
            if v == 0 and tr in ("yes", "mixed"):
                n2 = "반환분에 없음 — 절단이라 «닿지 않는다» 판정 보류"
            share_rows.append({"seed": seed_name.get(sc, "전체"), "outcome": o, "brand": b, "paths": v,
                               "share_all_paths": ratio(v, base),
                               "share_branded_paths": None if o == "no_brand" else ratio(v, branded),
                               "base_paths": base, "branded_paths": branded, "limit": lim, "truncated": tr,
                               "verdict_note": n2})
    write_csv(out / f"leak_share_{cat}.csv", SHARE_COLS, share_rows)

    # ── 엣지
    edge_rows = []
    for (et, x, y), e in sorted(edges.items(), key=lambda kv: (-kv[1]["paths"], kv[0])):
        edge_rows.append({"edge_type": et, "from_canonical": x, "to_canonical": y, "from_example": ex(x),
                          "to_example": ex(y), "from_class": cls[x]["node_class"], "to_class": cls[y]["node_class"],
                          "to_brand": cls[y]["brand"], "path_count": e["paths"], "stem_count": len(e["stems"]),
                          "seeds": ";".join(sorted(seed_name[s] for s in e["seeds"]))})
    write_csv(out / f"leak_edges_{cat}.csv", EDGE_COLS, edge_rows)

    # ── 허브 점유
    hub_rows = []
    for k, m in node.items():
        kc = cls[k]
        resolved = m["my"] + m["comp"]
        if kc["node_class"] == "my_brand":
            group = "my_node"
        elif m["paths"] < a.min_paths:
            group = "low_evidence"
        elif resolved / m["paths"] < 0.1:
            group = "uncaptured"
        elif not D.my:
            group = "brand_reached"
        elif m["my"] / resolved >= 0.7:
            group = "winning"
        elif m["comp"] / resolved >= 0.7:
            group = "leaking"
        else:
            group = "contested"
        notes = []
        if m["seeds"] & trunc_seeds:
            notes.append("반환분 기준(절단)")
            if group == "uncaptured" or (D.my and m["my"] == 0 and kc["node_class"] != "my_brand"):
                notes.append("«내 브랜드 미도달» 판정 보류")
        if len(m["stems"]) == 1 and m["paths"] > 1:
            notes.append("경로가 한 줄기에서 갈라진 것 — 세션 수로 읽지 않는다")
        top = m["comp_by"].most_common(1)
        hub_rows.append({"canonical": k, "example": ex(k), "node_class": kc["node_class"], "brand": kc["brand"],
                         "paths_through": m["paths"], "stems_through": len(m["stems"]),
                         "seeds": ";".join(sorted(seed_name[s] for s in m["seeds"])),
                         "my_paths": m["my"], "competitor_paths": m["comp"], "vs_paths": m["vs"], "no_brand_paths": m["none"],
                         "my_share_all": ratio(m["my"], m["paths"]), "competitor_share_all": ratio(m["comp"], m["paths"]),
                         "no_brand_share": ratio(m["none"], m["paths"]),
                         "my_share_branded": ratio(m["my"], resolved), "competitor_share_branded": ratio(m["comp"], resolved),
                         "top_competitor": top[0][0] if top else "", "top_competitor_paths": top[0][1] if top else 0,
                         "group": group, "verdict_note": " · ".join(notes),
                         "volume_lo": vol.get(k, {}).get("lo"), "volume_hi": vol.get(k, {}).get("hi"),
                         "volume_status": vstat(k)})
    order = {"leaking": 0, "contested": 1, "brand_reached": 1, "uncaptured": 2, "winning": 3, "my_node": 4, "low_evidence": 5}
    hub_rows.sort(key=lambda h: (order[h["group"]], -h["competitor_paths"], -h["paths_through"], -(h["volume_lo"] or 0), h["canonical"]))
    write_csv(out / f"leak_hubs_{cat}.csv", HUB_COLS, hub_rows)

    # ── 노드 분류표
    node_rows = []
    for k in sorted(examples, key=lambda k: (-outbound_cnt[k], k)):
        node_rows.append({"canonical": k, "example": ex(k), "variants": ";".join(examples[k]),
                          "node_class": cls[k]["node_class"], "brand": cls[k]["brand"],
                          "facets": ";".join(cls[k]["facets"]), "outbound_paths": outbound_cnt[k],
                          "inbound_paths": inbound_cnt[k], "volume_lo": vol.get(k, {}).get("lo"),
                          "volume_hi": vol.get(k, {}).get("hi"), "volume_rows": vol.get(k, {}).get("rows"),
                          "volume_status": vstat(k)})
    write_csv(out / f"leak_nodes_{cat}.csv", NODE_COLS, node_rows)

    # ── 미등록 브랜드 후보 (T8 — 빠진 브랜드는 논브랜드를 부풀린다)
    stop = D.stop_tokens()
    tok_nodes, tok_lead, tok_ex = collections.Counter(), collections.Counter(), collections.defaultdict(list)
    for k, kc in cls.items():
        if kc["node_class"] not in ("generic", "comparison", "community", "unmatched"):
            continue
        toks = ex(k).split()
        for i, t in enumerate(toks):
            tc = D.canon(t)
            if len(tc) < 2 or tc.isdigit() or tc in stop or any(s and (s in tc or tc in s) for s in stop if len(s) >= 2):
                continue
            tok_nodes[tc] += 1
            tok_lead[tc] += i == 0
            if len(tok_ex[tc]) < 3:
                tok_ex[tc].append(ex(k))
    cand_rows = [{"token": t, "nodes": n, "leading_nodes": tok_lead[t], "examples": " | ".join(tok_ex[t])}
                 for t, n in tok_nodes.most_common() if n >= 2 and tok_lead[t] >= 1][:30]
    write_csv(out / f"brand_candidates_{cat}.csv", CAND_COLS, cand_rows)

    # ── 노드 볼륨 요청 목록 (STEP 5 — 1 × 표기 수)
    pick = [h["canonical"] for h in hub_rows if h["paths_through"] >= a.min_paths]
    pick += [e["to_canonical"] for e in edge_rows if e["edge_type"] in ("leak", "switch")]
    pick = list(dict.fromkeys(pick))[: a.volume_top]
    req_kws = [kw for k in pick for kw in examples[k]]
    (out / f"volume_request_{cat}.json").write_text(json.dumps(
        {"keywords": req_kws, "nodes": len(pick), "planned_cost": 1 * len(req_kws), "data_type": "ads_metrics"},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # ── 결과 JSON (리포트 재료) · 요약
    class_dist = collections.Counter()
    for k, n in outbound_cnt.items():
        class_dist[cls[k]["node_class"]] += n
    used_paths = sum(len(s) for _, _, s in runs)
    base_all = sum(total_oc.values())
    groups = collections.Counter(h["group"] for h in hub_rows)
    verdict_hold = bool(trunc_seeds)
    result = {
        "category": cat, "gl": D.gl, "queried_at": a.queried_at, "seeds": D.seeds,
        "time_points": sorted({str(r.get("time_point") or "curr") for r, _, _ in runs}),
        "my_brand": D.my["name"] if D.my else None, "competitors": [c["name"] for c in D.competitors],
        "mode": "brand" if D.my else "landscape", "brand_seed": bool(D.raw.get("brand_seed")),
        "analysis_call_ids": sorted(used_ids, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0),
        "min_paths": a.min_paths, "stem_len": a.stem_len,
        "totals": {"used_paths": used_paths, "outbound_paths": base_all, "off_seed": n_off, "seed_terminal": n_term,
                   "variant_loops": variant_loops, "nodes": len(examples), "truncated_calls": len(trunc_seeds),
                   "analysis_calls": len(runs), "limits": sorted(x for x in limits if x is not None),
                   "unmatched_share_of_appearances": ratio(class_dist["unmatched"], sum(class_dist.values())),
                   "distinct_stems": {seed_name[s]: len(c) for s, c in stems_by_seed.items()}},
        "verdict_hold": verdict_hold,
        "class_distribution": dict(class_dist),
        "share": share_rows, "edges": edge_rows[:40], "hubs": hub_rows[:60],
        "group_counts": dict(groups), "candidates": cand_rows,
        "calls": [{k: v for k, v in r.items() if not k.startswith("_")} for r in led],
        "cost_total": sum((r.get("cost") or 0) for r in led),
        "volume_request": {"keywords": len(req_kws), "nodes": len(pick)},
        "alias_misses": alias_misses,
        "warnings": warnings,
    }
    (out / f"leak_result_{cat}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    pct = lambda v: "—" if v is None else f"{v * 100:.1f}%"
    L = [f"# 검색 경로 누수 — {cat}", "",
         f"- 조회일 {a.queried_at or '미기재'} · time_point {', '.join(result['time_points'])} (월 그레인 스냅샷 — 실시간 아님) · gl {D.gl}",
         f"- 세션 인접 경로의 집계다. 개인 여정 · 구매 · 전환이 아니다. 경로 끝은 이탈이 아니라 반환 경로의 끝이다",
         f"- 모드: {'내 브랜드 ' + D.my['name'] if D.my else '경쟁 지형(내 브랜드 미지정)'} · 경쟁 {', '.join(result['competitors'])}", ""]
    L += ["## 호출과 크레딧 [실제 데이터]", "", "| ID | 도구 | 역할 | 시드 | 요청 | 반환 | 고유 키워드 | 절단 | 실측 | 식 | 분석 사용 |", "|---|---|---|---|---:|---:|---:|---|---:|---:|---|"]
    for c in call_rows:
        L.append(f"| {c['id']} | {c['tool']} | {c['role']} | {c['seed'] or ''} | {c['requested']} | {c['returned']} | "
                 f"{c['unique_keywords'] if c['unique_keywords'] is not None else ''} | {c['truncated']} | {c['cost_actual']} | "
                 f"{c['cost_formula'] if c['cost_formula'] is not None else ''} | {c['used_in_analysis']} |")
    L += ["", f"이번 분석 소진(원장 cost 합): {result['cost_total']}", ""]
    if verdict_hold:
        L += [f"> ⚠ 절단 — 반환 경로 수가 요청 limit 에 닿은 호출이 {len(trunc_seeds)}건이다. 모든 비중은 **반환분 기준**이며 "
              "«내 브랜드가 닿지 않는다 · 경쟁사로 새지 않는다 · 경로를 다 봤다»는 판정은 보류한다.", ""]
    for w in warnings:
        L.append(f"> ⚠ {w}")
    L += ["", "## 경로 귀속 — 시드 다음 처음 닿은 브랜드 [추정: 규칙 분류]", "",
          f"사용 경로 {used_paths} · 진행 경로(분모) {base_all} · off_seed {n_off} · seed_terminal {n_term} · 표기변형 인접 제외 {variant_loops}", "",
          "| 시드 | 귀속 | 브랜드 | 경로 | 진행 경로 대비 | 브랜드 도달 경로 대비 | 메모 |", "|---|---|---|---:|---:|---:|---|"]
    for s in share_rows:
        L.append(f"| {s['seed']} | {s['outcome']} | {s['brand']} | {s['paths']} | {pct(s['share_all_paths'])} | "
                 f"{pct(s['share_branded_paths'])} | {s['verdict_note']} |")
    L += ["", "논브랜드 분리(T8): «진행 경로 대비»는 브랜드에 닿지 않은 경로(no_brand)를 분모에 둔다. «브랜드 도달 경로 대비»만 보면 "
          "브랜드 간 몫이 커 보인다 — 둘을 함께 읽는다.", "",
          "## 누수 엣지 상위 (→ 경쟁 브랜드) [실제 데이터: 인접 수 · 추정: 분류]", "",
          "| 유형 | 앞 검색 | 앞 분류 | 다음 검색(경쟁) | 브랜드 | 경로 | 줄기 |", "|---|---|---|---|---|---:|---:|"]
    for e in [e for e in edge_rows if e["edge_type"] in ("leak", "switch")][:15]:
        L.append(f"| {e['edge_type']} | {e['from_example']} | {e['from_class']} | {e['to_example']} | {e['to_brand']} | {e['path_count']} | {e['stem_count']} |")
    L += ["", "## 허브 노드 점유 (논브랜드 노드 다음 처음 닿은 브랜드) [추정]", "",
          f"그룹 수: {dict(groups)} · 허브 판정 최소 경로 {a.min_paths} [가정]", "",
          "| 그룹 | 노드 | 분류 | 경로 | 내 브랜드 | 경쟁 | 브랜드 없음 | 최다 경쟁 | 볼륨 하한~상한 | 메모 |", "|---|---|---|---:|---:|---:|---:|---|---|---|"]
    for h in hub_rows[:20]:
        vr = "—" if h["volume_lo"] is None else f"{h['volume_lo']:g}~{h['volume_hi']:g}"
        L.append(f"| {h['group']} | {h['example']} | {h['node_class']} | {h['paths_through']} | {pct(h['my_share_all'])} | "
                 f"{pct(h['competitor_share_all'])} | {pct(h['no_brand_share'])} | {h['top_competitor']} | {vr} | {h['verdict_note']} |")
    L += ["", f"## 미등록 브랜드 후보 — 사전 보강용 ({len(cand_rows)}개)", "",
          "논브랜드 노드에서 반복되는 말이다. 브랜드면 --competitor 로 넣고 다시 돌린다(0크레딧). 빠진 브랜드는 no_brand 를 부풀린다.", ""]
    for c in cand_rows[:15]:
        L.append(f"- {c['token']} — 노드 {c['nodes']} (맨 앞 {c['leading_nodes']}) · 예: {c['examples']}")
    L += ["", f"노드 분류 분포(진행 경로 등장 수): {dict(class_dist)} · 미분류 비중 {pct(result['totals']['unmatched_share_of_appearances'])}",
          f"노드 볼륨 요청 목록: volume_request_{cat}.json — 노드 {len(pick)} · 표기 {len(req_kws)} → 계획 1 × {len(req_kws)} = {len(req_kws)} 크레딧", "",
          "라벨: 경로 · 인접 수 · cost_detail · volume_avg [실제 데이터] / 노드 분류 · 귀속 · 그룹 · 볼륨 하한·상한 [추정] / "
          "사전 · 최소 경로 · 그룹 임계(0.1 · 0.7) · 줄기 길이 [가정] / 빈 응답 · 미반환 볼륨 · off_seed [데이터 공백]"]
    (out / f"leak_summary_{cat}.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"사용 경로 {used_paths} · 진행 {base_all} · 노드 {len(examples)} · 누수 엣지 "
          f"{sum(1 for e in edge_rows if e['edge_type'] == 'leak')} · 허브 그룹 {dict(groups)}"
          + (" · 절단 — 반환분 기준" if verdict_hold else ""))
    for w in warnings:
        print(f"⚠ {w}")
    print(f"→ {out}/leak_*_{cat}.csv · leak_result_{cat}.json · leak_summary_{cat}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
