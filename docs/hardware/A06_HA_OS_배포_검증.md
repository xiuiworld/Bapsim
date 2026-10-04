# A06 기존 HA OS 배포 검증

2026-10-05 · 진행 중

최신 재시험 대상은 PR #10의 `c7a5354e0bb1dab48c76f49770939846c90144e0`이다. 아래 초기 기록의 `46c1b3d`는 이전 설치본이며 이 문서 끝의 후속 재시험 결과를 함께 본다.

## 기준과 구성

- B 배포 PR #10, 원본 커밋 `46c1b3d7db9d9b198a38ed3b57cc3b652cd4da3e`.
- A 검증 브랜치 `a/a06-haos-validation`. 기존 HA OS 내부 분석 모드, Docker는 Mosquitto만 실행한다.
- HA `http://192.168.1.13`, Windows LAN/MQTT `192.168.1.8:1883`, 브로커 `eclipse-mosquitto:2.0.22`.
- 인증 파일은 사용자 대화형 입력으로 생성. 익명 접속 금지, 로컬 비밀 파일과 실행 데이터는 Git 제외.

## 확인 완료

- A05 관련 테스트 6개 통과, Compose 구성 검사 통과.
- `python -m tools.build_ha_bundle` 실행. ZIP SHA256 `754098DEEB0DBECB58C8C941D63B8FE49B29113EAF7E17DA01565AC1866D40F2`.
- `haos-mosquitto-1` 기동, HA MQTT 통합 생성 및 계정 `bapsim_ha`의 인증 접속 로그 확인. MQTT entry `01M43RRZHH98S2K711BVRHG7CT`.
- 읽기 전용 mosquitto.conf의 chown 경고가 있으나 설정 로딩과 브로커 실행은 성공했다.
- HA/Docker 동시 실행 확인. 확인 시 Windows 가용 메모리 약 1.07 GiB / 총 15.57 GiB로 여유가 작으며 장시간 안정성 성공으로 간주하지 않는다.
- HA 수동 백업 `A06-before-Bapsim-2026-10-05`, ID `2c9e3b76`, 1.77 MB 생성 완료. 상세 화면에서 HA 설정/기록, File editor, Matter Server 포함 확인. 자동 반복 백업 일정은 설정하지 않았다.

- 2026-10-05 사용자가 백업 비상 키트 다운로드 완료를 확인했다. 키 내용은 수집하지 않았다.

## 초기 설치 직후 남았던 항목

- B 통합의 MQTT 결과 출력/만료/복구는 별도 실기 검증이 필요하다.
- 실제 live 입력과 폰까지의 통합 시험은 시험 이미지로 검증할 수 없다.
- MQTT 출력/만료/복구와 저장·백업 복원 시험. 실제 폰 알림 대상은 아직 설정하지 않았다.
- 현재 보드는 test 입력이므로 실물 분석·학습·폰 알림 성공으로 체크하지 않는다.

증거 이미지는 Git 제외 `local-data/a06/mqtt-connected.png`, `ha-backup.png`에 보관했다.

## B 통합 설치·시험 출처 확인 완료

- File editor로 검증된 ZIP을 HA 설정 폴더에 업로드했다. 기존 custom_components에는 bapsim_matter_probe만 있음을 확인하고 B 통합을 추가했다.
- ZIP 해제에 임시 shell_command를 사용했고 반환코드 0을 확인했다. 이후 임시 명령을 configuration.yaml에서 제거했으며 재시작 후 해당 액션이 사라졌음을 확인했다.
- 최초 B 활성화 검사에서는 `Integration bapsim not found` 경고가 발생했다. 자동 승인 검토가 그 상태에서의 재시작을 거부했다. 기존 정상 YAML로 복원하고 검사 통과 후 재시작해 통합 검색을 갱신했다. 다시 B 설정을 추가했을 때 경고 없이 검사에 통과했고 이후 Core 재기동과 B 서비스 생성을 확인했다.
- 설정은 `bapsim: {household_id: household_a_test}`이며 독립 작업자 모드와 폰 알림 대상을 활성화하지 않았다. 기존 Matter/probe 설정은 보존했다.
- 실제 A05 ReportCounter 1550에서 input_kind=test, entry `01M42JG6BJ84TBN5G7SCZZQRN0`, node 4, path `1/4294048769/4293984260` 확인 후 `bapsim.register_a05_snapshot` 실행. 논리 device_id는 `cooker_a_test`.
- `sensor.bapsim_matter_input`에 해당 바인딩이 나타났고 source_verified=true, inputs_available=false, cooking_available=false, 상태 connection_unverified를 확인했다. 이후 원본 카운터 1554까지 증가했으나 live 준비 상태로 전환하지 않았다.
- 이는 B의 시험 출처 거부 상태를 실기에서 확인한 결과다. test 데이터의 B 내부 이벤트 전달·학습·실물 알림을 검증한 결과가 아니다. 원시 이벤트/저장소 전체 감사와 폰 전송 시험은 수행하지 않았다.
- B 준비 상태 속성의 lid_state에는 개폐 bool이 아닌 원본 ReportCounter 문자열이 표시된다. 이번 판정은 source_verified/inputs_available와 원본 Snapshot을 기준으로 했으며 이 표시를 뚜껑 상태로 해석하지 않는다.

추가 증거: `local-data/a06/a05-test-rejected.yaml`, `a05-test-rejected.png`. 원본 HA YAML 백업은 `local-data/a06/ha-configuration-before.yaml`이며 Git 제외다. 생성 ZIP과 설치 파일은 보존했다.

## 2026-10-05 시뮬레이터·MQTT·폰 후속 검증

- B 원본 패키지와 대시보드를 HA의 `bapsim_simulator.yaml`, `bapsim_lab.yaml`로 설치했다. 기존 설정에 packages와 Lovelace 대시보드를 추가하고 시간대를 Asia/Seoul로 설정했다. 구성 검사 통과 후 Core 재시작과 `/bapsim-lab/simulator` 표시를 확인했다.
- 통신 복구 → 닫힘 → 열림 시험 후 MQTT 수신 UI에서 `bapsim/simulation/v1/household_a_test/state`의 revision 3, observation=available 및 availability=online을 retained 메시지로 수신했다. 시험 토픽 발행은 성공했지만 HA 화면 카드는 직접 상태 출력이므로 MQTT→카드 전체 경로 성공으로 간주하지 않는다.
- 시뮬레이터 통신 끊기 후 disconnected/data_unavailable 표시를 확인했고 통신 복구 후 helper는 online으로 돌아왔다.
- B 내부 분석 모드의 `service/ha_analysis_flow.py`는 출처와 관계없이 output_state를 갱신한다. 시험 결과가 '실물 분석 상태' 카드에 표시되고 simulation_output_state는 없는 문제가 관측됐다. B 수정 대상으로 기록하며 A가 분석/출력 코드를 중복 구현하지 않는다. MQTT 토픽은 시험 전용으로 분리되어 있었다.
- Mosquitto를 정상 중지하고 저장소 밖 `C:\Users\xiui\Documents\Bapsim-backups\a06-mqtt-20261005-0106\mosquitto`에 DB와 암호 해시를 복사한 뒤 start 성공을 확인했다. 복원 시험이나 재시작 후 retained 재수신 성공을 의미하지 않는다. HA와의 원자적 동시 백업도 아니다.
- `notify.mobile_app_nothing_phone_4a`로 '밥심 A06 연결 시험'을 직접 보냈다. clickAction은 `/bapsim-lab/status`. 사용자가 알림 수신을 확인했고, 이후 알림 클릭으로 '생활 루틴 확인'이 열린 폰 스크린샷을 제공했다. **폰 수신과 상세 화면 이동 각각 성공**이다.
- 이 알림은 B 자동 판단/정책/발송 경로를 거치지 않은 전송 시험이다. 화면의 notification_delivery=not_sent와 모순되지 않는다. B 시뮬레이터는 모바일 전송을 제외하므로 실제 보드→B→폰 경로는 여전히 미검증이다.
- `bapsim.set_notification_target`에 위 폰 서비스를 저장하는 실행 도중 사용자가 작업을 중단했다. 적용 여부는 다음 작업에서 조회해야 하며 저장 완료로 간주하지 않는다.
- 남은 사항: B 화면 출처 분리, 독립 저장소 요구 충족 여부, 실제 결과 만료, HA/B 상태와 MQTT retained의 재시작 복구, 백업 복원, 지원되는 입력으로 B 자동 알림 전체 경로 검증. 보존 90/180일은 B 구성/코드 기준이며 장기간 경과에 따른 삭제 실기는 미검증이다.

증거: Git 제외 `local-data/a06/simulator-mqtt.png`, `simulator-disconnected.png`, 설치 전 YAML `ha-before-simulator.yaml`. 폰 화면은 대화의 사용자 첨부 증거를 사용했다.

## 2026-10-05 추가 재시작·복원 시험 결과

- B 폰 대상 등록을 재실행했다. `service: notify.mobile_app_nothing_phone_4a`는 Unknown error로 실패했다. 실제 구현은 도메인을 제외한 이름을 요구하므로 `service: mobile_app_nothing_phone_4a`로 수정했고 오류 없이 실행 완료됐다. 재시작 후 저장된 대상 값 자체를 조회하거나 B 자동 모바일 전송으로 재확인하지는 않았다.
- MQTT 화면에서 온라인 및 시험 토픽 retained 메시지 수신을 확인했다(revision 13, availability=online). 이는 이전 브로커 재시작 후 재연결/수신 증거다. B가 재발행할 수 있으므로 재시작 전 DB의 메시지가 그대로 복구됐다는 단독 증거로는 사용하지 않는다.
- broker 백업 DB/암호 해시 사본을 `local-data/a06/restore-drill`로 복사하고 동일 이미지·인증 설정의 `bapsim-a06-restore-drill` 컨테이너를 `--network none`, 공개 포트 없이 기동했다. 설정 로드 및 running 로그를 확인했다. 시험 컨테이너만 stop/rm 했고 원본 `haos-mosquitto-1`은 정상 실행 중이다. 복원 사본의 인증 접속/retained 내용 대조까지 수행한 것은 아니다.
- HA 구성 검사 통과 후 Core 재시작. 시뮬레이터 online과 최근 뚜껑 열림 1회가 유지됐다. 기동 직후 출력 카드 없음/mqtt_output=waiting 상태가 있었고 주기 처리 이후 published로 돌아왔다.
- 수동 백업 `A06-after-validation-2026-10-05` 생성 완료: ID `2d30e8cd`, 2.63 MB, KST 01:13. HA 설정·기록, SSL, File editor와 Matter Server가 포함돼 있다.
- 이 최신 백업에서 **HA 설정 및 기록만** 선택해 복원했다. 완료 후 대시보드, online helper, 열림 1회 유지 확인. Matter Server/SSL/File editor 복원은 선택하지 않았다. 전체 HA OS/VM 또는 Matter Fabric 복원 성공으로 확대 해석하지 않는다.
- 복원 후 읽기 전용 템플릿 결과: A05 ReportCounter 1677, `cooker_a_test` 바인딩 유지, source_verified=true, inputs_available=false, B input=connection_unverified. B MQTT output=published, revision=16, topic=`bapsim/simulation/v1/household_a_test/state`. 시험 입력의 live 거부 경계도 유지됐다.
- `python -m tools.simulate_notification_flow` 성공: 재시작 후 resolved, real_push_sent=false. `python -m pytest tests/test_observation_journal.py tests/test_status_output.py -q`는 3개 통과했다. 90일 보존 경계·180초 valid_until·출처별 topic의 소프트웨어 검증이며 실제 HA 화면 만료/180일 경과 삭제를 증명하지 않는다.
- HA File editor에서 `bapsim_events`, `bapsim_observations`, `bapsim_records` 생성 확인. 설정된 관측 90일/서비스 기록 180일은 B 코드 기준이며 저장소는 출처별 물리적 격리가 아닌 공유 루트다.

증거: `local-data/a06/mqtt-reconnected.png`, `ha-after-backup.png`, `ha-restored-verification.png`. 비밀 자료는 Git에 포함하지 않는다.

현재 남은 조건은 [B 후속 검증 요청](A06_B_후속검증_요청.md)과 같다. A06 전체 완료로 체크하지 않는다. B가 시험/실물 화면·만료 처리 및 시험 전용 전체 경로를 제공하면 A가 설치 후 재시험한다. 전체 Matter 앱 복원, 폰 대상 영속화 조회, broker 복원 사본의 인증/메시지 대조와 장기 보존 실기는 미검증으로 유지한다.

## c7a5354 갱신 및 재시험

- 원격 PR #10 배포 브랜치를 fetch하고 정확한 커밋 `c7a5354e0bb1dab48c76f49770939846c90144e0`에서 설치에 필요한 소스를 `local-data/a06/c7a5354-source`로 별도 추출했다. A 브랜치의 미커밋 문서를 보존했으며 main/PR을 merge하지 않았다. firmware는 추출·수정하지 않았다.
- 이 소스에서 ZIP 재생성: SHA256 `2C73ABF8CB5FF10BA815244CE5849B584E14C603FB2CD1B61E4D221EBA0DA84B`. A06 내부 출력/A05 HA 통합/계약 기록 관련 테스트 12개 통과. B가 보고한 전체 178개는 A가 전체 재실행한 결과와 구분한다.
- HA 자동 백업 성공 확인 후 ZIP과 `bapsim_lab.yaml` 업로드. 임시 shell_command의 ZIP 해제는 returncode=0, stderr 없음. 임시 명령을 제거하고 기존 configuration.yaml과 문자열 일치 확인 후 저장했다. 구성 검사 통과, Core 재시작 후 임시 액션이 Unknown action으로 사라진 것까지 확인했다.
- Simulator 탭에 시뮬레이션 분석 카드가 나타났다. 시험 개폐 후 실물 상태는 awaiting_matter, 시뮬레이션 상태만 사용 기록 요약을 표시했고 실물 개인 기준/특징은 waiting으로 유지됐다. 기존 누락 엔티티 경고가 사라졌다.
- `bapsim.set_notification_target`에 `notify.mobile_app_nothing_phone_4a` 전체 이름을 주어 실제 HA에서 오류 없이 완료했다. 기존 접미 이름은 이전 버전에서 실기 확인했고 새 버전에서는 양쪽 형식의 단위 테스트가 통과했다. 이 호출은 새 푸시 전송이 아니다.
- B의 개정 출력 계약은 출처별 모델 키·화면·MQTT prefix 및 실제 모바일 발송 차단을 개발 격리 기준으로 명시한다. 물리 저장소 공유 자체는 현재 계약 위반으로 보지 않으며 시험 데이터만 독립 복원/삭제할 수 있다고 보장하지 않는다.
- `bapsim.test_pause_simulation_output`을 duration_seconds=240으로 실행했다. 만료 및 자동 복구 결과는 아래에 추가한다.
- 만료 전: HA 시험 카드와 MQTT discovery 센서 `sensor.babsim_simyulreisyeon_annae`가 같은 결과를 표시(revision 37), valid_until=`2026-10-04T16:40:08.898314+00:00`(KST 01:40:08). 실물 카드는 awaiting_matter였다.
- KST 01:40:25 조회: HA 시험 카드 **expired**, MQTT 센서 **unavailable** 확인. 실물 awaiting_matter 유지. A05 카운터 1772와 출처 바인딩도 계속 갱신됐다.
- KST 01:42:25 조회: 중지 자동 해제 후 HA 시험 카드/MQTT 센서 모두 새 결과로 복귀(revision 42), valid_until=KST 01:45:08. A05 카운터 1780, source_verified=true, inputs_available=false 유지. 시험 결과 발행만 중지됐으며 보드 수신은 지속됐다.
- 결과: 시험/실물 화면 분리, 시험 MQTT discovery 수신, 180초 결과 만료, 240초 발행 중지 후 자동 재개, 전체 폰 서비스명 수용은 실기 통과. 실제 live 결과의 만료와 보드→B→폰 전체 경로까지 성공한 것은 아니다.

새 증거: `local-data/a06/c7-separated.png`, `c7-before-expiry.png`, `c7-expired.png`, `c7-resumed.png`. 이전 실패 기록은 이력을 위해 유지하되 현재 판단에는 이 절을 우선한다. PR #10은 merge하지 않았고 A 브랜치에 문서만 변경했다. HA의 실행 코드는 위 별도 추출 커밋으로 갱신되어 있으므로 이전 브랜치 소스로 ZIP을 다시 생성해 덮어쓰지 않는다.

## b356897 개발용 연결 시험 설치

- PR #10 원격 인계 커밋 `b356897935422ce1ba34375880c94178cdffac35` 확인. 설치 소스를 `local-data/a06/b356897-source`로 별도 추출했다. main/PR merge와 firmware 변경은 하지 않았다.
- A가 `python -m tools.build_ha_bundle`로 생성한 ZIP SHA256은 `B1C5CAA214607AD74C6E40E161DC0CAC7AE8BC57FE731423DB50621EA20D45D4`다. B가 전달한 `839306E902150C831E0A8A39D32C3649E4486D6F73B65E2A3F928AC1567C30DE`와 다르며 원인은 미확정이다. 설치 관련 소스 58개를 해당 Git 커밋과 줄바꿈 정규화 후 대조해 내용 차이 0개를 확인했고, A가 직접 빌드한 ZIP을 설치했다. B 원본 ZIP 자체를 동일 파일로 검증한 것은 아니다.
- 해당 소스에서 `tests/test_a05_connection_test.py`, `tests/test_a05_ha_integration.py`, `tests/test_a06_internal_output.py` 총 11개 통과. B 보고 전체 181개와 구분한다.
- 설치 직전 HA 자동 백업 성공 확인. ZIP과 lab YAML 업로드 후 `custom_components/bapsim/` 경로만 추출했다(returncode=0, stderr 없음). 기존 probe와 펌웨어는 유지했다.
- 대시보드의 실제 기존 경로는 `/config/bapsim_lab.yaml`이다. B 안내의 `dashboards/` 경로로 임의 변경하지 않고 기존 filename에 맞춰 갱신했다.
- 임시 설치 shell_command를 제거하고 configuration.yaml을 설치 전과 문자열 일치로 복원했다. 설정 검사 통과 후 Core 재시작. 임시 액션이 Unknown action으로 제거됐고 새 시험 액션이 등록된 것을 UI에서 확인했다.
- 새 `/bapsim-lab/a05-test` 화면의 `not_armed` 확인. 재시작 후 A05 카운터 1911, source_verified=true, test의 live 거부 inputs_available=false, 기존 cooker_a_test 바인딩 유지. MQTT 출력 simulation topic revision 72 확인.
- 설치 증거: `local-data/a06/b356-before-backup.png`, `b356-not-armed.png`. 최종 버튼·폰 실기는 다음 기록으로 구분한다.

### 첫 개발용 연결 시험

- 실행 ID `0c884a3ddce747d792c9c79f91ab9fcd`, 시작 KST 02:15:35. 사용자 버튼 1 누름/놓기 후 `closed_observed`→`ha_accepted` 확인.
- 동일 BootId `1920897178`, 닫힘 ReportCounter `1917`, 열림 `1920`. 닫힘 계약 `29b607226d8047c68c8fa09d098aea33:22`와 열림 계약 `29b607226d8047c68c8fa09d098aea33:43`은 같은 stream의 good 품질 lid_open false→true다.
- File editor로 실제 `/config/bapsim_test_runs/test-2026-10-04.jsonl`을 읽어 54개 행을 대조했다(파일 날짜 UTC, 현지 날짜 10월 5일). 모든 행이 동일 실행 ID와 mode=test였고 계약 기록→board_input_confirmed→mobile_attempt 1회→mobile_result/ha_accepted가 저장돼 있었다. HA 화면 결과 ID와 일치했다.
- 사용자는 실제 알림 수신을 확인했다. 첨부 사진은 01:07 생활 루틴 화면이라 이번 `/bapsim-lab/a05-test` 클릭/실행 ID 일치의 증거로 사용하지 않는다. 사용자가 재전송을 요청해 새 실행으로 재시험한다.
- 증거: `b356-closed.png`, `b356-ha-accepted.png`, `b356-test-result-ui.txt`, `b356-test-journal.jsonl` (모두 ignored local-data/a06).

### 클릭 확인을 위한 재시험

- 기존 실행 중지 후 새 ID `0ebfa6c347a247ee99a43e44f56a337e`로 시작했다(KST 02:19:39). 첫 닫힘 뒤 offline/online 경계로 stream이 교체되어 `await_closed`로 복귀했고 알림은 호출되지 않았다. 사용자도 이때 알림 미수신을 보고했다. 한 번의 누름/놓기가 항상 성공한다고 기록하지 않는다.
- 같은 실행에서 닫힘을 다시 유지하고 놓은 후 `ha_accepted` 도달. 최종 유효 계약 stream `f323263f4b914eaca0cdb520e0434239`, 닫힘 ID `:1`(counter 1948), 열림 ID `:8`(counter 1949), BootId 1920897178, ObservationSequence 1949. 두 계약은 mode=test, good 품질의 false→true이며 열림은 kind=change다.
- journal의 해당 실행 116행에서 위 계약과 결과, mobile_attempt 정확히 1회, mobile_result/ha_accepted(KST 02:22:09.577)를 확인했다. 보고 누락 시 시험을 초기화한 뒤 새 유효 연결에서만 발송한 동작이다. 폴링 중간 보고 누락의 원인/개선은 별도다.
- 증거: `local-data/a06/b356-retry-ha-accepted.png`, `b356-retry-journal.jsonl`.
- 사용자 확인 및 02:22 폰 스크린샷 2장으로 실제 알림 수신, 알림 클릭 후 A05 개발용 연결 시험 화면 이동, 실행 ID `0ebfa6c347a247ee99a43e44f56a337e` 일치를 검증했다. 폰 화면의 닫힘/열림 계약 ID `f323263f4b914eaca0cdb520e0434239:1`/`:8`도 HA journal과 같다. 증거는 ignored `b356-phone-notification.png`, `b356-phone-click.png`에 보관했다.
- `bapsim.stop_a05_connection_test` 실행 후 같은 ID의 `stopped` 확인. 시험 종료 후 화면에 mobile_attempted=True와 결과가 남는 것은 과거 실행 기록이며 새 발송 활성화를 뜻하지 않는다.
- **판정: A06 개발용 첫 연결 시험 완료.** A05 계약 구현과 A06 환경/개발용 보드→B→폰 연결 범위의 B 대기는 해소됐다. 실제 센서·개인 학습/AI 성능·운영 live 입력·외부망·장기 보존/전체 Matter 복원과 빠른 보고 누락 개선을 완료했다고 해석하지 않는다. PR #10 merge와 A 문서 커밋/push는 이 시험에 포함하지 않았다.

## 노트북 절전 후 작업 재개

- 브라우저는 HA 서버 자체가 아니다. 탭을 닫아도 실행 중인 HA VM과 Docker는 유지되지만, 호스트 노트북이 절전 상태면 HA/MQTT의 지속 수집·분석·알림을 기대하지 않는다. 그동안 보드에서 발생한 모든 중간 변화가 나중에 복구된다고 보장하지 않는다.
- 복귀 후 기존 공유기/LAN에 연결하고 잠시 기다린 다음 `http://192.168.1.13/`을 새로고침한다. 대시보드가 열리면 재설치나 Matter 재등록을 하지 않는다. 절전/복귀 실기는 아직 검증하지 않았다.
- HA가 안 열리면 Hyper-V 관리자에서 `Bapsim-HAOS`의 상태를 확인한다. 꺼짐/저장됨이면 시작, 일시 중지면 다시 시작(Resume)을 선택한다. Windows 재부팅 시 VM 자동 시작 정책은 현재 세션의 Hyper-V 조회 권한 부족으로 확인하지 못했다.
- Docker Desktop이 실행 중인지 확인한다. 저장소 루트에서 `docker compose --env-file deploy/haos/.env -f deploy/haos/compose.yaml ps`로 MQTT 상태를 확인하고, 멈춰 있다면 같은 명령의 `ps` 대신 `up -d`로 기동한다. Compose는 `restart: unless-stopped`지만 Docker 엔진 자체가 실행돼 있어야 한다.
- 현재 Windows MQTT 바인딩 주소는 `192.168.1.8:1883`다. 다른 공유기/네트워크로 이동하거나 주소가 바뀌면 `.env`의 바인딩과 HA MQTT 접속 주소를 함께 확인한다. 임의의 다른 어댑터 IP로 바꾸지 않는다. 인터넷만 끊긴 경우와 로컬 LAN까지 끊긴 경우를 구분한다.
- A05 카운터 갱신과 HA MQTT 연결을 확인하고 작업을 재개한다. 개발용 연결 시험은 종료해 두었으므로 자동 재발송하지 않으며, 다시 시험할 때 새 실행을 명시적으로 시작한다.
