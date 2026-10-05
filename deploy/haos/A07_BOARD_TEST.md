# A07 합성 원시 입력 → 실제 보드 추론 → A05 Matter → B 시험 화면·폰

이 경로는 센서 도착 전 연결 시험이다. 합성 20 Hz ADC/접점 입력을 **실제
nRF54LM20B 보드의 A07 전처리와 모델 코드**에 통과시킨 후, 보드가 만든
대기→취사→보온→판단 불가 결과를 기존 SnapshotPacked v1에
`input_kind=test`로 발행한다. 기존 Matter entry/node, A05 5초 수신기와
B의 출처 검사를 사용한다. 실측·개인 학습·AI 정확도 시험이 아니다.

## 배포 기준과 준비물

- 이 문서가 들어 있는 통합 PR의 커밋을 기록한다. B의 PR #10
  `b356897935422ce1ba34375880c94178cdffac35`과 A의 PR #11
  `2b1e6f35386ef8667dc0009cd4ce783805090aca`를 포함한 소스다.
- A가 보유한 SDK 3.4.1, nRF54LM20B / PCA10184 rev 0.7.0, 기존 Matter
  자격 데이터, 보드 앱 영역 백업 signed HEX, `west`와 Python/NumPy가 필요하다.
  이 PR은 SDK나 보드를 포함하지 않으며 `firmware/`를 수정하지 않는다.
- A07 합성 학습 후보의 `model.json`이 필요하다. A가 검증 기록에 남긴
  `local-data/a07/refined-v1/model.json`을 우선 사용한다. 모델은
  `input_kind=synthetic`, `live_qualified=false`, 버전 접두어 `a07-`여야 한다.
  생성기가 대기·취사·보온·판단 불가의 실제 모델 결과를 갖는 원시 입력을
  찾지 못하면 실패하며 임의의 고정 판정을 전송하지 않는다.
- A의 HA OS는 `http://192.0.2.10/`, 인증 MQTT는 `192.0.2.10:1883`,
  논리 가정은 `household_a_test`, 보드는 `cooker_a_test`, A05 엔티티는
  `sensor.a05_reportcounter`이다. 비밀번호·HA 토큰은 이 절차에 넣지 않는다.

## A용 보드 앱 생성과 설치

기존 보드의 signed HEX와 Matter 등록 데이터를 먼저 백업한다. 저장소
루트에서 **새** A 소유 임시 앱 폴더를 만든다. `local-data/a05/a07-phase-test`
가 이미 있다면 다른 새 이름을 사용한다. 기존 앱/SDK 원본을 덮어쓰지 않는다.

```powershell
python tools/prepare_a05_probe.py --sdk C:/ncs/v3.4.1 --output local-data/a05/a07-phase-test
python -m tools.a07_edge.matter_publish --model local-data/a07/refined-v1/model.json --app local-data/a05/a07-phase-test
```

생성된 `src/a07/a07_matter_manifest.json`에 모델 버전, 네 단계의 합성 원시
출처와 1,200표본씩의 개수를 기록한다. 생성 앱의 A05 SnapshotPacked
attribute 경로와 HA 수신기는 기존과 같다. 별도 자기시험 대형 배열은 빌드
대상에서 제외한다. 보드에서 한 단계당 1,200표본을 실제 전처리·모델 함수로
가속 재생하고 **모델이 예상 단계를 내지 않으면 Matter 발행 없이 중지**한다.
생성기는 판정값을 고정해서 스냅샷에 넣지 않는다. 표본의 가상 60초를
실제 생활 60초로 해석하지 않는다.

SDK 환경이 잡힌 PowerShell에서 기존 A05 절차대로 ZAP과 앱을 빌드한다.
앱 슬롯 범위와 롤백 HEX를 확인한 뒤 **앱 영역만** 플래시한다.
Windows에서는 빌드 폴더도 짧게 지정한다. A 노트북에서 긴 빌드 경로를
사용하면 Matter 생성 객체의 전체 경로가 265자가 되어 `arm-zephyr-eabi-ar`
단계에서 파일이 존재해도 `No such file or directory`로 실패했다.
아래 `$a07Build`는 새 임시 빌드 경로이며, SDK 명령 래퍼가 작업 폴더를
변경할 수 있으므로 앱과 ZAP 경로도 절대 경로로 전달한다.

```powershell
$a07App = (Resolve-Path local-data/a05/a07-phase-test).Path
$a07Build = Join-Path $env:TEMP 'bap7'
west zap-generate --full -z "$a07App/src/default_zap/contact_sensor.zap" -j "$a07App/src/default_zap/zcl.json"
west build --sysbuild -b nrf54lm20dk/nrf54lm20b/cpuapp $a07App -d $a07Build -- -DCONFIG_CHIP_DEVICE_VENDOR_ID=65521
Get-Content "$a07Build/domains.yaml"
```

`domains.yaml`의 `domains` 목록에서 `build_dir`가 이 앱의 빌드 폴더를
가리키는 **앱 도메인**을 확인한다. 이 폴더 이름대로 생성된 빌드에서는
`name: a07-phase-test`가 예상되며, 이전 `probe` 앱의 도메인을 재사용하지
않는다. `default` 값이나 MCUboot·factory data 도메인을 추측해 기록하지
말고, 실제 출력의 앱 `name`이 아래와 같을 때에만 실행한다. 이름이 다르면
`--domain` 값을 확인된 앱 이름으로 바꾸고, signed HEX 주소가 앱 슬롯에만
들어가는지 기존 A05 절차대로 확인한다.

```powershell
west flash -d $a07Build --domain a07-phase-test --no-rebuild --dev-id BOARD_SERIAL
```

보드가 재부팅한 뒤 기존 Node 4와 Matter entry
`01M42JG6BJ84TBN5G7SCZZQRN0`를 유지하고,
`sensor.a05_reportcounter`가 같은 `bapsim_matter_probe`로 보이는지
확인한다. 새 앱은 자동으로 결과를 발행하지 않는다. Button 1을 눌러
재생을 시작해야 한다. 재플래시/재부팅만으로 B의 시험 폰이 울리지 않는다.

## B/HA 설치와 실행

같은 통합 커밋에서 `python -m tools.build_ha_bundle`로 ZIP을 만들고,
기존 HA의 `/config/custom_components/bapsim/`만 덮어쓴다. 기존
`bapsim_matter_probe`와 HA 설정을 통째로 덮어쓰지 않는다. 같은 커밋의
`infrastructure/home_assistant/config/dashboards/bapsim_lab.yaml`을 **A의
기존 Lovelace `filename`이 가리키는 경로**에 올린다(A의 이전 설치는
`/config/bapsim_lab.yaml`). HA 구성 검사 후 Core를 재시작한다. 기존
`bapsim.register_a05_snapshot` 바인딩의 entity/device/entry/node를 확인한다.
필요하면 [A05 개발용 연결 절차](A05_CONNECTION_TEST.md)의 등록 YAML을 쓴다.

먼저 HA 개발자 도구 → 작업에서 아래 서비스를 **명시적으로** 호출한다.
`scenario`를 생략하면 기존 개폐 시험이므로 반드시 지정한다.

```yaml
action: bapsim.start_a05_connection_test
data:
  scenario: a07_phase_sequence
  device_id: cooker_a_test
  entity_id: sensor.a05_reportcounter
  phone_service: notify.mobile_app_your_phone
```

`/bapsim-lab/a05-test`에서 `await_inactive`와 새 `test_run_id`를 기록한다.
그다음 **Button 1을 한 번 눌렀다 놓는다.** 이 버튼은 A07 합성 재생의
시작 신호다. 2초 뒤 첫 보고, 이후 약 15초 간격으로 4단계가 보드에서
발행된다. 5초 HA 폴링을 고려해 끝까지 약 70초 기다리고, 각 단계의
ReportCounter·계약 기록 ID를 확인한다. 중간 보고를 놓치면 B가 스트림을
다시 시작하거나 단계가 멈춘다. 같은 실행의 10분 제한 안에서 Button 1을
다시 눌러 새 네 단계 재생을 시작할 수 있다. 새 B 실행이 필요하면 먼저
중지하고 새 실행 ID를 만든다. 보고를 live로 바꾸지 않는다.

기대 단계는 `observed_1_of_4`(inactive), `observed_2_of_4`(cooking),
`observed_3_of_4`(warming), `board_input_confirmed`→`ha_accepted`
(unknown)이다. 네 단계는 동일 B stream·모델 버전, 서로 다른 보고 카운터와
계약 기록 ID를 갖는다. `bapsim_test_runs/test-YYYY-MM-DD.jsonl`에
`mode=test`, `scenario=a07_phase_sequence`, `test_run_id`, 네
`cooker_phase` 기록과 결과, 최대 1회의 `mobile_attempt`가 남는다.
실물 개인 모델과 운영 알림에는 전달되지 않는다. 알림은 “개발용 연결 시험”으로
표시되고 `/bapsim-lab/a05-test`를 열어 같은 실행 ID를 보여야 한다.
`ha_accepted`만으로 실제 폰 수신·클릭 성공을 기록하지 않는다.

```yaml
action: bapsim.stop_a05_connection_test
data: {}
```

중지·10분 만료·HA 재시작은 발송 권한을 해제한다. 이미 HA가 수락한 알림은
취소할 수 없다. simulator 모바일 차단과 운영 quiet-hours는 변경하지 않는다.
모든 보드 입력은 합성이며, 외부망/실측/운영 개인 학습 검증은 이후 단계다.

## 자동 시험과 실기 경계

```powershell
python -m unittest tools.a07_edge.test_matter_publish tests.test_a05_connection_test -v
python -m unittest discover -s tests -q
python -m tools.a07_edge.check_ab_contract --b-root .
```

자동 시험은 A07 모델 출력에서 A05 test wire와 B 계약·결과, 기존 live 거부,
한 번의 폰 호출 및 실행 ID 격리를 확인한다. 이 개발 PC에는 Nordic SDK,
컴파일러, 실제 보드와 A의 HA 권한이 없으므로 생성 C++의 실제 SDK 빌드,
Matter 송신·5초 HA 폴링·폰 수신/클릭은 A의 현장 시험 항목으로 남는다.
