# A08 보드 오프라인 원본 기록

센서 취득과 Matter 발행은 유지하면서 외부 SPI NOR의 `[0x200000, 0x800000)`에 원본을 기록한다.
OTA 슬롯 `[0, 0x1e3000)`과 내부 factory/settings 영역은 사용하지 않는다.
`firmware/`를 수정하지 않고 기존 생성 앱에 기능을 추가하는 도구다.

## 동작

- 20Hz 표본 200개(약 10초)를 두 개의 고정 버퍼로 받는다. 취득 스레드는 저장을 기다리지 않는다.
- 낮은 우선순위의 저장 스레드가 무손실 차분/비트 압축 후 CRC와 commit marker를 기록한다.
- 표본: boot, 64비트 순번/uptime, ADC 원본 6개, 개폐 상태, 센서 드라이버 상태 5개.
- 외부 플래시 6MiB를 8KiB 슬롯 768개로 나눈다. 슬롯에는 최대 6개 블록을 넣는다.
  데이터가 복잡해 공간이 부족하면 일찍 닫거나 블록을 나눈다. 일정한 저장 시간을 보장하지 않는다.
  블록 6개가 들어가는 경우 약 12.8시간이다. 실제 사용 가능 시간은 압축률·미회수 기록에 따라 줄어든다.
- PC는 닫힌 슬롯을 읽고 CRC·레코드·순번을 검증한 후 SQLite `synchronous=FULL` 트랜잭션과 CSV 내보내기를 완료한다.
  그 뒤에만 ACK한다. 보드는 ACK된 슬롯 중 오래된 것부터 재사용한다.
- 미회수 슬롯은 덮어쓰지 않는다. 공간 부족/버퍼 초과 시 원본 취득·Matter는 계속되지만
  저장되지 않은 표본을 `A08 STORE not_saved`와 `lost`로 표시한다.
- 재부팅 시 committed 슬롯을 스캔하고 열린 슬롯을 닫는다. 쓰다 만 레코드는 정상 표본으로 간주하지 않는다.
  손상 슬롯은 원본을 PC에 격리 보관하고 검증된 앞부분만 복구하며 ACK하지 않는다. 이후 슬롯 회수는 계속한다.
- **전원이 끊기면 아직 RAM에만 있는 마지막 블록과 처리 중인 데이터는 잃을 수 있다.**
  이는 PC가 잠들어도 보드 전원이 유지되는 상황과 다르다. 보드 전원이 꺼져 있는 시간에는 측정하지 못한다.

## 빌드 / 적용

기존 `tools/prepare_a05_probe.py`와 `python -B -m firmware.a08.prepare`로 만든 생성 앱에 적용한다.

```powershell
python -m tools.a08_storage.prepare --app <generated-A08-app>
```

생성 앱을 SDK 3.4.1로 빌드한다. 앱 signed HEX의 주소가 `[0xd000, 0x1f0000)` 안인지 확인하고
앱 도메인만 설치한다. MCUboot·factory data·설정·외부 플래시를 일괄 삭제하지 않는다.
Windows의 긴 ZAP 생성 파일 경로는 아카이브 도구 오류를 일으킬 수 있으므로 짧은 생성 경로를 사용한다.

`src/baplog/manifest.json`의 config_hash는 모델·전처리·센서 설정을 식별한다.
기존 분류기와 raw adapter/normalizer 경계에는 변경이 없다. 입력 출처는 A08 생성 옵션을 따른다. 회수한 과거 자료를 live로 재전송하지 않는다.

## PC 수집 / 절전 후 회수

```powershell
python tools/a08_capture/continuous_uart.py --output <new-session> `
  --until 2026-10-14T12:00:00+09:00 `
  --offline-archive local-data/a08/offline-archive --allow-sleep
```

선택한 직렬 포트를 소유하는 수집기는 하나만 실행한다. `--offline-archive`가 없으면 이전의 UART 읽기 전용 모드다.
오프라인 모드만 `baplog status/get/ack` 명령을 보낸다. MCU reset/flash 명령을 보내지 않는다.
전송 중 연결이 끊기면 다시 읽고, 이미 저장한 슬롯/표본은 중복 삽입하지 않는다.

설치 완료 후 `local-data/a08/offline-enabled.json`에 `{"archive":"<absolute-archive-directory>"}`를 저장하면
`tools/a08_capture/start_continuous.ps1`이 이 모드를 자동 선택한다. 이 파일은 배포 상태이며 Git에 넣지 않는다.
이 모드는 Windows 절전 방지를 요청하지 않는다. 기존 별도 keep_awake 프로세스가 있으면 이전 수집 종료로 해제한다.
PC 재시작/수집기 종료 후에는 위 시작 스크립트를 다시 실행한다. 기존 수집 종료 시각은 유지한다.

## 결과 / 시간 / 분석

- `archive.sqlite3`: 슬롯 바이너리, 중복 없는 원본 표본, 시간 기준점. 복구의 기준 자료다.
- `<device-id>-slot-*.csv`: 슬롯별 분석용 보기. 일반 연속 수집 `raw.csv`와 겹칠 수 있으므로
  함께 분석할 때 `(device, boot, sequence)`로 중복을 제거한다.
- `*.error.json`: 손상 슬롯과 검증된 복구 구간. 보드 사본을 자동 삭제하지 않는다.
- 수집 `status.json`의 `offline_storage.board`: 대기 슬롯, 여유 슬롯, 격리 슬롯, 누락 수.

보드에는 PC의 UTC 수신 시각이 없다. 요청 번호와 CRC로 검증한 상태 응답의 uptime/PC 시각을 기준으로
`utc_estimated`를 계산한다. RTT와 **미검증 100ppm 드리프트 가정**을 시간 불확실성에 표시한다.
기준점이 없는 재부팅 구간의 UTC는 빈 값으로 남기며 정확한 시각을 만들어내지 않는다.
수신 시각과 측정 추정 시각을 혼동하지 않는다.

복구 기록은 분석용 저장소에만 넣는다. 과거 기록을 현재 HA 이벤트나 개인화 학습에 자동 재전송하지 않는다.
PC가 잠든 동안의 실시간 HA 알림 기능을 제공하는 것은 아니다.

## 검증

```powershell
python -m unittest tests.test_a08_offline_storage -v
```

보드 부팅 시 압축 경계값·잘린 데이터·미회수 슬롯 가득 참에 대한 자체 검사를 수행한다.
실기 검증 자료와 배포 HEX는 Git 제외 `local-data/offline-storage-deployment-20261009/`에 보관한다.
짧은 실기 검증은 밤샘 내구성·모든 순간의 전원 차단·PC 절전 중 USB 전원 유지까지 증명하지 않는다.


여러 장치를 회수할 때는 `continuous_uart.py --device <stable-device-id> --port <port>`로 아카이브 식별자를 분리한다. 기본 식별자는 `cooker`다. 기존 아카이브를 계속 쓸 때는 이전 식별자를 명시한다.
