# Matter·HA 연동 명세 설계서

2026-10-02 · 팀 합의 반영 · 구현·인증·상호운용 시험 완료를 의미하지 않음

이 문서는 **보드의 Matter 데이터 모델 → HA 노출 → B 어댑터**를 다룬다. JSON 정규화 이후는 [기존 입력 계약](AB_데이터_계약.md), 전체 결정 목록은 [팀 합의안](../planning/팀_상세설계_합의안.md)이다. 아래 Endpoint 번호와 확장 필드는 프로젝트 제안이며 Matter 표준이 정한 밥솥 모델이 아니다.

## 1. 기기 구성 추천

하나의 Matter 노드에 여러 Endpoint를 두는 읽기 중심 센서 장치로 만든다. 실제 밥솥 취사·보온을 제어하는 명령은 제공하지 않는다. Google Home과 HA는 같은 노드를 각자의 Fabric에 등록한다. Nest는 Thread와 가정 IP망 사이를 연결하며 HA 데이터를 Google Home에서 복사하지 않는다.

| Endpoint 제안 | 기능 | 데이터 모델 |
|---|---|---|
| 0 | 노드 관리 | SDK의 Root Node 기본 구성을 사용. 등록·접근 제어·Descriptor 등 필수 기능 유지 |
| 1 | 실제 뚜껑 개폐 | Contact Sensor, Boolean State의 StateValue. 필수 Descriptor·기타 필수 클러스터는 선택 SDK의 기기 정의를 따름 |
| 2 | 부착 위치 온도 | Temperature Sensor, Temperature Measurement의 MeasuredValue 및 필수 범위 속성 |
| 1의 부가 클러스터 후보 | LED·보드 추정·관측 진단 | 의미가 일치하는 표준 매핑을 먼저 검토한 뒤 부족한 항목만 제조사 확장으로 제공 |

Google 공식 목록은 Contact Sensor와 Temperature Sensor를 지원 유형으로 열거한다. 실제 복합 노드의 등록·화면 표시는 별도 확인한다. [Google 지원 유형](https://developers.home.google.com/matter/supported-devices)

A가 사용할 SDK 버전을 고른 뒤 그 버전의 기기 정의로 정확한 Device Type ID, Cluster ID, revision, FeatureMap, 필수 속성·이벤트를 기록한다. 최신 온라인 문서의 숫자만 복사하여 다른 SDK에 섞지 않는다. Endpoint 1/2는 이 설계의 번호 제안이며 변경 시 매핑 파일을 함께 갱신한다.

## 2. 표준 데이터의 의미

| 항목 | 보드 의미 | HA → 내부 계약 |
|---|---|---|
| 개폐 | 접점 극성을 실제 뚜껑과 대조. Boolean State는 표준 Contact Sensor 의미로 인코딩 | HA 문 열림 상태 on→lid_open=true, off→false. 이 변환은 실제 엔티티 device_class를 확인한 뒤 적용 |
| 온도 | 부착 위치의 보정된 온도. 표준 형식의 단위·범위·null 표현 준수 | HA unit_of_measurement 확인 후 섭씨 실수 surface_temperature_c로 변환 |
| 측정 불가 | 유효하지 않은 값은 표준에서 허용한 unknown/null/진단 방식 활용 | 의미를 확인해 unknown 또는 error. 마지막 정상 값을 새 측정으로 보이지 않음 |

HA Matter 구현에는 Contact Sensor StateValue를 HA 열림 의미로 반전하는 처리가 있다. raw Matter 수신 경로와 HA 경로에서 두 번 반전하지 않는다. [HA 구현 참고](https://github.com/home-assistant/core/blob/dev/homeassistant/components/matter/binary_sensor.py)

Boolean State의 false를 ‘센서 고장’ 표현에 빌려 쓰지 않는다. 표준 속성만으로 센서 오류를 구분하기 어려우면 확장 진단과 결합해 현재 품질을 결정한다. 온도는 raw Matter 단위 변환과 HA 표시 단위 변환을 구분한다. 실제 값 0°C는 오류가 아니다.

## 3. LED·추정값: 표준 우선, 확장 대안

표준 지원은 세 단계를 각각 확인한다: ① 의미가 일치하는 표준이 있는가 ② 선택 SDK가 구현하는가 ③ HA와 Google이 어떻게 수신·표시하는가. 세 가지가 같은 뜻은 아니다. 사용자 정의 상태를 억지로 조명·밥솥 제어 명령에 넣지 않는다.

추천은 **표준 개폐·온도를 우선 확보하고, 표준으로 적절히 표현되지 않는 LED·추정값에는 공개된 확장 모델을 사용**하는 것이다. 다음은 확장이 필요할 때 사용할 의미 설계다. 제조사 ID·클러스터 ID와 바이너리 타입 인코딩은 SDK/시험 VID 선택 후 A가 배정한다. 표준 클러스터 번호를 임의로 점유하지 않는다.

| 확장 논리 항목 | 타입 제안 | 설명 |
|---|---|---|
| SchemaVersion | uint16 | 확장 의미 버전, 최초 1 |
| BootId | uint32 | 부팅마다 달라지는 식별값. 영구 기기 ID 아님 |
| ObservationSequence | uint32 | 의미 있는 관측 변화마다 증가. BootId와 함께 비교. overflow 시 새 구간 처리 |
| UptimeMs | uint64 | 부팅 이후 단조 증가 시간. UTC로 가장하지 않음 |
| LockLed / WarmLed / WhiteRiceLed / TimerFirstLed | enum8 | 0=unknown, 1=off, 2=on, 3=blinking |
| CookerPhase | enum8 | 0=unknown, 1=inactive, 2=cooking, 3=warming |
| SignalQuality | 신호별 enum8 목록 | 0=unknown, 1=good, 2=error, 3=unsupported. 네트워크 unavailable은 수신 측 판정 |
| InferenceMethod | enum8 | 0=none, 1=edge_model, 2=rule |
| ModelVersion | UTF-8 string, 최대 64바이트 제안 | 사람이 추적 가능한 모델/규칙 버전 |
| InferenceScore | nullable uint16 | 0~10000을 내부 0~1로 변환. 미제공은 null, 정확도 보장 아님 |

초기 확장은 **일관된 한 시점의 구조체 Snapshot 속성(읽기·구독 가능)**으로 묶는 안을 추천한다. 별도 속성으로 나누면 수집기가 같은 세대의 값인지 확인할 수 있도록 ObservationSequence와 갱신 순서 처리가 필요하다. 표현 방식의 SDK 적합성은 A가 검토한다.

빠른 개방을 최종 상태 하나로 덮어쓰지 않도록 표준 StateChange 이벤트 지원을 먼저 검토한다. 필요한 경우 확장 ObservationChanged 이벤트에 순번·개폐·당시 품질을 포함하는 대안을 둔다. 이벤트 재조회 범위는 장치 버퍼와 SDK에 제한되므로 무제한 이력 복원을 약속하지 않는다.

공식 밥솥 표준을 새로 제정했다는 표현은 쓰지 않는다. 확장 의미는 저장소에 공개할 수 있게 작성하고 실제 공개 여부는 팀이 결정한다. Google Home에 모든 확장 상태가 표시되는 것은 목표 보장 범위가 아니다.

## 4. HA 수신 경로와 책임

| 정보 | 추천 경로 | 책임 |
|---|---|---|
| 표준 개폐·온도 | HA 공식 Matter 통합 → HA 엔티티 → B WebSocket 수집기 | A 연결·극성/단위 확인, B 정규화 |
| 확장 정보 | A가 선택 버전 Matter Server의 확장 읽기/구독 지원을 확인 → 필요한 HA 통합 어댑터 구현 → B 수집기 | A가 HA까지 노출 책임 |
| 확장 미지원 | 해당 신호 unsupported. 가상 입력은 simulation으로 분리 | B는 정상 실측처럼 분석하지 않음 |

**미확인 사항:** 임의 제조사 클러스터가 현재 Matter Server를 통해 읽히고 HA 엔티티로 노출되는 경로는 아직 검증하지 않았다. 확장 경로를 단지 ‘어댑터’라고 이름 붙여 해결됐다고 보지 않는다.

A의 첫 소프트웨어 검토 과제는 선택 SDK·Matter Server에서 샘플 확장 속성을 끝까지 읽을 수 있는지 확인하는 것이다. 가능하면 전용 HA 통합을 작게 추가한다. 코어 패치가 필요하면 유지보수 범위·버전 고정까지 기록한다. 불가능하면 의미가 맞는 다른 표준 표현을 재검토한다. USB 진단 입력은 학습·개발 대안일 뿐 ‘Matter로 추정값까지 전달’한 결과로 시연하지 않는다. 이 경로의 변경은 팀 결정 D03으로 돌아간다.

## 5. 보고·연속성: 첫 시험 설정 제안

다음 수치는 **팀 검토용 시작값**이며 확정된 센서 특성이나 전달 지연 보장이 아니다.

| 항목 | 추천 시작값 | 목적 |
|---|---|---|
| 접점 흔들림 제거 | 동일 상태 50ms 유지 | 떨림 중복 제거. 짧은 실제 개방을 버리지 않는지 확인 |
| LED 샘플링 | 20Hz, 최근 2초로 점등/점멸 판정 | 실제 LED 주기와 주변광에 맞춰 조정 |
| 온도 샘플링 | 1Hz | 상태 추정용 추이 확보 |
| 온도 보고 | 0.5°C 변화 또는 60초 경과 | 트래픽과 최신성 균형 |
| 추정 갱신 | 2초마다, 값/품질 변화 보고 | 모델 입력 창 길이는 학습 설계에서 별도 선택 |
| 연결 확인용 확장 갱신 | 60초마다 Snapshot/Uptime 갱신 | 값이 그대로인 정상 상태와 무수신 구분 |
| 연결 확인 실패 | 보고 상한 3회 상당을 첫 의심 기준으로 사용 | 출처 연결 상태 확인 후 unavailable 판정. 단순 무개방과 구분 |

Matter 구독의 min/max interval과 장치 보고 구현은 별도다. 위 표가 실제 구독 설정에 반영되는지 A가 기록한다. 느린 보고 구간 안에서 발생한 두 변화를 상태 스냅샷만으로 복원할 수 없다. 이벤트 또는 버퍼 전략이 없으면 해당 한계를 명시한다.

수집 재접속 때 기준 snapshot을 다시 잡는다. BootId 변화·순번 공백·단절은 B에 관측 공백으로 알린다. 중복 이벤트는 BootId+순번으로 제거하고, 알 수 없는 과거를 ‘활동 없음’으로 채우지 않는다. HA 엔티티가 boot/순번을 전달하지 못하면 그 경로에서는 누락 탐지 불가를 명시한다.

## 6. 실제 매핑 파일에 채울 항목

A는 신호별 `device_id, endpoint, cluster, attribute_or_event, ha_entity_or_extension_path, value_mapping, unit, quality_mapping, reports, firmware_version`을 기록한다. B는 이를 설정으로 읽고 entity_id를 코드에 박지 않는다. 보드→HA 속성표와 HA→내부 signal 표를 한 줄씩 대응시키며, 추정 provenance는 누락하지 않는다.

Endpoint·버전·VID·PID·클러스터 실제 숫자는 구현 버전을 선택한 다음 기입하는 **실행 확인 항목**이다. 임의 숫자에 팀의 찬반을 받는 항목이 아니다.

## 7. 등록 관련 전제

개발용 장치의 Google 등록에는 VID/PID 조합과 개발자 프로젝트·시험 계정 조건을 확인해야 한다. 시험 VID는 소비자용 인증 제품 식별자가 아니다. [Google 개발 시작 안내](https://developers.home.google.com/matter/get-started)

HA의 공식 안내는 HA OS의 Matter Server 설치를 권장한다. 네트워크·VM 선택은 [운영·시험 계획](../planning/설치_운영_통합시험_계획.md)에서 다룬다. [HA Matter 안내](https://www.home-assistant.io/integrations/matter/)

## 8. 예약 추정 범위 갱신

예약 관련 추정을 가능한 범위에서 검토하는 방향을 채택했다. 맨 왼쪽 LED만으로 다른 시간대의 예약을 판정하지 않는다. 현재 확장 CookerPhase의 세 상태와 실제 예약 여부를 혼합하지 않는다. 별도 예약 보조 추정의 값/품질/근거를 확정하면 확장 및 내부 계약 버전을 올려 추가한다. 현재는 LED 원 관측을 유지하며 예약 추정 지원 완료를 주장하지 않는다.
