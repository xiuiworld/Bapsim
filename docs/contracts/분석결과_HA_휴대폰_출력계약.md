# 분석 결과·HA·휴대폰 출력 계약 설계서

2026-10-02 · 팀 합의 반영 · 입력 JSON 계약과 별도

## 1. 추천 연결 방식

**상태·화면은 로컬 MQTT → HA MQTT 엔티티, 알림은 B → HA 알림 서비스 호출**로 연결하는 안을 추천한다. MQTT는 보드의 Matter 통신을 대체하지 않고 분석 결과를 HA로 되돌리는 데만 사용한다. 로컬 브로커 한 개가 추가되는 비용이 있다.

HA는 MQTT discovery와 availability를 지원한다. B가 엔티티 정의와 상태를 제공하고 A가 브로커·HA 접속 환경을 구성하는 방식이다. [HA MQTT 안내](https://www.home-assistant.io/integrations/mqtt/)

대안은 HA REST 상태 API로 가상 상태를 게시하는 것이다. 추가 브로커는 줄지만, 이 API는 실제 기기를 생성·제어하는 것이 아니며 엔티티 등록·재시작 복원·상태 만료를 별도로 다뤄야 한다. 이번 추천은 수명주기 처리를 명시하기 쉬운 MQTT다. [HA REST API](https://developers.home-assistant.io/docs/api/rest/)

D04에서 MQTT 상태 반환과 HA 알림 호출을 채택했다. 아직 설치나 서비스 생성은 하지 않았다.

## 2. 결과 상태 메시지

UTF-8 JSON `schema_version=0.1.0`. 한 가정에 대한 현재 상태를 한 객체로 게시한다. 식별자와 UTC 시각 규칙은 [입력 계약](AB_데이터_계약.md)을 따른다.

| 필드 | 타입·값 | 의미 |
|---|---|---|
| schema_version | string | `0.1.0` |
| household_id | string | 가정 식별자 |
| revision | integer ≥ 0 | 저장된 결과 변경 순서. 재시작에도 계속 증가 |
| generated_at | UTC string | 평가 결과 생성 시각 |
| valid_until | UTC string | 이 결과를 현재 판단으로 사용할 만료 시각 |
| last_usage_at | UTC string 또는 null | 마지막 유효한 사용 묶음 시작 시각. 실제 식사 시각 아님 |
| learning | `collecting` / `personalized` / `insufficient` | 개인 기준 상태 |
| observation | `available` / `partial` / `unavailable` | 관측 가능 범위 |
| sensor_issues | string 배열 | 오류/미지원으로 판단을 제한하는 신호 |
| assessments | 객체 배열 | 아래 판단 항목. 단기 공백과 장기 감소 동시 존재 허용 |
| summary | string | 첫 화면 안내, 짧은 한국어 문장 |
| policy_version / model_version | string | 정책·개인 패턴 모델 버전 |

assessment 필수 필드: `assessment_id`(string), `type`(`usage_gap` 또는 `declining_usage`), `status`(`watch`/`deferred`/`notified`/`resolved`), `reason_codes`(string 배열), `evidence_ids`(관측/행동 ID 배열), `next_evaluate_at`(UTC 또는 null).

reason_codes의 첫 목록은 `expected_window_missed`, `unusual_gap`, `declining_frequency`, `auxiliary_activity`, `quiet_hours`, `observation_gap`, `usage_resumed`다. 상태 모델은 각 판단별로 관리하고 learning/observation을 하나의 상태 enum으로 합치지 않는다. 목록에 없는 코드를 임의로 의미 추정하지 않는다.

결과 만료는 **게시 주기 60초, 유효 180초**를 시작값으로 제안한다. 변화가 없어도 게시 시각과 만료를 갱신한다. 계산 프로세스가 멈췄는데 오래된 ‘정상’이 그대로 보이는 것을 방지하려는 서비스 기준이며 센서 보고 주기와 다르다.

## 3. MQTT 게시 규칙

- 상태 topic: `bapsim/v1/{household_id}/state`, QoS 1, retain=true 제안.
- 연결 topic: `bapsim/v1/{household_id}/availability`, online/offline. LWT offline, 정상 종료도 offline.
- simulation/replay는 별도 시험 broker 또는 별도 topic prefix를 사용하고 실제 알림 대상과 분리한다. 이번 HA 내부 개발 구성은 `bapsim/simulation/v1/{household_id}` prefix와 simulator 모델 키를 사용하며 시뮬레이터의 모바일 발송을 차단한다.
- Discovery unique_id는 가정+항목으로 안정적으로 생성한다. B 재시작/HA 재시작 후 필요한 discovery와 현재 상태를 다시 게시한다.
- HA는 LWT와 결과 만료를 함께 반영한다. 연결만 살아 있고 분석이 멈춘 경우도 오래된 상태로 표시한다. 만료 판정은 B가 아닌 HA 측 템플릿/자동화에서도 수행한다.
- HA 내부 분석 모드의 실물·시뮬레이션 화면 엔티티는 각각 자기 `valid_until` 시각에 `expired`로 바뀐다. 두 출처의 MQTT discovery에도 각각 `expire_after: 180`을 둔다. HA 재시작 직후 새 평가 전에는 실물 `awaiting_matter`, 시뮬레이션 `waiting`, MQTT 게시 상태 `waiting`을 표시한다. `bapsim.test_pause_simulation_output`은 최대 600초 동안 시뮬레이션 결과 발행만 멈춰 이 만료를 시험하며, 실물 결과와 분석은 계속한다.
- 같은 revision 재수신은 같은 결과다. QoS 1은 중복 가능하므로 수신이 정확히 한 번이라고 가정하지 않는다.
- 발송 명령은 retain된 상태 topic에 넣지 않는다. HA 재접속으로 이전 알림이 다시 실행되지 않도록 한다.

추천 표시 엔티티: 최근 사용 시각, 학습 상태, 관측 상태, 현재 안내 요약, 상세 판단 속성. 엔티티의 실제 이름과 discovery payload는 B가 구성 파일로 제공한다. 전체 관측 기록을 MQTT 속성 한 개에 끝없이 누적하지 않는다.

## 4. 알림 요청과 결과

B가 다음 내부 명령을 영속 저장한 뒤 HA의 `notify.mobile_app_<등록된_휴대폰>` 서비스를 호출한다. 대상 이름은 A가 실제 폰 등록 후 설정으로 전달한다. Companion 앱은 알림 서비스와 플랫폼별 클릭 링크를 지원한다. [Companion 알림 안내](https://companion.home-assistant.io/docs/notifications/notifications-basic/)

| 필드 | 타입 | 의미 |
|---|---|---|
| notification_id | string | 논리 발송 ID. 재시도에도 유지 |
| assessment_id | string | 어떤 공백/추세/관측 문제의 안내인지 |
| type | `check_in` / `usage_resumed` / `observation_issue` | 발송 목적 |
| title / message | string | 실제 폰 문구 |
| created_at / expires_at | UTC string | 생성·유효 시각. 지난 안내는 재발송하지 않음 |
| detail_path | string | 해당 가정의 HA 상세 화면 경로 |
| mode | `live` / `simulation` / `replay` | 실제 발송은 live만 허용 |

발송 이력은 `pending`, `ha_accepted`, `failed`, `uncertain`, `cancelled`로 저장한다. HA 응답 성공은 ha_accepted이며 폰 수신·읽음과 동일하지 않다. 성공 응답 후 기록 저장 전에 장애가 나면 uncertain일 수 있으므로 정확히 한 번의 푸시를 보장하지 않는다.

명백한 전송 전 실패는 유효기간 안에서 30초/2분/5분 간격 최대 3회 재시도를 제안한다. 재시도 전 해소·방해금지·관측 조건을 다시 평가한다. 응답 유실 등 결과 불명확이면 자동 재시도를 멈추고 로그에 남기는 안을 추천한다. 복구 후 과거 큐를 일괄 전송하지 않는다. 같은 판단의 다음 정책상 재안내는 새로운 notification_id를 사용한다.

## 5. 휴대폰 화면과 상세 이력

첫 화면: 최근 사용, 평소 대비 변화, 안부 확인 안내, 관측/학습 상태. 상세 화면: 최근 사용 묶음·개폐/취사 추정·보온 추정·알림 사유. 관측과 추정을 문구로 구분한다.

첫 구현은 HA 기본 카드와 HA Recorder 이력을 활용하는 안을 추천한다. 세밀한 분석 근거는 B의 저장소에 보존하고, HA에는 최근 요약·제한된 이력만 게시한다. 필요한 UI 범위가 기본 카드로 부족하면 별도 앱 대신 HA 카드/화면 보완 범위를 먼저 검토한다. 프런트엔드의 실현 가능성도 B의 구현 과제다.

알림의 Android/iOS 링크 필드 차이는 실제 대상 폰에 맞춰 설정한다. ‘푸시 수신’과 ‘누르면 상세 화면 열림’을 각각 시험한다. 부모님 집 밖에서는 원격 HA 접속 경로가 필요하다. 외부 접속은 HA Cloud 무료 체험으로 구성한다. 세부 운영은 [운영 계획](../planning/설치_운영_통합시험_계획.md)을 따른다.

## 6. 보존·책임 추천

B의 로컬 저장소에는 정규화 관측 90일, 사용 묶음·개인 기준·판단·발송 이력 180일을 제안한다. A의 학습용 원시 센서 기록은 별도 관리한다. 보존 기간은 D12에서 채택했다. 로컬 파일/SQLite 등 구체 저장 구현은 B가 선택하되 재시작 후 상태 복원과 버전 추적을 보장한다.

이번 HA 내부 개발 구성에서 요구하는 격리는 **출처별 모델 키·화면 엔티티·MQTT prefix·실제 모바일 발송 차단**이다. 시험과 실물의 물리적 저장소 분리는 공통 계약의 필수 조건으로 채택하지 않았다. `.storage/bapsim.pipeline`과 HA 아래 journal 루트는 공유된다. 따라서 HA 전체 백업에는 두 출처가 함께 들어가며, 파일 단위로 시험 기록만 독립 복원·삭제할 수 있다고 보장하지 않는다. 운영상 시험 데이터의 독립 삭제·접근 권한·백업이 필요해지면 B가 별도 저장소 설계와 마이그레이션을 제공한 뒤 사용한다.

A05 `input_kind=test`는 현재 B의 live 수집 경로에서 거부된다. 시험 보드→B 분석→시험 폰의 전 구간 검증 경로는 아직 제공하지 않는다. 별도 검증 출처·저장·시험 폰 대상 경로를 B가 구현해 검증하거나, 지원되는 `input_kind=live` 생산자가 준비된 후 live 통합 시험을 수행한다. 수동 HA 폰 알림과 오프라인 시나리오는 이 전 구간의 성공 증거로 취급하지 않는다.

A: 로컬 브로커, HA 연결, 폰 등록·외부 접근. B: 결과 형식·discovery·화면 설정·발송 코드·중복 방지·저장. 계정 비밀은 예시 파일이나 공유 로그에 넣지 않는다. 가족 식별자는 익명 ID로 공유한다.

형식 예시: [가상 분석 결과 JSON](examples/analysis-state.simulation.json). 시험 topic 전용이며 실제 학습 결과가 아니다.

## 7. 보호자 조작 입력 계약 — 추가

확인 버튼과 여행 기간 보류를 구현한다. 기존 결과 상태 JSON 0.1.0은 유지하며, 별도 조작 메시지 버전 0.1.0을 정의한다. 폰의 HA 화면/액션 → HA에서 검증한 명령 → B로 전달한다. MQTT 명령 topic은 `bapsim/v1/{household_id}/control`, retain=false다. 보호자 폰이 브로커에 직접 접속하는 구조가 아니다.

필수 필드: `schema_version`, `command_id`, `household_id`, `action`, `created_at`, `assessment_id`, `starts_at`, `ends_at`, `pause_id`. 시각은 UTC, 해당 없는 ID/시각은 null이다.

| action | 필수 의미 |
|---|---|
| acknowledge | assessment_id와 ends_at 필수. 현재 안내를 확인하고 선택한 시각까지 같은 판단의 재안내 보류 |
| pause_lifestyle | starts_at < ends_at, pause_id 필수. 여행 등 지정 기간의 생활 변화 알림 보류 |
| cancel_pause | pause_id 필수. 해당 보류를 해제하고 최신 상태로 재평가 |

‘확인했어요’는 상세 화면에서 ‘오늘까지’ 또는 날짜·시각을 선택해 완료한다. 명령 저장이 확인되기 전에 적용됐다고 표시하지 않는다. B는 command_id별 결과를 저장해 중복 적용하지 않고 accepted/rejected와 사유를 돌려준다. 종료일이 지난 명령, 잘못된 가정/판단 ID는 거절한다. 인증된 HA 경로와 가정별 접근 범위를 확인한다.

보류 상태 topic `bapsim/v1/{household_id}/notification_controls`는 retain=true, revision과 적용된 보류 목록을 게시한다. 이는 명령 재실행용이 아니라 화면 표시·재접속 동기화용이다. 상태와 발송 이력은 별개로 저장한다. 보류는 안전 확인이 아니며, 여행 표지는 기록 삭제가 아니라 학습/감소 비교에서의 구분이다.
