# 맥락 파일 스키마 — 우리 회사 스킬이 매번 읽는 설정

version: 1.0

> 정본. 맥락을 쓰는 스킬(`new_skill.py --context`)은 `references/`에 사본을 두고 따른다.

## 0. 왜 파일인가

에이전트는 스킬 폴더 밖을 읽지 않는다. 대화에서 받은 브랜드 · 경쟁사 · 용어는 대화가 끝나면 사라지고,
다음 요청에서 스킬은 우리 회사를 모른다. 그래서 **회사마다 다른 것은 스킬 `assets/`의 파일에 두고, 스킬 본체(SKILL.md ·
스크립트)는 업종 공통으로 둔다.** 같은 스킬을 다른 회사에 쓰려면 이 파일만 바꾼다.

| 파일 | 담는 것 | 바뀌는 때 |
|---|---|---|
| `assets/context.json` | 결정 · 국가 · 카테고리 경계 · 브랜드(우리 · 경쟁 · 브랜드 아님) · 용어 · 내부 지표 이름 · 가드레일 · 요청 예시 | 회사 · 담당이 바뀔 때 |
| `assets/validation-table.json` | 질문 ID별 검증표 — 데이터 · 비교 기간 · 대조 검색어 · 계산식 · 완료 기준 · 관측 사실 · 원인 가설 | 답할 질문이 바뀔 때 |
| `assets/themes.json` | 분류 사전(브랜드 목록 · 테마 · 표기 변형) — `themes-schema.md` | 브랜드 · 용어가 바뀔 때 |

요청마다 다른 것(이번 질문 · 기간 · 시드)은 파일이 아니라 **실행 기록**이다 — §3.

## 1. `context.json`

| 키 | 필수 | 뜻 |
|---|---|---|
| `schema` | ✅ | `lm-skill-context/v1` |
| `skill` | ✅ | 스킬 이름(frontmatter `name`과 같게) |
| `decision.who_decides` | ✅ | 누가 이 답으로 무엇을 결정하나 — 리포트 요약 · 실행 카드의 담당 기본값 |
| `decision.question_example` | ✅ | 현업에서 받는 질문 원문 |
| `market.gl` | ✅ | 국가 하나. 다른 국가 요청은 진행 전에 묻는다 |
| `market.category_boundary` | ✅ | 무엇이 안이고 무엇이 밖인지 한 문장 — 확장어 적합 판정 기준 |
| `brands.ours` · `competitors` · `not_brands` | ✅ | 배열. `not_brands`는 판매 채널 · 커뮤니티(브랜드 점유에서 뺀다) |
| `terms` | | 우리 용어 · 동의어 · 구분해야 하는 쌍 |
| `internal_metrics` | | 함께 볼 내부 지표 **이름**. 값 · 수치를 넣지 않는다 |
| `guardrails` | ✅ | 업종 표현 규칙 — 리포트 문구 검토 기준 |
| `request_examples` | ✅ | 이 스킬을 켜는 요청 예시 — description · eval 과 맞춘다 |

`⬜`가 남은 칸은 미작성이다. 감사기가 P0로 막는다.

## 2. `validation-table.json`

```json
{"schema": "lm-skill-validation-table/v1",
 "questions": [{"id": "Q1", "question": "…", "data": "…", "period": "…", "control_keywords": ["…"],
                "calculation": "…", "done_when": "…", "observed": "…", "hypothesis": "…"}]}
```

- `id`는 겹치지 않는다. 리포트 섹션 · 발견 카드 · 실행 카드가 이 ID로 근거를 단다(렌더링 계약).
- `done_when`은 숫자나 조건으로 쓴다 — «충분히 보이면» 같은 문장은 완료를 판정할 수 없다.
- `observed`와 `hypothesis`를 섞지 않는다. 원인은 검색 데이터만으로 확정하지 않는다.

## 3. 실행할 때 — STEP 0

1. `assets/context.json` · `validation-table.json` · `themes.json`을 읽는다.
2. 요청을 검증표 질문 ID에 대응시킨다. 맞는 ID가 없거나, 국가가 `market.gl`과 다르거나, 품목이 `category_boundary` 밖이면 **진행 전에 묻는다.**
3. 실행 폴더 `work/<스킬>/<실행 ID>/`(**에이전트를 연 작업 공간 기준** — 스킬을 설치한 곳. 키트에서 만들 때는 키트 루트, 분석 작업 공간에 설치하면 그 작업 공간)를 만들고 `request.json`을 쓴다:
   `{"request": 요청 원문, "question_ids": [...], "gl": …, "period": …, "seeds": [...], "context_version": context.json 해시}`.
4. 견적을 보여 주고 승인을 받은 뒤 수집한다. 원본 응답 · 호출 원장 · 리포트는 모두 이 실행 폴더에 쓴다.
5. 리포트 데이터 범위에 이번 실행에 쓴 맥락(국가 · 경계 · 질문 ID)을 적고, 질문 ID별 상태(답함 · 일부 · 미해결 · 원인 가설)를 부록에 둔다.

## 4. 만드는 곳과 돌리는 곳

- **만드는 곳**: 이 키트(lm-skills). `lm-skill-creator`가 키트 전체(공유 계약 · 스크립트 정본)를 읽으므로 여기서 만든다. 키가 없어도 된다.
- **돌리는 곳**: API 키와 API 연결이 있는 작업 공간. 완성한 스킬 폴더를 그 작업 공간의 스킬 위치(Claude Code면 `.claude/skills/`)로 복사한다.
- **API 호출**은 설치된 작업 공간의 연결과 키를 쓴다. 스킬 폴더 안에 키 · 주소를 두지 않는다 — 작업 공간마다 키를 따로 넣지 않게 하고, 스킬 폴더를 넘길 때 키가 따라가지 않게 한다.

## 5. 보관

- 회사 맥락이 담긴 스킬 폴더는 **공개 키트에 되돌려 보내지 않는다.** 회사 저장소에 따로 둔다.
- `work/`는 키트 `.gitignore`에 있다 — 실행 결과를 커밋하지 않는다.
- 내부 지표 값은 어느 파일에도 넣지 않는다. 이름 · 기간 · 단위만 적는다.
