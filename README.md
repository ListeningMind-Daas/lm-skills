# lm-skills

ListeningMind DaaS 검색 데이터로 시장을 읽는 **에이전트 스킬 모음**입니다. Claude Code·Claude Desktop 등 스킬을 읽는 에이전트 호스트에 그대로 넣어 쓸 수 있습니다.

MIT 라이선스입니다. 데이터는 포함돼 있지 않습니다 — 스킬은 여러분의 ListeningMind DaaS API 키로 동작합니다.

## 들어 있는 것

| 스킬 | 하는 일 | 쓰는 커넥터 |
|---|---|---|
| `map-your-market` | 시드 키워드에서 연관 쿼리로 카테고리 키워드 유니버스를 넓히고, 새 수요가 더 나오지 않는 지점에서 멈춘 뒤 검색량·추세·논브랜드 분리를 붙여 CSV와 커버리지 리포트로 낸다 | keyword_info · intent_finder |
| `decode-search-intent` | 검색어를 니즈→목표→해결책→제품→브랜드 5층과 탐색·비교·구매 단계로 나눠 검색량 비중으로 보여준다 | intent_finder · keyword_info |
| `trace-search-journey` | 이 검색 앞뒤에 무엇을 검색하는지 세션 인접 경로로 세어 단계 전이·다음 검색 표로 보여준다 (예산 게이트 포함) | path_finder · keyword_info |
| `discover-sub-markets` | 공동검색 군집에서 브랜드 묶음·표기변형을 걸러내고, 시드와 떨어져 있지만 논브랜드 검색량이 있는 하위 시장 후보를 찾는다 | cluster_finder · keyword_info |
| `spot-trends` | 시드 주변 급부상 이슈에서 후보를 받아 월별 검색량으로 라이징·하강 신호를 가려낸다 — 이슈 근거 첨부 · 검색량으로 아직 확인되지 않은 이슈는 따로 적는다 | trend_finder · keyword_info |
| `track-search-share` | 브랜드별 검색 점유율(SoV)을 표기변형·품목 미특정 검색을 걷어내고 논브랜드를 분리해 산출한다 — 검색량 기준 근사, 판매 점유율 아님 | keyword_info · intent_finder |
| `separate-buyers-from-browsers` | 키워드를 규칙 사전으로 거래 의도·브랜드 지명·탐색·구매 후·중고로 나눠, 거래 의도 검색의 비중을 보여준다 | keyword_info |
| `find-customer-needs` | 검색어를 기능·속성·불편 3축으로 나눠 니즈를 검색량과 전년 대비 추세로 줄 세운다 | intent_finder · keyword_info |
| `benchmark-competitors` | 자사·경쟁 브랜드의 월간 검색 점유율과 수식어 구성을 월별 패널로 비교하고 전년 동월 대비 갭 변화를 알린다 (월 단위, 실시간 아님) | keyword_info |
| `validate-product-ideas` | 제품 가설이 기대는 검색 수요가 문제·목표·해결책·제품 층마다 있는지, 커지는지, 브랜드에 잠겨 있는지 수요 신호 카드로 확인한다 (출시 판정 아님) | keyword_info · intent_finder |
| `compare-markets-across-countries` | 같은 카테고리를 한·미·일에서 국가별 동의어 매핑으로 묶어 나라 안 비중 지수·전년 동기 성장·시즌 형태를 비교한다 | keyword_info |
| `plug-into-your-ai-agents` | API 운영 명세의 엔드포인트 전부(현재 9개)를 여러분의 AI 에이전트에 도구로 연결하고, 호출 전 예산 가드와 크레딧 원장을 붙여 모의 실행으로 먼저 검증한다 — 명세가 바뀌면 `sync`가 알리고 새 엔드포인트는 도구로 자동 추가된다 | 운영 명세 전부 (keyword_info · intent_finder · cluster_finder · path_finder · serp · trend_finder · keyword_list 2종 · usage) |
| `lm-skill-creator` | 같은 규율을 지키는 새 스킬을 만든다 · 기존 스킬을 감사한다 | — |

### 이전 카탈로그와의 대응

v1.5의 태스크 스킬 12종은 이전에 카드로 공개했던 카탈로그 12종을 `lm-skill-creator` 골격으로 다시 만든 것입니다. 각 스킬 문서의 «카드 NN»은 아래 번호를 가리킵니다.

| 카드 | 이전 이름 | 이 저장소의 스킬 |
|---|---|---|
| 01 | map-your-market | `map-your-market` |
| 02 | decode-why-people-search | `decode-search-intent` |
| 03 | see-the-full-buying-path | `trace-search-journey` |
| 04 | discover-hidden-sub-markets | `discover-sub-markets` |
| 05 | spot-trends-6-months-early | `spot-trends` |
| 06 | track-your-true-market-share | `track-search-share` |
| 07 | separate-buyers-from-browsers | `separate-buyers-from-browsers` |
| 08 | find-what-customers-actually-want | `find-customer-needs` |
| 09 | benchmark-competitors-live | `benchmark-competitors` |
| 10 | validate-ideas-before-you-build | `validate-product-ideas` |
| 11 | compare-markets-across-countries | `compare-markets-across-countries` |
| 12 | plug-into-your-ai-agents | `plug-into-your-ai-agents` |

이름을 바꾼 것은 데이터가 뒷받침하지 못하는 약속(«전체 구매 경로», «진짜 점유율», «실시간», «6개월 앞서»)을 빼기 위해서입니다. 검색 데이터는 월 단위이고, 점유는 검색량 기준 근사이며, 여정은 같은 세션에서 앞뒤로 이어진 검색만 잡습니다.

`shared/`에는 모든 스킬이 공유하는 데이터 계약(`lm-data-contract.md`)과 스크립트가 있습니다. 각 스킬 폴더에는 그 사본이 들어 있고(에이전트는 스킬 폴더 밖을 읽지 않습니다), `shared/verify_shared.py`가 사본이 낡지 않았는지 해시로 검사합니다.

`lm-skill-creator`만은 예외로 **저장소 전체(클론)에서 실행**합니다 — 새 스킬 골격을 루트의 `shared/` 정본에서 복사해 만들기 때문입니다.

## 버전과 변경 이력

- 키트 버전은 루트 `VERSION`, 스킬 버전은 각 SKILL.md frontmatter `metadata.version`이다. 둘은 따로 올라간다.
- 스킬별 변경 이력은 같은 폴더의 `CHANGELOG.md`에 있다 — SKILL.md는 에이전트가 매번 읽는 절차서라 지난 판의 결함 서술을 섞지 않는다.

## 시작하기

```bash
# 1) 스킬을 에이전트가 읽는 위치에 둔다 (예: Claude Code)
cp -r skills/map-your-market ~/.claude/skills/

# 2) API 키를 환경변수로 (또는 호스트가 안내하는 방식으로)
export LM_API_KEY="발급받은 키"
#    plug-into-your-ai-agents 는 API 주소도 받는다 — 아래 공개 API 문서에 적힌 주소를 넣는다
export LM_API_BASE="공개 API 문서의 서버 주소"

# 3) 문서의 예시 명령이 실제로 도는지 확인 — API 호출도 크레딧도 쓰지 않는다
python3 ~/.claude/skills/map-your-market/scripts/selftest.py
```

그다음 에이전트에게 평소 말로 요청하면 됩니다: "선크림 시장 지도 만들어줘".

## 크레딧을 쓰기 전에 알아야 할 것

- **호출은 과금됩니다.** 스킬은 단계마다 예상 비용을 먼저 알리고, 예산 안에서 수집 범위를 정합니다. 단가의 정본은 [공개 API 문서](https://listeningmind-data-api.ascentlab.io/)입니다.
- **수치에는 출처 표시가 붙습니다.** 실제 데이터 / 추정 / 가정 / 데이터 공백 네 가지를 구분해서 적습니다. 구분이 없는 숫자는 이 스킬의 산출물이 아닙니다.
- **검색 데이터가 보여주는 것은 검색 행동입니다.** 구매도, 사람 수도 아닙니다. 산출물은 그 한계를 문장으로 함께 적습니다.

## 이 스킬들이 지키는 함정 8종

같은 데이터를 다뤄 본 사람이 반복해서 밟는 함정을 계약으로 고정했습니다 — 기준월·지연(T1), 마지막 달 미완결(T2), `volume_trend` 대체(T3), 기저 게이트(T4), 표기 변형 정규화(T5), 결측 월(T6), 절단 검사(T7), 논브랜드 분리(T8).

`skills/lm-skill-creator/scripts/audit_skill.py <스킬 폴더>`로 어떤 스킬이든 이 계약을 지키는지 정적 감사를 돌릴 수 있습니다. 이 저장소의 13개 스킬은 P0·P1·P2 모두 0건입니다.

## 만든 곳

[ListeningMind](https://www.listeningmind.com) · Ascent Korea
