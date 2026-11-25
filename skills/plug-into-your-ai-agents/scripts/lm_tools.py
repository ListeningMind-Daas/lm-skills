#!/usr/bin/env python3
"""ListeningMind DaaS API를 에이전트 도구로 꽂는 런타임 — 명세 추적 · 도구 정의 · 예산 가드 · 크레딧 원장.

에이전트에 커넥터를 «그냥» 연결하면 네 가지가 조용히 무너진다.
  ① 비용 — 에이전트는 호출 비용을 모른다. 10크레딧짜리 `all`을 유니버스 전량에 건다.
  ② 해석 — 응답의 `volume_trend`를 추세로 읽고, 조회일을 데이터 시점으로 읽는다.
  ③ 확장 — `limit`에 붙은 반환을 «더 없다»로 읽는다.
  ④ 낡음 — API에 엔드포인트·파라미터·단가가 바뀌어도 손으로 적은 도구 목록은 그대로다.
그래서 도구는 **운영 명세(openapi.json)에서 만든다** — 인자 스키마와 단가는 `assets/api/api-lock.json`
(명세를 정규화한 잠금 파일)에서, 명세만으로 정할 수 없는 판단(추정 상한·절단 규칙·주석·사용 규칙)은
`assets/api/tool-policy.json`에서 온다. 도구 호출은 전부 이 런타임을 거친다: 호출 전 결정식으로 예산을
막고, 응답에 해석 주석(`lm_meta`)을 붙이고, 호출마다 원장(JSONL)에 한 줄을 남긴다.

    python3 lm_tools.py sync [--spec "$LM_API_BASE/openapi.json"] [--write]   # 명세 추적
    python3 lm_tools.py sync --check-lock                                     # 잠금 파일만 점검(네트워크 없음)
    python3 lm_tools.py list                                                  # 도구 목록·단가
    python3 lm_tools.py schema --format claude -o tools.json [--tools lm_keyword_info,lm_intent_finder]
    python3 lm_tools.py rules
    python3 lm_tools.py dry-run --scenario S.json --fixtures DIR --budget 2000 \\
        --ledger ledger.jsonl --raw-dir raw --queried-at 2026-09-28 [--tools-json tools.json] [--rules '{"썬":"선"}'] \\
        [--per-call-cap 1000] [--market kr|auto]
    python3 lm_tools.py summary ledger.jsonl -o usage.md --period 2026-09

실제 연결은 파이썬에서 `make_runtime_from_env()`로 만든다 — 주소는 `LM_API_BASE`, 키는
`LM_API_KEY` 환경변수에서만 읽는다. `sync`(`--check-lock` 제외) 말고는 네트워크를 쓰지 않는다(API 호출 0 · 크레딧 0).
`sync`는 명세 문서만 읽는다 — 데이터 엔드포인트를 부르지 않으므로 크레딧을 쓰지 않는다.
"""
import argparse, json, math, os, pathlib, re, sys, time
import urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timezone

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from series_dedup import canon as _canon, dedup_map, load_rules  # noqa: E402  계약 T5 (공유 사본)

API_DIR = HERE.parent / "assets" / "api"
LOCK_PATH = API_DIR / "api-lock.json"
POLICY_PATH = API_DIR / "tool-policy.json"
TIME_POINTS = ["curr", "3m", "6m", "9m", "12m"]

LEDGER_FIELDS = [
    "call_id", "ts", "tool", "endpoint", "spec_version", "gl", "data_type", "n_inputs", "requested_limit",
    "estimate", "estimate_basis", "status", "block_reason", "http_status", "attempts",
    "returned", "truncated", "formula_cost", "actual_cost", "formula_match",
    "spent_after", "budget", "monthly_used_credits_seen",
]


# ── 명세 정규화 (openapi.json → api-lock.json) ────────────────────────────
_KEEP = ("type", "enum", "default", "minimum", "maximum", "minItems", "maxItems", "minLength",
         "pattern", "format", "items")


def _ref_name(x):
    return x["$ref"].rsplit("/", 1)[-1] if isinstance(x, dict) and "$ref" in x else None


def _resolve(x, comps, depth=0):
    if depth > 12:
        return x
    if isinstance(x, dict):
        if "$ref" in x:
            return _resolve(comps.get(_ref_name(x), {}), comps, depth + 1)
        return {k: _resolve(v, comps, depth + 1) for k, v in x.items()}
    if isinstance(x, list):
        return [_resolve(v, comps, depth + 1) for v in x]
    return x


def _int_bounds(s):
    """integer 인자의 minimum·maximum·default가 명세에서 1.0처럼 실수로 오면 정수로 바꾼다
    (도구 스키마를 엄격히 검사하는 호스트가 integer에 실수 경계를 거부할 수 있다)."""
    if s.get("type") == "integer":
        for k in ("minimum", "maximum", "default"):
            v = s.get(k)
            if isinstance(v, float) and v.is_integer():
                s[k] = int(v)
    return s


def _param(s):
    """파라미터 스키마를 도구용으로 줄인다 — anyOf[X, null]은 X로, title·예시는 버린다."""
    s = dict(s)
    if "anyOf" in s:
        opts = [o for o in s.pop("anyOf") if o.get("type") != "null"]
        if len(opts) == 1:
            s = {**opts[0], **s}
        else:
            s["anyOf"] = [_param(o) for o in opts]
    out = {k: s[k] for k in _KEEP if k in s}
    if "anyOf" in s:
        out["anyOf"] = s["anyOf"]
    if isinstance(out.get("items"), dict):
        out["items"] = _param(out["items"])
    desc = " ".join(str(s.get("description") or s.get("title") or "").split())
    if desc:
        out["description"] = desc
    return _int_bounds(out)


def _camel(v):
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[_\-\s]+", str(v)) if p)


def _costs(op, comps, params):
    """응답 cost_detail이 가리키는 *Cost 스키마에서 단가(입력·출력 기본값)를 읽는다.
    여러 개면 data_type 열거값에 이름으로 붙인다(KeywordInfoAllCost → all)."""
    sch = (((op.get("responses") or {}).get("200") or {}).get("content") or {}).get("application/json", {}).get("schema")
    resp = comps.get(_ref_name(sch), sch or {})
    cd = (resp.get("properties") or {}).get("cost_detail") or {}
    names = [_ref_name(o) for o in cd.get("anyOf", [cd])]
    table = {}
    for n in names:
        if not n or n == "BasicCost":
            continue
        p = comps.get(n, {}).get("properties") or {}
        if "input_cost" not in p:
            continue
        table[n] = {"input": p["input_cost"].get("default"), "output": (p.get("output_cost") or {}).get("default", 0),
                    "output_unit": " ".join(str((p.get("output_cost") or {}).get("description") or "").split())}
    if not table:
        return None, None
    enum = (params.get("data_type") or {}).get("enum")
    if len(table) > 1 and enum:
        by = {}
        for v in enum:
            hit = [n for n in table if n.endswith(_camel(v) + "Cost")]
            if hit:
                by[v] = table[max(hit, key=len)]
        if by:
            return by, "data_type"
    return {"*": next(iter(table.values()))}, None


def normalize_spec(spec, synced_at=None):
    """운영 openapi.json → 잠금 파일. 런타임이 쓰는 것만 남긴다(경로·메서드·인자·필수·단가)."""
    comps = (spec.get("components") or {}).get("schemas") or {}
    eps = {}
    for path, methods in sorted((spec.get("paths") or {}).items()):
        for method, op in methods.items():
            if method.upper() not in ("GET", "POST") or not isinstance(op, dict):
                continue
            if method.upper() == "POST":
                body = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
                body = _resolve(body or {}, comps)
                params = {k: _param(v) for k, v in (body.get("properties") or {}).items()}
                required = list(body.get("required") or [])
            else:
                params, required = {}, []
                for prm in op.get("parameters") or []:
                    prm = _resolve(prm, comps)
                    if prm.get("in") != "query":
                        continue
                    params[prm["name"]] = _param({**(prm.get("schema") or {}),
                                                  "description": prm.get("description") or ""})
                    if prm.get("required"):
                        required.append(prm["name"])
            cost, cost_by = _costs(op, comps, params)
            eps[path] = {"method": method.upper(), "operation_id": op.get("operationId") or "",
                         "summary": " ".join(str(op.get("summary") or "").split()),
                         "description": " ".join(str(op.get("description") or "").split()),
                         "params": params, "required": required, "cost": cost, "cost_by": cost_by}
    info = spec.get("info") or {}
    return {"api_title": info.get("title"), "spec_version": info.get("version"),
            "synced_at": synced_at or date.today().isoformat(), "endpoints": eps}


def diff_lock(old, new):
    """잠금 파일 두 판의 차이 — (종류, 경로, 내용). 종류: added · removed · cost · shape."""
    out = []
    oe, ne = (old or {}).get("endpoints", {}), new.get("endpoints", {})
    for p in sorted(set(ne) - set(oe)):
        out.append(("added", p, f"{ne[p]['method']} · {ne[p]['summary']}"))
    for p in sorted(set(oe) - set(ne)):
        out.append(("removed", p, "명세에서 사라짐"))
    for p in sorted(set(oe) & set(ne)):
        a, b = oe[p], ne[p]
        if a.get("cost") != b.get("cost"):
            out.append(("cost", p, f"단가 {a.get('cost')} → {b.get('cost')}"))
        if a["method"] != b["method"]:
            out.append(("shape", p, f"메서드 {a['method']} → {b['method']}"))
        for k in sorted(set(b["params"]) - set(a["params"])):
            out.append(("shape", p, f"인자 추가 {k}"))
        for k in sorted(set(a["params"]) - set(b["params"])):
            out.append(("shape", p, f"인자 삭제 {k}"))
        for k in sorted(set(a["params"]) & set(b["params"])):
            for f in ("type", "enum", "default", "maximum", "minimum", "maxItems"):
                if a["params"][k].get(f) != b["params"][k].get(f):
                    out.append(("shape", p, f"{k}.{f} {a['params'][k].get(f)!r} → {b['params'][k].get(f)!r}"))
        if sorted(a["required"]) != sorted(b["required"]):
            out.append(("shape", p, f"필수 {a['required']} → {b['required']}"))
    return out


# ── 도구 레지스트리 (잠금 파일 + 판단 파일) ─────────────────────────────────
class Tool:
    def __init__(self, path, ep, pol, reviewed, hidden, spec_version):
        self.path, self.ep, self.pol, self.reviewed = path, ep, pol, reviewed
        self.name = pol["name"]
        self.method = ep["method"]
        self.spec_version = spec_version
        self.cost, self.cost_by = ep.get("cost"), ep.get("cost_by")
        props = {}
        for k, v in ep["params"].items():
            if k in hidden:
                continue
            v = _int_bounds(dict(v))                 # 이전 잠금 파일의 1.0 경계도 정수로 낸다
            if k in pol.get("enum_only", {}):
                v["enum"] = [e for e in v.get("enum", pol["enum_only"][k]) if e in pol["enum_only"][k]]
                if v.get("default") not in v["enum"]:
                    v.pop("default", None)
                v["description"] = f"{k} — {' · '.join(v['enum'])} 중 하나"   # 명세 설명은 뺀 값까지 나열한다
            if k in pol.get("defaults", {}):
                v["default"] = pol["defaults"][k]
            if k == "time_point" and "enum" not in v:
                v["enum"] = TIME_POINTS
            props[k] = v
        self.required = [k for k in dict.fromkeys(ep["required"] + pol.get("required_add", [])) if k in props]
        self.schema = {"type": "object", "properties": props, "required": self.required}
        self.defaults = {k: v["default"] for k, v in props.items() if "default" in v}
        self.input_field = "keywords" if "keywords" in props else "keyword" if "keyword" in props else None
        self.estimate_kind = pol.get("estimate", "auto")
        self.count = pol.get("count", "reported")
        self.annotate = pol.get("annotate", "generic")
        self.expansion = bool(pol.get("expansion", "limit" in props))
        self.enabled_default = reviewed and pol.get("enabled", True)

    def unit(self, args):
        if not self.cost:
            return None
        if self.cost_by:
            return self.cost.get(args.get(self.cost_by)) or self.cost.get(self.defaults.get(self.cost_by))
        return self.cost["*"]

    def cost_line(self):
        v = self.spec_version
        if not self.cost:
            return f"크레딧: 명세({v})에 단가 없음 — 원장의 cost_detail로 확인한다."
        if self.cost_by:
            allowed = self.schema["properties"].get(self.cost_by, {}).get("enum") or list(self.cost)
            parts = " · ".join(f"{k} {self.cost[k]['input']}×키워드" for k in allowed if k in self.cost)
            return f"크레딧(명세 {v}): {self.cost_by}별 — {parts}."
        c = self.cost["*"]
        unit = self.pol.get("unit") or c.get("output_unit") or "출력"
        tail = f" + {c['output']}×{unit}" if c["output"] else " (출력 무과금)"
        empty = " · 빈 결과는 0(실측 · 환경마다 확인)" if self.pol.get("zero_if_empty") else ""
        unver = " · 명세값 · 실측 대조 전 [가정]" if self.pol.get("cost_unverified") else ""
        return f"크레딧(명세 {v}): {c['input']}×입력 키워드{tail}{empty}{unver}."

    def description(self):
        if self.reviewed:
            return f"{self.pol['guide']} {self.cost_line()}"
        base = self.ep.get("description") or self.ep.get("summary") or self.path
        return (f"[검토 전 · 명세에서 자동 추가된 도구] {base} {self.cost_line()} 응답 해석 규칙이 아직 없다 — "
                f"수치를 판정에 쓰기 전에 사람이 응답 모양을 확인한다.")


def _auto_name(ep, path, taken):
    base = "lm_" + re.sub(r"[^a-z0-9_]", "_", (ep.get("operation_id") or path.strip("/")).lower()).strip("_")
    name, i = base, 2
    while name in taken:
        name, i = f"{base}_{i}", i + 1
    return name


class Registry:
    """잠금 파일(명세)과 판단 파일(사람)을 합쳐 도구 목록을 만든다.

    - 판단 파일에 있는 경로 → 검토된 도구(기본으로 켜짐)
    - 명세에만 있는 경로 → 자동 추가된 도구(검토 전 · 기본으로 꺼짐 · 이름을 지정해야 켜진다)
    - 판단 파일에만 있는 경로 → 명세에서 사라진 것 — 도구를 만들지 않고 `orphans`에 남긴다
    """

    def __init__(self, lock, policy):
        self.lock, self.policy = lock, policy
        self.spec_version = lock.get("spec_version")
        hidden = set(policy.get("hidden_params", []))
        pols = policy.get("tools", {})
        self.tools, taken = {}, {p["name"] for p in pols.values()}
        for path, ep in lock.get("endpoints", {}).items():
            if path in pols:
                t = Tool(path, ep, pols[path], True, hidden, self.spec_version)
            else:
                name = _auto_name(ep, path, taken)
                taken.add(name)
                t = Tool(path, ep, {"name": name}, False, hidden, self.spec_version)
            self.tools[t.name] = t
        self.orphans = sorted(set(pols) - set(lock.get("endpoints", {})))

    @classmethod
    def load(cls, lock_path=LOCK_PATH, policy_path=POLICY_PATH):
        read = lambda p: json.loads(pathlib.Path(p).read_text(encoding="utf-8"))
        lock = read(lock_path) if pathlib.Path(lock_path).is_file() else {"endpoints": {}}   # 첫 sync 전
        return cls(lock, read(policy_path))

    def get(self, name):
        return self.tools.get(name)

    def default_names(self):
        return [n for n, t in self.tools.items() if t.enabled_default]

    def select(self, names=None):
        if not names:
            return [self.tools[n] for n in self.default_names()]
        bad = [n for n in names if n not in self.tools]
        if bad:
            raise ValueError(f"알 수 없는 도구: {bad} (가능: {sorted(self.tools)})")
        return [self.tools[n] for n in names]


REG = Registry.load()
# 이전 판과의 호환 — 모듈 수준 이름(검토된 도구 기준)
ENDPOINTS = {n: t.path for n, t in REG.tools.items()}
TOOL_SPECS = [{"name": t.name, "description": t.description(), "schema": t.schema} for t in REG.tools.values()]
DEFAULTS = {n: t.defaults for n, t in REG.tools.items()}

SYSTEM_RULES = """\
[ListeningMind 도구 사용 규칙]
1. 모든 수치에 라벨을 붙인다: [실제 데이터] API 반환값 · [추정] 해석·결합 · [가정] 게이트·임계값 · [데이터 공백] 미반환·결측·계산 불가.
2. 시점은 lm_meta.data_ref_month(데이터 기준월)로 말한다. 조회일이 아니다. 지연은 lm_meta.lag_months로 응답에서 읽는다(고정값이 아니다). 월 그레인이므로 주간 조기경보에 쓰지 않는다.
3. volume_trend_do_not_use는 판정에 쓰지 않는다. 추세는 yoy_ex_last_month, 기저 게이트 미달(below_base_gate)은 랭킹에서 뺀다.
4. canonical이 같은 키워드는 표기변형이다. dup_of가 있는 행은 같은 검색의 중복이라 한 번만 센다. 집계는 lm_meta.variant_groups 기준으로 하되, 그룹 볼륨은 volume_lo(하한·최댓값)와 volume_hi(상한·중복 제외 합)를 병기한다 [추정]. 한쪽만 고르거나 행을 그냥 합산하지 않는다. 순위·판정은 volume_lo 기준, volume_hi로 뒤집히면 그 사실을 적는다.
5. lm_meta.truncated가 true이거나 null(판정 불가)이면 «더 없다»고 말하지 않는다. limit을 올리거나 수렴 판정을 보류한다.
6. 도구가 budget_guard·per_call_cap으로 막히면 남은 예산(remaining)·줄일 limit(max_limit)을 사용자에게 알리고 범위를 줄인다. market_lock이면 이 세션에서 다른 시장을 조회하지 않는다. 우회하지 않는다.
7. 검색량 단위는 «건»이다(사람 수 아님). 검색은 관심 신호이지 구매가 아니다.
8. SERP는 저장된 스냅샷이다 — lm_meta.collected_at(수집 시각)을 적고 «지금 순위»라고 말하지 않는다.
9. 트렌드 이슈는 검색량이 아니다. 수요·추세 판정은 keyword_info로 하고, 검색량으로 확인되지 않은 이슈는 «이슈 신호만 · 검색량 미확인»으로 따로 적는다. 빈 결과는 «그 기간 매칭 없음»이라는 실측이다.
10. [검토 전] 도구의 응답은 해석 규칙이 없다 — 수치를 판정에 쓰지 않고 사람에게 확인을 요청한다.
"""


def canon(kw, rules=None):
    """표기변형 그룹키 — NFKC → 소문자 → 변형치환(공백 유지 상태) → 공백 제거 (series_dedup.canon)."""
    return _canon(str(kw), rules)


def tool_definitions(fmt, tools=None, registry=None):
    """tools = 켤 도구 이름 목록(None이면 검토된 도구 전부). 같은 목록을 Runtime(tools=…)에 넘겨야 런타임도 막는다."""
    specs = (registry or REG).select(tools)
    if fmt == "claude":
        return [{"name": t.name, "description": t.description(), "input_schema": t.schema} for t in specs]
    if fmt == "openai":
        return [{"type": "function", "function": {"name": t.name, "description": t.description(),
                                                  "parameters": t.schema}} for t in specs]
    raise ValueError(f"알 수 없는 형식: {fmt}")


def tools_from_definitions(path):
    """에이전트에 준 도구 정의 JSON(Claude·OpenAI 형식)에서 도구 이름을 읽는다 — Runtime(tools=…)용."""
    defs = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return [d.get("name") or (d.get("function") or {}).get("name") for d in defs]


# ── 크레딧 결정식 ──────────────────────────────────────────────────────────
# 결정식 = 입력 단가 × 입력 수 + 출력 단가 × 출력 수. 단가는 명세(잠금 파일)에서 온다.
# 호출 전 상한 [가정] — 반환 수를 호출 전에 모르는 도구의 출력 상한.
#  cluster: limit은 관계 행 수 상한이다. 저장된 실측 응답에서 노드 ≤ limit+1 → 기본 "observed".
#           관계 행 1개는 끝점 2개라 구조상 노드 ≤ 2×limit → 보수 옵션 "structural".
#  path   : limit은 경로 수 상한이다. 저장된 실측 응답에서 고유 키워드/limit이 3을 넘지 않았다
#           → 기본 배수 3.
CLUSTER_BOUNDS = {"observed": lambda lim: lim + 1, "structural": lambda lim: 2 * lim}
PATH_KW_MULT = 3.0


def n_inputs(tool, args):
    if tool.input_field == "keywords":
        return len(args.get("keywords") or [])
    return 1 if tool.input_field == "keyword" else 0


def estimate(tool, args, path_kw_mult=PATH_KW_MULT, cluster_bound="observed", registry=None):
    """호출 전 추정(상한). (값, 근거) — 값이 None이면 추정할 수 없어 막는다."""
    t = tool if isinstance(tool, Tool) else (registry or REG).get(tool)
    args = {**t.defaults, **args}
    u, n = t.unit(args), n_inputs(t, args)
    if u is None:
        if t.estimate_kind == "unpriced":
            return 0, "명세에 단가 없음 — 0으로 두고 cost_detail로 기록 [데이터 공백]"
        return None, "명세에 단가 없음 — 검토 전 도구라 추정 불가"
    fi, fo = u["input"], u["output"]
    lim = args.get("limit")
    kind = t.estimate_kind
    if kind == "exact" or not fo:
        return fi * n, f"{fi}×{n}입력 (출력 무과금) [실제 데이터·결정식]"
    if kind == "limit":
        return fi * n + fo * lim, f"{fi}×{n} + {fo}×limit {lim} (반환 ≤ limit 상한) [추정]"
    if kind == "cluster_bound":
        cap = CLUSTER_BOUNDS[cluster_bound](lim)
        why = "노드 ≤ limit+1 관측" if cluster_bound == "observed" else "노드 ≤ 2×limit 구조 상한"
        return fi * n + fo * cap, f"{fi}×{n} + {fo}×출력 상한 {cap} ({why}) [가정]"
    if kind == "path_mult":
        cap = math.ceil(path_kw_mult * lim)
        return fi * n + fo * cap, f"{fi}×{n} + {fo}×출력 상한 {cap} (limit {lim}×{path_kw_mult:g}) [가정]"
    # 검토 전 도구 — 명세의 limit 최댓값(없으면 요청 limit)을 출력 상한으로 둔다(최악 기준)
    if lim is None:
        return None, "출력 과금이 있는데 limit 인자가 없어 상한을 잡을 수 없다 — 검토 전 도구"
    cap = t.schema["properties"].get("limit", {}).get("maximum") or lim
    return fi * n + fo * cap, f"{fi}×{n} + {fo}×명세 최대 limit {cap} (검토 전 · 최악 기준) [가정]"


def _data(body):
    return body.get("data") if isinstance(body, dict) else body


def _kw(x):
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return x.get("keyword") or x.get("name") or x.get("id")
    return None


def _rel_ends(r):
    if isinstance(r, (list, tuple)):
        return [_kw(x) for x in r]
    if isinstance(r, dict):
        return [_kw(r.get("source") or r.get("from")), _kw(r.get("target") or r.get("to"))]
    return []


def _community_members(c):
    """communities는 실측 {id: [키워드…]} 모양이다. 목록형([{keywords:[…]}] · [[…]])도 받는다."""
    groups = c.values() if isinstance(c, dict) else (c or [])
    for g in groups:
        if isinstance(g, dict):
            g = g.get("keywords") or g.get("members") or []
        if isinstance(g, (list, tuple)):
            yield from (_kw(x) for x in g)


def cluster_nodes(data):
    """고유 키워드(노드) — nodes · rels 양끝 · communities 구성원의 합집합.

    실측 응답 두 모양: 기본(data_type=communities) = {"communities": {id: [kw…]}} ·
    data_type=all = {"rels": [[a, b], …], "communities": {…}}. 둘 다 cost_detail.output_count와 같다.
    """
    data = data if isinstance(data, dict) else {}
    names = {_kw(n) for n in (data.get("nodes") or [])}
    for r in data.get("rels") or []:
        names.update(_rel_ends(r))
    names.update(_community_members(data.get("communities")))
    return {n for n in names if n}


def path_keywords(data):
    return {k for p in (data or []) if isinstance(p, (list, tuple)) for k in p if k}



def _billed_inputs(tool, args, body):
    """과금된 입력 수 — 응답의 request_detail이 있으면 그것(서버가 받은 요청), 없으면 호출 인자.
    dry-run 픽스처가 다른 요청으로 만든 응답이어도 결정식 대조가 호출 순서에 기대지 않게 한다."""
    rd = (body or {}).get("request_detail") if isinstance(body, dict) else None
    if tool.input_field == "keywords" and isinstance(rd, dict) and isinstance(rd.get("keywords"), list):
        return len(rd["keywords"])
    return n_inputs(tool, args)


def output_count(tool, body):
    """결정식의 출력 수 — 판단 파일의 count 규칙으로 응답에서 직접 센다."""
    d = _data(body)
    if tool.count == "rows":
        return len(d) if isinstance(d, list) else 0
    if tool.count == "cluster_nodes":
        return len(cluster_nodes(d))
    if tool.count == "path_keywords":
        return len(path_keywords(d))
    if tool.count == "none":
        return 0
    # reported — 검토 전 도구: 응답이 스스로 적은 출력 수(단가 대조만 된다)
    return ((body or {}).get("cost_detail") or {}).get("output_count") or 0


def formula_cost(tool, args, body, registry=None):
    """응답으로 다시 계산한 결정식 비용 — cost_detail.total_cost와 대조한다. 단가가 없으면 None."""
    t = tool if isinstance(tool, Tool) else (registry or REG).get(tool)
    rd = (body or {}).get("request_detail") if isinstance(body, dict) else None
    u = t.unit({**t.defaults, **args, **({t.cost_by: rd[t.cost_by]} if t.cost_by and isinstance(rd, dict)
                                           and rd.get(t.cost_by) else {})})
    if u is None:
        return None
    out = output_count(t, body)
    if t.pol.get("zero_if_empty") and out == 0:
        return 0
    return u["input"] * _billed_inputs(t, args, body) + u["output"] * out
# ── 응답 주석 (함정 처리) ────────────────────────────────────────────────
def _shift(month, n):
    y, m = int(month[:4]), int(month[5:7])
    t = y * 12 + (m - 1) - n
    return f"{t // 12}-{t % 12 + 1:02d}"


def _acc(series, months):
    """(합계, 결측월수) — nv_code 월은 total 키가 없다. 0이 아니라 결측으로 센다."""
    tot = miss = 0
    for m in months:
        r = series.get(m)
        if r is None or "total" not in r:
            miss += 1
        else:
            tot += r["total"]
    return tot, miss


VARIANT_VOLUME_FIELD = "ads_metrics.volume_avg"
VARIANT_BOUNDS_NOTE = (
    "T5 — ① dup_of가 있는 행은 같은 검색의 중복(시계열 일치, 또는 볼륨·CPC·경쟁도 일치+토큰 겹침)이라 "
    "canonical이 달라도 한 번만 센다. ② 나머지 표기변형은 volume_lo = 그룹 내 최댓값(같은 검색일 경우) · "
    "volume_hi = 중복을 뺀 그룹 내 합(서로 다른 검색일 경우). 응답은 어느 쪽인지 알려주지 않는다 — 하나를 "
    "고르거나 합산하지 말고 두 값을 병기한다. 순위·판정은 volume_lo 기준으로 하고, volume_hi로 순위가 "
    "뒤집히면 그 사실을 적는다. [추정]")


def variant_bounds(rows):
    """T5 — 표기변형 그룹의 볼륨 하한(최댓값)·상한(중복 제외 합). 볼륨 없는 행은 0으로 치지 않고 따로 적는다."""
    vols, missing = [], []
    for r in rows:
        if r.get("dup_of"):
            continue                                     # 같은 검색의 중복 — 한 번만 센다
        v = (r.get("ads_metrics") or {}).get("volume_avg")
        if isinstance(v, (int, float)):
            vols.append(v)
        else:
            missing.append(r.get("keyword"))
    return {"keywords": [r.get("keyword") for r in rows], "volume_field": VARIANT_VOLUME_FIELD,
            "volume_lo": max(vols) if vols else None, "volume_hi": sum(vols) if vols else None,
            "volume_missing": missing,
            "duplicates": {r["keyword"]: r["dup_of"] for r in rows if r.get("dup_of")}}


def _variant_groups(rows):
    """canonical이 같은 행 + dup_of로 이어진 행을 한 그룹으로 묶는다(union-find). 키 = 대표 행의 canonical."""
    parent = {i: i for i in range(len(rows))}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    first = {}
    by_kw = {r.get("keyword"): i for i, r in enumerate(rows)}
    for i, r in enumerate(rows):
        j = first.setdefault(r["canonical"], i)
        parent[find(i)] = find(j)
        if r.get("dup_of") in by_kw:
            parent[find(i)] = find(by_kw[r["dup_of"]])
    groups = {}
    for i, r in enumerate(rows):
        groups.setdefault(find(i), []).append(r)
    out = {}
    for members in groups.values():
        rep = next((m for m in members if not m.get("dup_of")), members[0])
        out[rep["canonical"]] = members
    return out


SERIES_TYPES = ("all", "ads_info")   # monthly_volume 을 주는 data_type — 계약 §1 v1.3


def annotate_keyword_info(body, data_type, queried_at, base_gate, rules=None, requested=None):
    rows = [r for r in (_data(body) or []) if isinstance(r, dict)]
    dmap = dedup_map(rows, rules)                        # T5 ① 같은 검색의 중복 (series_dedup)
    for r in rows:
        r["canonical"] = canon(r.get("keyword", ""), rules)
        d = dmap.get(r.get("keyword"), {})
        r["dup_of"] = d["rep"] if d and d.get("rep") != r.get("keyword") else ""
        r["dup_basis"] = d.get("basis", "") if r["dup_of"] else ""
        am = r.get("ads_metrics") or {}
        if "volume_trend" in am:                         # T3 — 판정 근거에서 격리
            am["volume_trend_do_not_use"] = am.pop("volume_trend")
    groups = _variant_groups(rows)
    got = {r["canonical"] for r in rows}
    req = {canon(k, rules): k for k in (requested or [])}
    meta = {"rows": len(rows), "canonical_groups": len(groups),
            "variant_groups": {k: variant_bounds(v) for k, v in groups.items() if len(v) > 1},
            "variant_bounds_note": VARIANT_BOUNDS_NOTE,
            "variant_rules": dict(rules or {}),
            "not_returned": [k for c, k in req.items() if c not in got],
            "unrequested": [r["keyword"] for r in rows if req and r["canonical"] not in req],
            "queried_at": queried_at, "base_gate": base_gate,
            "labels": {"ads_metrics": "실제 데이터", "canonical": "가정", "variant_rules": "가정",
                       "dup_of": "추정", "volume_lo": "추정", "volume_hi": "추정",
                       "not_returned": "데이터 공백",
                       "yoy_ex_last_month": "실제 데이터", "below_base_gate": "가정",
                       "last_month_suspect": "추정", "missing_months": "데이터 공백"}}
    series_rows = [r for r in rows if r.get("monthly_volume")]
    if data_type in SERIES_TYPES and series_rows:
        ref = max(r["monthly_volume"][-1]["month"] for r in series_rows)
        qy, qm = int(queried_at[:4]), int(queried_at[5:7])
        meta.update(data_ref_month=ref,
                    lag_months=(qy - int(ref[:4])) * 12 + (qm - int(ref[5:7])))
        suspects = []
        ex_end = _shift(ref, 1)                          # T2 — 마지막 달을 뺀 3개월 창
        cur = [_shift(ex_end, i) for i in (2, 1, 0)]
        prev = [_shift(m, 12) for m in cur]
        for r in series_rows:
            s = {x["month"]: x for x in r["monthly_volume"]}
            a, m1 = _acc(s, cur)
            b, m2 = _acc(s, prev)
            r["missing_months"] = sum(1 for x in r["monthly_volume"] if "total" not in x)  # T6
            r["yoy_base_3m"] = b if not m2 else None
            if m1 + m2:
                r["yoy_ex_last_month"], r["yoy_status"] = None, "계산 불가(결측 월)"
            elif not b:
                r["yoy_ex_last_month"], r["yoy_status"] = None, "계산 불가(기저 0)"
            else:
                r["yoy_ex_last_month"], r["yoy_status"] = round(a / b - 1, 4), "ok"
            r["below_base_gate"] = (b < base_gate) if not m2 else True          # T4
            # 마지막 달 완결성: 이번 달이 실제로 떨어졌고 전년 같은 구간보다 30%p 이상 나쁘면 의심
            def mom(end):
                c, p = s.get(end), s.get(_shift(end, 1))
                if not c or not p or "total" not in c or "total" not in p or not p["total"]:
                    return None
                return c["total"] / p["total"] - 1
            now, ly = mom(ref), mom(_shift(ref, 12))
            r["last_month_suspect"] = bool(now is not None and ly is not None
                                           and now < 0 and now - ly < -0.30)
            if r["last_month_suspect"]:
                suspects.append(r.get("keyword"))
        meta.update(last_month_suspect=suspects, yoy_window=f"{cur[0]}~{cur[-1]} vs {prev[0]}~{prev[-1]}",
                    last_month_policy="yoy_ex_last_month는 마지막 달을 뺀 창이다")
    body["lm_meta"] = meta
    return body


def _meta_limit(lim, returned, truncated, convergence=None, **extra):
    """convergence를 주면 그 문구를 쓴다 — 절단의 원인이 «limit에 닿음»이 아닐 때(trend의 total > 반환)."""
    return {"requested_limit": lim, "returned": returned, "truncated": truncated,
            "convergence": convergence or ("보류 — limit에 닿음" if truncated else
                                           "보류 — 절단 판정 불가" if truncated is None else "limit 미만"),
            **extra}


def trend_truncation(lim, returned, total):
    """trend 절단 판정과 원인 — (절단 여부, 원인, 문구). 원인: limit(반환 = limit) · total(매칭 total > 반환) ·
    both · None. 매칭이 반환보다 많아도 반환이 limit 미만이면 «limit에 닿음»이 아니다(관측: limit 20 · 매칭 9 · 반환 7)."""
    at_limit = lim is not None and returned >= lim
    more = isinstance(total, int) and total > returned
    if at_limit and more:
        return True, "both", f"보류 — limit에 닿음 (매칭 {total}건 중 {returned}건 반환)"
    if at_limit:
        return True, "limit", "보류 — limit에 닿음"
    if more:
        return True, "total", (f"보류 — 매칭 {total}건 중 {returned}건만 반환 (limit {lim} 미만 — "
                               f"limit 때문이 아니다. limit을 올려도 나머지가 온다는 보장은 없다)")
    return False, None, None


def annotate_response(tool, body, args):
    """keyword_info 밖의 응답 주석. (body, 반환 수, 절단 여부) — 절단 여부가 의미 없는 도구는 None."""
    d = _data(body)
    lim = args.get("limit")
    kind = tool.annotate
    if kind == "list_limit":                              # intent — T7 반환 수가 limit에 닿았는가
        kws = [_kw(x) for x in (d or []) if _kw(x)]
        groups = {}
        for k in kws:
            groups.setdefault(canon(k), []).append(k)
        truncated = len(kws) >= lim
        body["lm_meta"] = _meta_limit(
            lim, len(kws), truncated,
            variant_groups={k: v for k, v in groups.items() if len(v) > 1},
            variant_groups_note="intent_finder는 볼륨을 반환하지 않는다 — 그룹 볼륨 경계"
                                "(volume_lo·volume_hi)는 lm_keyword_info로 조회해야 나온다 (T5)")
        return body, len(kws), truncated
    if kind == "cluster":
        # limit은 관계 행 수 상한(계약 §1). 관계 행이 limit에 닿거나 노드가 limit을 넘으면 절단.
        # 기본 응답(communities만)은 관계 행을 볼 수 없어, 노드 ≤ limit이면 판정 불가(null)로 둔다.
        dd = d if isinstance(d, dict) else {}
        returned = len(cluster_nodes(dd))
        rel_rows = len(dd["rels"]) if isinstance(dd.get("rels"), list) else None
        truncated = True if returned > lim or (rel_rows is not None and rel_rows >= lim) else \
            None if rel_rows is None else False
        body["lm_meta"] = _meta_limit(
            lim, returned, truncated,
            unit="고유 노드 (communities·rels 합집합 — cost_detail.output_count와 같은 단위)", rel_rows=rel_rows,
            truncation_rule="관계 행 ≥ limit 또는 노드 > limit → 절단 · rels 없고 노드 ≤ limit → 판정 불가 "
                            "(data_type=all로 다시 받으면 판정된다 · 비용 동일) [가정]")
        return body, returned, truncated
    if kind == "cluster_list":
        returned = len(d or [])
        truncated = True if returned > lim else None
        body["lm_meta"] = _meta_limit(
            lim, returned, truncated, unit="반환 키워드",
            truncation_rule="키워드 > limit → 절단 · 그 밖은 관계 행이 안 보여 판정 불가 — 구조가 필요하면 "
                            "lm_cluster_finder(data_type=all) [가정]")
        return body, returned, truncated
    if kind == "path":
        returned = len(d or [])
        truncated = returned >= lim
        body["lm_meta"] = _meta_limit(lim, returned, truncated,
                                      unit="경로 수 (limit은 경로 수 상한 — 관측 확인)",
                                      unique_keywords=len(path_keywords(d)))
        return body, returned, truncated
    if kind == "path_list":
        returned = len(d or [])
        body["lm_meta"] = _meta_limit(lim, returned, None, unit="반환 키워드",
                                      truncation_rule="limit은 경로 수 상한인데 이 응답엔 경로가 없다 — 판정 불가")
        return body, returned, None
    if kind == "trend":
        returned = len(d or [])
        total = body.get("total") if isinstance(body, dict) else None
        truncated, cause, conv = trend_truncation(lim, returned, total)
        body["lm_meta"] = _meta_limit(
            lim, returned, truncated, conv, truncation_cause=cause,
            matched_total=total, period_days=args.get("period_days"),
            empty_note="빈 결과는 오류가 아니라 «그 기간 매칭 없음» 실측 — period_days를 넓혀 한 번 더 본다" if not returned else None,
            evidence_note="이슈는 검색량이 아니다 — 수요·추세는 lm_keyword_info로 판정한다 · summary는 AI 생성 요약 [추정]")
        return body, returned, truncated
    if kind == "serp":
        blocks = d if isinstance(d, list) else []
        body["lm_meta"] = {"collected_at": body.get("collected_at"), "blocks": len(blocks),
                           "block_types": sorted({b.get("type") for b in blocks if isinstance(b, dict) and b.get("type")}),
                           "snapshot_note": "저장된 SERP 스냅샷이다 — 실시간 순위가 아니다. collected_at을 함께 적는다"
                                            + ("" if body.get("collected_at") else " · 수집 시각 없음 [데이터 공백]")}
        return body, len(blocks), None
    if kind == "usage":
        body["lm_meta"] = {"scope_note": "계정 전체 집계 — 다른 세션·파이프라인 소비가 섞인다. 이 세션 소진은 "
                                         "원장의 cost_detail 합산으로 말한다. total_credits는 잔여량이 아니다"}
        return body, None, None
    # generic — 검토 전 도구
    returned = len(d) if isinstance(d, list) else None
    truncated = (returned >= lim) if (lim is not None and returned is not None) else None
    body = body if isinstance(body, dict) else {"data": body}
    body["lm_meta"] = {"review": "검토 전 — 응답 해석 규칙이 없다. 수치를 판정에 쓰기 전에 사람이 확인한다",
                       "requested_limit": lim, "returned": returned, "truncated": truncated}
    return body, returned, truncated


def annotate_expansion(tool, body, args):
    """이전 판 호환 — annotate_response의 별칭(도구 이름도 받는다)."""
    t = tool if isinstance(tool, Tool) else REG.get(tool)
    return annotate_response(t, body, {**t.defaults, **args})


# ── 전송 ─────────────────────────────────────────────────────────────────
class HttpTransport:
    """실제 호출. 주소·키는 환경변수에서만 읽는다 — 코드·저장소·로그에 남기지 않는다."""

    def __init__(self, base=None, key=None, timeout=60, registry=None):
        self.base = (base or os.environ.get("LM_API_BASE") or "").rstrip("/")
        self.key = key or os.environ.get("LM_API_KEY") or ""
        if not self.base:
            raise RuntimeError("LM_API_BASE가 없다 — 저장소 README가 링크한 공개 API 문서의 주소를 넣을 것")
        if not self.key:
            raise RuntimeError("LM_API_KEY가 없다 — 발급받은 키를 환경변수로 넣을 것")
        self.timeout, self.reg, self.calls = timeout, registry or REG, 0

    def post(self, tool, payload):
        self.calls += 1
        t = self.reg.get(tool)
        headers = {"LM-API-KEY": self.key}
        if t.method == "GET":
            q = urllib.parse.urlencode({k: v for k, v in payload.items() if v is not None}, doseq=True)
            req = urllib.request.Request(f"{self.base}{t.path}?{q}", headers=headers, method="GET")
        else:
            req = urllib.request.Request(self.base + t.path, data=json.dumps(payload).encode(),
                                         headers={**headers, "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {"error": str(e)}


def _matches(cond, payload):
    return all(payload.get(k) == v for k, v in cond.items())


class MockTransport:
    """dry-run용. fixtures/<도구명>.json = 응답 목록 [{status, body, match?, times?, note?}].

    - `match`(요청 인자의 부분집합)가 있는 항목은 호출 순서와 무관하게 인자로 고른다. `times`가 있으면
      그 횟수만 쓰고 빠진다(예: 429 한 번 뒤 정상) · 없으면 계속 쓴다
    - `match`가 없는 항목은 순서대로 소비하고 마지막 하나는 반복한다
    - 맞는 항목이 없으면 404 `no_fixture` — 픽스처를 보충하라는 뜻이다
    - `echo_keywords: true`인 항목은 응답 행을 요청 키워드에 맞춘다(`echo_keywords()` — 합성 픽스처용).
      없으면 본문을 그대로 돌려준다 — 실측 응답 원문(요청하지 않은 변형 행 동반)은 그대로 재생해야 한다
    """

    def __init__(self, fixtures_dir):
        self.dir = pathlib.Path(fixtures_dir)
        self.queues = {}
        self.calls = 0

    def post(self, tool, payload):
        self.calls += 1
        if tool not in self.queues:
            f = self.dir / f"{tool}.json"
            self.queues[tool] = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else []
        q = self.queues[tool]
        item = next((it for it in q if it.get("match") and _matches(it["match"], payload)), None)
        if item is not None:
            if item.get("times") is not None:
                item["times"] -= 1
                if item["times"] <= 0:
                    q.remove(item)
        else:
            plain = [it for it in q if not it.get("match")]
            if not plain:
                return 404, {"error": "no_fixture", "tool": tool, "payload": payload}
            item = plain[0]
            if len(plain) > 1:
                q.remove(item)
        body = json.loads(json.dumps(item.get("body", {})))
        if item.get("echo_keywords") and isinstance(payload.get("keywords"), list):
            body = echo_keywords(body, payload["keywords"])
        return item.get("status", 200), body


def echo_keywords(body, requested):
    """합성 keyword_info 픽스처를 요청 키워드에 맞춘다 — 모의 전송 전용.

    - 요청 키워드와 같은 이름의 행은 그대로 둔다
    - 요청했는데 행이 없으면 요청 밖의 남는 행을 하나씩 가져와 이름만 바꾼다(`_mock_renamed_from`에 원래 이름).
      수치는 그 행의 합성값이다. 남는 행이 모자라면 복제하지 않고 비워 둔다 — 같은 시계열을 복제하면
      «같은 검색의 중복»(T5 ①)으로 잘못 잡힌다. 빈 키워드는 lm_meta.not_returned로 드러난다
    - 쓰지 않은 요청 밖 행은 버린다(실측 응답의 «함께 온 변형 행»은 흉내 내지 않는다 — 그건 실측 원문 픽스처로)
    - request_detail.keywords를 요청으로 바꾸고, cost_detail은 원래 요청의 키워드당 단가로 다시 맞춘다
    """
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        return body
    req = list(dict.fromkeys(str(k) for k in requested))
    have = {r.get("keyword"): r for r in rows}
    spare = [r for r in rows if r.get("keyword") not in req]
    out = []
    for k in req:
        if k in have:
            out.append(have[k])
        elif spare:
            r = spare.pop(0)
            r["_mock_renamed_from"], r["keyword"] = r.get("keyword"), k
            out.append(r)
    rd = body.get("request_detail") if isinstance(body.get("request_detail"), dict) else {}
    n0 = len(rd["keywords"]) if isinstance(rd.get("keywords"), list) and rd["keywords"] else len(rows)
    body["data"] = out
    body["request_detail"] = {**rd, "keywords": req}
    cd = body.get("cost_detail")
    if isinstance(cd, dict) and isinstance(cd.get("total_cost"), (int, float)) and n0:
        per = cd["total_cost"] / n0
        total = per * len(req)
        cd["total_cost"] = int(total) if float(total).is_integer() else total
        if "input_count" in cd:
            cd["input_count"] = len(req)
        if "input_total_cost" in cd:
            cd["input_total_cost"] = cd["total_cost"]
    return body


# ── 런타임 (예산 가드 + 원장) ─────────────────────────────────────────────
BLOCK_REASONS = ("budget_guard", "two_stage", "halted_after_overrun", "unknown_tool", "tool_not_enabled",
                 "invalid_args", "no_estimate", "per_call_cap", "market_lock")
# 1회 상한 권장값 [가정] — 기본 limit의 cluster(추정 5,200)·path(2,450)를 막아 limit을 줄이게 한다.
# 이 값이면 cluster는 limit ≤ 16(추정 1,000), path는 limit ≤ 7(추정 890)까지 한 번에 나간다.
RECOMMENDED_PER_CALL_CAP = 1000


class Runtime:
    def __init__(self, transport, budget, ledger_path, raw_dir=None, queried_at=None,
                 base_gate=3000, path_kw_mult=PATH_KW_MULT, cluster_bound="observed", tools=None,
                 variant_rules=None, strict_two_stage=True, sleep=time.sleep, max_429=5, max_5xx=3,
                 registry=None, pass_params=None, per_call_cap=None, market=None):
        """tools = 에이전트에 준 도구 이름 목록(None이면 검토된 도구 전부). 목록 밖 도구는 dispatch가 막는다.
        검토 전(명세에서 자동 추가된) 도구는 이름을 지정해야 열린다.
        variant_rules = 표기 치환 사전(dict · 인라인 JSON · 사전 파일 · themes.json) — 예: {"썬": "선"}.
        pass_params = 숨긴 인자 중 사람이 열기로 한 것(예: ["user_query"]) — 에이전트가 넘기면 그대로 보낸다.
        per_call_cap = 호출 1건의 사전 추정 상한(크레딧). None이면 끈다(세션 예산만 본다). 권장
        RECOMMENDED_PER_CALL_CAP(1,000) [가정]. 넘으면 보내지 않고 `per_call_cap`으로 막는다 — 예산 가드와 같은 추정.
        market = 세션 시장 고정. "kr" 같은 값이면 그 gl만, "auto"면 처음 전송한 호출의 gl로 잠근다. None이면 끈다.
        다른 gl은 보내지 않고 `market_lock`으로 막는다."""
        if cluster_bound not in CLUSTER_BOUNDS:
            raise ValueError(f"cluster_bound는 {sorted(CLUSTER_BOUNDS)} 중 하나")
        if per_call_cap is not None and (not isinstance(per_call_cap, (int, float)) or per_call_cap < 0):
            raise ValueError("per_call_cap은 0 이상의 숫자이거나 None")
        self.per_call_cap = per_call_cap
        self.market_mode = market
        self.market = None if market in (None, "auto") else str(market)
        self.reg = registry or REG
        self.t, self.budget, self.spent = transport, budget, 0
        self.ledger = pathlib.Path(ledger_path)
        self.raw = pathlib.Path(raw_dir) if raw_dir else None
        self.queried_at = queried_at or date.today().isoformat()
        self.base_gate, self.path_kw_mult, self.strict = base_gate, path_kw_mult, strict_two_stage
        self.cluster_bound = cluster_bound
        self.tools = {t.name for t in self.reg.select(tools)}
        self.pass_params = set(pass_params or [])
        self.rules = variant_rules if isinstance(variant_rules, dict) else load_rules(variant_rules)
        self.sleep, self.max_429, self.max_5xx = sleep, max_429, max_5xx
        self.screened, self.n, self.halted = set(), 0, False

    def _max_limit_under(self, t, args, cap):
        """cap 안에 드는 가장 큰 limit — 추정은 limit에 대해 단조 증가라 이분 탐색한다. 없으면 None."""
        lim = args.get("limit")
        if not isinstance(lim, int) or lim < 1:
            return None
        fits = lambda L: (lambda e: e is not None and e <= cap)(
            estimate(t, {**args, "limit": L}, self.path_kw_mult, self.cluster_bound)[0])
        if not fits(1):
            return None
        lo, hi = 1, lim
        while lo < hi:
            mid = (lo + hi + 1) // 2
            lo, hi = (mid, hi) if fits(mid) else (lo, mid - 1)
        return lo

    def _write(self, row):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open("a", encoding="utf-8") as f:
            f.write(json.dumps({k: row.get(k) for k in LEDGER_FIELDS}, ensure_ascii=False) + "\n")

    def _block(self, row, reason, out):
        row.update(status="blocked", block_reason=reason, spent_after=self.spent)
        self._write(row)
        return out

    def dispatch(self, tool, args):
        """에이전트의 tool_use/function_call을 받아 tool_result로 돌려줄 dict를 만든다."""
        self.n += 1
        args = dict(args or {})
        t = self.reg.get(tool)
        row = {"call_id": f"c{self.n:04d}", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "tool": tool, "endpoint": t.path if t else None, "spec_version": self.reg.spec_version,
               "gl": args.get("gl"), "data_type": args.get("data_type"), "budget": self.budget, "attempts": 0}
        if not t:
            return self._block(row, "unknown_tool", {"error": "unknown_tool", "tool": tool})
        if tool not in self.tools:                     # 도구 JSON에서 뺀 도구는 런타임도 막는다
            return self._block(row, "tool_not_enabled", {"error": "tool_not_enabled", "tool": tool,
                                                         "enabled": sorted(self.tools)})
        miss = [k for k in t.required if k not in args]
        extra = [k for k in args if k not in t.schema["properties"] and k not in self.pass_params]
        if miss or extra:
            return self._block(row, "invalid_args", {"error": "invalid_args", "missing": miss, "unexpected": extra})
        enum_bad = [k for k, v in args.items() if "enum" in t.schema["properties"].get(k, {})
                    and v not in t.schema["properties"][k]["enum"]]
        if enum_bad:
            return self._block(row, "invalid_args", {"error": "invalid_args", "not_in_enum": enum_bad})
        # 스키마 기본값을 채워 보낸다 — 생략된 limit이 API 기본값(더 큼)으로 나가 추정을 넘지 않게
        args = {**t.defaults, **args}
        row["data_type"] = args.get("data_type")
        est, basis = estimate(t, args, self.path_kw_mult, self.cluster_bound)
        row.update(n_inputs=n_inputs(t, args), requested_limit=args.get("limit"), estimate=est, estimate_basis=basis)

        gl = args.get("gl") if "gl" in t.schema["properties"] else None
        reason = None
        if self.halted:
            reason = "halted_after_overrun"
        elif self.market and gl is not None and gl != self.market:
            reason = "market_lock"                     # 세션당 시장 하나
        elif est is None:
            reason = "no_estimate"
        elif self.per_call_cap is not None and est > self.per_call_cap:
            reason = "per_call_cap"                    # 호출 1건 상한 — 예산 가드와 같은 사전 추정
        elif self.spent + est > self.budget:
            reason = "budget_guard"
        elif (t.pol.get("two_stage") and args.get("data_type") in SERIES_TYPES and self.strict
              and any(canon(k, self.rules) not in self.screened for k in args["keywords"])):
            reason = "two_stage"                       # 2단 조회 강제
        if reason:
            out = {"error": reason, "estimate": est, "spent": self.spent, "remaining": self.budget - self.spent}
            if reason == "two_stage":
                out["unscreened"] = [k for k in args["keywords"] if canon(k, self.rules) not in self.screened]
                out["hint"] = "먼저 data_type=ads_metrics로 조회하고 게이트 통과분만 ads_info(시계열만) 또는 all로 다시 요청할 것"
            if reason == "no_estimate":
                out["hint"] = basis
            if reason == "market_lock":
                out.update(market=self.market, gl=gl,
                           hint=f"이 세션의 시장은 {self.market} 하나다 — 다른 시장은 새 세션·새 예산으로 연다")
            if reason == "per_call_cap":
                ml = self._max_limit_under(t, args, self.per_call_cap)
                out.update(per_call_cap=self.per_call_cap, max_limit=ml,
                           hint=(f"호출 1건 상한 {self.per_call_cap:,}을 넘는다 — "
                                 + (f"limit을 {ml} 이하로 줄여 다시 요청" if ml else
                                    "입력 키워드 수를 줄이거나 나눠서 요청" if t.input_field == "keywords" else
                                    "이 도구는 이 상한 안에서 부를 수 없다 — 사용자에게 알린다")))
            return self._block(row, reason, out)
        if self.market_mode == "auto" and self.market is None and gl is not None:
            self.market = gl                           # 첫 전송 호출의 시장으로 잠근다

        status, body, n429, n5xx = None, None, 0, 0
        while True:
            row["attempts"] += 1
            status, body = self.t.post(tool, args)
            if status == 429 and n429 < self.max_429:
                self.sleep(min(60, 2 ** n429)); n429 += 1; continue      # 1→2→4→… 최대 60초
            if status >= 500 and n5xx < self.max_5xx:
                self.sleep(min(60, 2 ** n5xx)); n5xx += 1; continue
            break
        row["http_status"] = status
        if status != 200:
            row.update(status="error", actual_cost=None, spent_after=self.spent)
            self._write(row)
            return {"error": "http", "http_status": status, "detail": body,
                    "label": "데이터 공백 — 응답의 사유를 그대로 전할 것"}

        body = body if isinstance(body, dict) else {"data": body}
        cd = body.get("cost_detail") or {}
        actual = cd.get("total_cost")
        fcost = formula_cost(t, args, body)
        if t.annotate == "keyword_info":
            body = annotate_keyword_info(body, args["data_type"], self.queried_at, self.base_gate,
                                         self.rules, args["keywords"])
            returned, truncated = len(_data(body) or []), False
            if args["data_type"] == "ads_metrics":        # 요청 키워드 + 함께 돌아온 변형 행
                self.screened |= {canon(k, self.rules) for k in args["keywords"]}
                self.screened |= {r["canonical"] for r in (_data(body) or []) if isinstance(r, dict)}
        else:
            body, returned, truncated = annotate_response(t, body, args)
        self.spent += actual if actual is not None else (fcost or 0)
        if self.spent > self.budget:
            self.halted = True                         # 추정을 넘은 실제 비용 — 이후 호출 차단
        row.update(status="ok", returned=returned, truncated=truncated, formula_cost=fcost,
                   actual_cost=actual,
                   formula_match=(actual == fcost) if (actual is not None and fcost is not None) else None,
                   spent_after=self.spent, monthly_used_credits_seen=body.get("used_credits"))
        self._write(row)
        if self.raw:                                   # 응답 본문만 — 요청 헤더(키)는 저장하지 않는다
            self.raw.mkdir(parents=True, exist_ok=True)
            (self.raw / f"{row['call_id']}_{tool}.json").write_text(
                json.dumps(body, ensure_ascii=False), encoding="utf-8")
        body.setdefault("lm_meta", {}).update(call_cost=actual, spent=self.spent,
                                              remaining=self.budget - self.spent,
                                              tool_review="검토됨" if t.reviewed else "검토 전")
        return body


def make_runtime_from_env(budget, ledger_path, **kw):
    return Runtime(HttpTransport(registry=kw.get("registry")), budget, ledger_path, **kw)


# ── 원장 요약 ─────────────────────────────────────────────────────────────
def summarize(ledger_path, period, registry=None):
    reg = registry or REG
    rows = [json.loads(l) for l in pathlib.Path(ledger_path).read_text(encoding="utf-8").splitlines() if l.strip()]
    ok = [r for r in rows if r["status"] == "ok"]
    blocked = [r for r in rows if r["status"] == "blocked"]
    err = [r for r in rows if r["status"] == "error"]
    actual = sum(r["actual_cost"] or 0 for r in ok)
    est_ok = sum(r["estimate"] or 0 for r in ok)
    mism = [r for r in ok if r["formula_match"] is False]
    nocost = [r for r in ok if r["actual_cost"] is None]
    trunc = [r for r in ok if r["truncated"]]
    expansion = {n for n, t in reg.tools.items() if t.expansion}
    unk = [r for r in ok if r["truncated"] is None and r["tool"] in expansion]
    seen = [r["monthly_used_credits_seen"] for r in ok if r.get("monthly_used_credits_seen") is not None]
    versions = sorted({r.get("spec_version") for r in rows if r.get("spec_version")})
    L = [f"# 크레딧 원장 요약 — {period}", "",
         f"- 원장: `{pathlib.Path(ledger_path).name}` · 호출 {len(rows)}건 (성공 {len(ok)} · 차단 {len(blocked)} · 오류 {len(err)})",
         f"- 이번 세션 소진: **{actual:,}** 크레딧 [실제 데이터 — cost_detail.total_cost 합산]",
         f"- 성공 호출의 사전 추정 합: {est_ok:,} [추정 — 결정식에 limit·상한 대입]",
         f"- 예산: {rows[-1]['budget']:,} · 마지막 누적: {rows[-1]['spent_after']:,}" if rows else "- 원장이 비었다",
         f"- 단가 출처: 명세 {', '.join(versions)} (잠금 파일)" if versions else "- 단가 출처: 기록 없음",
         ""]
    if seen:
        L += [f"- 응답의 `used_credits` 마지막 값: {seen[-1]:,} [실제 데이터] — **월간 누적 사용량**이다. "
              "잔여량이 아니고, 이번 세션 소진도 아니다(다른 세션·파이프라인 소비가 섞인다).", ""]
    L += ["## 도구별", "", "| 도구 | 성공 | 차단 | 소진(cost_detail) | 결정식 재계산 |", "|---|---:|---:|---:|---:|"]
    for t in dict.fromkeys([r["tool"] for r in rows]):
        o = [r for r in ok if r["tool"] == t]
        b = [r for r in blocked if r["tool"] == t]
        tag = "" if (reg.get(t) and reg.get(t).reviewed) else " (검토 전)" if reg.get(t) else " (알 수 없음)"
        L.append(f"| `{t}`{tag} | {len(o)} | {len(b)} | {sum(r['actual_cost'] or 0 for r in o):,} | "
                 f"{sum(r['formula_cost'] or 0 for r in o):,} |")
    L += ["", "## 점검", ""]
    L.append(f"- 결정식 불일치: {len(mism)}건" + (" — " + ", ".join(
        f"{r['call_id']} {r['tool']} 식 {r['formula_cost']} ≠ 실제 {r['actual_cost']}" for r in mism)
        + " → 단가가 바뀌었거나 응답 구조를 잘못 셌다. `lm_tools.py sync`로 명세를 확인할 것" if mism else ""))
    L.append(f"- cost_detail 없는 성공 호출: {len(nocost)}건 [데이터 공백 — 결정식 값으로 누적함]")
    L.append(f"- 절단(limit에 닿음 · 또는 매칭 total > 반환): {len(trunc)}건" + (" — " + ", ".join(
        f"{r['call_id']} {r['tool']} {r['returned']}/{r['requested_limit']}" for r in trunc)
        + " → 이 호출들로 «더 없다»고 판정하지 않는다" if trunc else ""))
    L.append(f"- 절단 판정 불가: {len(unk)}건" + (" — " + ", ".join(
        f"{r['call_id']} {r['tool']} {r['returned']}/{r['requested_limit']}" for r in unk)
        + " → «더 없다»고 판정하지 않는다(cluster는 data_type=all로 받으면 판정된다)" if unk else ""))
    reasons = {}
    for r in blocked:
        reasons[r["block_reason"]] = reasons.get(r["block_reason"], 0) + 1
    L.append("- 차단 사유: " + (", ".join(f"{k} {v}건" for k, v in reasons.items()) or "없음"))
    L.append(f"- 오류: {len(err)}건" + (" — " + ", ".join(f"{r['call_id']} HTTP {r['http_status']}" for r in err) if err else ""))
    L += ["", "## 라벨 규약", "",
          "`[실제 데이터]` 응답 반환값 · `[추정]` 결정식에 limit을 대입한 사전 추정 · "
          "`[가정]` 노드·경로 상한(limit에서 유도), 기저 게이트 · `[데이터 공백]` cost_detail 미반환·오류 호출·명세에 단가 없음", ""]
    return "\n".join(L)


# ── 명세 추적 ─────────────────────────────────────────────────────────────
def fetch_spec(src):
    """명세 문서를 읽는다 — 파일 경로 또는 URL. 데이터 엔드포인트가 아니라 크레딧을 쓰지 않는다."""
    if re.match(r"^https?:", src):
        with urllib.request.urlopen(urllib.request.Request(src, headers={"Accept": "application/json"}),
                                    timeout=60) as r:
            return json.loads(r.read().decode())
    return json.loads(pathlib.Path(src).read_text(encoding="utf-8"))


def sync_report(old, new, policy):
    """(보고 줄, 종료 코드) — 0 같음 · 2 모양 변경(추가·삭제·인자) · 3 단가 변경."""
    changes = diff_lock(old, new)
    reg = Registry(new, policy)
    L = [f"명세 {old.get('spec_version') if old else '(잠금 없음)'} → {new.get('spec_version')} · "
         f"엔드포인트 {len(new['endpoints'])}개 · 검토된 도구 {len(reg.default_names())}개"]
    if not changes:
        L.append("변경 없음 — 잠금 파일과 운영 명세가 같다")
    for kind, path, what in changes:
        mark = {"added": "＋", "removed": "－", "cost": "＄", "shape": "～"}[kind]
        extra = ""
        if kind == "added":
            t = next(x for x in reg.tools.values() if x.path == path)
            extra = (f" → 도구 `{t.name}` 자동 추가" + ("" if t.reviewed else
                     " (검토 전 · 기본 꺼짐 — 이름을 지정해야 열린다. 검토 후 tool-policy.json에 항목을 넣으면 기본으로 켜진다)"))
        L.append(f"  {mark} {path} — {what}{extra}")
    for p in reg.orphans:
        L.append(f"  ！ tool-policy.json에만 있는 경로 {p} — 명세에서 사라져 도구를 만들지 않는다")
    if any(k == "cost" for k, _, _ in changes):
        L.append("단가가 바뀌었다 — 도구 설명·호출 전 추정·결정식이 새 단가를 쓴다. dry-run 픽스처의 cost_detail을 "
                 "새 단가로 다시 만들고, 실연결이면 작은 호출 1회로 formula_match를 다시 확인할 것")
    code = 3 if any(k == "cost" for k, _, _ in changes) else 2 if changes else 0
    return L, code


def check_lock(lock, policy):
    """잠금 파일만 점검한다(네트워크 없음) — 문제 목록. 운영 명세와 같은지는 모른다(그건 sync의 몫)."""
    bad = []
    if not isinstance(lock, dict) or not isinstance(lock.get("endpoints"), dict) or not lock["endpoints"]:
        return ["endpoints가 없거나 비었다"]
    for k in ("spec_version", "synced_at"):
        if not lock.get(k):
            bad.append(f"{k} 없음")
    for path, ep in sorted(lock["endpoints"].items()):
        if not isinstance(ep, dict):
            bad.append(f"{path}: 항목이 객체가 아니다"); continue
        if ep.get("method") not in ("GET", "POST"):
            bad.append(f"{path}: method {ep.get('method')!r}")
        params = ep.get("params")
        if not isinstance(params, dict):
            bad.append(f"{path}: params 없음"); continue
        miss = [r for r in ep.get("required") or [] if r not in params]
        if miss:
            bad.append(f"{path}: 필수 인자 {miss}가 params에 없다")
        for k, v in params.items():
            if v.get("type") == "integer":
                for f in ("minimum", "maximum", "default"):
                    x = v.get(f)
                    if x is not None and not (isinstance(x, int) or (isinstance(x, float) and x.is_integer())):
                        bad.append(f"{path}: {k}.{f} {x!r}가 정수가 아니다")
            if "enum" in v and "default" in v and v["default"] not in v["enum"]:
                bad.append(f"{path}: {k}.default {v['default']!r}가 열거값 밖")
        cost, by = ep.get("cost"), ep.get("cost_by")
        if cost is not None:
            if not isinstance(cost, dict) or not cost:
                bad.append(f"{path}: cost 모양"); continue
            for ck, u in cost.items():
                if not isinstance(u, dict) or not isinstance(u.get("input"), (int, float)) \
                        or not isinstance(u.get("output"), (int, float)):
                    bad.append(f"{path}: 단가 {ck} input·output이 숫자가 아니다")
            if by:
                enum = (params.get(by) or {}).get("enum") or []
                if by not in params:
                    bad.append(f"{path}: cost_by {by}가 인자에 없다")
                elif enum and set(cost) - set(enum):
                    bad.append(f"{path}: 단가 키 {sorted(set(cost) - set(enum))}가 {by} 열거값 밖")
            elif "*" not in cost:
                bad.append(f"{path}: cost_by 없이 '*' 단가가 없다")
    try:
        reg = Registry(lock, policy)
    except Exception as e:                              # 판단 파일과 합쳐 도구를 만들 수 없음
        return bad + [f"도구 생성 실패: {type(e).__name__}: {e}"]
    for p in reg.orphans:
        bad.append(f"tool-policy.json에만 있는 경로 {p} (잠금 파일에 없음)")
    for n, t in reg.tools.items():
        if t.reviewed and t.cost is None and t.estimate_kind != "unpriced":
            bad.append(f"{n}: 단가가 없는데 추정 방식이 unpriced가 아니다")
    return bad


# ── CLI ──────────────────────────────────────────────────────────────────
def _names(s):
    return [t.strip() for t in s.split(",") if t.strip()] if s else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    y = sub.add_parser("sync", help="운영 명세와 잠금 파일 대조 · --write면 갱신(새 엔드포인트 자동 추가)")
    y.add_argument("--spec", help="openapi.json 경로 또는 URL (생략하면 $LM_API_BASE/openapi.json)")
    y.add_argument("--lock", default=str(LOCK_PATH)); y.add_argument("--policy", default=str(POLICY_PATH))
    y.add_argument("--write", action="store_true"); y.add_argument("--synced-at")
    y.add_argument("--check-lock", action="store_true",
                   help="네트워크 없이 잠금 파일만 점검(모양·필수 인자·정수 경계·단가·판단 파일과의 짝) — 0 정상 · 1 문제")
    lst = sub.add_parser("list", help="도구 목록 · 단가 · 검토 상태")
    lst.add_argument("--lock", default=str(LOCK_PATH)); lst.add_argument("--policy", default=str(POLICY_PATH))
    s = sub.add_parser("schema"); s.add_argument("--format", choices=["claude", "openai"], required=True)
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--tools", help="켤 도구(쉼표 구분). 생략하면 검토된 도구 전부")
    sub.add_parser("rules")
    d = sub.add_parser("dry-run")
    d.add_argument("--scenario", required=True); d.add_argument("--fixtures", required=True)
    d.add_argument("--budget", type=int, required=True); d.add_argument("--ledger", required=True)
    d.add_argument("--raw-dir"); d.add_argument("--queried-at", default=date.today().isoformat())
    d.add_argument("--base-gate", type=int, default=3000)
    d.add_argument("--path-kw-mult", type=float, default=PATH_KW_MULT, help="path 키워드 상한 = limit × 배수 [가정]")
    d.add_argument("--cluster-bound", choices=sorted(CLUSTER_BOUNDS), default="observed",
                   help="cluster 노드 상한: observed=limit+1(관측) · structural=2×limit(보수) [가정]")
    d.add_argument("--tools", help="켤 도구(쉼표 구분) — 에이전트에 준 도구와 같게")
    d.add_argument("--tools-json", help="에이전트에 준 도구 정의 JSON — 여기서 도구 이름을 읽는다")
    d.add_argument("--rules", help="표기 치환 사전: 인라인 JSON · 사전 파일 · themes.json (예: '{\"썬\":\"선\"}')")
    d.add_argument("--per-call-cap", type=int,
                   help=f"호출 1건 사전 추정 상한(크레딧) — 생략하면 시나리오 config, 없으면 끔. 권장 {RECOMMENDED_PER_CALL_CAP:,} [가정]")
    d.add_argument("--market", help="세션 시장 고정: kr·us·jp 같은 gl 값 또는 auto(첫 전송 호출의 gl) — 생략하면 시나리오 config, 없으면 끔")
    m = sub.add_parser("summary"); m.add_argument("ledger"); m.add_argument("-o", "--out", required=True)
    m.add_argument("--period", default=date.today().strftime("%Y-%m"))
    a = ap.parse_args()

    if a.cmd == "sync" and a.check_lock:
        lock_p = pathlib.Path(a.lock)
        if not lock_p.is_file():
            print(f"잠금 파일 없음 — {lock_p.name}", file=sys.stderr)
            return 1
        try:
            lock = json.loads(lock_p.read_text(encoding="utf-8"))
        except ValueError as e:
            print(f"잠금 파일 JSON 오류: {e}", file=sys.stderr)
            return 1
        bad = check_lock(lock, json.loads(pathlib.Path(a.policy).read_text(encoding="utf-8")))
        print(f"잠금 파일 점검 (네트워크 없음) — 명세 {lock.get('spec_version')} · 동기화 {lock.get('synced_at')} · "
              f"엔드포인트 {len(lock.get('endpoints') or {})}개")
        for b in bad:
            print(f"  ✗ {b}")
        print("문제 없음 — 운영 명세와 같은지는 sync(네트워크)로 따로 본다" if not bad else f"문제 {len(bad)}건")
        return 1 if bad else 0
    if a.cmd == "sync":
        src = a.spec or ((os.environ.get("LM_API_BASE") or "").rstrip("/") + "/openapi.json"
                         if os.environ.get("LM_API_BASE") else None)
        if not src:
            print("명세 위치가 없다 — --spec 또는 LM_API_BASE를 줄 것", file=sys.stderr)
            return 1
        lock_p = pathlib.Path(a.lock)
        old = json.loads(lock_p.read_text(encoding="utf-8")) if lock_p.is_file() else None
        new = normalize_spec(fetch_spec(src), a.synced_at)
        policy = json.loads(pathlib.Path(a.policy).read_text(encoding="utf-8"))
        lines, code = sync_report(old, new, policy)
        print("\n".join(lines))
        if a.write:
            if old and not diff_lock(old, new):
                new["synced_at"] = old.get("synced_at", new["synced_at"])   # 변경 없으면 파일을 건드리지 않는다
            lock_p.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(f"→ {lock_p.name} 갱신" if code else f"→ {lock_p.name} 그대로")
            return 0
        if code:
            print("잠금 파일과 다르다 — 반영하려면 --write")
        return code
    reg = Registry.load(a.lock, a.policy) if a.cmd == "list" else REG
    if a.cmd == "list":
        print(f"명세 {reg.spec_version} · 동기화 {reg.lock.get('synced_at')} · 도구 {len(reg.tools)}개")
        for t in reg.tools.values():
            state = "기본 켜짐" if t.enabled_default else "검토 전 · 기본 꺼짐"
            print(f"  {t.name:<24} {t.method:<4} {t.path:<30} {state:<14} {t.cost_line()}")
        for p in reg.orphans:
            print(f"  ！ tool-policy.json에만 있는 경로 {p} (명세에 없음)")
        return 0
    if a.cmd == "schema":
        defs = tool_definitions(a.format, _names(a.tools))
        pathlib.Path(a.out).write_text(json.dumps(defs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"도구 {len(defs)}종 ({a.format} 형식 · 명세 {REG.spec_version}) → {a.out}")
        return 0
    if a.cmd == "rules":
        print(SYSTEM_RULES)
        return 0
    if a.cmd == "summary":
        pathlib.Path(a.out).write_text(summarize(a.ledger, a.period), encoding="utf-8")
        print(f"→ {a.out}")
        return 0

    # dry-run — 모의 응답만. 네트워크를 쓰지 않는다.
    scen = json.loads(pathlib.Path(a.scenario).read_text(encoding="utf-8"))
    pathlib.Path(a.ledger).unlink(missing_ok=True)
    tools = tools_from_definitions(a.tools_json) if a.tools_json else _names(a.tools)
    cfg = scen.get("config") or {}                     # 시나리오 config — CLI 인자가 우선한다
    cap = a.per_call_cap if a.per_call_cap is not None else cfg.get("per_call_cap")
    market = a.market or cfg.get("market")
    rt = Runtime(MockTransport(a.fixtures), a.budget, a.ledger, raw_dir=a.raw_dir, queried_at=a.queried_at,
                 base_gate=a.base_gate, path_kw_mult=a.path_kw_mult, cluster_bound=a.cluster_bound,
                 tools=tools, variant_rules=a.rules, sleep=lambda s: None, per_call_cap=cap, market=market)
    fails = []
    for i, step in enumerate(scen["steps"]):
        out = rt.dispatch(step["tool"], step["args"])
        got = "blocked" if out.get("error") in BLOCK_REASONS else "error" if out.get("error") else "ok"
        meta = out.get("lm_meta", {})
        tag = out.get("error") or ("truncated" if meta.get("truncated") else
                                   "trunc?" if meta.get("truncated", False) is None else "")
        print(f"  {i+1:>2}. {step['tool']:<24} {got:<8} {tag:<14} 누적 {rt.spent:,}/{rt.budget:,}")
        if step.get("expect") and step["expect"] != got:
            fails.append(f"{i+1}번 기대 {step['expect']} ≠ 실제 {got}")
        if step.get("expect_reason") and step["expect_reason"] != out.get("error"):
            fails.append(f"{i+1}번 차단 사유 기대 {step['expect_reason']} ≠ {out.get('error')}")
    rows = [json.loads(l) for l in pathlib.Path(a.ledger).read_text(encoding="utf-8").splitlines()]
    if len(rows) != len(scen["steps"]):
        fails.append(f"원장 행 {len(rows)} ≠ 호출 {len(scen['steps'])}")
    if rt.spent > rt.budget and not rt.halted:
        fails.append("예산 초과인데 런타임이 멈추지 않았다")
    mism = [r["call_id"] for r in rows if r["formula_match"] is False]
    if mism:
        fails.append(f"결정식 불일치 {mism}")
    guards = (f" · 1회 상한 {rt.per_call_cap:,}" if rt.per_call_cap is not None else "") + \
             (f" · 시장 {rt.market or '(auto · 미확정)'}" if rt.market_mode else "")
    print(f"\n원장 {len(rows)}행 · 소진 {rt.spent:,} / 예산 {rt.budget:,}{guards} · 전송 호출 {rt.t.calls}회 (모의) · "
          f"명세 {rt.reg.spec_version}")
    if fails:
        print("dry-run 실패:\n  " + "\n  ".join(fails))
        return 1
    print("dry-run 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
