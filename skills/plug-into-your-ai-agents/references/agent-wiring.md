# 에이전트 루프에 디스패처 끼우기

원칙은 하나다: **에이전트가 요청한 도구 호출을 커넥터로 직접 보내지 않고 `Runtime.dispatch`로
보낸다.** 디스패처가 돌려준 dict(막힌 경우의 `error` 포함)를 그대로 도구 결과로 돌려준다 —
에이전트가 `remaining`을 보고 범위를 줄일 수 있어야 하기 때문이다.

```python
import json, sys
sys.path.insert(0, "<이 스킬 폴더>/scripts")
from lm_tools import make_runtime_from_env, tool_definitions, SYSTEM_RULES

TOOLS = ["lm_keyword_info", "lm_intent_finder"]          # 에이전트에 줄 도구 — 두 곳에 같은 목록
tools = tool_definitions("claude", TOOLS)
rt = make_runtime_from_env(budget=1500, ledger_path="ledger.jsonl", raw_dir="raw",
                           tools=TOOLS, variant_rules={"썬": "선"})
# dry-run 단계에서는 대신:
#   from lm_tools import Runtime, MockTransport
#   rt = Runtime(MockTransport("assets/dryrun/fixtures"), 1500, "ledger.jsonl",
#                tools=TOOLS, sleep=lambda s: None)
```

`tools`를 런타임에 주지 않으면 검토된 도구(현재 9종)가 모두 열린다 — 도구 JSON에서 뺀 도구도 에이전트가
이름을 추측해 부르면 나간다. 명세에서 자동 추가된 «검토 전» 도구는 `tools`에 이름을 넣어야만 열린다. 이미 만든 JSON이 있으면 `tools_from_definitions("tools.claude.json")`로 읽어 넘긴다.
`variant_rules`는 시장의 철자 변형 치환 사전이다(인라인 dict · JSON 문자열 · 사전 파일 · themes.json의
`rules.변형치환`) — 없으면 공백 규칙만 쓰고, 한국어 선/썬 같은 변형은 따로 센다.

주소(`LM_API_BASE`)와 키(`LM_API_KEY`)는 환경변수로만 넣는다. 코드·저장소·로그·원장에
남기지 않는다. 응답 원본을 보존할 때도 요청 헤더는 저장하지 않는다.

## Claude tool use

도구 정의는 `schema --format claude` 출력(`name` · `description` · `input_schema`)을 그대로 쓴다.
시스템 프롬프트에 `SYSTEM_RULES`를 붙인다.

```python
# 응답의 content 중 type == "tool_use" 블록마다
results = []
for block in response.content:
    if block.type == "tool_use":
        out = rt.dispatch(block.name, block.input)
        results.append({"type": "tool_result", "tool_use_id": block.id,
                        "content": json.dumps(out, ensure_ascii=False)})
messages.append({"role": "assistant", "content": response.content})
messages.append({"role": "user", "content": results})
```

## OpenAI function calling

도구 정의는 `schema --format openai` 출력(`{"type": "function", "function": {...}}`)을 쓴다.

```python
msg = completion.choices[0].message
messages.append(msg)
for call in msg.tool_calls or []:
    out = rt.dispatch(call.function.name, json.loads(call.function.arguments))
    messages.append({"role": "tool", "tool_call_id": call.id,
                     "content": json.dumps(out, ensure_ascii=False)})
```

## 파이썬 함수를 도구로 받는 프레임워크

도구마다 얇은 함수를 만들어 등록한다. 함수 본문은 `rt.dispatch` 한 줄이어야 한다 —
여기서 커넥터를 직접 부르면 예산 가드와 원장을 건너뛴다.

```python
def lm_keyword_info(keywords: list[str], gl: str, data_type: str) -> dict:
    """도구 설명은 tool_definitions("claude")의 description을 그대로 쓴다."""
    return rt.dispatch("lm_keyword_info", {"keywords": keywords, "gl": gl, "data_type": data_type})
```

## 막혔을 때 에이전트가 받는 것

| `error` | 뜻 | 에이전트가 할 일 |
|---|---|---|
| `tool_not_enabled` | 에이전트에 주지 않은 도구 호출 | 받은 도구만 쓴다 — 필요하면 사용자에게 도구 추가를 요청 |
| `budget_guard` | 호출 전 추정이 남은 예산을 넘음 | `remaining`을 사용자에게 알리고 limit·키워드 수를 줄인다 |
| `two_stage` | `ads_metrics`로 확인하지 않은 키워드에 `all` 요청 | `unscreened`를 먼저 `ads_metrics`로 조회 |
| `halted_after_overrun` | 실제 비용이 가정 상한을 넘어 예산 초과 | 멈추고 사용자에게 보고 — 새 예산은 사람이 연다 |
| `http` | 재시도 후에도 실패 | `detail`의 사유를 그대로 전한다. 원인을 추측하지 않는다 |
| `invalid_args` · `unknown_tool` | 스키마 위반(필수 누락 · 스키마 밖 인자 · 열거값 밖 값) | 인자를 고쳐 다시 요청 |
| `no_estimate` | 검토 전 도구인데 출력 상한(limit)이 없어 비용을 추정할 수 없음 | 사용자에게 알린다 — 사람이 판단 파일에 규칙을 넣어야 열린다 |

## 사용자 시나리오 픽스처

번들 시나리오는 연결이 도는지만 보인다. 합격 기준의 두 번째 시나리오는 **사용자 에이전트가 실제로 낼
호출 순서**로 쓰고, 그 호출에 맞는 픽스처를 둔다. API 호출 0 · 크레딧 0이다.

**시나리오** (`my-scenario.json`) — 단계마다 도구·인자·기대 결과:

```json
{"steps": [
  {"tool": "lm_keyword_info", "args": {"keywords": ["선크림", "선스틱"], "gl": "kr", "data_type": "ads_metrics"}, "expect": "ok"},
  {"tool": "lm_keyword_info", "args": {"keywords": ["선크림"], "gl": "kr", "data_type": "all"}, "expect": "ok"},
  {"tool": "lm_cluster_finder", "args": {"keyword": "선크림", "gl": "kr", "limit": 10}, "expect": "blocked",
   "expect_reason": "tool_not_enabled"}
]}
```

`expect`는 `ok` · `blocked` · `error`, `expect_reason`은 차단 사유(`two_stage` · `budget_guard` ·
`tool_not_enabled` · `halted_after_overrun` …)다.

**픽스처** (`my-fixtures/<도구명>.json`) — 응답 목록. 항목 필드:

| 필드 | 뜻 |
|---|---|
| `body` | 응답 본문. **저장해 둔 실측 응답 원문을 그대로 넣는 것이 가장 좋다** — 응답 모양 결함은 합성으로 못 잡는다 |
| `status` | HTTP 상태(기본 200). 429·5xx로 재시도 경로를 시험한다 |
| `match` | 요청 인자의 부분집합(예: `{"data_type": "all"}` · `{"keyword": "선크림"}`). 있으면 **호출 순서와 무관하게** 인자로 고른다 |
| `times` | `match` 항목을 쓸 횟수(예: 429 한 번 뒤 정상). 없으면 계속 쓴다 |
| `note` | 설명(런타임은 읽지 않는다) |

`match`가 없는 항목은 순서대로 소비되고 마지막 하나가 반복된다(이전 판과 같은 동작). 맞는 항목이 없으면
전송이 404 `no_fixture`를 돌려준다 — 픽스처를 보충하라는 뜻이다.

**결정식 대조를 호출 순서에서 떼는 법**: `body`에 `request_detail`(실측 응답에는 원래 있다)과
`cost_detail.total_cost`를 **같은 요청 기준으로** 넣는다. 런타임은 keyword_info·intent의 결정식을
`request_detail.keywords` 수로 다시 계산하므로, 픽스처를 다른 키워드 수로 불러도 `formula_match`가
그 응답 자체의 정합성을 본다. `request_detail`이 없으면 호출 인자로 계산한다 — 이때는 픽스처의
`total_cost`가 시나리오의 요청과 맞아야 한다.

```json
[
 {"match": {"data_type": "all"}, "times": 1, "status": 429, "body": {"detail": "Too Many Requests"}},
 {"match": {"data_type": "all"}, "status": 200, "body": {"request_detail": {"keywords": ["선크림"], "data_type": "all"},
   "cost_detail": {"total_cost": 10}, "data": [{"keyword": "선크림", "ads_metrics": {"volume_avg": 65186}, "monthly_volume": []}]}},
 {"match": {"data_type": "ads_metrics"}, "status": 200, "body": "<저장한 ads_metrics 응답 원문>"}
]
```

돌린다:

```bash
python3 scripts/lm_tools.py dry-run --scenario my-scenario.json --fixtures my-fixtures \
    --budget 50000 --ledger ledger.jsonl --queried-at 2026-09-28 \
    --tools-json tools.claude.json --rules '{"썬": "선"}'
```

cluster·path 픽스처는 `assets/dryrun/real-shapes/`의 실측 응답 모양을 따른다 — cluster 기본 응답은
`data.communities = {id: [키워드…]}`, `data_type=all`이면 `data.rels = [[a, b], …]`가 더해진다.
path는 `data = [[키워드, …], …]`.


## 명세가 바뀌었을 때

도구는 운영 명세에서 만들어진다(`assets/api/api-lock.json` + `assets/api/tool-policy.json`). 명세 변경은
`sync`로 잡는다 — 명세 문서만 읽으므로 크레딧 0이다.

```bash
python3 scripts/lm_tools.py sync            # 0 같음 · 2 모양 변경 · 3 단가 변경
python3 scripts/lm_tools.py sync --write    # 잠금 파일 갱신 — 새 엔드포인트는 도구로 자동 추가
python3 scripts/lm_tools.py list
```

| `sync` 보고 | 뜻 | 할 일 |
|---|---|---|
| `＋` 새 엔드포인트 | 도구로 자동 추가됐다 — «검토 전» · 기본 꺼짐 · 추정은 명세 최대 limit(최악) | 작은 호출 1회로 응답을 본 뒤 `tool-policy.json`에 항목을 넣는다(아래) |
| `＄` 단가 변경 | 도구 설명·추정·결정식이 새 단가를 쓴다 | 픽스처 `cost_detail`을 새 단가로 다시 만들고 dry-run 재통과 · 실연결은 작은 호출로 `formula_match` 확인 |
| `～` 인자 변경 | 인자 스키마(열거값·기본값·최댓값)가 바뀌었다 | 에이전트에 준 도구 JSON을 `schema`로 다시 만든다 |
| `－` · `！` 삭제 | 명세에서 사라져 도구를 만들지 않는다 | 에이전트 도구 JSON·시나리오에서 뺀다 |

**검토 전 도구를 검토된 도구로 올리기** — `tool-policy.json`의 `tools`에 경로를 키로 항목을 넣는다:

| 키 | 뜻 | 값 |
|---|---|---|
| `name` | 도구 이름 | `lm_…` |
| `estimate` | 호출 전 추정 | `exact`(출력 무과금) · `limit`(반환 ≤ limit) · `cluster_bound` · `path_mult` · `unpriced`(명세에 단가 없음) |
| `count` | 결정식의 출력 수를 응답에서 세는 법 | `rows`(data 길이) · `cluster_nodes` · `path_keywords` · `none` |
| `annotate` | 응답 주석 | 기존 주석 이름 — 새 모양이면 `lm_tools.annotate_response`에 분기를 추가 |
| `unit` · `guide` | 도구 설명에 들어갈 출력 단위 · 사용 규칙 | 에이전트가 읽는 문장 |
| `defaults` · `enum_only` · `required_add` | 안전 기본값 · 열거값 제한 · 필수로 올릴 인자 | 선택 |
| `zero_if_empty` · `expansion` · `two_stage` | 빈 결과 무과금 · 절단 요약 대상 · 2단 조회 강제 | 선택(실측 근거를 `_note`로 함께 적는다) |

넣은 뒤 `selftest_tools.py`가 «검토 전 도구가 번들에 있다»를 더 이상 내지 않는지, 시나리오에 그 도구 단계와
픽스처를 추가해 dry-run이 통과하는지 본다.
