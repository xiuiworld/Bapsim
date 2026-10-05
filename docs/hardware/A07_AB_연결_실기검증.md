# A07 보드 → A05 Matter → B 시험 경로 실기 검증

검증일: 2026-10-05. 센서 도착 전 합성 원시 입력 연결 시험이며 실측 정확도 검증이 아니다.

## 소스와 자동 시험

- PR #12 기준: `74c698813388083a5e0a7087c8f5728eb3f501f9`.
- A 노트북에서 B 단위 시험 184개, A07 17개, A05 8개 통과.
- 순서 이탈/모델 버전 변경 시 기존 증거 초기화 회귀 시험 통과.
- 모델: `a07-refined-v1-b1673351122b`, 합성 학습, live 미검증.
- 실제 모델이 선택한 1,200표본 입력: inactive=`cooling`, cooking=`steady_ramp`, warming=`warming`, unknown=`missing_led`.

## HA 설치

- 기존 HA OS `192.168.1.13`, 기존 Matter node 4와 A05 수신기를 유지했다.
- 같은 소스에서 생성한 B 번들과 실험실 대시보드만 포함한 갱신 ZIP:
  `local-data/a07/pr12-ha-update.zip`, SHA-256
  `8eed1acc37f06599a08867954e4123e627b60783c29e807da598202d5dd3132f`.
- 교체 전 HA 로컬 백업: `/config/a07-before-20261005T085146.zip`.
  기존 `custom_components/bapsim`, `bapsim_lab.yaml`, 당시 설정 YAML을 포함한다.
  이 백업의 설정 YAML에는 일회성 설치용 shell_command가 포함돼 있으므로
  설정 복원은 별도 보존한 원본 `local-data/a07/pr12-ha-config-before.yaml`을 사용한다.
- 55개 파일 쓰기 후 SHA-256 대조, 설치 명령 반환코드 0.
- 임시 shell_command를 설정에서 제거하고 원본과 동일하게 복원했다.
- 구성 검사 통과 후 Core 재시작, A07 시나리오·모델 버전 카드와 `not_armed` 확인.
- 기존 `cooker_a_test` → `sensor.a05_reportcounter` 바인딩 및 source_verified=true 확인.

## 보드 빌드·실기 시험

- 생성 앱: `local-data/a05/a07-phase-test`. SDK 원본과 `firmware/`는 수정하지 않는다.
- SDK 3.4.1, toolchain `4f5b6ad6dd`, 보드 `nrf54lm20dk/nrf54lm20b/cpuapp`.
- 디버거 `1051887912`, PCA10184, COM8 UART 확인.
- 긴 빌드 폴더에서 265자 생성 객체 경로를 ar가 읽지 못했다. 짧은 `%TEMP%/bap7`로 재빌드 성공(exit 0).
- 롤백 이미지: `local-data/a07/raw-board/a07-raw-test.signed.hex`, SHA-256
  `28EE5263CB0307AC7122E5912E00207ED508B85F71FF14D5E46B2B359625DB86`.
- 새 앱: `local-data/a07/pr12-app.signed.hex`, SHA-256
  `78f047048e62a1b15a0f682f5f7de4939c88860a7d3f313b3ebae4c375d5c8bc`.
- 주소 `[0xD000, 0xE45D4)`, 앱 슬롯 `[0xD000, 0x1F0000)` 내부.
- 전체 앱 FLASH 881,924 B / 1,949,552 B (45.24%), RAM 201,692 B / 523,264 B (38.54%).
- `--domain a07-phase-test --no-rebuild --dev-id 1051887912`만 플래시하고 검증·리셋 성공.
- 재부팅에서 Fabric 2개 복원·Thread CHILD 확인. 신규 commissioning이나 공장 초기화 없음.
- [x] 앱 빌드·슬롯 주소 확인·앱만 플래시.
- [x] 실제 네 단계 Matter 보고와 B 연속 기록 확인.
- [x] 지정 폰 실제 수신·실행 ID·알림 클릭 화면 확인.
- [x] 시험 중지 및 검증 기록 반영. 실제 병합 상태는 PR #10~#12에서 확인한다.

실행 ID: `3fceec6bdf8442e9b2e553d3f7f89e48`. Boot ID `179333800`.
버튼 1을 한 번 눌렀다 놓아 네 단계가 자동 발행됐으며 UART에서 BEGIN, 4개 보고, END를 확인했다.
각 보고는 73 bytes, counter와 observation sequence는 모두 1→2→3→4였다.

| 단계 | 보고 | B 계약 기록 ID | 품질 |
|---|---:|---|---|
| inactive | 1 | `915ef56c68794ca991c7a6fddc761100:7` | good |
| cooking | 2 | `915ef56c68794ca991c7a6fddc761100:14` | good |
| warming | 3 | `915ef56c68794ca991c7a6fddc761100:21` | good |
| unknown | 4 | `915ef56c68794ca991c7a6fddc761100:28` | unknown |

HA `ha_accepted` 및 `mobile_attempted=true` 확인. 이는 폰 수신 확인과 별개다.
사용자가 실제 알림 수신과 실행 ID 일치를 확인했다. 당시 폰은 모바일 데이터에
연결돼 있었고 로컬 HA 상세 화면 진입은 확인하지 못했다. 외부 접근은 미구성이다.
첫 실행은 명시적으로 중지했고, 같은 Wi-Fi에서 아래 새 실행으로 클릭 경로를 재시험했다.
HA journal의 첫 실행 33행을 별도 보존했으며 모두 mode=test이고 mobile_attempt는 1회다.
로그·화면 증거는 Git 제외 `local-data/a07/pr12-{build-short,flash,boot}.log`,
`pr12-test-result-ui.txt`, `pr12-ha-accepted.png`에 보관한다.

## 같은 Wi-Fi 재시험 — 최종 통과

실행 ID: `4ada89491070442faace0bc37b19f574`. Boot ID와 모델 버전은 첫 실행과 동일하다.

| 단계 | 보고 | B 계약 기록 ID | 품질 |
|---|---:|---|---|
| inactive | 5 | `ac272e02c70840589345dc49322ce88c:7` | good |
| cooking | 6 | `ac272e02c70840589345dc49322ce88c:14` | good |
| warming | 7 | `ac272e02c70840589345dc49322ce88c:21` | good |
| unknown | 8 | `ac272e02c70840589345dc49322ce88c:28` | unknown |

UART BEGIN→4개 보고→END, HA의 `ha_accepted`를 확인했다. 사용자가 폰의 실제 알림 수신,
알림 클릭 후 개발용 연결 시험 화면, 같은 실행 ID 및 네 단계 표시를 모두 확인했다.
이 사용자 확인을 폰 수신 근거로 사용하며 `ha_accepted`만으로 성공 판정하지 않는다.
`bapsim.stop_a05_connection_test` 실행 후 같은 실행 ID의 `stopped`를 확인했다.

마지막 원시 snapshot은 input_kind=test, InferenceMethod=1, 추론 품질 unknown이었다.
누락 LED는 값 0/품질 0이며 온도 32.94°C 및 다른 정상 관측 품질은 유지됐다.
시험 자료가 실측·live 데이터 또는 개인 학습 완료를 의미하지 않는다.

증거는 Git 제외 `local-data/a07/pr12-retry.log`, `pr12-retry-ui.txt`,
`pr12-retry-ha-accepted.png`, `pr12-retry-stopped.png`, `pr12-a05-snapshot.txt`에 보존한다.
실센서 정확도·배선/보정·장시간 장애 복구·외부 접속은 이번 통과 범위에 포함되지 않는다.
