#!/usr/bin/env python3
"""path_finder 응답 → 세션 인접 전이 집계 (이 스킬 고유 스크립트).

path_finder는 경로 배열만 돌려준다 — 노드·엣지·단계·세션 수 필드는 없다. 그래서 전이 빈도와
단계 분포는 전부 여기서 **로컬로** 만든다. 만드는 과정에서 세 가지를 막는다.

  ① 표기변형 이중 계상(T5) — `캠핑 의자`→`캠핑의자`처럼 같은 수요의 표기만 바뀐 인접쌍을
     전이로 세면 «가장 흔한 다음 검색»이 자기 자신의 띄어쓰기가 된다. canonical 그룹키로 묶고
     같은 그룹끼리의 인접은 전이에서 빼서 따로 센다(variant_loops).
  ② 절단 오독(T7) — 반환 경로 수가 요청 limit에 닿으면 «이 시드의 전체 경로»가 아니다.
     truncated로 표시하고 요약에 «반환분 기준»이라고 적는다.
  ③ 비용 오산 — 과금은 경로 수가 아니라 **경로 안 고유 키워드 수**다(50 + 40 × U).
     응답의 cost_detail.total_cost(실측)와 식으로 다시 계산한 값을 나란히 적는다.
  ④ 시드 표기 변경 — path_finder도 시드를 다른 표기로 돌려줄 수 있다(선크림 예시 실행: `선크림` 요청 →
     파일럿 경로 전부 `썬크림`). 변형치환 규칙이 없으면 시드가 0경로에서 발견되고 모든 인접이
     off_seed가 된다. 시드가 0경로인데 한 글자만 다른 표기가 사용 경로 절반 이상에 있으면 **멈추고**
     규칙(예: {"썬": "선"})을 제안한다(`--allow-seed-variant`로만 진행). 일부 경로에만 있으면 경고한다.
  ⑤ 공통 줄기 — 반환 경로는 앞 몇 걸음을 공유하고 끝만 갈라지는 경우가 있다(선크림 예시 실행: 반환
     경로 일부가 앞 3걸음 동일). 그러면 path_count는 «세션 빈도»보다 «한 줄기 아래 갈래 수»로
     커진다. 줄기(앞 `--stem-len` 걸음, 기본 3) 수를 stem_count·stems_with_keyword로 함께 적는다.

    python3 journey_paths.py --run "캠핑의자=resp_camping_chair.json@50" \\
        --stages stages.json --category camping-chair-kr --out-dir out \\
        --queried-at 2026-09-28 --plan path_plan.csv [--plan-full path_plan_all_candidates.csv]

`--run`은 반복할 수 있다(시드마다 한 번). `@limit`을 생략하면 응답의 request_detail.limit을 쓰고,
그것도 없으면 절단 판정을 «판정 불가»로 남긴다. API 호출은 하지 않는다.
"""
import argparse, collections, csv, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from series_dedup import canon as _canon  # noqa: E402  (계약 T5 — 치환은 공백 제거 전에)


def load_json_arg(v):
    """인라인 JSON 또는 파일 경로 — 문서 예시가 둘 다 쓰므로 둘 다 받는다."""
    v = v.strip()
    if v.startswith("{") or v.startswith("["):
        return json.loads(v)
    return json.loads(pathlib.Path(v).read_text(encoding="utf-8"))


def seed_variant_scan(seed_c, path_canons):
    """시드와 한 글자만 다른(같은 길이 · 치환 1) canonical 표기를 찾는다.

    반환: (variant_only 경로 수, {변형: 경로 수}, 제안 규칙 {변형 글자: 시드 글자}).
    variant_only = 시드는 없고 변형만 있는 경로. 시드 3글자 미만이면 한 글자 차이가 다른 말이 되기
    쉬워 검사하지 않는다 [가정].
    """
    if len(seed_c) < 3:
        return 0, {}, {}
    var_paths = collections.Counter()
    only = 0
    for cs in path_canons:
        found = set()
        for c in cs:
            if c != seed_c and len(c) == len(seed_c) and sum(x != y for x, y in zip(c, seed_c)) == 1:
                found.add(c)
        for c in found:
            var_paths[c] += 1
        if found and seed_c not in cs:
            only += 1
    rules = {}
    for v, _ in var_paths.most_common():
        i = next(i for i, (x, y) in enumerate(zip(v, seed_c)) if x != y)
        rules.setdefault(v[i], seed_c[i])
    return only, dict(var_paths.most_common()), rules


def stem_of(cs, k):
    """연속 중복(표기변형 인접)을 접은 canonical 경로의 앞 k걸음."""
    out = []
    for c in cs:
        if not out or out[-1] != c:
            out.append(c)
    return tuple(out[:k])


def make_canon(subs):
    def canon(kw):
        return _canon(str(kw), subs)
    return canon


def make_classifier(stages, canon):
    themes = [(t["name"], re.compile(t["pattern"])) for t in stages.get("themes", [])]
    brands = [canon(b) for b in stages.get("brands", []) if b]
    nm = stages.get("no_match", {})
    with_brand = nm.get("with_brand", "브랜드·제품명")
    without_brand = nm.get("without_brand", "미분류")

    def classify(c):
        hits = [name for name, rx in themes if rx.search(c)]
        is_brand = any(b and b in c for b in brands)
        if hits:
            return hits[0], hits, is_brand
        return (with_brand if is_brand else without_brand), [], is_brand
    return classify


def parse_run(spec):
    if "=" not in spec:
        sys.exit(f"--run 형식은 '시드=응답.json[@limit]' 이다: {spec}")
    seed, rest = spec.split("=", 1)
    limit = None
    m = re.match(r"^(.*)@(\d+)$", rest)
    if m:
        rest, limit = m.group(1), int(m.group(2))
    return seed.strip(), rest, limit


def clean_paths(resp):
    """2개 미만 시퀀스는 전이가 없으므로 버린다. 공백만 정리하고 표기는 바꾸지 않는다."""
    out = []
    for p in resp.get("data") or []:
        if not isinstance(p, list):
            continue
        seq = [" ".join(str(k).split()) for k in p if str(k).strip()]
        if len(seq) >= 2:
            out.append(seq)
    return out


def main():
    ap = argparse.ArgumentParser(description="path_finder 경로 → 전이·단계 집계 (0크레딧)")
    ap.add_argument("--run", action="append", required=True, help="시드=응답.json[@limit] (반복)")
    ap.add_argument("--stages", required=True, help="단계 사전 (themes 스키마) — 파일 또는 인라인 JSON")
    ap.add_argument("--category", required=True, help="산출 파일명 접미사")
    ap.add_argument("--out-dir", default=".")
    ap.add_argument("--queried-at", default="", help="조회일 YYYY-MM-DD (요약 헤더에 적는다)")
    ap.add_argument("--top", type=int, default=10, help="요약의 다음 검색 상위 N")
    ap.add_argument("--plan", help="path_budget.py의 path_plan.csv — 보류한 출발 후보를 요약에 싣는다")
    ap.add_argument("--plan-full", help="후보를 좁히기 전 전체 계획 CSV — 좁힌 계획에서 빠진 후보도 요약에 싣는다")
    ap.add_argument("--stem-len", type=int, default=3, help="공통 줄기로 볼 앞 걸음 수 [가정] (기본 3)")
    ap.add_argument("--allow-seed-variant", action="store_true",
                    help="시드가 0경로이고 한 글자 다른 표기만 있어도 멈추지 않고 진행 (전부 off_seed가 된다)")
    a = ap.parse_args()

    stages = load_json_arg(a.stages)
    canon = make_canon(stages.get("rules", {}).get("변형치환", {}))
    classify = make_classifier(stages, canon)
    out = pathlib.Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    edge_rows, node_rows, runs = [], [], []
    stop = []
    for spec in a.run:
        seed, path, limit_arg = parse_run(spec)
        resp = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        req = resp.get("request_detail") or {}
        limit = limit_arg if limit_arg is not None else req.get("limit")
        time_point = req.get("time_point", "")
        gl = req.get("gl", "")
        paths = clean_paths(resp)
        seed_c = canon(seed)

        raw_unique = {k for p in paths for k in p}
        measured = (resp.get("cost_detail") or {}).get("total_cost")
        formula = 50 + 40 * len(raw_unique)
        truncated = None if limit is None else (len(resp.get("data") or []) >= int(limit))

        edges = collections.Counter()
        edge_pos = {}
        example = {}
        node_paths = collections.Counter()
        node_pre, node_post = collections.Counter(), collections.Counter()
        loops = 0
        seed_found = 0
        stems = collections.Counter()
        edge_stems, node_stems = {}, {}
        path_canons = [[canon(k) for k in p] for p in paths]
        for p, cs in zip(paths, path_canons):
            stem = stem_of(cs, a.stem_len)
            stems[stem] += 1
            for c in set(cs):
                node_stems.setdefault(c, set()).add(stem)
            for k, c in zip(p, cs):
                example.setdefault(c, k)
            idx = cs.index(seed_c) if seed_c in cs else None
            seed_found += idx is not None
            seen = set()
            for i, c in enumerate(cs):
                if c in seen:
                    continue
                seen.add(c)
                node_paths[c] += 1
                if idx is not None and i < idx:
                    node_pre[c] += 1
                elif idx is not None and i > idx:
                    node_post[c] += 1
            for i in range(len(cs) - 1):
                f, t = cs[i], cs[i + 1]
                if f == t:
                    loops += 1          # 표기만 바뀐 인접 — 전이가 아니다
                    continue
                if idx is None:
                    pos = "off_seed"
                elif i + 1 <= idx:
                    pos = "pre_seed"
                else:
                    pos = "post_seed"
                edges[(f, t)] += 1
                edge_stems.setdefault((f, t), set()).add(stem)
                edge_pos.setdefault((f, t), collections.Counter())[pos] += 1

        n_paths = len(paths)
        total_edges = sum(edges.values())
        for (f, t), n in edges.most_common():
            fs, _, _ = classify(f)
            ts, _, _ = classify(t)
            edge_rows.append({
                "seed": seed, "from_canonical": f, "to_canonical": t,
                "from_example": example[f], "to_example": example[t],
                "from_stage": fs, "to_stage": ts,
                "position": edge_pos[(f, t)].most_common(1)[0][0],
                "path_count": n,
                "edge_share": round(n / total_edges, 4) if total_edges else "",
                "stem_count": len(edge_stems[(f, t)]),
            })
        for c, n in node_paths.most_common():
            st, facets, is_brand = classify(c)
            node_rows.append({
                "seed": seed, "canonical": c, "example": example[c], "stage": st,
                "stage_facets": "|".join(facets), "is_brand": int(is_brand),
                "paths_with_keyword": n,
                "share_of_paths": round(n / n_paths, 4) if n_paths else "",
                "pre_seed_paths": node_pre[c], "post_seed_paths": node_post[c],
                "stems_with_keyword": len(node_stems.get(c, ())),
            })
        v_only, variants, v_rules = seed_variant_scan(seed_c, path_canons)
        if n_paths and seed_found == 0 and variants and v_only * 2 >= n_paths and not a.allow_seed_variant:
            stop.append((seed, v_only, n_paths, variants, v_rules))
        runs.append({
            "seed": seed, "gl": gl, "time_point": time_point, "limit": limit,
            "returned": len(resp.get("data") or []), "usable": n_paths,
            "seed_found": seed_found, "truncated": truncated,
            "unique_raw": len(raw_unique), "unique_canonical": len(example),
            "measured": measured, "formula": formula, "loops": loops,
            "edges": edges, "total_edges": total_edges, "classify": classify,
            "node_paths": node_paths, "example": example,
            "stems": stems, "edge_stems": {e: len(v) for e, v in edge_stems.items()}, "v_only": v_only, "variants": variants, "v_rules": v_rules,
        })

    if stop:
        for seed, only, n, variants, rules in stop:
            vs = ", ".join(f"«{v}» {k}경로" for v, k in variants.items())
            rj = json.dumps(rules, ensure_ascii=False)
            print(f"✗ 시드 «{seed}»가 사용 경로 {n}개 중 0개에서 발견됐다. 한 글자 다른 표기가 {only}경로에 있다: {vs}.\n"
                  f"  path_finder가 시드를 다른 표기로 돌려준 것으로 보인다. 이대로 집계하면 모든 인접이 off_seed가 된다.\n"
                  f"  단계 사전 rules.변형치환에 {rj} 를 더하고 다시 돌린다(0크레딧). "
                  "다른 검색이 맞다면 --allow-seed-variant로 진행한다.", file=sys.stderr)
        return 3

    cat = a.category
    ecols = ["seed", "from_canonical", "to_canonical", "from_example", "to_example",
             "from_stage", "to_stage", "position", "path_count", "edge_share", "stem_count"]
    ncols = ["seed", "canonical", "example", "stage", "stage_facets", "is_brand",
             "paths_with_keyword", "share_of_paths", "pre_seed_paths", "post_seed_paths",
             "stems_with_keyword"]
    for name, cols, rows in ((f"journey_edges_{cat}.csv", ecols, edge_rows),
                             (f"journey_nodes_{cat}.csv", ncols, node_rows)):
        with open(out / name, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    L = [f"# 검색 여정 요약 — {cat}", ""]
    tps = sorted({str(r["time_point"]) for r in runs if r["time_point"]}) or ["(응답에 없음)"]
    L.append(f"- 조회일: {a.queried_at or '(미기재)'} · 데이터 시점: `time_point` = {', '.join(tps)}"
             " (월 그레인 스냅샷 · 조회일과 다르다) [실제 데이터]")
    L.append("- 이 표는 **세션 인접 검색**의 집계다. 개인 여정·구매·전환이 아니다.")
    L.append("")
    spent_m = sum(r["measured"] for r in runs if isinstance(r["measured"], (int, float)))
    spent_f = sum(r["formula"] for r in runs)
    L.append("## 호출과 크레딧 [실제 데이터: 반환·절단·고유 키워드·실측 크레딧 · 추정: 식 재계산]")
    L.append("")
    L.append("| 시드 | gl | time_point | 요청 limit | 반환 경로 | 사용 경로 | 절단 | 고유 키워드 | 실측 크레딧 | 식 50+40×U |")
    L.append("|---|---|---|---:|---:|---:|---|---:|---:|---:|")
    for r in runs:
        tr = "판정 불가" if r["truncated"] is None else ("예 — 반환분 기준" if r["truncated"] else "아니오")
        mc = r["measured"] if r["measured"] is not None else "(응답에 없음)"
        L.append(f"| {r['seed']} | {r['gl']} | {r['time_point']} | {r['limit'] if r['limit'] is not None else '?'} | "
                 f"{r['returned']} | {r['usable']} | {tr} | {r['unique_raw']} | {mc} | {r['formula']} |")
    L.append("")
    L.append(f"- 이번 분석 path_finder 소진(실측 합): {spent_m} [실제 데이터] · 식 재계산 합: {spent_f} [추정]")
    lims = sorted({r["limit"] for r in runs if r["limit"] is not None})
    if len(lims) > 1:
        L.append(f"- ⚠️ 요청 limit이 시드마다 다르다({', '.join(map(str, lims))}) — 반환 규모가 달라 시드 간 비중·순위를 "
                 "나란히 비교하지 않는다 [가정]")
    mism = [r["seed"] for r in runs if isinstance(r["measured"], (int, float)) and r["measured"] != r["formula"]]
    if mism:
        L.append(f"- ⚠️ 실측과 식이 다른 시드: {', '.join(mism)} — 과금 단위(고유 키워드) 가정을 재확인할 것")
    trunc = [r["seed"] for r in runs if r["truncated"]]
    if trunc:
        L.append(f"- ⚠️ 절단: {', '.join(trunc)} — 반환 경로 수가 limit에 닿았다. 아래 비중은 **반환분 기준**이며 "
                 "이 시드의 경로 전체로 일반화하지 않는다. limit을 올려도 수렴 여부는 비용을 치러야 알 수 있다.")
    for r in runs:
        if r["usable"] and r["seed_found"] == 0:
            vs = ", ".join(f"«{v}» {k}경로" for v, k in r["variants"].items())
            L.append(f"- ⚠️ 시드 «{r['seed']}»가 어느 경로에도 없다 — 이 시드의 인접은 전부 off_seed다"
                     + (f". 한 글자 다른 표기: {vs} · 제안 규칙 {json.dumps(r['v_rules'], ensure_ascii=False)}"
                        if r["variants"] else "") + " [데이터 공백]")
        elif r["v_only"]:
            vs = ", ".join(f"«{v}» {k}경로" for v, k in r["variants"].items())
            L.append(f"- ⚠️ 시드 «{r['seed']}»: 시드 없이 한 글자 다른 표기만 있는 경로 {r['v_only']}개({vs}) — "
                     f"같은 검색이면 변형치환 {json.dumps(r['v_rules'], ensure_ascii=False)}를 더해 다시 집계한다 [가정]")
    if a.plan or a.plan_full:
        plan = list(csv.DictReader(open(a.plan, encoding="utf-8-sig"))) if a.plan else []
        held = [p for p in plan if p.get("decision", "").startswith("hold")]
        if a.plan_full:
            # 좁힌 계획에서 빠진 후보 — 실제로 부른 시드·좁힌 계획에 이미 있는 행은 뺀다
            covered = {canon(s) for s, _, _ in map(parse_run, a.run)}
            covered |= {canon(p["keyword"]) for p in plan}
            covered |= {canon(p.get("requested_keyword") or p["keyword"]) for p in plan}
            for p in csv.DictReader(open(a.plan_full, encoding="utf-8-sig")):
                if canon(p["keyword"]) in covered or canon(p.get("requested_keyword") or p["keyword"]) in covered \
                        or p.get("decision") == "hold_unrequested":      # 요청하지 않은 변형 행은 출발 후보가 아니다
                    continue
                held.append(dict(p, decision=f"excluded_from_plan (전체 계획: {p.get('decision', '')})",
                                 note=("좁힌 계획에서 뺀 후보 — 부르지 않았다. " + (p.get("note") or "")).strip()))
        L.append("")
        L.append("## 보류한 출발 후보 (" + " · ".join(pathlib.Path(x).name for x in (a.plan, a.plan_full) if x) + ")")
        L.append("")
        if not held:
            L.append(f"- 이 계획에는 보류 행이 없다(계획 {len(plan)}행). 후보 일부만 남긴 metrics로 계획을 세웠다면 "
                     "빠진 후보는 여기 보이지 않는다 — 전체 계획을 `--plan-full`로 주거나 "
                     "`path_budget.py --seeds … --seeds-only`로 한 계획에 남긴다")
        else:
            L.append("| 후보 | volume_avg | 판정 | 비고 |")
            L.append("|---|---:|---|---|")
            for p in held:
                vol = p.get("volume_avg") or "—"
                L.append(f"| {p['keyword']} | {vol} | {p['decision']} | {p.get('note', '')} |")
            L.append("")
            L.append("- volume_avg는 [실제 데이터], 판정은 [가정](게이트·예산), 볼륨 없음·합쳐짐·미반환은 [데이터 공백]. "
                     "비수기 조회라면 hold_volume 후보는 계절 탓일 수 있다")
    L.append("")
    for r in runs:
        L.append(f"## {r['seed']}")
        L.append("")
        if r["usable"] == 0:
            L.append("- [데이터 공백] 사용 가능한 경로가 없다 (빈 응답 또는 2단계 미만 경로뿐).")
            L.append("")
            continue
        L.append(f"- 사용 경로 {r['usable']} · 시드가 경로 안에 있는 경로 {r['seed_found']} · "
                 f"표기변형 인접(전이에서 제외) {r['loops']}건 [실제 데이터 · 로컬 집계]")
        (top_stem, top_n), = r["stems"].most_common(1)
        L.append(f"- 공통 줄기(앞 {a.stem_len}걸음, 정규화 표기): 서로 다른 줄기 {len(r['stems'])}개 · "
                 f"가장 큰 줄기 {top_n}/{r['usable']}경로 «{' → '.join(r['example'][c] for c in top_stem)}» "
                 "[실제 데이터 · 로컬 집계]")
        if top_n > 1:
            L.append("- ⚠️ 경로 여러 개가 같은 줄기를 공유하고 끝만 갈라진다 — 인접 수(path_count)는 «세션 빈도»보다 "
                     "«그 줄기 아래 갈래 수»로 커질 수 있다. 순위를 말할 때 CSV의 stem_count(그 인접이 나온 서로 다른 "
                     "줄기 수)를 함께 본다 [추정]")
        cls = r["classify"]
        seed_c = canon(r["seed"])
        stage_paths = collections.Counter()
        for c, n in r["node_paths"].items():
            if c != seed_c:                     # 시드 자신은 매 경로에 나와 분포를 시드 라벨로 끌고 간다
                stage_paths[cls(c)[0]] += n
        L.append("")
        L.append(f"- 시드 «{r['seed']}»(단계: {cls(seed_c)[0]}) 자신은 아래 단계 분포·단계 간 전이에서 뺐다 — "
                 f"출현 {r['node_paths'].get(seed_c, 0)}경로. 시드 전후는 «바로 다음/앞 검색» 표로 본다")
        L.append("")
        L.append("### 단계 분포 (시드 제외 · 키워드 출현 경로 수 합 기준) [추정 · 규칙 분류]")
        L.append("")
        L.append("| 단계 | 출현 합 |")
        L.append("|---|---:|")
        for s, n in stage_paths.most_common():
            L.append(f"| {s} | {n} |")
        mat = collections.Counter()
        seed_edges = 0
        for (f, t), n in r["edges"].items():
            if seed_c in (f, t):
                seed_edges += n
                continue
            mat[(cls(f)[0], cls(t)[0])] += n
        rest = sum(mat.values())
        L.append("")
        L.append(f"### 단계 간 전이 상위 (시드가 낀 인접 {seed_edges}건 제외 · 남은 {rest}건 기준) [추정 · 규칙 분류]")
        L.append("")
        if not rest:
            L.append("- [데이터 공백] 시드가 끼지 않은 인접이 없다")
        else:
            L.append("| from → to | 인접 수 | 비중 |")
            L.append("|---|---:|---:|")
            for (f, t), n in mat.most_common(a.top):
                L.append(f"| {f} → {t} | {n} | {n / rest:.1%} |")
        nxt = [(t, n) for (f, t), n in r["edges"].items() if f == seed_c]
        prv = [(f, n) for (f, t), n in r["edges"].items() if t == seed_c]
        for title, rows in (("시드 바로 다음 검색", nxt), ("시드 바로 앞 검색", prv)):
            L.append("")
            L.append(f"### {title} 상위 [실제 데이터 · 로컬 집계]")
            L.append("")
            if not rows:
                L.append("- [데이터 공백] 해당 방향 인접이 없다")
                continue
            L.append("| 키워드 | 단계 | 인접 수 | 서로 다른 줄기 |")
            L.append("|---|---|---:|---:|")
            for k, n in sorted(rows, key=lambda x: -x[1])[: a.top]:
                key = (seed_c, k) if title == "시드 바로 다음 검색" else (k, seed_c)
                L.append(f"| {r['example'][k]} | {cls(k)[0]} | {n} | {r['edge_stems'][key]} |")
        L.append("")
    L.append("## 라벨")
    L.append("")
    L.append("- [실제 데이터] 경로 시퀀스 · 인접 쌍 수 · cost_detail")
    L.append("- [추정] 단계 분류(단계 사전 규칙) · 단계 분포 · 단계 간 전이")
    L.append("- [가정] 단계 정의와 순서 · 변형치환 규칙 · limit · 볼륨 게이트")
    L.append("- [데이터 공백] 빈 응답 · 시드 미포함 경로 · 방향별 인접 없음")
    L.append("- 공통 줄기 수·stem_count는 [실제 데이터 · 로컬 집계], 줄기 길이(앞 걸음 수)는 [가정]")
    (out / f"journey_summary_{cat}.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    for r in runs:
        tr = "판정 불가" if r["truncated"] is None else ("절단" if r["truncated"] else "미절단")
        print(f"{r['seed']}: 경로 {r['usable']}/{r['returned']} · {tr} · 고유 {r['unique_raw']} · "
              f"실측 {r['measured']} / 식 {r['formula']} · 표기변형 인접 {r['loops']} · "
              f"시드 발견 {r['seed_found']} · 줄기 {len(r['stems'])}")
        if r["usable"] and r["seed_found"] == 0:
            print(f"⚠️ 시드 «{r['seed']}»가 0경로에서 발견됐다 — 인접이 전부 off_seed다. 요약 경고를 볼 것", file=sys.stderr)
    print(f"출력: journey_edges_{cat}.csv · journey_nodes_{cat}.csv · journey_summary_{cat}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
