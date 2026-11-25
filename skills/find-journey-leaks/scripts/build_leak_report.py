#!/usr/bin/env python3
"""저장 계산 결과(leak_result_{category}.json) → report.json (이 스킬 고유 · 0크레딧).

숫자는 전부 결과 파일에서 사실 목록(facts)으로 옮기고 문구에는 {{ID}}로만 쓴다. 호출 기록(provenance.calls)은
결과 파일에 실린 호출 원장(calls.jsonl)에서 만든다 — 손으로 적지 않는다. 문구는 규칙 템플릿이며, 에이전트가 해석을
덧붙이려면 --notes 로 넘긴다 — 모든 메모는 «[추정]»으로 시작해야 하고 숫자는 {{ID}}로만 쓴다(렌더러가 검사).

    python3 scripts/build_leak_report.py out/leak_result_running-shoes-kr.json -o report.json [--notes notes.json]
    python3 scripts/render_report.py report.json -o report.html

--notes 형식: {"D1.interpretation": "[추정] …", "A1.hypothesis": "[추정] …"}  (카드 ID.필드 → 문구 교체)
"""
import argparse, json, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from leak_lib import utf8_console  # noqa: E402

GROUP_KO = {"leaking": "경쟁 우세", "contested": "경합", "winning": "내 브랜드 우세", "uncaptured": "브랜드 미도달",
            "brand_reached": "브랜드 도달(내 브랜드 미지정)", "my_node": "내 브랜드 노드", "low_evidence": "근거 부족"}
CLASS_KO = {"seed": "시드", "my_brand": "내 브랜드", "competitor": "경쟁 브랜드", "brand_vs": "브랜드 비교",
            "generic": "일반", "comparison": "비교", "community": "커뮤니티", "channel": "채널", "unmatched": "미분류"}


class Facts:
    def __init__(self, src):
        self.f, self.n, self.k, self.src = {}, 0, 0, src

    def add(self, value, unit, label, calc, src=None):
        self.n += 1
        fid = f"F{self.n}"
        self.f[fid] = {"value": value, "unit": unit, "label": label, "source": src or self.src, "calc": calc}
        return fid

    def ref(self, *a, **k):
        return "{{" + self.add(*a, **k) + "}}"

    def kw(self, text):
        """검색어 표기에 숫자가 있으면(예: 모델 번호) 사실로 등록해 부른다 — 문구에 날숫자가 남지 않게."""
        if re.search(r"\d", str(text)):
            self.k += 1
            fid = f"K{self.k}"
            self.f[fid] = {"value": str(text), "unit": "", "label": "[실제 데이터]", "source": self.src,
                           "calc": "path_finder 응답의 검색어 표기"}
            return "«{{" + fid + "}}»"
        return f"«{text}»"


def pct(v):
    return None if v is None else round(v * 100, 1)


def main():
    utf8_console()
    ap = argparse.ArgumentParser(description="누수 결과 → report.json (0크레딧)")
    ap.add_argument("result", help="journey_leaks.py 가 쓴 leak_result_{category}.json")
    ap.add_argument("-o", "--out", default="report.json")
    ap.add_argument("--notes", help="에이전트 해석 메모 JSON — 값은 «[추정]»으로 시작")
    ap.add_argument("--estimate", type=float, default=None, help="호출 전 견적(leak_plan.csv 누적 계획)")
    a = ap.parse_args()

    R = json.loads(pathlib.Path(a.result).read_text(encoding="utf-8"))
    tot = R["totals"]
    used_ids = R.get("analysis_call_ids") or [c["id"] for c in R["calls"] if c.get("tool") == "path_finder"]
    src = "·".join(used_ids) or "C1"
    F = Facts(src)
    hold = R.get("verdict_hold")
    allrows = [s for s in R["share"] if s["seed"] == "전체"]
    comp_paths = sum(s["paths"] for s in allrows if s["outcome"] == "competitor")
    my_row = next((s for s in allrows if s["outcome"] == "my_brand"), None)
    nb = next(s for s in allrows if s["outcome"] == "no_brand")
    vs_paths = sum(s["paths"] for s in allrows if s["outcome"] == "brand_vs")
    base, branded = nb["base_paths"], nb["branded_paths"]
    trunc_note = "반환분 기준(절단 호출 있음)" if hold else "절단 없음"

    f_base = F.ref(base, "개", "[실제 데이터]", "진행 경로 수 = 시드 뒤에 한 걸음 이상 있는 사용 경로 (off_seed · seed_terminal 제외)")
    f_comp = F.ref(comp_paths, "개", "[추정]", "시드 다음 처음 닿은 브랜드가 경쟁 브랜드인 경로 (규칙 분류)")
    f_comp_all = F.ref(pct(comp_paths / base) if base else None, "%", "[추정]", "경쟁 먼저 ÷ 진행 경로 (no_brand 포함 분모)")
    f_comp_br = F.ref(pct(comp_paths / branded) if branded else None, "%", "[추정]", "경쟁 먼저 ÷ 브랜드 도달 경로 (no_brand 제외 분모)")
    f_nb = F.ref(nb["paths"], "개", "[추정]", "반환 경로 안에서 어떤 등록 브랜드에도 닿지 않은 진행 경로")
    f_nb_all = F.ref(pct(nb["share_all_paths"]), "%", "[추정]", "no_brand ÷ 진행 경로")
    f_vs = F.ref(vs_paths, "개", "[추정]", "처음 닿은 노드가 두 브랜드 이상 비교 검색인 경로")
    if my_row:
        f_my = F.ref(my_row["paths"], "개", "[추정]", "시드 다음 처음 닿은 브랜드가 내 브랜드인 경로")
        f_my_all = F.ref(pct(my_row["share_all_paths"]), "%", "[추정]", "내 브랜드 먼저 ÷ 진행 경로")
        f_my_br = F.ref(pct(my_row["share_branded_paths"]), "%", "[추정]", "내 브랜드 먼저 ÷ 브랜드 도달 경로")
    f_used = F.ref(tot["used_paths"], "개", "[실제 데이터]", "분석에 쓴 호출의 2걸음 이상 경로")
    f_trunc = F.ref(tot["truncated_calls"], "건", "[실제 데이터]", "반환 경로 수 ≥ 요청 limit 인 분석 호출")
    f_off = F.ref(tot["off_seed"], "개", "[데이터 공백]", "시드가 없는 경로 — 분모 제외")
    f_term = F.ref(tot["seed_terminal"], "개", "[데이터 공백]", "시드가 경로 끝 — 진행 없음 · 분모 제외")
    f_loop = F.ref(tot["variant_loops"], "건", "[실제 데이터]", "같은 canonical 끼리 붙은 표기변형 인접 — 엣지에서 제외 (T5)")
    f_unm = F.ref(pct(tot["unmatched_share_of_appearances"]), "%", "[추정]", "진행 경로 노드 등장 중 사전에 안 걸린 비중")
    f_cand = F.ref(len(R["candidates"]), "개", "[추정]", "논브랜드 노드에서 반복되는 미등록 말 (브랜드 후보)")
    f_cost = F.ref(R["cost_total"], "크레딧", "[실제 데이터]", "원장 cost 합 = cost_detail.total_cost 합", src="·".join(c["id"] for c in R["calls"]))

    f_minp = F.ref(R["min_paths"], "개", "[가정]", "허브 판정 최소 경로 수 (--min-paths)", src="leak_dict · 실행 인자")
    f_g1 = F.ref(10.0, "%", "[가정]", "브랜드 미도달 임계 — (내 브랜드 + 경쟁) ÷ 허브 경로", src="journey_leaks.py 규칙")
    f_g2 = F.ref(70.0, "%", "[가정]", "우세 임계 — 한쪽 ÷ 브랜드 도달 경로", src="journey_leaks.py 규칙")
    leak_hubs = [h for h in R["hubs"] if h["group"] in ("leaking", "contested", "brand_reached") and h["competitor_paths"] > 0]
    hub = leak_hubs[0] if leak_hubs else None
    leak_edges = [e for e in R["edges"] if e["edge_type"] in ("leak", "switch")]
    switch = [e for e in R["edges"] if e["edge_type"] == "switch"]
    my = R.get("my_brand")
    seeds_txt = " · ".join(F.kw(s) for s in R["seeds"])

    findings, actions = [], []
    obs1 = (f"진행 경로 {f_base} 중 시드 다음 처음 닿은 브랜드가 경쟁 브랜드인 경로 {f_comp}(진행 경로 대비 {f_comp_all} · "
            f"브랜드 도달 경로 대비 {f_comp_br})")
    if my:
        obs1 += f", 내 브랜드 {f_my}({f_my_all} · {f_my_br})"
    obs1 += f", 어떤 등록 브랜드에도 닿지 않은 경로 {f_nb}({f_nb_all})"
    d1 = {"type": "finding", "id": "D1", "evidence": [x[2:-2] for x in (f_base, f_comp, f_comp_all, f_comp_br, f_nb, f_nb_all)],
          "observed": obs1,
          "interpretation": "카테고리 진입 검색 뒤 첫 브랜드 접점이 어느 쪽에 더 자주 붙는지의 세션 인접 신호다 — 규칙 분류 위의 집계",
          "alternatives": f"브랜드 없이 끝난 경로에 미등록 브랜드가 섞여 있으면(후보 {f_cand}) 경쟁 몫이 과소다. 두 분모 중 하나만 보면 몫이 달라 보인다",
          "limits": f"{trunc_note} · 검색은 구매가 아니고 세션 인접은 개인 여정이 아니다", "actions": ["A1"]}
    findings.append(d1)
    if hub:
        h_paths = F.ref(hub["paths_through"], "개", "[실제 데이터]", f"허브 노드를 지난 진행 경로 (노드 {hub['canonical']})")
        h_comp = F.ref(hub["competitor_paths"], "개", "[추정]", "허브 다음 처음 닿은 브랜드가 경쟁인 경로")
        h_share = F.ref(pct(hub["competitor_share_all"]), "%", "[추정]", "허브 다음 경쟁 먼저 ÷ 허브 경로 (no_brand 포함)")
        h_stem = F.ref(hub["stems_through"], "개", "[실제 데이터]", "허브 경로의 서로 다른 줄기(진행 앞 걸음) 수")
        vol = ""
        if hub.get("volume_lo") is not None:
            h_lo = F.ref(hub["volume_lo"], "건", "[추정]", "keyword_info volume_avg 표기변형 그룹 하한(최댓값)")
            h_hi = F.ref(hub["volume_hi"], "건", "[추정]", "같은 그룹 상한(같은 검색 중복을 뺀 합)")
            vol = f" · 월평균 검색량 하한 {h_lo} ~ 상한 {h_hi}"
        top = F.kw(hub["top_competitor"]) if hub["top_competitor"] else "—"
        findings.append({"type": "finding", "id": "D2", "evidence": [h_paths[2:-2], h_comp[2:-2], h_share[2:-2]],
                         "observed": f"{F.kw(hub['example'])}({CLASS_KO.get(hub['node_class'])}) 를 지난 경로 {h_paths}(줄기 {h_stem}) 중 "
                                     f"다음 첫 브랜드가 경쟁인 경로 {h_comp}({h_share}) · 최다 {top}{vol}",
                         "interpretation": f"논브랜드 허브 중 경쟁 브랜드로 넘어가는 경로가 가장 많은 지점이다 — 그룹 «{GROUP_KO[hub['group']]}»",
                         "alternatives": "같은 줄기에서 갈라진 경로가 겹쳐 세어졌을 수 있다(줄기 수 확인). 허브 문구 자체가 특정 브랜드 상품을 가리킬 수 있다",
                         "limits": (hub["verdict_note"] or "반환분 기준") + " · 최소 경로 기준은 [가정]",
                         "actions": ["A1"]})
    if switch:
        sw = sum(e["path_count"] for e in switch)
        f_sw = F.ref(sw, "개", "[실제 데이터]", "내 브랜드 노드 바로 다음이 경쟁 브랜드인 인접(경로 수 합)")
        e0 = switch[0]
        findings.append({"type": "finding", "id": "D3", "evidence": [f_sw[2:-2]],
                         "observed": f"내 브랜드 검색 바로 다음 경쟁 브랜드 검색이 붙은 인접 {f_sw} · 예: {F.kw(e0['from_example'])} → {F.kw(e0['to_example'])}",
                         "interpretation": "내 브랜드에 닿은 뒤에도 같은 세션에서 경쟁 제품과 비교하는 흐름이 있다",
                         "alternatives": "비교 후 내 브랜드로 돌아오는 경로(win_back)가 반환분 밖에 있을 수 있다",
                         "limits": trunc_note, "actions": ["A2"]})

    a1_target = F.kw(hub["example"]) if hub else seeds_txt
    a1_ev = findings[1]["evidence"] if hub else d1["evidence"]
    a1_basis = "D2" if hub else "D1"   # 판정 조건 칸은 렌더러가 사실을 치환하지 않는다 — 숫자 대신 근거 카드를 가리킨다
    actions.append({"type": "action", "id": "A1", "evidence": a1_ev,
                    "hypothesis": f"[추정] {a1_target} 검색 다음 화면(검색 결과 · 콘텐츠)에 "
                                  f"{('«' + my + '»') if my else '선택 근거'}가 없어 경쟁 브랜드 검색으로 먼저 넘어간다",
                    "deliverable": f"{a1_target} 의도에 맞춘 비교 · 선택 가이드 콘텐츠와 검색 광고 · 랜딩 초안 1건",
                    "owner_role": "검색 콘텐츠 담당 · 브랜드 마케터",
                    "internal_data": "해당 검색어 유입 · 랜딩 체류 · 장바구니 전환(내부 지표 이름만 — 값은 이 리포트에 없다)",
                    "criteria": {"expand": f"같은 시드 · 같은 limit 재수집에서 이 지점의 경쟁 먼저 비중이 이번 값({a1_basis} 근거 사실)보다 내려가면 다음 허브로 넓힌다",
                                 "revise": "비중이 그대로이고 내부 유입만 늘면 콘텐츠 각도(비교 축)를 바꾼다",
                                 "stop": "재수집에서 이 지점을 지나는 경로가 최소 경로 기준 아래로 줄면 멈춘다 — 표본이 판단을 못 버틴다"}})
    if switch:
        actions.append({"type": "action", "id": "A2", "evidence": findings[-1]["evidence"],
                        "hypothesis": "[추정] 내 브랜드 제품 검색자가 같은 세션에서 경쟁 제품과 비교한다 — 비교 정보가 내 쪽에 없다",
                        "deliverable": "내 브랜드 제품 페이지 · 검색 결과에 경쟁 비교표(사양 · 용도) 초안",
                        "owner_role": "브랜드 마케터 · 상품 콘텐츠 담당",
                        "internal_data": "제품 페이지 이탈 · 비교 콘텐츠 클릭(내부 지표 이름만)",
                        "criteria": {"expand": "재수집에서 switch 인접이 줄면 다른 내 브랜드 노드로 넓힌다",
                                     "revise": "switch 는 줄지 않고 win_back 도 없으면 비교 축을 바꾼다",
                                     "stop": "switch 인접이 반환분에서 사라지면 멈춘다(절단이면 사라짐 판정 보류)"}})

    # 메모 덮어쓰기 — [추정]만 받는다
    if a.notes:
        notes = json.loads(pathlib.Path(a.notes).read_text(encoding="utf-8"))
        cards = {c["id"]: c for c in findings + actions}
        for key, text in notes.items():
            cid, _, field = key.partition(".")
            if not str(text).startswith("[추정]"):
                sys.exit(f"메모 {key} 가 «[추정]»으로 시작하지 않는다 — 에이전트 해석은 추정으로 표시한다")
            if cid not in cards or not field:
                sys.exit(f"메모 {key} — 없는 카드 · 필드")
            # 해석 칸은 렌더러가 «해석 [추정]»으로 라벨을 붙인다 — 접두어를 겹치지 않게 뗀다
            cards[cid][field] = text[len("[추정]"):].strip() if field == "interpretation" else text

    # 차트 · 표
    labels, values = [], []
    for s in allrows:
        labels.append({"my_brand": f"{s['brand']}(내 브랜드)", "competitor": s["brand"], "brand_vs": f"{s['brand']}(비교)",
                       "no_brand": "브랜드 없음"}[s["outcome"]] + f" ({s['paths']})")
        values.append(pct(s["share_all_paths"]))
    chart = {"type": "chart", "chart": "bar",
             "meta": {"title": "시드 다음 처음 닿은 브랜드 — 진행 경로 대비",
                      "takeaway": f"경쟁 브랜드 먼저 {f_comp_all} · 브랜드 없음 {f_nb_all} — 브랜드 도달 경로만 분모로 하면 경쟁 몫은 {f_comp_br}",
                      "period": f"time_point {', '.join(R['time_points'])} 스냅샷 · 조회일 {R['queried_at'] or '미기재'}",
                      "unit": "진행 경로 대비 % (막대 이름 괄호 = 경로 수)",
                      "method": "진행 경로마다 시드 다음 처음 닿은 브랜드 노드의 주인 · 분모 = 진행 경로(브랜드 없음 포함)",
                      "source": f"path_finder {src}", "limits": f"{trunc_note} · 규칙 분류 · 세션 인접 집계"},
             "data": {"labels": labels, "values": values, "value_unit": ""}}
    tmeta = lambda t, m: {"period": f"time_point {', '.join(R['time_points'])}", "unit": "경로 수 · %", "method": m,
                          "source": f"path_finder {src}", "limits": trunc_note, "takeaway": t}
    edge_tbl = {"type": "table", "caption": "누수 엣지 상위 — 바로 다음 검색이 경쟁 브랜드",
                "columns": [{"key": "t", "label": "유형"}, {"key": "a", "label": "앞 검색"}, {"key": "ac", "label": "앞 분류"},
                            {"key": "b", "label": "다음 검색"}, {"key": "br", "label": "경쟁 브랜드"},
                            {"key": "n", "label": "경로"}, {"key": "s", "label": "줄기"}],
                "rows": [{"t": e["edge_type"], "a": e["from_example"], "ac": CLASS_KO.get(e["from_class"]), "b": e["to_example"],
                          "br": e["to_brand"], "n": e["path_count"], "s": e["stem_count"]} for e in leak_edges[:12]],
                "meta": tmeta("leak = 논브랜드 → 경쟁, switch = 내 브랜드 → 경쟁", "진행 경로 안 인접 쌍 · 경로마다 한 번 · 표기변형 인접 제외")}
    hub_tbl = {"type": "table", "caption": "허브 노드 점유 — 노드 다음 처음 닿은 브랜드",
               "columns": [{"key": "g", "label": "그룹"}, {"key": "k", "label": "노드"}, {"key": "c", "label": "분류"},
                           {"key": "n", "label": "경로"}, {"key": "my", "label": "내 브랜드 %"}, {"key": "cp", "label": "경쟁 %"},
                           {"key": "nb", "label": "브랜드 없음 %"}, {"key": "tc", "label": "최다 경쟁"},
                           {"key": "lo", "label": "볼륨 하한"}, {"key": "hi", "label": "볼륨 상한"}],
               "rows": [{"g": GROUP_KO[h["group"]], "k": h["example"], "c": CLASS_KO.get(h["node_class"]), "n": h["paths_through"],
                         "my": pct(h["my_share_all"]), "cp": pct(h["competitor_share_all"]), "nb": pct(h["no_brand_share"]),
                         "tc": h["top_competitor"], "lo": h["volume_lo"], "hi": h["volume_hi"],
                         **({"note": "반환분 기준 — 부재 판정 보류"} if "보류" in (h["verdict_note"] or "") else {})}
                        for h in R["hubs"][:15]],
               "meta": tmeta("논브랜드 허브는 두 분모(전체 · 브랜드 도달)로 읽는다 — 여기 %는 허브 경로 전체 대비",
                             "그룹 임계 · 최소 경로는 [가정](값은 부록 정의) · 볼륨은 표기변형 그룹 하한/상한 [추정]")}
    cand_tbl = {"type": "table", "caption": "미등록 브랜드 후보 — 사전 보강용",
                "columns": [{"key": "t", "label": "말"}, {"key": "n", "label": "노드 수"}, {"key": "l", "label": "맨 앞"},
                            {"key": "e", "label": "예"}],
                "rows": [{"t": c["token"], "n": c["nodes"], "l": c["leading_nodes"], "e": c["examples"]} for c in R["candidates"][:15]],
                "meta": tmeta("브랜드면 경쟁 목록에 넣고 0크레딧으로 다시 집계한다", "논브랜드 노드 토큰 중 2노드 이상 · 1번 이상 맨 앞")}
    cls_tbl = {"type": "table", "caption": "노드 분류 분포 — 진행 경로 등장 수",
               "columns": [{"key": "c", "label": "분류"}, {"key": "n", "label": "등장"}],
               "rows": [{"c": CLASS_KO.get(k, k), "n": v} for k, v in sorted(R["class_distribution"].items(), key=lambda x: -x[1])],
               "meta": tmeta("미분류가 크면 카테고리어를 보강한다", "규칙 사전 — 브랜드 → 채널 → 커뮤니티 → 비교 → 일반 순")}

    counter = (f"<b>반대 근거</b> — 절단 호출 {f_trunc}: 반환분 밖 경로는 보지 못했다. 브랜드 없음 {f_nb}과 미분류 비중 {f_unm}, "
               f"미등록 브랜드 후보 {f_cand}는 경쟁 몫을 과소로 만들 수 있다. 브랜드 비교 검색으로 먼저 간 경로 {f_vs}는 어느 쪽에도 귀속하지 않았다.")
    am = R.get("alias_misses") or []
    if am:
        f_am = F.ref(len(am), "개", "[추정]", "선언 브랜드 별칭이 글자로 들어 있는데 그 브랜드로 분류되지 않은 노드 (별칭 누락 의심)")
        ex_am = ", ".join(f"{m['example']} ⊃ {m['alias']}({m['brand']})" for m in am[:5])
        counter += (f" 별칭 누락 의심 노드 {f_am}({ex_am}{' 외' if len(am) > 5 else ''})는 선언 브랜드 별칭이 글자로 들어 있는데 "
                    "그 브랜드로 분류되지 않았다 — 브랜드 검색이면 경쟁 몫이 과소이고 허브 «미확보»가 거짓일 수 있다.")
    boundary = (f"{seeds_txt} 진입 검색 뒤 세션 인접 경로 {f_used}(반환분)에서 처음 닿은 브랜드를 셌다 — "
                "개인 여정 · 구매 · 며칠 뒤 행동 · limit 밖 경로 · 시점 변화는 미관측")
    if R.get("brand_seed"):
        boundary += " · 브랜드 출발 표본(카테고리 진입 경로 아님)"
    sections = [
        {"kind": "summary", "title": "경쟁 브랜드가 먼저 잡는 몫과 브랜드 없이 끝나는 몫을 함께 본다",
         "blocks": findings + [{"type": "paragraph", "html": counter}]},
        {"kind": "structure", "title": "첫 브랜드 접점 · 누수 엣지 · 허브 점유",
         "blocks": [chart, edge_tbl, hub_tbl]},
        {"kind": "action", "title": "경쟁으로 넘어가는 허브부터 방어 · 공략 가설을 검증한다", "blocks": actions},
        {"kind": "appendix", "title": "분류 · 정의 · 후보",
         "blocks": [{"type": "paragraph", "html": (
             "정의 — 진행 경로: 시드가 처음 나온 자리부터 뒤(앞은 유입 · 누수에 넣지 않음). 귀속: 시드 다음 처음 닿은 브랜드 노드. "
             f"분모에서 뺀 경로: off_seed {f_off} · seed_terminal {f_term}. 표기변형 인접 {f_loop}은 엣지에서 뺐다. "
             f"허브 그룹 임계 [가정] — 최소 경로 {f_minp}, 브랜드 도달 비중이 {f_g1} 미만이면 «브랜드 미도달», "
             f"브랜드 도달 경로 중 한쪽이 {f_g2} 이상이면 «우세», 아니면 «경합». 이번 분석 소진 {f_cost}.")}, cls_tbl, cand_tbl]},
    ]
    vol_calls = [c for c in R["calls"] if c.get("tool") == "keyword_info"]
    missing = sum(c.get("missing") or 0 for c in vol_calls)
    report = {
        "meta": {"title": f"검색 경로 누수 — {R['category']}", "subtitle": f"{'내 브랜드 ' + my if my else '경쟁 지형'} · 경쟁 {', '.join(R['competitors'])}",
                 "skill": "find-journey-leaks", "version": "0.1.1"},
        "boundary": boundary,
        "lede_html": (f"진행 경로 {f_base} 중 경쟁 브랜드에 먼저 닿은 경로는 {f_comp_all}(브랜드 도달 경로 대비 {f_comp_br}), "
                      + (f"내 브랜드는 {f_my_all}, " if my else "") + f"브랜드 없이 끝난 경로는 {f_nb_all}다. {trunc_note}."),
        "scope": {"collected_at": R["queried_at"] or "미기재", "latest_month": f"월 미표기 — path_finder time_point {', '.join(R['time_points'])} 스냅샷",
                  "gl": R["gl"], "selection": f"시드 {', '.join(R['seeds'])} · path_finder limit {', '.join(map(str, tot['limits']))} 반환 경로 표본",
                  "missing": f"off_seed · seed_terminal 경로는 분모 제외 · 볼륨 미반환 키워드 {missing}개(데이터 공백)",
                  "excluded": "표기변형 인접(같은 canonical) · 대체된 파일럿 호출(superseded)",
                  "windows": {"경로 시점": ", ".join(R["time_points"]), "비교": "없음 — 시점 비교는 0.1 범위 밖"}},
        "facts": F.f,
        "provenance": {"calls": [{"id": c["id"], "step": c.get("step") or c.get("role"), "at": c.get("at"), "tool": c["tool"],
                                  "params": c.get("params") or "", "purpose": c.get("purpose") or c.get("role"),
                                  "returned": c.get("returned"), "cost": c.get("cost") or 0, "raw": c.get("raw") or "-",
                                  "data_through": None, "status": c.get("status") or "ok",
                                  **({"reused_from": c["reused_from"]} if c.get("reused_from") else {})} for c in R["calls"]],
                       "currency": "크레딧", **({"estimate": a.estimate} if a.estimate is not None else {})},
        "sections": sections,
    }
    pathlib.Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"report.json → {a.out} · 사실 {len(F.f)} · 발견 {len(findings)} · 실행 {len(actions)} · 호출 {len(R['calls'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
