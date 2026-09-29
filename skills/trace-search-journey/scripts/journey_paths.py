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

    python3 journey_paths.py --run "캠핑의자=resp_camping_chair.json@50" \\
        --stages stages.json --category camping-chair-kr --out-dir out \\
        --queried-at 2026-09-28

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
    a = ap.parse_args()

    stages = load_json_arg(a.stages)
    canon = make_canon(stages.get("rules", {}).get("변형치환", {}))
    classify = make_classifier(stages, canon)
    out = pathlib.Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    edge_rows, node_rows, runs = [], [], []
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
        for p in paths:
            cs = [canon(k) for k in p]
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
            })
        for c, n in node_paths.most_common():
            st, facets, is_brand = classify(c)
            node_rows.append({
                "seed": seed, "canonical": c, "example": example[c], "stage": st,
                "stage_facets": "|".join(facets), "is_brand": int(is_brand),
                "paths_with_keyword": n,
                "share_of_paths": round(n / n_paths, 4) if n_paths else "",
                "pre_seed_paths": node_pre[c], "post_seed_paths": node_post[c],
            })
        runs.append({
            "seed": seed, "gl": gl, "time_point": time_point, "limit": limit,
            "returned": len(resp.get("data") or []), "usable": n_paths,
            "seed_found": seed_found, "truncated": truncated,
            "unique_raw": len(raw_unique), "unique_canonical": len(example),
            "measured": measured, "formula": formula, "loops": loops,
            "edges": edges, "total_edges": total_edges, "classify": classify,
            "node_paths": node_paths, "example": example,
        })

    cat = a.category
    ecols = ["seed", "from_canonical", "to_canonical", "from_example", "to_example",
             "from_stage", "to_stage", "position", "path_count", "edge_share"]
    ncols = ["seed", "canonical", "example", "stage", "stage_facets", "is_brand",
             "paths_with_keyword", "share_of_paths", "pre_seed_paths", "post_seed_paths"]
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
    if a.plan:
        plan = list(csv.DictReader(open(a.plan, encoding="utf-8-sig")))
        held = [p for p in plan if p.get("decision", "").startswith("hold")]
        L.append("")
        L.append("## 보류한 출발 후보 (path_plan.csv)")
        L.append("")
        if not held:
            L.append("- 없음")
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
            L.append("| 키워드 | 단계 | 인접 수 |")
            L.append("|---|---|---:|")
            for k, n in sorted(rows, key=lambda x: -x[1])[: a.top]:
                L.append(f"| {r['example'][k]} | {cls(k)[0]} | {n} |")
        L.append("")
    L.append("## 라벨")
    L.append("")
    L.append("- [실제 데이터] 경로 시퀀스 · 인접 쌍 수 · cost_detail")
    L.append("- [추정] 단계 분류(단계 사전 규칙) · 단계 분포 · 단계 간 전이")
    L.append("- [가정] 단계 정의와 순서 · 변형치환 규칙 · limit · 볼륨 게이트")
    L.append("- [데이터 공백] 빈 응답 · 시드 미포함 경로 · 방향별 인접 없음")
    (out / f"journey_summary_{cat}.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    for r in runs:
        tr = "판정 불가" if r["truncated"] is None else ("절단" if r["truncated"] else "미절단")
        print(f"{r['seed']}: 경로 {r['usable']}/{r['returned']} · {tr} · 고유 {r['unique_raw']} · "
              f"실측 {r['measured']} / 식 {r['formula']} · 표기변형 인접 {r['loops']}")
    print(f"출력: journey_edges_{cat}.csv · journey_nodes_{cat}.csv · journey_summary_{cat}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
