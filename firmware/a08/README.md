# A08 센서 제품 앱 통합

2026-10-06. nRF Connect SDK 3.4.1 / nrf54lm20dk/nrf54lm20b/cpuapp.
센서 입고 전 제품 경로의 통합 소스다. 실측 검증·live 승인은 아직 하지 않았다.
Nordic SDK 자체를 저장소에 복사하지 않고 A05 데이터 모델 생성기에 제품 코드를 적용한다.

## 연결과 기본값

`sensors.hpp` ADC/GPIO → `pipeline.hpp` → 전처리 v3 → 고정 모델 →
SnapshotPacked + 최근 8개 SnapshotHistory → 기존 HA 수신기.
50 ms 취득 루프에서 50 ms 안정된 접점 변화를 즉시 기록하고, 정기 보고는 2초다.
ADC 다섯 채널은 한 번의 sequence로 읽는다. 취득 스레드는 Matter 통신보다 우선하며
서버 초기화 완료를 기다린 뒤 시작한다. 동기식 UART 출력이 관측을 방해하지 않도록
Matter 상세/진행 로그는 제품 루프 시작 때 끄고 오류 로그는 유지한다.
관측 간격은 200개 표본마다 `A08 TIMING`으로 기록한다.
모델은 60초 연속 이력을 요구하고 2초마다 계산한다. 광 판정은 20초 창이다.
늦어진 루프는 과거 표본을 가짜로 채우지 않는다.

`config.hpp`의 `SENSORS_CONFIRMED=false`, `SUPPLY_MV=0`은 의도한 기본값이다.
ADC 드라이버는 실행하지만 확인 전 입력은 결측 처리한다. 떠 있는 핀을 정상 온도·개폐로
보고하지 않는다. 합성 보정값을 실물 보정으로 사용하면 안 된다.
센서 확인 뒤 공급 전압, 광 4채널 off/on, NTC 방향·저항·계수·오프셋,
접점 극성을 실측해 설정하고 보정 버전을 바꾼다.

ADC는 AIN7/6/5/4/3, 접점은 P1.10을 사용한다. GPIO는 외부 풀업을 전제로 입력만 설정한다.
12비트, 내부 기준, gain 1/4, 40 us 취득 시간을 사용하며 SDK의 내부 기준 전압 API로
raw를 mV로 바꾼 후 `mV / SUPPLY_MV * 4095`로 정규화한다.
포화·읽기 실패·공급 전압 범위 밖은 결측이다. 원시 코드/드라이버 상태는 UART에 10초마다 남긴다.
채널 설정과 mV 환산은 `SENSOR_ADC_GAIN`을 공유한다. LM20의 900mV 기준에서
변환 범위는 3.6V지만 핀 허용 전압은 여전히 VDD 이하다. 실제 보드에서 기존 gain 1은
NTC 입력 약 1.17V를 4092로 잘랐고, gain 1/4에서 raw 1336~1340으로 읽혔다.
레일 근처 raw(0~4, 4091~4095)는 결측 처리한다. UART의 `A08 ADC mv`는
환산 전압이며 변환 불가 값은 -1이다. 공급 전압·온도 보정이 완료된 것은 아니다.

출처는 **항상 test**다. 센서를 확인해도 이 앱이 자동으로 B의 live 학습·알림을 활성화하지 않는다.
고정 모델은 `model.json`의 합성 학습 후보이며 live 미승인 상태를 유지한다.
표준 BooleanState에는 unknown이 없어 마지막 값이 남을 수 있으므로 품질을 포함한
SnapshotPacked가 정식 입력이다. Button 1은 물리 접점 값을 덮어쓰지 않는다.

생산자 스레드가 이력을 먼저 보존하고 Matter 이벤트 루프가 mutex 아래 일관된 복사본을
가져간다. 발행 실패 때 원시 이력을 지우지 않으며 다음 보고에서 다시 발행한다.
저장 한도는 정기 보고를 포함한 8개다. 장기 단절·전원 소실을 무손실로 보장하지 않는다.
8개를 넘으면 HA의 순번 공백 진단으로 누락을 구분한다.

## 새 환경에서 재현

프로젝트 루트, NumPy 설치 Python으로 실행한다. 출력 폴더는 새 경로여야 한다.

```powershell
python tools/prepare_a05_probe.py --sdk C:/ncs/v3.4.1 --output local-data/a08/product
python -m firmware.a08.prepare --app local-data/a08/product --model firmware/a08/model.json
```

SDK 환경에서 아래 명령의 경로를 절대 경로로 지정한다.

```powershell
west zap-generate --full -z <app>/src/default_zap/contact_sensor.zap -j <app>/src/default_zap/zcl.json
west build --sysbuild -b nrf54lm20dk/nrf54lm20b/cpuapp <app> -d <build> -- -DCONFIG_CHIP_DEVICE_VENDOR_ID=65521
```

빌드 후 signed HEX가 앱 슬롯 `[0xd000, 0x1f0000)` 안인지 검사하고 이전 signed HEX를
보관한 뒤 앱 도메인만 플래시한다. factory data와 저장소를 지우지 않는다.
생성된 `src/a08/manifest.json`에는 보정 설정·모델 버전·소스 해시가 남는다.
의존 전처리·wire·이력 소스는 저장소의 `tools/a07_edge`, `tools/a05_contract`를 공유한다.

## 통합 검사

부팅 후 12초에 같은 Pipeline으로 합성 입력을 실행한다. 첫 60초 이전 짧은 개폐의
즉시 기록과 이력 보존, 저장 한도, 관측/boot 공백, 잘못된 보정, 느린 점멸을 검사한다.
이 합성 결과는 Matter에 발행하지 않고 전체 상태를 초기화한 뒤 실제 취득을 시작한다.
실패하면 취득을 시작하지 않는다. 성공 로그는 `A08 INTEGRATION END failures=0`이다.
`A08 START`의 driver_errors와 이후 `A08 R`의 p(스냅샷), h(이력) 발행 결과도 확인한다.
둘 다 0이어야 성공이다. `firmware.a08.verify`는 부팅 검사, 미보정 값 보류,
ADC 읽기, 발행 성공과 최소 60초 연속 정상 주기를 검사한다.

```powershell
python -m firmware.a08.verify <board.log> --output <new-verification.json>
```

## ADC 범위 수정 실기 확인 — 2026-10-06

`fix/a08-adc-input-range`, `3.4.1-a08-adc-range-2+8`을 보드 1051887912에 설치했다.
앱 HEX 범위는 `[0xd000, 0xca540)`이며 기존 앱 복구본을 보관했다.
실행 중 쓰기 보호로 첫 설치가 실패하여 CPU reset/halt 후 앱 영역만 다시 설치했고,
프로그램 메모리 대조 및 재부팅 검증을 통과했다. 공장 초기화는 하지 않았다.

재부팅 로그에서 gain 1/4, 기준 900mV, 12비트가 확인됐다. 통합 검사·드라이버 오류 0,
90초 연속 49~51ms 취득(공백 0), 원시값→mV 환산 10회 검사를 통과했다.
NTC 입력은 약 1.42V로 읽혔다. `SENSORS_CONFIRMED=false`와 `SUPPLY_MV=0`은 유지하므로
섭씨 온도 보고는 아직 unknown이다. 프로브 가열/냉각 반응과 절대 온도 보정은 별도 검증한다.

설치 이미지·복구본·생성 소스·로그·검증 JSON은 Git 제외 로컬 폴더
`local-data/deployments/2026-10-06-adc-range/`에 있다.
