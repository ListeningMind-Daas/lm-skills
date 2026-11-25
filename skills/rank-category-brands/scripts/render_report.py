#!/usr/bin/env python3
# shared-asset: lm-skills/shared — 이 파일은 사본이다. 고칠 때는 정본을 고치고 재배포할 것.
"""공통 리포트 렌더러 — report.json → 단독 HTML. 렌더링 계약(references/report-contract.md)을 검사한다.

스킬은 «무엇을 넣을지»를 report.json 으로 정하고, 이 스크립트는 «어떻게 보여줄지»와 계약 위반을 맡는다.
값은 만들지 않는다 — 차트·표·문구의 숫자는 모두 입력(스킬 스크립트의 저장 계산 결과)에서 온다.

    python3 scripts/render_report.py report.json -o report.html   # + report.calls.csv · report.cost.json
    python3 scripts/render_report.py report.json --check        # 검사만 (HTML 안 씀)
    python3 scripts/render_report.py --schema                    # 입력 형식

exit 0 = 렌더 · 1 = 계약 위반(목록 출력) · 2 = 입력 오류. stdlib 전용 · 외부 스크립트·폰트 없음.
"""
from __future__ import annotations

import argparse, html, json, math, pathlib, re, sys

SPINE = ["summary", "scope", "trend", "structure", "action", "appendix"]
REQUIRED_KINDS = {"summary", "appendix"}              # scope 는 입력 `scope` 로 렌더러가 만든다
KIND_LABEL = {"summary": "분석 요약", "scope": "데이터 범위", "trend": "트렌드 분석",
              "structure": "관심 구조", "action": "기회와 실행", "appendix": "근거 부록"}
SCOPE_FIELDS = ["collected_at", "latest_month", "gl", "selection", "missing", "excluded", "windows"]
SCOPE_LABEL = {"collected_at": "수집일", "latest_month": "최신 관측 월", "gl": "국가", "selection": "검색어 선정",
               "missing": "누락", "excluded": "제외", "windows": "비교 기간"}
CHART_META = ["title", "takeaway", "period", "unit", "method", "source", "limits"]
META_LABEL = {"period": "기간", "unit": "단위", "method": "계산법", "source": "출처", "limits": "한계"}
FINDING = ["id", "evidence", "observed", "interpretation", "alternatives", "limits"]
ACTION = ["id", "evidence", "hypothesis", "deliverable", "owner_role", "internal_data", "criteria"]
CRITERIA = {"expand": "확대", "revise": "수정", "stop": "중단"}
BLOCK_TAGS = re.compile(r"<\s*(p|div|table|section|ul|ol|h[1-6]|figure|details)\b", re.I)
# 문구 숫자 — 콤마·소수·%·배가 붙었거나 100 이상이면 사실 목록을 거쳐야 한다. 연도·월·작은 정수는 허용.
NUM = re.compile(r"(?<![\w.{])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+)(\s*(?:%p|%|배|건|개|명|원)(?![가-힣]))?")
ALLOWED_INT = re.compile(r"^(?:19|20)\d\d$")
CALL_FIELDS = ["id", "tool", "params", "cost", "raw", "status"]
CALL_COLS = [("id", "ID"), ("step", "단계"), ("at", "시각"), ("tool", "도구"), ("params", "호출 조건"),
             ("returned", "반환 수"), ("cost", "비용"), ("data_through", "데이터 마지막 달"), ("raw", "원본 파일"), ("status", "상태")]
WARN_PHRASES = ["통계적으로 유의", "유의미한 차이", "시장 규모는", "시장 점유율", "구매 요인 1위", "성장 등급"]

SCHEMA = """report.json
{
  "meta": {"title": "…", "subtitle": "…", "skill": "스킬 이름", "version": "0.1"},
  "boundary": "과거 가을 패턴과 최근 여름 변화이며 올해 가을은 미관측",          ← 필수 · 첫 화면
  "lede_html": "짧은 요약 (인라인 HTML)",                                          ← 필수
  "scope": {"collected_at": "2026-10-12", "latest_month": "2026-08", "gl": "kr",
            "selection": "…", "missing": "…", "excluded": "…",
            "windows": {"base": "2025-06~08", "compare": "2026-06~08"}},           ← 필수 7필드
  "facts": {"F1": {"value": 10837, "unit": "건", "label": "[실제 데이터]", "source": "raw/…", "calc": "…"}},
  "provenance": {"calls": [{"id": "C1", "step": "확장", "at": "2026-10-12 14:03", "tool": "intent_finder",
                  "params": "seed=보습크림 · limit 50", "purpose": "연관 검색어로 후보 넓히기", "returned": 27,
                  "cost": 84, "raw": "raw/intent_보습크림.json", "data_through": null, "status": "ok"}],
                 "estimate": 500, "monthly_used": 12345, "currency": "크레딧"},          ← 필수 · 호출 순서 · 로그 · 비용 보고서
     저장본만 썼으면 호출마다 "cost": 0 · "reused_from": "원래 호출 ID 또는 파일" · 사실의 source 는 "C1" 처럼 호출 ID 로 쓸 수 있다
  "sections": [ {"kind": "summary|trend|structure|action|appendix", "title": "결론을 말하는 헤딩", "blocks": [ … ]} ]
}
블록 — 문단의 숫자는 {{F1}} 로만 쓴다
  {"type": "paragraph", "html": "인라인 HTML · {{F1}}"}
  {"type": "table", "caption": "…", "columns": [{"key": "q", "label": "검색어", "role": "base|compare|abs_change|pct_change|"}],
   "rows": [{"q": "…", "b": 1, "note": "작은 기준값"}], "meta": {period, unit, method, source, limits}}
  {"type": "chart", "chart": "line|bar|heatmap|scatter", "meta": {title, takeaway, period, unit, method, source, limits}, "data": {…}}
     line    {"x": [...], "series": [{"name": "…", "values": [... null 허용]}], "normalized": false, "raw": 표(선택)}
     bar     {"labels": [...], "values": [...], "abs": [...](증감률 막대면 필수), "value_unit": "%"}
     heatmap {"rows": [...], "cols": [...], "values": [[지수]], "raw": [[절대량]]}
     scatter {"points": [{"label", "x", "y", "size"}], "x_label", "y_label", "size_label"}
  {"type": "finding", "id": "D1", "evidence": ["F1"], "observed", "interpretation", "alternatives", "limits", "actions": ["A1"]}
  {"type": "action", "id": "A1", "evidence": ["F1"], "hypothesis", "deliverable", "owner_role", "internal_data",
   "criteria": {"expand", "revise", "stop"}}
  {"type": "details", "summary": "…", "blocks": [ … ]}
"""


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def fmt(v, unit=""):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int) or (isinstance(v, float) and v.is_integer() and abs(v) >= 100):
        s = f"{int(v):,}"
    elif isinstance(v, float):
        s = f"{v:,.2f}".rstrip("0").rstrip(".") if abs(v) < 10 else f"{v:,.1f}"
    else:
        return esc(v)
    return s + (unit or "")


# ───────────────────────── 검사
class Check:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def warn(self, where, msg):
        self.warnings.append(f"{where}: {msg}")


def prose_numbers(text: str):
    """사실 목록을 거치지 않은 숫자 — {{F1}} 와 태그 속성은 빼고 본다."""
    t = re.sub(r"\{\{\s*\w+\s*\}\}", " ", text)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\b(?:19|20)\d\d-\d\d\b", " ", t)                      # 2026-08 같은 월 표기
    bad = []
    for m in NUM.finditer(t):
        n, suf = m.group(1), (m.group(2) or "").strip()
        plain = n.replace(",", "")
        if "," in n or "." in n or suf in ("%", "%p", "배"):
            bad.append(m.group(0).strip())
        elif ALLOWED_INT.match(plain) or int(plain) <= 12:
            continue
        elif int(plain) >= 100 or suf:
            bad.append(m.group(0).strip())
    return bad


def check_text(c: Check, where, text, facts):
    if text is None:
        return
    for ref in re.findall(r"\{\{\s*(\w+)\s*\}\}", str(text)):
        if ref not in facts:
            c.err(where, f"없는 사실 ID {{{{{ref}}}}}")
    bad = prose_numbers(str(text))
    if bad:
        c.err(where, f"사실 목록을 거치지 않은 숫자 {bad[:4]} — facts 에 등록하고 {{{{ID}}}} 로 부를 것")
    if BLOCK_TAGS.search(str(text)):
        c.err(where, "문단 안에 블록 태그(<p>·<div>·<table> 등) — 인라인 HTML만 쓴다")
    for ph in WARN_PHRASES:
        if ph in str(text):
            c.warn(where, f"금지 표현 후보 «{ph}» — 근거가 없으면 지운다(계약 §3-7)")


def check_meta(c: Check, where, meta, facts=None):
    meta = meta or {}
    miss = [k for k in CHART_META if not str(meta.get(k) or "").strip()]
    if miss:
        c.err(where, f"차트·표 메타 누락 {miss} (계약 §4)")
    for k in CHART_META:
        if k != "takeaway":                                  # takeaway 는 호출한 쪽이 따로 본다
            check_text(c, f"{where} · meta.{k}", meta.get(k), facts or {})


def check_block(c: Check, where, b, facts, ids):
    t = b.get("type")
    if t == "paragraph":
        check_text(c, where, b.get("html"), facts)
    elif t == "table":
        roles = {col.get("role") for col in b.get("columns", []) if col.get("role")}
        if "pct_change" in roles and not {"base", "compare", "abs_change"} <= roles:
            c.err(where, f"증감률 열만 있다 — base · compare · abs_change 열을 함께 둔다(계약 §3-1) · 있는 열 {sorted(roles)}")
        if b.get("meta"):
            check_meta(c, where, {**{"title": b.get("caption"), "takeaway": b.get("meta", {}).get("takeaway", "-")}, **b["meta"]}, facts)
        for r in b.get("rows", []):
            check_text(c, where + " · note", r.get("note"), facts)
    elif t == "chart":
        check_meta(c, where, b.get("meta"), facts)
        check_text(c, where + " · takeaway", (b.get("meta") or {}).get("takeaway"), facts)
        d, kind = b.get("data") or {}, b.get("chart")
        if kind == "line":
            n = len(d.get("x") or [])
            for s in d.get("series") or []:
                if len(s.get("values") or []) != n:
                    c.err(where, f"계열 «{s.get('name')}» 길이가 x 와 다르다 — 같은 기간으로 맞춘다")
        elif kind == "bar":
            if d.get("value_unit") in ("%", "%p") and not d.get("abs"):
                c.err(where, "증감률 막대에 절대 변화량(abs)이 없다(계약 §4)")
        elif kind == "heatmap":
            if not d.get("raw"):
                c.err(where, "히트맵에 절대 검색량(raw)이 없다 — 정규화 지수만으로 판단하지 않는다(계약 §3-5)")
        elif kind == "scatter":
            if not d.get("size_label") or not d.get("x_label") or not d.get("y_label"):
                c.err(where, "산점도에 x_label · y_label · size_label(크기 범례)이 모두 있어야 한다")
        else:
            c.err(where, f"알 수 없는 차트 유형 {kind!r}")
    elif t == "finding":
        miss = [k for k in FINDING if not b.get(k)]
        if miss:
            c.err(where, f"발견 카드 필드 누락 {miss} (계약 §5)")
        for e in b.get("evidence") or []:
            if e not in facts:
                c.err(where, f"근거 사실 ID {e} 가 facts 에 없다")
        for k in ("observed", "interpretation", "alternatives", "limits"):
            check_text(c, f"{where} · {k}", b.get(k), facts)
    elif t == "action":
        miss = [k for k in ACTION if not b.get(k)]
        crit = b.get("criteria") or {}
        miss += [f"criteria.{k}" for k in CRITERIA if not crit.get(k)]
        if miss:
            c.err(where, f"실행 카드 필드 누락 {miss} (계약 §5)")
        for e in b.get("evidence") or []:
            if e not in facts:
                c.err(where, f"근거 사실 ID {e} 가 facts 에 없다")
        for k in ("hypothesis", "deliverable", "owner_role", "internal_data"):
            check_text(c, f"{where} · {k}", b.get(k), facts)
        for k in CRITERIA:
            check_text(c, f"{where} · criteria.{k}", crit.get(k), facts)
    elif t == "details":
        for i, x in enumerate(b.get("blocks") or []):
            check_block(c, f"{where} › {i}", x, facts, ids)
    else:
        c.err(where, f"알 수 없는 블록 유형 {t!r}")


def collect_ids(blocks, out):
    for b in blocks or []:
        if b.get("type") in ("finding", "action") and b.get("id"):
            out.setdefault(b["type"], set()).add(b["id"])
        if b.get("type") == "details":
            collect_ids(b.get("blocks"), out)


def validate(r: dict) -> Check:
    c = Check()
    facts = r.get("facts") or {}
    if not str(r.get("boundary") or "").strip():
        c.err("boundary", "첫 화면 경계 문장이 없다 — 무엇을 관측했고 무엇을 아직 못 했는지(계약 §2)")
    if not str(r.get("lede_html") or "").strip():
        c.err("lede_html", "요약이 없다")
    check_text(c, "boundary", r.get("boundary"), facts)
    check_text(c, "lede_html", r.get("lede_html"), facts)
    sc = r.get("scope") or {}
    miss = [k for k in SCOPE_FIELDS if not sc.get(k)]
    if miss:
        c.err("scope", f"데이터 범위 필드 누락 {miss} (계약 §2)")
    for fid, f in facts.items():
        if not isinstance(f, dict) or "value" not in f or not f.get("source") or not f.get("label"):
            c.err(f"facts.{fid}", "value · label(4-Label) · source 가 있어야 한다")
    pv = r.get("provenance") or {}
    calls = pv.get("calls")
    if not isinstance(calls, list) or not calls:
        c.err("provenance", "호출 기록(provenance.calls)이 없다 — 산출물마다 호출 순서 · 로그 · 비용 보고서를 낸다(계약 §9). "
              "저장본만 썼으면 그 저장본을 만든 호출을 cost 0 · reused_from 으로 적는다")
        calls = []
    seen = set()
    for i, cl in enumerate(calls):
        miss = [k for k in CALL_FIELDS if cl.get(k) in (None, "")]
        if miss:
            c.err(f"provenance.calls[{i}]", f"필드 누락 {miss}")
        if cl.get("id") in seen:
            c.err(f"provenance.calls[{i}]", f"호출 ID {cl.get('id')} 중복")
        seen.add(cl.get("id"))
        cost = cl.get("cost")
        if cost is not None and (not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0):
            c.err(f"provenance.calls[{i}]", f"비용은 0 이상의 숫자 — {cost!r}")
        if cost == 0 and not cl.get("reused_from") and cl.get("status") == "ok":
            c.warn(f"provenance.calls[{i}]", "비용 0 인 호출 — 저장본 재사용이면 reused_from 을 적는다")
    for fid, f in facts.items():
        src = str((f or {}).get("source") or "")
        for ref in re.findall(r"\bC\d+\b", src):
            if calls and ref not in seen:
                c.err(f"facts.{fid}", f"출처 호출 {ref} 가 provenance.calls 에 없다")
    secs = r.get("sections") or []
    kinds = [s.get("kind") for s in secs]
    for k in kinds:
        if k not in SPINE or k == "scope":
            c.err("sections", f"쓸 수 없는 kind {k!r} — {', '.join(x for x in SPINE if x != 'scope')} 중 하나(scope 는 자동)")
    order = [SPINE.index(k) for k in kinds if k in SPINE]
    if order != sorted(order):
        c.err("sections", f"뼈대 순서 위반 {kinds} — 요약 → 범위 → 트렌드 → 관심 구조 → 기회·실행 → 부록(계약 §1)")
    for k in REQUIRED_KINDS - set(kinds):
        c.err("sections", f"필수 섹션 {KIND_LABEL[k]}({k}) 없음")
    ids = {}
    for s in secs:
        collect_ids(s.get("blocks"), ids)
    for si, s in enumerate(secs):
        check_text(c, f"sections[{si}].title", s.get("title"), facts)
        for bi, b in enumerate(s.get("blocks") or []):
            check_block(c, f"sections[{si}]({s.get('kind')}).blocks[{bi}]", b, facts, ids)
            for a in (b.get("actions") or []) if b.get("type") == "finding" else []:
                if a not in ids.get("action", set()):
                    c.err(f"sections[{si}].blocks[{bi}]", f"연결된 실행 카드 {a} 가 없다")
    return c


# ───────────────────────── 렌더
def sub_facts(text, facts):
    def rep(m):
        f = facts.get(m.group(1)) or {}
        lab = esc(f.get("label", ""))
        return (f'<span class="fact" title="{lab} · {esc(f.get("source", ""))}">'
                f'{fmt(f.get("value"), f.get("unit", ""))}</span>')
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", rep, text or "")


def meta_html(m):
    rows = "".join(f"<div><dt>{META_LABEL[k]}</dt><dd>{esc(m.get(k))}</dd></div>" for k in META_LABEL if m.get(k))
    return f'<dl class="meta">{rows}</dl>'


def table_html(b, facts):
    cols = b.get("columns") or []
    th = "".join(f'<th scope="col"{" class=num" if c.get("num", c.get("role")) else ""}>{esc(c.get("label", c.get("key")))}</th>' for c in cols)
    has_note = any(r.get("note") for r in b.get("rows") or [])
    if has_note:
        th += '<th scope="col">비고</th>'
    body = []
    for r in b.get("rows") or []:
        tds = []
        for c in cols:
            v = r.get(c["key"])
            num = c.get("num", c.get("role"))
            unit = "%" if c.get("role") == "pct_change" else c.get("unit", "")
            txt = fmt(v, unit) if isinstance(v, (int, float)) or v is None else esc(v)
            if c.get("role") in ("abs_change", "pct_change") and isinstance(v, (int, float)) and v > 0:
                txt = "+" + txt
            tds.append(f'<td{" class=num" if num else ""}>{txt}</td>')
        if has_note:
            tds.append(f'<td class="note">{sub_facts(r.get("note"), facts)}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    cap = f"<caption>{esc(b.get('caption'))}</caption>" if b.get("caption") else ""
    meta = meta_html(b["meta"]) if b.get("meta") else ""
    return f'<div class="tbl wide">{meta}<table>{cap}<thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def nice_max(v):
    if v <= 0:
        return 1
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


def svg_line(d):
    x, series = d.get("x") or [], d.get("series") or []
    vals = [v for s in series for v in s.get("values") or [] if v is not None]
    if len(x) < 2 or not vals:
        return '<p class="note">계열이 부족해 차트를 그리지 않는다 — 표로 대체</p>'
    W, H, L, R, T, B = 860, 300, 60, 140, 16, 40
    lo = min(0, min(vals)); hi = nice_max(max(vals))
    sx = lambda i: L + i * (W - L - R) / (len(x) - 1)
    sy = lambda v: T + (H - T - B) * (1 - (v - lo) / ((hi - lo) or 1))
    out = []
    for f in (0, .25, .5, .75, 1):
        v = lo + (hi - lo) * f
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="grid"/>'
                   f'<text x="{L - 6}" y="{sy(v) + 4:.1f}" class="tick" text-anchor="end">{fmt(round(v, 2))}</text>')
    step = max(1, len(x) // 8)
    for i in range(0, len(x), step):
        out.append(f'<text x="{sx(i):.1f}" y="{H - B + 18}" class="tick" text-anchor="middle">{esc(x[i])}</text>')
    for k, s in enumerate(series):
        segs, cur = [], []
        for i, v in enumerate(s.get("values") or []):
            if v is None:
                if cur: segs.append(cur); cur = []
            else:
                cur.append(f"{sx(i):.1f},{sy(v):.1f}")
        if cur: segs.append(cur)
        for sg in segs:
            out.append(f'<polyline points="{" ".join(sg)}" class="ln s{k % 6}"/>')
        last = [(i, v) for i, v in enumerate(s.get("values") or []) if v is not None]
        if last:
            i, v = last[-1]
            out.append(f'<text x="{sx(i) + 6:.1f}" y="{sy(v) + 4:.1f}" class="lab s{k % 6}">{esc(s.get("name"))} {fmt(v)}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">{"".join(out)}</svg>'


def svg_bar(d):
    labels, vals, absv = d.get("labels") or [], d.get("values") or [], d.get("abs") or []
    unit = d.get("value_unit", "")
    nums = [v for v in vals if v is not None]
    if not nums:
        return '<p class="note">값이 없어 차트를 그리지 않는다</p>'
    W, rowh, L = 860, 30, 220
    m = max(abs(v) for v in nums) or 1
    span = (W - L - 160) / (2 if min(nums) < 0 < max(nums) else 1)
    zero = L + span if min(nums) < 0 < max(nums) else (L + (W - L - 160) if max(nums) <= 0 else L)
    out = []
    for i, (lab, v) in enumerate(zip(labels, vals)):
        y = 10 + i * rowh
        short = lab if len(str(lab)) <= 16 else str(lab)[:15] + "…"
        out.append(f'<text x="{L - 8}" y="{y + 17}" class="tick" text-anchor="end"><title>{esc(lab)}</title>{esc(short)}</text>')
        if v is None:
            out.append(f'<text x="{W - 150}" y="{y + 17}" class="tick">— 결측</text>'); continue
        w = abs(v) / m * span
        x0 = zero if v >= 0 else zero - w
        a = f" · 절대 {'+' if (absv[i] or 0) > 0 else ''}{fmt(absv[i])}" if i < len(absv) and absv[i] is not None else ""
        out.append(f'<rect x="{x0:.1f}" y="{y + 4}" width="{max(w, 1):.1f}" height="{rowh - 10}" class="bar{" neg" if v < 0 else ""}"/>'
                   f'<text x="{W - 150}" y="{y + 17}" class="lab">'
                   f'{"+" if v > 0 and unit in ("%", "%p") else ""}{fmt(v)}{esc(unit)}{a}</text>')
    out.append(f'<line x1="{zero:.1f}" x2="{zero:.1f}" y1="6" y2="{10 + len(labels) * rowh}" class="axis"/>')
    return f'<svg viewBox="0 0 {W} {20 + len(labels) * rowh}" class="chart" role="img">{"".join(out)}</svg>'


def heatmap(d):
    rows, cols, vals, raw = d.get("rows") or [], d.get("cols") or [], d.get("values") or [], d.get("raw") or []
    flat = [v for r in vals for v in r if v is not None]
    lo, hi = (min(flat), max(flat)) if flat else (0, 1)
    head = "".join(f'<th scope="col">{esc(c)}</th>' for c in cols)
    body = []
    for i, r in enumerate(rows):
        tds = []
        for j, _ in enumerate(cols):
            v = vals[i][j] if i < len(vals) and j < len(vals[i]) else None
            rv = raw[i][j] if i < len(raw) and j < len(raw[i]) else None
            if v is None:
                tds.append('<td class="hm na">—</td>'); continue
            a = (v - lo) / ((hi - lo) or 1)
            tds.append(f'<td class="hm" style="--a:{a:.2f}"><b>{fmt(v)}</b><small>{fmt(rv)}</small></td>')
        body.append(f'<tr><th scope="row">{esc(r)}</th>{"".join(tds)}</tr>')
    return (f'<div class="tbl"><table class="heat"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
            f'<p class="note">칸의 큰 숫자 = 정규화 지수 · 작은 숫자 = 절대 검색량. 색은 지수의 높낮이를 보조한다.</p></div>')


def svg_scatter(d):
    pts = [p for p in d.get("points") or [] if p.get("x") is not None and p.get("y") is not None]
    if not pts:
        return '<p class="note">점이 없어 차트를 그리지 않는다</p>'
    W, H, L, R, T, B = 860, 380, 70, 180, 20, 50
    xs, ys = [p["x"] for p in pts], [p["y"] for p in pts]
    x0, x1 = min(xs + [0]), max(xs) or 1; y0, y1 = min(ys + [0]), max(ys) or 1
    pad = lambda a, b: ((b - a) * 0.08 or 1)
    x0, x1, y0, y1 = x0 - pad(x0, x1), x1 + pad(x0, x1), y0 - pad(y0, y1), y1 + pad(y0, y1)
    sx = lambda v: L + (v - x0) / (x1 - x0) * (W - L - R)
    sy = lambda v: T + (1 - (v - y0) / (y1 - y0)) * (H - T - B)
    smax = max((p.get("size") or 0) for p in pts) or 1
    rad = lambda s: 4 + 22 * math.sqrt(max(s or 0, 0) / smax)          # 면적 ∝ 값
    out = [f'<line x1="{L}" x2="{W - R}" y1="{sy(0):.1f}" y2="{sy(0):.1f}" class="axis"/>' if y0 < 0 < y1 else "",
           f'<line x1="{sx(0):.1f}" x2="{sx(0):.1f}" y1="{T}" y2="{H - B}" class="axis"/>' if x0 < 0 < x1 else "",
           f'<text x="{(L + W - R) / 2}" y="{H - 10}" class="tick" text-anchor="middle">{esc(d.get("x_label"))}</text>',
           f'<text x="16" y="{(T + H - B) / 2}" class="tick" text-anchor="middle" transform="rotate(-90 16 {(T + H - B) / 2})">{esc(d.get("y_label"))}</text>']
    for p in sorted(pts, key=lambda p: -(p.get("size") or 0)):
        out.append(f'<circle cx="{sx(p["x"]):.1f}" cy="{sy(p["y"]):.1f}" r="{rad(p.get("size")):.1f}" class="pt"/>'
                   f'<text x="{sx(p["x"]):.1f}" y="{sy(p["y"]) - rad(p.get("size")) - 3:.1f}" class="lab" text-anchor="middle">{esc(p.get("label"))}</text>')
    lx, ly = W - R + 30, T + 20
    out.append(f'<text x="{lx}" y="{ly}" class="tick">{esc(d.get("size_label"))}</text>')
    for k, f in enumerate((1, .25)):
        r = rad(smax * f); cy = ly + 20 + k * 55 + r
        out.append(f'<circle cx="{lx + 26}" cy="{cy:.1f}" r="{r:.1f}" class="pt legend"/>'
                   f'<text x="{lx + 56}" y="{cy + 4:.1f}" class="tick">{fmt(round(smax * f))}</text>')
    return f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">{"".join(out)}</svg>'


def chart_html(b, facts):
    m, d, kind = b.get("meta") or {}, b.get("data") or {}, b.get("chart")
    body = {"line": svg_line, "bar": svg_bar, "heatmap": heatmap, "scatter": svg_scatter}.get(kind, lambda _: "")(d)
    body = body.replace('role="img">', f'role="img" aria-label="{esc(m.get("title"))} — {esc(re.sub(r"<[^>]+>|\{\{\s*\w+\s*\}\}", "", m.get("takeaway") or ""))}">', 1)
    extra = ""
    if kind == "line" and d.get("raw"):
        extra = f'<details><summary>원수치 표</summary>{table_html(d["raw"], facts)}</details>'
    norm = '<p class="note">정규화 보기 — 원수치는 아래 표를 본다</p>' if d.get("normalized") else ""
    return (f'<figure class="fig wide"><figcaption><b>{esc(m.get("title"))}</b>'
            f'<span class="take">{sub_facts(m.get("takeaway"), facts)}</span></figcaption>{norm}'
            f'<div class="scroll">{body}</div>{meta_html(m)}{extra}</figure>')


def finding_html(b, facts):
    ev = " ".join(f'<a href="#fact-{esc(e)}" class="chip">{esc(e)}</a>' for e in b.get("evidence") or [])
    acts = " ".join(f'<a href="#{esc(a)}" class="chip act">{esc(a)}</a>' for a in b.get("actions") or [])
    row = lambda k, lab: f"<div><dt>{lab}</dt><dd>{sub_facts(b.get(k), facts)}</dd></div>"
    return (f'<article class="card finding" id="{esc(b.get("id"))}"><header><span class="cid">{esc(b.get("id"))}</span> 발견 · 근거 {ev}</header><dl>'
            + row("observed", "관측 사실") + row("interpretation", "해석 [추정]") + row("alternatives", "대안 설명")
            + row("limits", "판단 한계") + (f"<div><dt>연결 실행</dt><dd>{acts}</dd></div>" if acts else "") + "</dl></article>")


def action_html(b, facts):
    ev = " ".join(f'<a href="#fact-{esc(e)}" class="chip">{esc(e)}</a>' for e in b.get("evidence") or [])
    cr = b.get("criteria") or {}
    row = lambda k, lab: f"<div><dt>{lab}</dt><dd>{sub_facts(b.get(k), facts)}</dd></div>"
    crit = "".join(f"<li><b>{lab}</b> {esc(cr.get(k))}</li>" for k, lab in CRITERIA.items())
    return (f'<article class="card action" id="{esc(b.get("id"))}"><header><span class="cid">{esc(b.get("id"))}</span> 실행 가설 · 근거 {ev}</header><dl>'
            + row("hypothesis", "검증할 가설") + row("deliverable", "결과물") + row("owner_role", "담당 역할")
            + row("internal_data", "필요한 내부 데이터") + f"<div><dt>판정 조건</dt><dd><ul>{crit}</ul></dd></div></dl></article>")


def block_html(b, facts):
    t = b.get("type")
    if t == "paragraph":
        return f'<p class="prose">{sub_facts(b.get("html"), facts)}</p>'
    if t == "table":
        return table_html(b, facts)
    if t == "chart":
        return chart_html(b, facts)
    if t == "finding":
        return finding_html(b, facts)
    if t == "action":
        return action_html(b, facts)
    if t == "details":
        inner = "".join(block_html(x, facts) for x in b.get("blocks") or [])
        return f'<details class="more"><summary>{esc(b.get("summary"))}</summary>{inner}</details>'
    return ""


def scope_html(sc):
    def val(k):
        v = sc.get(k)
        if isinstance(v, dict):
            return " · ".join(f"{esc(a)} {esc(b)}" for a, b in v.items())
        return esc(v)
    rows = "".join(f'<tr><th scope="row">{SCOPE_LABEL[k]}</th><td>{val(k)}</td></tr>' for k in SCOPE_FIELDS)
    return f'<div class="tbl"><table class="kv"><tbody>{rows}</tbody></table></div>'


def facts_html(facts):
    if not facts:
        return ""
    rows = "".join(f'<tr id="fact-{esc(k)}"><th scope="row">{esc(k)}</th><td class="num">{fmt(f.get("value"), f.get("unit", ""))}</td>'
                   f'<td>{esc(f.get("label"))}</td><td>{esc(f.get("calc"))}</td>'
                   f'<td>{re.sub(r"\b(C\d+)\b", lambda m: f"<a href=\"#call-{m.group(1)}\">{m.group(1)}</a>", esc(f.get("source")))}</td></tr>'
                   for k, f in facts.items())
    return ('<h3>사실 목록</h3><div class="tbl wide"><table><thead><tr><th scope="col">ID</th><th scope="col" class="num">값</th>'
            f'<th scope="col">라벨</th><th scope="col">계산</th><th scope="col">출처</th></tr></thead><tbody>{rows}</tbody></table></div>')


def cost_summary(pv):
    calls = pv.get("calls") or []
    total = sum(cl.get("cost") or 0 for cl in calls)
    by_tool, by_step = {}, {}
    for cl in calls:
        for key, bucket in ((cl.get("tool"), by_tool), (cl.get("step") or "—", by_step)):
            b = bucket.setdefault(key, {"calls": 0, "cost": 0})
            b["calls"] += 1; b["cost"] += cl.get("cost") or 0
    reused = sum(1 for cl in calls if cl.get("reused_from"))
    return {"total": total, "calls": len(calls), "reused": reused, "by_tool": by_tool, "by_step": by_step,
            "estimate": pv.get("estimate"), "monthly_used": pv.get("monthly_used"), "unit": pv.get("currency", "크레딧")}


def provenance_html(pv):
    calls = pv.get("calls") or []
    cs = cost_summary(pv)
    unit = esc(cs["unit"])
    seq = "".join(
        f'<li id="call-{esc(cl.get("id"))}"><b>{esc(cl.get("id"))}</b> · {esc(cl.get("step") or "—")} · <code>{esc(cl.get("tool"))}</code> — '
        f'{esc(cl.get("purpose") or cl.get("params"))}'
        f'<span class="note"> → 반환 {fmt(cl.get("returned"))} · {fmt(cl.get("cost"))} {unit}'
        f'{" · 재사용: " + esc(cl.get("reused_from")) if cl.get("reused_from") else ""}</span></li>' for cl in calls)
    head = "".join(f'<th scope="col"{" class=num" if k in ("returned", "cost") else ""}>{lab}</th>' for k, lab in CALL_COLS)
    rows = "".join("<tr>" + "".join(
        f'<td{" class=num" if k in ("returned", "cost") else ""}>{fmt(cl.get(k)) if k in ("returned", "cost") else esc(cl.get(k) if cl.get(k) is not None else "—")}</td>'
        for k, _ in CALL_COLS) + "</tr>" for cl in calls)
    def bucket(title, d):
        tr = "".join(f'<tr><th scope="row">{esc(k)}</th><td class="num">{v["calls"]}</td><td class="num">{fmt(v["cost"])}</td>'
                     f'<td class="num">{fmt(round(v["cost"] / cs["total"] * 100, 1) if cs["total"] else 0, "%")}</td></tr>' for k, v in d.items())
        return (f'<div class="tbl"><table><caption>{title}</caption><thead><tr><th scope="col"></th><th scope="col" class="num">호출</th>'
                f'<th scope="col" class="num">비용({unit})</th><th scope="col" class="num">비중</th></tr></thead><tbody>{tr}</tbody></table></div>')
    est = cs["estimate"]
    kv = [("이번 산출물 소진", f'{fmt(cs["total"])} {unit} — 호출 {cs["calls"]}건의 비용 합(응답 cost_detail 합산)'),
          ("재사용 호출", f'{cs["reused"]}건 (비용 0)'),
          ("견적 대비", f'견적 {fmt(est)} {unit} · 차이 {"+" if cs["total"] - est > 0 else ""}{fmt(cs["total"] - est)} {unit}' if isinstance(est, (int, float)) else "견적 기록 없음"),
          ("월간 누적", f'{fmt(cs["monthly_used"])} {unit} — 이번 달 계정 전체 사용량이며 잔여량이 아니다' if cs["monthly_used"] is not None else "기록 없음")]
    kvh = "".join(f'<tr><th scope="row">{a}</th><td>{b}</td></tr>' for a, b in kv)
    return (f'<h3 id="calls-seq">호출 순서</h3><ol class="calls">{seq}</ol>'
            f'<h3 id="calls-log">호출 로그</h3><div class="tbl wide"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>'
            f'<h3 id="cost">비용 보고서</h3><div class="tbl"><table class="kv"><tbody>{kvh}</tbody></table></div>'
            + bucket("도구별", cs["by_tool"]) + bucket("단계별", cs["by_step"]))


CSS = r"""
:root{--bg:#fbfbfc;--fg:#17171f;--body:#3f3f4a;--mute:#6b6b78;--line:#dcdbe5;--panel:#f0eff5;--card:#fff;
--pri:#4b2bd0;--neg:#b42318;--pos:#1f6f43;--warn-bg:#fff4e0;--warn:#8a4b00;
--s0:#4b2bd0;--s1:#c2410c;--s2:#0f766e;--s3:#a21caf;--s4:#475569;--s5:#b45309;
--sans:"Apple SD Gothic Neo","Noto Sans KR",system-ui,sans-serif;--mono:ui-monospace,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121218;--fg:#ececf3;--body:#c9c9d4;--mute:#9a9aa8;--line:#34343f;
--panel:#1e1e27;--card:#191921;--pri:#a594ff;--neg:#ff8a7a;--pos:#6ee0a0;--warn-bg:#3a2a10;--warn:#ffc77a;
--s0:#a594ff;--s1:#ff9a5c;--s2:#4fd1c5;--s3:#e879f9;--s4:#a3b1c6;--s5:#fbbf24;color-scheme:dark}}
:root[data-theme=dark]{--bg:#121218;--fg:#ececf3;--body:#c9c9d4;--mute:#9a9aa8;--line:#34343f;--panel:#1e1e27;--card:#191921;
--pri:#a594ff;--neg:#ff8a7a;--pos:#6ee0a0;--warn-bg:#3a2a10;--warn:#ffc77a;--s0:#a594ff;--s1:#ff9a5c;--s2:#4fd1c5;--s3:#e879f9;--s4:#a3b1c6;--s5:#fbbf24;color-scheme:dark}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.7 var(--sans);word-break:keep-all;overflow-wrap:anywhere}
.page{display:grid;grid-template-columns:220px minmax(0,1fr);gap:40px;max-width:1240px;margin:0 auto;padding:32px 20px 80px}
nav.toc{position:sticky;top:20px;align-self:start;font-size:14px}nav.toc ol{list-style:none;padding:0;margin:8px 0;display:flex;flex-direction:column;gap:6px}
nav.toc a{color:var(--body);text-decoration:none}nav.toc ol.sub{margin:4px 0 0 14px;gap:3px;font-size:13px}nav.toc a:hover,nav.toc a:focus-visible{color:var(--pri);text-decoration:underline}
main{min-width:0;display:flex;flex-direction:column;gap:44px}
h1{font-size:clamp(26px,4vw,36px);line-height:1.25;margin:0}h2{font-size:23px;margin:0 0 14px}h3{font-size:17px;margin:20px 0 8px}
.eyebrow{font-size:13px;letter-spacing:1px;color:var(--mute);font-weight:700}
.boundary{border:2px solid var(--warn);background:var(--warn-bg);color:var(--fg);border-radius:10px;padding:12px 16px;font-weight:700}
.lede,.prose{max-width:72ch;color:var(--body);margin:0 0 12px}.lede{font-size:18px}
section>.kicker{font-size:12px;color:var(--mute);font-weight:700;letter-spacing:1px}
.fact{font-weight:700;color:var(--fg);border-bottom:1px dotted var(--mute);font-variant-numeric:tabular-nums}
.tbl{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card);margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:14.5px;font-variant-numeric:tabular-nums}
th,td{word-break:keep-all;overflow-wrap:normal}tbody th,tbody td:first-child{white-space:nowrap}td.note{min-width:16em}
th,td{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
thead th{background:var(--panel);font-size:13px;color:var(--mute)}td.num,th.num{text-align:right;white-space:nowrap}
td.note{color:var(--mute);font-size:13.5px}caption{caption-side:top;text-align:left;padding:8px 12px;font-weight:700}
table.kv th{width:9em;color:var(--mute);font-weight:600}
table.heat td.hm{text-align:center;background:color-mix(in srgb,var(--pri) calc(var(--a)*45%),var(--card));min-width:64px}
table.heat td.hm b{display:block}table.heat td.hm small{color:var(--body)}table.heat td.na{color:var(--mute);text-align:center}
.fig{margin:14px 0;border:1px solid var(--line);border-radius:12px;background:var(--card);padding:14px 16px}
.fig figcaption{display:flex;flex-direction:column;gap:2px;margin-bottom:6px}.fig figcaption .take{color:var(--body);max-width:72ch}
.scroll{overflow-x:auto}.chart{width:100%;min-width:640px;height:auto;display:block}
.chart .grid{stroke:var(--line)}.chart .axis{stroke:var(--mute);stroke-width:1.2}.chart .tick{fill:var(--mute);font-size:12px}
.chart .lab{fill:var(--fg);font-size:12.5px;font-weight:600}.chart .ln{fill:none;stroke-width:2.4}
.chart .bar{fill:var(--pos)}.chart .bar.neg{fill:var(--neg)}.chart .pt{fill:color-mix(in srgb,var(--pri) 35%,transparent);stroke:var(--pri)}
.chart .pt.legend{fill:none}
.s0{stroke:var(--s0);fill:var(--s0)}.s1{stroke:var(--s1);fill:var(--s1)}.s2{stroke:var(--s2);fill:var(--s2)}.s3{stroke:var(--s3);fill:var(--s3)}.s4{stroke:var(--s4);fill:var(--s4)}.s5{stroke:var(--s5);fill:var(--s5)}
.chart .ln.s0,.chart .ln.s1,.chart .ln.s2,.chart .ln.s3,.chart .ln.s4,.chart .ln.s5{fill:none}
.tbl>dl.meta{padding:8px 12px 0;margin:0}dl.meta{display:flex;flex-wrap:wrap;gap:4px 18px;margin:8px 0 0;font-size:13px;color:var(--mute)}dl.meta div{display:flex;gap:6px}dl.meta dt{font-weight:700}dl.meta dd{margin:0}
.card{border:1px solid var(--line);border-left:4px solid var(--pri);border-radius:10px;background:var(--card);padding:12px 16px;margin:12px 0;max-width:880px}
.card.action{border-left-color:var(--s2)}.card header{font-size:13px;color:var(--mute);margin-bottom:6px}
.card dl{margin:0;display:grid;gap:6px}.card dl div{display:grid;grid-template-columns:9em minmax(0,1fr);gap:10px}.card dt{color:var(--mute);font-size:14px}.card dd{margin:0}
.card ul{margin:0;padding-left:18px}.cid{font-family:var(--mono);font-weight:700;color:var(--fg)}
.chip{font:600 12px var(--mono);border:1px solid var(--line);border-radius:999px;padding:0 7px;color:var(--pri);text-decoration:none}.chip.act{color:var(--s2)}
details.more{border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:10px 0;background:var(--card)}details.more summary{cursor:pointer;font-weight:700}
.note{font-size:13px;color:var(--mute);margin:6px 0}
ol.calls{margin:6px 0 12px;padding-left:22px;display:flex;flex-direction:column;gap:4px;max-width:900px}ol.calls code{font:13px var(--mono)}
.tools{display:flex;justify-content:space-between;align-items:center;gap:12px}
button.theme{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:8px;padding:4px 10px;cursor:pointer}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--pri);outline-offset:2px}
@media (max-width:900px){.page{grid-template-columns:minmax(0,1fr);gap:20px;padding:20px 16px 60px}nav.toc{position:static}
.card dl div{grid-template-columns:minmax(0,1fr);gap:0}}
@media print{nav.toc,button.theme{display:none}.page{display:block;max-width:none;padding:0}body{background:#fff;color:#000;font-size:11pt}
.fig,.card,tr{break-inside:avoid}thead{display:table-header-group}.scroll,.tbl{overflow:visible}.chart{min-width:0}h2{break-after:avoid}
details.more>*{display:block}details::details-content{content-visibility:visible;display:block}a{color:inherit}}
"""

JS = r"""
(function(){var r=document.documentElement,b=document.querySelector('button.theme');
function lab(){b.textContent=(r.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'))==='dark'?'밝게':'어둡게'}
b.addEventListener('click',function(){var d=(r.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'))==='dark';r.setAttribute('data-theme',d?'light':'dark');lab()});lab();
addEventListener('beforeprint',function(){document.querySelectorAll('details').forEach(function(d){d.dataset.o=d.open;d.open=true})});
addEventListener('afterprint',function(){document.querySelectorAll('details').forEach(function(d){d.open=d.dataset.o==='true'})});})();
"""


def render(r: dict) -> str:
    facts = r.get("facts") or {}
    meta = r.get("meta") or {}
    secs = list(r.get("sections") or [])
    i = next((k for k, s in enumerate(secs) if SPINE.index(s.get("kind")) > SPINE.index("scope")), len(secs))
    secs.insert(i, {"kind": "scope", "title": KIND_LABEL["scope"], "_scope": True})
    toc, body = [], []
    for n, s in enumerate(secs, 1):
        sid = f"s{n}"
        toc.append(f'<li><a href="#{sid}">{n}. {esc(re.sub(r"<[^>]+>", "", sub_facts(s.get("title"), facts)))}</a></li>')
        inner = scope_html(r.get("scope") or {}) if s.get("_scope") else "".join(block_html(b, facts) for b in s.get("blocks") or [])
        if s.get("kind") == "appendix" and n == len(secs):
            inner += facts_html(facts) + provenance_html(r.get("provenance") or {})
            toc[-1] = toc[-1].replace("</a></li>", '</a><ol class="sub"><li><a href="#calls-seq">호출 순서</a></li>'
                                      '<li><a href="#calls-log">호출 로그</a></li><li><a href="#cost">비용 보고서</a></li></ol></li>')
        body.append(f'<section id="{sid}"><div class="kicker">{n:02d} · {KIND_LABEL[s["kind"]]}</div>'
                    f'<h2>{sub_facts(s.get("title"), facts)}</h2>{inner}</section>')
    title = esc(meta.get("title") or "리포트")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{CSS}</style></head><body>
<div class="page"><nav class="toc" aria-label="목차"><b>목차</b><ol>{"".join(toc)}</ol></nav>
<main><header style="display:flex;flex-direction:column;gap:12px">
<div class="tools"><span class="eyebrow">{esc(meta.get("subtitle"))}</span><button class="theme" type="button">어둡게</button></div>
<h1>{title}</h1><p class="boundary" role="note">{sub_facts(esc(r.get("boundary")), facts)}</p>
<p class="lede">{sub_facts(r.get("lede_html"), facts)}</p></header>
{"".join(body)}
<footer class="note">{esc(meta.get("skill"))} {esc(meta.get("version"))} · 렌더링 계약 v1.2 · 공통 렌더러 render_report.py</footer>
</main></div><script>{JS}</script></body></html>"""


def post_check(c: Check, page: str):
    text = re.sub(r"<style>.*?</style>|<script>.*?</script>", "", page, flags=re.S)
    for bad in ("{{", "}}", ">None<", "NaN", "undefined"):
        if bad in text:
            c.err("출력", f"미치환 · 오류 문자열 {bad!r} 이 HTML 에 남았다")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="공통 리포트 렌더러 — 렌더링 계약 검사 + 단독 HTML")
    ap.add_argument("report", nargs="?", help="report.json")
    ap.add_argument("-o", "--out", help="출력 HTML (기본: report.json 옆 같은 이름 .html)")
    ap.add_argument("--check", action="store_true", help="검사만 한다")
    ap.add_argument("--schema", action="store_true", help="입력 형식을 출력한다")
    a = ap.parse_args()
    if a.schema:
        print(SCHEMA); return 0
    if not a.report:
        ap.error("report.json 경로가 필요하다")
    try:
        r = json.loads(pathlib.Path(a.report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"입력 오류: {e}", file=sys.stderr); return 2
    c = validate(r)
    page = None
    if not c.errors:
        page = render(r)
        post_check(c, page)
    for w in c.warnings:
        print(f"  ⚠ {w}")
    if c.errors:
        print(f"계약 위반 {len(c.errors)}건 — 렌더하지 않는다")
        for e in c.errors:
            print(f"  ✗ {e}")
        return 1
    if a.check:
        print("계약 검사 통과"); return 0
    out = pathlib.Path(a.out) if a.out else pathlib.Path(a.report).with_suffix(".html")
    out.write_text(page, encoding="utf-8")
    # 산출물마다 호출 기록을 따로 볼 수 있게 — 표 계산기용 호출 로그와 비용 보고서를 HTML 옆에 쓴다
    import csv
    pv = r.get("provenance") or {}
    with out.with_suffix(".calls.csv").open("w", encoding="utf-8-sig", newline="") as fp:
        w = csv.writer(fp)
        w.writerow([k for k, _ in CALL_COLS] + ["purpose", "reused_from"])
        for cl in pv.get("calls") or []:
            w.writerow([cl.get(k) for k, _ in CALL_COLS] + [cl.get("purpose"), cl.get("reused_from")])
    out.with_suffix(".cost.json").write_text(json.dumps(cost_summary(pv), ensure_ascii=False, indent=1), encoding="utf-8")
    cs = cost_summary(r.get("provenance") or {})
    print(f"렌더: {out} · 섹션 {len(r.get('sections') or []) + 1} · 사실 {len(r.get('facts') or {})}"
          f" · 호출 {cs['calls']}건 · 소진 {cs['total']:,} → {out.with_suffix('.calls.csv').name} · {out.with_suffix('.cost.json').name}"
          + (f" · 경고 {len(c.warnings)}" if c.warnings else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
