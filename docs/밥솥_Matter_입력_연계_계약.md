# 실제 밥솥 입력 연계 계약

상태: Home Assistant 쪽 제안. 실제 센서·펌웨어 인터페이스 확인 전.

2026-10-02 하드웨어 인계 반영: 실물 계획은 자석 개폐센서, 열림/잠김·보온·백미 LED용 광센서 3개, NTC 온도센서다. LED·온도는 아직 취사 시작/완료의 검증된 입력이 아니다. 장비와 미검증 항목은 [하드웨어 인계 반영](하드웨어_인계_반영_2026-10-02.md)에 정리했다.

## 팀 경계

Nordic 담당은 센서 입력과 Matter 기기 노출, Thread 연결과 commissioning을 맡는다. 밥심은 Home Assistant에서 수신한 상태를 `bapsim_raw_event`로 넘긴 뒤 같은 정규화·판정 코드를 사용한다. Nordic 펌웨어에 밥심 판정을 넣지 않는다.

## 하드웨어 변경에 대한 기준

`device_id`는 HA의 Matter 노드 번호가 아니라 **밥심 안에서 같은 밥솥을 가리키는 논리적 ID**다. 뚜껑과 취사 입력이 서로 다른 노드·엔티티에 있어도 같은 `device_id`로 등록한다. HA 엔티티 ID, 뚜껑 `on/off` 방향, 취사 상태값이 바뀌면 해당 등록 서비스를 새 값으로 다시 실행한다. 같은 밥솥·입력 종류의 예전 매핑은 교체되며 옛 엔티티의 상태 변화는 무시한다. 구성요소가 제거되면 `bapsim.unregister_matter_input`으로 `lid`, `cooking`, `all` 중 제거 대상을 지정한다. 접점 교체·제거는 밥솥 오프라인을, 취사 입력 교체·제거는 취사 신호만의 오프라인을 원시 기록에 남긴다. 실제 밥솥 자체가 다른 가구·사용자의 것으로 바뀌면 새 `device_id`를 써서 이전 개인 패턴이 섞이지 않게 한다.

현재 구현이 받는 형태는 뚜껑 `binary_sensor`와 취사의 두 상태값을 갖는 HA `sensor`·`binary_sensor`·`select`다. Nordic 쪽 구성 변경으로 입력이 다른 형태의 이벤트·속성·복수 값으로 노출되면 해당 **원시 어댑터만** 추가하거나 수정한다. [8필드 원시 계약](../inputs/base/contracts.py)과 정규화기 이후의 행동 분석은 유지한다. 장비가 취사 상태를 전혀 노출하지 않는 경우에는 매핑만으로 해결할 수 없다.

## 필수 관측값

아래 취사 입력은 현재 소프트웨어가 받는 형식이다. 실물 센서가 해당 이벤트를 제공한다는 뜻은 아니다.

| 관측값 | HA에서 필요한 형태 | 현재 준비 상태 |
| --- | --- | --- |
| 뚜껑 열림/닫힘 | `binary_sensor`의 `on/off`와 실제 열림 방향 확인 | `bapsim.register_matter_contact`와 어댑터 구현 |
| 접점·취사 입력 연결 상태 | 접점의 `unknown/unavailable`, 보드가 매 보고마다 증가시키는 Matter 정수 카운터, 복구 시점 | 접점 또는 신선한 카운터 보고가 없으면 밥솥 분석을 원시 `offline`으로 변환. 취사만 불가하면 `cooking_input_availability=offline`을 남기고 접점 학습은 유지 |
| 취사 시작/완료 | 실제 HA 엔티티와 시작·완료를 뜻하는 정확한 두 상태값 | `bapsim.register_matter_cooking_state`와 값 매핑 어댑터 구현. 실제 값은 미확인 |
| LED 3개·온도 | Matter endpoint/attribute와 HA entity, 원시값·단위·가용성 | 노출 방식과 동작 의미가 미확정. 현재 HA 등록 서비스 없음 |

취사 시작·완료가 실제 장비에서 제공되지 않는다면 ‘취사 후 첫 개방 지연’과 ‘취사 대비 개방 감소’를 실물 기반 성과로 주장할 수 없다. 이 경우에는 별도 입력원(API, 상태 센서 등)의 가용성을 확인하거나, 시연 범위를 뚜껑 행동 관측으로 다시 정한다. 제공되지 않는 취사 값을 추정값으로 채워 측정값처럼 사용하지 않는다.

백미 LED는 메뉴 선택, 보온 LED는 선택·대기, 온도는 잔열일 수 있다. 하나의 점등·온도 임계값을 `started`나 `finished`에 직접 대응시키지 않는다. 잠금 표시 LED도 실제 뚜껑 접점의 대체가 아니다. 관측값과 추정된 취사 상태는 별도로 기록한다.

## 원시 이벤트 경계

모든 입력은 [원시 이벤트 계약](../inputs/base/contracts.py)의 8필드(`device_id`, `event_type`, `value`, `observed_at`, `received_at`, `source`, `availability`, `quality`)를 따른다. 시간은 타임존을 포함한다. HA 엔티티는 센서 자체의 관측 시각을 보장하지 않으므로 현재 Matter 입력의 `observed_at`과 `received_at`에는 HA가 상태를 전달받아 변환한 시각을 함께 기록한다. `source=matter`로 전달된 뚜껑 접점은 `lid_state=open/closed`, 취사 상태는 `cooking_state=started/finished`, 연결 변화는 `availability=online/offline`으로 전달된다. 정규화기 이후의 코드는 입력원별로 나누지 않는다. 등록 서비스는 HA entity registry의 platform이 `matter`인지 검사한다. 복원된 매핑의 출처가 사라지거나 바뀌면 `source_unverified`로 표시하고 행동 입력을 중단한다. 이 검사는 Matter 출처만 확인하며 실제 물리 센서의 정확도나 동작 시험을 대신하지 않는다.

접점과 같은 `device_id`로 보드 자체가 증가시키는 정수 카운터를 `board_observation_snapshot`으로 등록하고, 실제 Matter 엔티티의 새 값 변경을 수신해야 연결이 확인된다. HA 시작 때 읽은 캐시값, 변하지 않는 값, 날짜/시각 문자열은 연결 증거가 아니다. HA Core 2026.9.4의 기본 Matter `GeneralDiagnostics.UpTime` 센서는 원시 초가 아니라 계산된 부팅 시각을 표시하므로 `board_observation_uptime`을 연결 근거로 사용하지 않는다. 마지막 유효 카운터 보고부터 3분을 넘기면 접점값이 남아 있어도 분석 입력을 보류한다. 카운터가 감소하면 관측 구간을 끊고 복구한 접점 상태를 새 개방으로 세지 않는다. 연결이 확인되면 취사 입력 없이도 유효한 접점 `on/off`를 `lid_state=open/closed`, `availability=online` 원시 이벤트로 남긴다. 첫 상태는 기준으로 저장하고 이후 열린 전이를 정규화된 `lid_opened`로 승격한다. 접점만으로 개인화 사용 패턴을 학습하며, 취사 기반 세션 지표는 취사 입력이 검증될 때만 계산한다.

접점 엔티티가 `unknown`/`unavailable`이 되거나 제거되면 원시 `availability=offline`, `quality=unknown`을 남긴다. 취사 엔티티만 불가하면 신호별 `cooking_input_availability`를 남긴다. 값을 실제 관측한 `quality=observed`와 구분하며, 단절을 뚜껑 닫힘이나 취사 완료로 바꾸지 않는다.

## 현물 수신 확인 순서

1. HA에서 실제 Matter 기기 commissioning과 뚜껑·취사 상태 엔티티 식별을 마친다.
2. 실물을 열고 닫으며 HA `on/off` 방향을 기록한다. 확인한 방향으로만 `on_is_open`을 등록한다.
3. 메뉴 선택만 한 경우, 실제 취사, 완료·보온 전환, 취소, 뚜껑 개방을 사람의 시각 기록과 HA 센서 로그로 대조한다. 시작·완료를 구별하는 실제 HA 상태값이 검증된 경우에만 `started_state`와 `finished_state`로 등록한다. 두 서비스에서 `device_id`는 같아야 한다.
4. `sensor.bapsim_matter_input`이 접점만 있으면 `lid_only_ready`, 취사도 유효하면 `ready`인지 확인한다. 뚜껑·취사 전이, 한쪽 단절·복구, HA 재시작의 원시 이벤트와 정규화 이벤트를 각각 기록한다. 이 상태는 엔티티 가용성일 뿐 실물 시험 통과는 아니다.
5. 접점 개방으로 개인별 예상 시간·사용 확률·판정이 생성되는지 확인한다. 취사가 검증되면 세션·첫 개방 지연도 따로 확인한다.
6. 로컬 HA 알림과 보호자 응답을 확인한 뒤, 보호자 휴대폰의 셀룰러 수신·액션 응답을 별도로 시험한다.

Matter 수신 성공, 밥심 판정 성공, 휴대폰 알림 수신은 서로 다른 검증 결과로 기록한다.
