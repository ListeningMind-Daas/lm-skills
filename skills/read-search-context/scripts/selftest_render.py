#!/usr/bin/env python3
# shared-asset: lm-skills/shared — 이 파일은 사본이다. 고칠 때는 정본을 고치고 재배포할 것.
"""공통 리포트 렌더러 회귀 시험 — 경계 사례는 렌더되고, 계약 위반은 거부되는가.

공유 견본(`assets/report.sample.json`)을 한 군데씩 바꿔 `render_report.py`에 넣는다.
렌더된 HTML은 태그 중첩 · 중복 id · 끊긴 목차 링크 · 미치환 문자열까지 본다.
API·크레딧·브라우저 없이 돈다. 공유 `selftest.py`가 이 파일을 함께 실행한다.

    python3 scripts/selftest_render.py        # 스킬 폴더 또는 shared/ 에서

2026-10-13 렌더링 시험에서 찾은 결함(모바일 표 글자 세로 쪼개짐 · 긴 막대 라벨 잘림 · 음수 막대 라벨 겹침 ·
인쇄 때 접힌 영역 숨김)은 화면 확인으로 찾았다 — 이 시험은 그 고친 결과가 HTML·CSS에 남아 있는지를 본다.
"""
import copy, json, pathlib, re, subprocess, sys, tempfile
from html.parser import HTMLParser

HERE = pathlib.Path(__file__).resolve().parent
RENDER = HERE / "render_report.py"
EXAMPLE = HERE.parent / "assets" / "report.sample.json"   # 공유 견본(고치지 않는다) — 스킬 자기 견본은 report.example.json
VOID = {"meta", "br", "img", "input", "link", "hr", "circle", "rect", "line", "polyline", "path"}


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.errors, self.ids = [], [], {}

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            self.ids[d["id"]] = self.ids.get(d["id"], 0) + 1
        if tag == "p" and "p" in self.stack:
            self.errors.append("<p> 중첩")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag not in self.stack:
            self.errors.append(f"짝 없는 </{tag}>")
            return
        while self.stack and self.stack.pop() != tag:
            self.errors.append(f"닫히지 않은 태그 (</{tag}> 앞)")


def html_problems(page: str):
    s = Structure()
    s.feed(page)
    body = re.sub(r"<style>.*?</style>|<script>.*?</script>", "", page, flags=re.S)
    probs = list(s.errors[:3])
    dup = [k for k, v in s.ids.items() if v > 1]
    if dup:
        probs.append(f"중복 id {dup[:3]}")
    bad = [x for x in ("{{", ">None<", "NaN", "undefined") if x in body]
    if bad:
        probs.append(f"미치환·오류 문자열 {bad}")
    dangling = [h for h in set(re.findall(r'href="#([^"]+)"', page)) if h not in s.ids]
    if dangling:
        probs.append(f"끊긴 링크 #{dangling[:3]}")
    return probs


def trend(r):
    return next(s for s in r["sections"] if s["kind"] == "trend")["blocks"]


def block(r, chart=None, btype=None):
    return next(b for b in trend(r) if (chart and b.get("chart") == chart) or (btype and b.get("type") == btype))


# (이름, 견본을 바꾸는 함수, 기대 exit) — 0 = 렌더돼야 한다 · 1 = 거부돼야 한다
CASES = [
    ("견본", lambda r: None, 0),
    ("계열 전체 결측", lambda r: block(r, "line")["data"]["series"][1].__setitem__("values", [None] * len(block(r, "line")["data"]["x"])), 0),
    ("점 하나짜리 선", lambda r: block(r, "line")["data"].update({"x": ["2026-08"], "series": [{"name": "a", "values": [1]}]}), 0),
    ("음수만 있는 막대", lambda r: block(r, "bar")["data"].update({"values": [-3.0, -9.5], "abs": [-10, -1900]}), 0),
    ("결측 막대", lambda r: block(r, "bar")["data"].update({"values": [22.0, None], "abs": [310, None]}), 0),
    ("기준값 0 행", lambda r: block(r, btype="table")["rows"].append(
        {"q": "신규어", "b": 0, "c": 120, "d": 120, "p": None, "note": "기준 0 — 증감률 계산 불가"}), 0),
    ("히트맵 결측 칸", lambda r: (block(r, "heatmap")["data"]["values"][0].__setitem__(3, None),
                             block(r, "heatmap")["data"]["raw"][0].__setitem__(3, None)), 0),
    ("점 하나 산점도", lambda r: block(r, "scatter")["data"].__setitem__("points", [{"label": "x", "x": 1.2, "y": -3, "size": 50}]), 0),
    ("크기 0인 점", lambda r: block(r, "scatter")["data"]["points"].append({"label": "z", "x": 1.0, "y": 0, "size": 0}), 0),
    ("긴 막대 라벨", lambda r: block(r, "bar")["data"].__setitem__(
        "labels", ["아주 긴 묶음 이름이 들어가서 축 라벨이 넘칠 수 있는 경우를 확인한다", "제품"]), 0),
    ("제목 이스케이프", lambda r: r["meta"].__setitem__("title", "<script>alert(1)</script> 제목"), 0),
    ("접기 영역", lambda r: r["sections"].insert(2, {"kind": "structure", "title": "관심 구조", "blocks": [
        {"type": "details", "summary": "원수치", "blocks": [{"type": "paragraph", "html": "접힌 영역 {{F1}}"}]}]}), 0),
    ("트렌드 섹션 둘", lambda r: r["sections"].insert(2, {"kind": "trend", "title": "계절성", "blocks": []}), 0),
    ("정규화 + 원수치 표", lambda r: block(r, "line")["data"].update({"normalized": True, "raw": {
        "columns": [{"key": "m", "label": "월"}, {"key": "v", "label": "건", "num": True}], "rows": [{"m": "2026-08", "v": 4650}]}}), 0),
    ("배수 · 배송처럼 단위 뒤에 글자", lambda r: r["sections"][-1]["blocks"].append(
        {"type": "paragraph", "html": "2023~2025 배수 범위와 3 배송 묶음은 사실 목록 밖이어도 된다"}), 0),
    ("재사용 호출 비용 0", lambda r: r["provenance"]["calls"].append(
        {"id": "C9", "tool": "keyword_info", "params": "C3 재사용", "cost": 0, "raw": "raw/x.json", "status": "ok", "reused_from": "C3"}), 0),
    # ── 거부
    ("호출 기록 없음", lambda r: r.pop("provenance"), 1),
    ("호출 필드 누락", lambda r: r["provenance"]["calls"][0].pop("raw"), 1),
    ("호출 ID 중복", lambda r: r["provenance"]["calls"][1].__setitem__("id", "C1"), 1),
    ("음수 비용", lambda r: r["provenance"]["calls"][0].__setitem__("cost", -5), 1),
    ("없는 호출을 출처로", lambda r: r["facts"]["F1"].__setitem__("source", "C99"), 1),
    ("실행 조건 속 숫자", lambda r: next(b for s in r["sections"] for b in s["blocks"] if b.get("type") == "action")["criteria"].__setitem__("stop", "유입 1,200건 미만"), 1),
    ("차트 한계 속 숫자", lambda r: block(r, "line")["meta"].__setitem__("limits", "최근 3개월 12.5% 과소"), 1),
    ("요약 섹션 없음", lambda r: r["sections"].pop(0), 1),
    ("scope 를 섹션으로", lambda r: r["sections"].insert(1, {"kind": "scope", "title": "x", "blocks": []}), 1),
    ("실행을 트렌드 앞에", lambda r: r["sections"].insert(1, {"kind": "action", "title": "초안", "blocks": []}), 1),
    ("뼈대 역순", lambda r: r["sections"].reverse(), 1),
    ("경계 문장 없음", lambda r: r.pop("boundary"), 1),
    ("비교 기간 누락", lambda r: r["scope"].pop("windows"), 1),
    ("출처 없는 사실", lambda r: r["facts"]["F1"].pop("source"), 1),
    ("없는 사실 ID", lambda r: r.__setitem__("lede_html", "요약 {{F99}}"), 1),
    ("제목 속 숫자", lambda r: r["sections"][0].__setitem__("title", "보습크림 10,837건"), 1),
    ("해석 문장 속 숫자", lambda r: block(r, "line")["meta"].__setitem__("takeaway", "가을에 2.11배"), 1),
    ("문단 속 숫자", lambda r: r["sections"][0]["blocks"].append({"type": "paragraph", "html": "최근 여름 16.0% 감소"}), 1),
    ("문단 안 <p>", lambda r: r["sections"][-1]["blocks"].append({"type": "paragraph", "html": "<p>중첩</p>"}), 1),
    ("차트 메타 누락", lambda r: block(r, "line")["meta"].pop("source"), 1),
    ("모르는 차트 유형", lambda r: block(r, "line").__setitem__("chart", "pie"), 1),
    ("계열 길이 불일치", lambda r: block(r, "line")["data"]["series"][0]["values"].pop(), 1),
    ("증감률만 있는 표", lambda r: [c.pop("role") for c in block(r, btype="table")["columns"] if c.get("role") in ("base", "abs_change")], 1),
    ("히트맵 원수치 없음", lambda r: block(r, "heatmap")["data"].pop("raw"), 1),
    ("산점도 크기 범례 없음", lambda r: block(r, "scatter")["data"].pop("size_label"), 1),
    ("증감률 막대 절대량 없음", lambda r: block(r, "bar")["data"].pop("abs"), 1),
    ("발견 카드 필드 누락", lambda r: r["sections"][0]["blocks"][0].pop("alternatives"), 1),
    ("없는 근거 ID", lambda r: r["sections"][0]["blocks"][0]["evidence"].append("F9"), 1),
    ("없는 실행 카드", lambda r: r["sections"][0]["blocks"][0]["actions"].append("A9"), 1),
    ("실행 카드 조건 누락", lambda r: next(b for s in r["sections"] for b in s["blocks"] if b.get("type") == "action")["criteria"].pop("stop"), 1),
]

# 화면 확인으로 찾아 고친 결함 — 고친 결과가 출력에 남아 있는가 (2026-10-13)
LAYOUT = [
    ("모바일 표 글자 세로 쪼개짐", "tbody th,tbody td:first-child{white-space:nowrap}"),
    ("인쇄 때 접힌 영역 숨김", "details::details-content{content-visibility:visible;display:block}"),
    ("표 머리 반복 인쇄", "thead{display:table-header-group}"),
    ("차트 글자 축소 대신 내부 스크롤", ".chart{width:100%;min-width:640px"),
]


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if not RENDER.is_file() or not EXAMPLE.is_file():
        print("건너뜀 — render_report.py 또는 assets/report.example.json 이 없다")
        return 0
    base = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    fails = []
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        for i, (name, fn, want) in enumerate(CASES):
            r = copy.deepcopy(base)
            try:
                fn(r)
            except (KeyError, IndexError, StopIteration) as e:
                fails.append(f"{name}: 견본 구조가 바뀌어 사례를 만들 수 없다 ({type(e).__name__})")
                continue
            src, out = tmp / f"c{i}.json", tmp / f"c{i}.html"
            src.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
            p = subprocess.run([sys.executable, str(RENDER), str(src), "-o", str(out)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if p.returncode != want:
                fails.append(f"{name}: exit {p.returncode} (기대 {want}) {p.stdout.strip().splitlines()[-1:]}")
                continue
            if want == 0:
                page = out.read_text(encoding="utf-8")
                probs = html_problems(page)
                if probs:
                    fails.append(f"{name}: HTML {probs}")
                if name == "제목 이스케이프" and "<script>alert(1)" in page:
                    fails.append(f"{name}: 입력 문자열이 이스케이프되지 않았다")
                if name == "긴 막대 라벨" and "…</text>" not in page:
                    fails.append(f"{name}: 긴 라벨을 줄이지 않았다(잘림)")
                if name == "음수만 있는 막대" and 'class="lab" text-anchor="end"' in page:
                    fails.append(f"{name}: 음수 막대 값 라벨이 막대 왼쪽(항목 이름 쪽)에 놓였다")
                if name == "견본":
                    for sid in ("calls-seq", "calls-log", "cost"):
                        if f'id="{sid}"' not in page:
                            fails.append(f"호출 기록 뷰 #{sid} 가 없다")
                    csv_f, cost_f = out.with_suffix(".calls.csv"), out.with_suffix(".cost.json")
                    if not csv_f.is_file() or not cost_f.is_file():
                        fails.append("호출 로그 CSV · 비용 보고서 JSON 이 HTML 옆에 써지지 않았다")
                    else:
                        cost = json.loads(cost_f.read_text(encoding="utf-8"))
                        want = sum(c.get("cost") or 0 for c in base["provenance"]["calls"])
                        if cost.get("total") != want:
                            fails.append(f"비용 보고서 합계 {cost.get('total')} ≠ 호출 비용 합 {want}")
                        if len(csv_f.read_text(encoding="utf-8-sig").strip().splitlines()) != len(base["provenance"]["calls"]) + 1:
                            fails.append("호출 로그 CSV 행 수가 호출 수와 다르다")
                    for what, css in LAYOUT:
                        if css not in page:
                            fails.append(f"레이아웃 회귀 — {what}: «{css}» 없음")
    if fails:
        print(f"렌더러 회귀 실패 {len(fails)}건")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    ok = sum(1 for c in CASES if c[2] == 0)
    print(f"통과 — 렌더 {ok}건 · 거부 {len(CASES) - ok}건 · 레이아웃 {len(LAYOUT)}건 (API·브라우저 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
