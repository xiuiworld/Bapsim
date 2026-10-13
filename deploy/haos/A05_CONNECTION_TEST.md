# A06: A05 실제 보드→B→시험 폰 개발용 연결 시험

이 문서는 A의 기존 Hyper-V HA OS, 기존 A05 펌웨어·`bapsim_matter_probe` 수신기,
HA 내부 분석 모드를 전제로 한다. 보드 펌웨어와 A05 수신기를 수정하지 않는다.
`input_kind=test`는 그대로 유지한다. 이 시험은 보드 입력이 B의 출처 검증,
신호별 계약 변환, 별도 시험 판정, 지정한 폰 알림 요청까지 연결되는지 확인한다.
개인별 7일 학습, 이상 탐지 정확도, 실제 운영 알림을 검증하는 시험이 아니다.

## 설치 파일과 선행 확인

1. PR #10의 인계 커밋을 체크아웃하고 `git rev-parse HEAD`를 시험 기록에 적는다.
   Git과 Python 3.12가 필요하며 ZIP 빌드에 추가 Python 패키지는 필요 없다.
2. 저장소 루트에서 `python -m tools.build_ha_bundle`을 실행한다.
   `dist/bapsim_ha_bundle.zip`을 생성한다. ZIP은 Git에 넣지 않는다.
3. 기존 HA OS 전체 백업을 만든다. ZIP 내부의
   `custom_components/bapsim/` **디렉터리 전체**를 HA 구성 디렉터리의
   `/config/custom_components/bapsim/`에 덮어쓴다. 기존
   `/config/custom_components/bapsim_matter_probe/`는 건드리지 않는다.
4. 같은 커밋의
   `infrastructure/home_assistant/config/dashboards/bapsim_lab.yaml`을
   `/config/dashboards/bapsim_lab.yaml`에 덮어쓴다. 기존 HA 대시보드가 이
   파일을 가리키는지 확인한다. 아니라면 `configuration.yaml`의
   `lovelace.dashboards.bapsim-lab`에 `mode: yaml`,
   `filename: dashboards/bapsim_lab.yaml`을 추가한다.
5. 기존 `/config/configuration.yaml`의 `bapsim:` 아래에
   `household_id: household_a_test`가 있는지 확인한다. `external_service`는
   설정하지 않는다. 기존 설정을 통째로 덮어쓰지 않는다. HA 설정 검사 후
   **HA Core 재시작**을 실행한다. `sensor.bapsim_a05_connection_test`가
   `not_armed`인지 확인한다. 대시보드는
   `http://192.0.2.10/bapsim-lab/a05-test`이다.
6. Mosquitto 인증 연결이 HA MQTT 통합에서 정상인지 확인한다.
   A의 브로커는 `192.0.2.20:1883`이며 사용자명·비밀번호는 A의 현지
   설정만 사용한다. 이 시험 서비스는 별도의 MQTT 비밀번호를 받지 않는다.
   HA 앱의 `notify.mobile_app_your_phone` 서비스와 폰 로그인을 확인한다.

시험 실행에는 코드의 상수나 `.env`에 폰 대상, 비밀번호, HA 토큰을 넣지 않는다.
매 실행마다 아래 서비스 호출의 `phone_service`를 명시한다.

## 등록과 1회 실행

HA 개발자 도구 → 작업에서 먼저 실제 A05 엔티티를 B에 등록한다. 이미 같은
값으로 등록되어 있고 `sensor.bapsim_matter_input`에 보이면 재등록할 필요 없다.

```yaml
action: bapsim.register_a05_snapshot
data:
  device_id: cooker_a_test
  entity_id: sensor.a05_reportcounter
  matter_entry_id: 01M42JG6BJ84TBN5G7SCZZQRN0
  node_id: 4
```

개발자 도구 → 상태에서 `sensor.a05_reportcounter`의 속성을 확인한다:
`input_kind: test`, `transport: matter`, 위 entry/node,
`attribute_path: 1/4294048769/4293984260`,
`read_method: explicit_read_5s`. 엔티티 레지스트리의 플랫폼은
`bapsim_matter_probe`, unique ID는
`a05_01M42JG6BJ84TBN5G7SCZZQRN0_4_ReportCounter`여야 한다.
`ReportCounter`와 `ObservationSequence`가 보드 조작 시 갱신되는지도 확인한다.

시험 폰이 실제 A의 Nothing Phone (4a)임을 확인한 다음, 아래 호출로 **명시적으로**
10분짜리 실행을 시작한다. 시작 전에는 시험 폰 발송이 비활성화되어 있다.

```yaml
action: bapsim.start_a05_connection_test
data:
  device_id: cooker_a_test
  entity_id: sensor.a05_reportcounter
  phone_service: notify.mobile_app_your_phone
```

`sensor.bapsim_a05_connection_test`와 실험실의 `A05 연결 시험` 화면에서
`await_closed`와 새 `test_run_id`, `expires_at`을 확인한다. 실행 ID를 기록한다.
시작 전 HA에 남아 있던 보드 상태는 시험 근거가 아니다.

1. **버튼 1을 누른 채 유지**한다. 이 보드의 시험 동작은 누름=`LidOpen=False`
   (닫힘), 놓기=`LidOpen=True` (열림)이다. HA 수신기가 5초마다 폴링하므로
   최소 15초 유지하고, `sensor.a05_reportcounter`의 새 닫힘 보고와
   시험 카드의 `closed_observed` 및 `closed_record_id`가 보일 때까지 놓지 않는다.
   30초 이상 바뀌지 않으면 중지하고 A05 상태·Matter 연결을 점검한다.
2. **버튼을 놓고** 최소 15초 기다린다. 새 열림 보고가 들어오면 카드가
   `board_input_confirmed`를 거쳐 `ha_accepted`로 변한다. 열림 보고의
   `ReportCounter`는 닫힘 보고와 달라야 한다. 중간 보고 누락·부팅 전환 등으로
   스트림이 끊기면 `await_closed`부터 다시 시작하고, 새 닫힘→열림을 수행한다.
3. 폰에서 제목 **“밥심 개발용 연결 시험”** 알림이 오는지 확인하고 누른다.
   `/bapsim-lab/a05-test` 화면으로 이동해야 한다. 알림 메시지와 화면의
   `test_run_id`가 같은지 확인한다. `ha_accepted`는 HA 알림 서비스가 호출을
   수락했다는 뜻이며 실제 폰 수신·클릭은 A가 직접 확인해야 한다.

실행 시간은 최대 10분, 지정한 폰으로의 호출은 **실행당 최대 1회**다.
밤에도 위와 같이 명시적으로 실행할 수 있다. 개발용 별도 경로가 운영
quiet-hours 판단을 사용하지 않으며, 운영 정책 값은 바꾸지 않는다.
기존 simulator는 계속 실제 모바일 전송을 차단한다.

## 기록 확인과 종료

HA 구성 디렉터리의 `bapsim_test_runs/test-YYYY-MM-DD.jsonl`에서 실행 ID를
검색한다. `armed`, 신호별 `mode:test` 계약 기록, `kind:a05_connection_test`
결과의 `closed_record_id`·`opened_record_id`, `mobile_attempt`,
`mobile_result`가 같은 `test_run_id`를 가져야 한다. 결과의 닫힘·열림
`ReportCounter`, `BootId`, `ObservationSequence`를 실제
`sensor.a05_reportcounter` 변화와 대조한다. 파일은 7일 보존 대상이다.
시험 데이터는 실물 개인 모델, `.storage/bapsim.pipeline`의 개인 학습,
실물 결과 카드와 실물 알림 경로에 넣지 않는다. HA 구성 저장소의 루트는
공유하지만 `bapsim_test_runs/`와 실행 ID로 논리 격리한다. 백업과 복원은
`README.md`의 HA OS 전체 백업 절차를 따른다. 백업에 시험 기록이 포함될 수
있으므로 공유 전에 민감 정보를 확인한다.

아래 작업은 언제든 시험을 중지한다. 시간이 끝나거나 HA Core가 재시작돼도
자동 해제된다. 이미 HA가 수락한 알림은 취소할 수 없다.

```yaml
action: bapsim.stop_a05_connection_test
data: {}
```

재시도할 때는 먼저 중지하고, 새 시작 호출로 **새 실행 ID**를 만든 뒤
닫힘→열림을 다시 수행한다. 저장 실패나 폰 서비스 오류가 나면 같은 실행에서
자동 재발송하지 않는다. 기록은 장애 분석을 위해 7일 보존하며, HA 파일을
직접 수정해 성공으로 바꾸지 않는다. 기록을 즉시 삭제해야 할 경우 HA Core를
중지한 뒤 `bapsim_test_runs/`의 해당 날짜 파일을 백업·검토하고 조치한다.

## 판정 범위

자동 테스트는 출처 검증, 현재 entry/node/path/unique ID 검사, 끊긴 스트림
거부, 실제 상태 변화에 연결된 계약 기록 ID, 저장 선행, 1회 폰 호출,
live 모델·이벤트 비오염을 확인한다. A의 실기에서만 Matter 전달 시간,
5초 폴링, 실제 버튼 동작, MQTT 연결, 폰 수신과 링크 이동을 확인할 수 있다.
폰이 울리지 않으면 `mobile_result`의 `delivery`와 HA 로그를 보고,
`ha_accepted`를 실제 수신으로 기록하지 않는다.
