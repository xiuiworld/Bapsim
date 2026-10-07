# A08 센서 제품 앱 통합

2026-10-07. nRF Connect SDK 3.4.1 / nrf54lm20dk/nrf54lm20b/cpuapp.
제품 경로의 통합 소스다. NTC는 명목 온도 출력을 지원하며 기준 온도계 보정·live 승인은 아직 하지 않았다.
Nordic SDK 자체를 저장소에 복사하지 않고 A05 데이터 모델 생성기에 제품 코드를 적용한다.

## 연결과 기본값

`sensors.hpp` ADC/GPIO → `pipeline.hpp` → 전처리 v3 → 고정 모델 →
SnapshotPacked + 최근 8개 SnapshotHistory → 기존 HA 수신기.
50 ms 취득 루프에서 50 ms 안정된 접점 변화를 즉시 기록하고, 정기 보고는 2초다.
ADC 센서 다섯 채널과 내부 VDD 채널은 한 번의 sequence로 읽는다. 취득 스레드는 Matter 통신보다 우선하며
서버 초기화 완료를 기다린 뒤 시작한다. 동기식 UART 출력이 관측을 방해하지 않도록
Matter 상세/진행 로그는 제품 루프 시작 때 끄고 오류 로그는 유지한다.
관측 간격은 200개 표본마다 `A08 TIMING`으로 기록한다.
모델은 60초 연속 이력을 요구하고 2초마다 계산한다. 광 판정은 20초 창이다.
늦어진 루프는 과거 표본을 가짜로 채우지 않는다.

현재 `config.hpp`는 `SENSORS_CONFIRMED=true`, `TEMPERATURE_ENABLED=true`,
`CONTACT_ENABLED=true`로 부착 광센서·명목 온도·개폐 관측을 활성화한다.
광센서 확인 플래그를 false로 돌려도 온도·개폐의 독립 활성화 설정은 유지된다.
`SUPPLY_MV=0`이면 6번째 ADC 채널로
매번 내부 VDD를 읽어 센서 공급 전압으로 사용한다. 공급 측정 실패·레일 포화·비정상 비율은
온도를 결측 처리한다. 이 방식은 센서 전원을 같은 VDD:IO에 연결한 현재 도면을 전제로 한다.
별도 외부 전원으로 센서를 공급할 때는 그 전압을 따로 측정해야 한다.
센서 확인 뒤 공급 전압, 광 4채널 off/on, NTC 방향·저항·계수·오프셋,
접점 극성을 실측해 설정하고 보정 버전을 바꾼다.

ADC는 AIN7/6/5/4/3, 접점은 P1.10을 사용한다. GPIO는 외부 풀업을 전제로 입력만 설정한다.
12비트, 내부 기준, gain 1/4, 40 us 취득 시간을 사용하며 SDK의 내부 기준 전압 API로
raw를 mV로 바꾼다. 내부 VDD를 사용하는 경우 `raw / supply_raw * 4095`로 정규화하여
mV 정수 반올림을 피한다. 공급 전압을 명시한 경우 `mV / SUPPLY_MV * 4095`를 사용한다.
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
ADC 읽기, 발행 성공과 최소 60초 연속 정상 주기를 검사한다. 현재 온도 전용 설정에는
`--temperature`를 추가하여 온도 유효·광/개폐 unknown·공급전압 환산을 함께 검사한다.

```powershell
python -m firmware.a08.verify <board.log> --output <new-verification.json>
```

## ADC 범위 수정 실기 확인 — 2026-10-06

`fix/a08-adc-input-range`, `3.4.1-a08-adc-range-2+8`을 보드 BOARD_SERIAL에 설치했다.
앱 HEX 범위는 `[0xd000, 0xca540)`이며 기존 앱 복구본을 보관했다.
실행 중 쓰기 보호로 첫 설치가 실패하여 CPU reset/halt 후 앱 영역만 다시 설치했고,
프로그램 메모리 대조 및 재부팅 검증을 통과했다. 공장 초기화는 하지 않았다.

재부팅 로그에서 gain 1/4, 기준 900mV, 12비트가 확인됐다. 통합 검사·드라이버 오류 0,
90초 연속 49~51ms 취득(공백 0), 원시값→mV 환산 10회 검사를 통과했다.
NTC 입력은 약 1.42V로 읽혔다. `SENSORS_CONFIRMED=false`와 `SUPPLY_MV=0`은 유지하므로
섭씨 온도 보고는 아직 unknown이다. 프로브 가열/냉각 반응과 절대 온도 보정은 별도 검증한다.

설치 이미지·복구본·생성 소스·로그·검증 JSON은 Git 제외 로컬 폴더
`local-data/deployments/2026-10-06-adc-range/`에 있다.

## 온도 전용 활성화 — 2026-10-06

`3.4.1-a08-temperature-3+9`는 내부 VDD와 NTC 입력의 비율로 저항을 구하고
10kΩ@25°C / B3950 식으로 명목 섭씨 온도를 계산한다. 내부 VDD 측정은 약 3.02V였다.
기준 온도계로 오차를 보정한 값은 아니다. 현재 지원 출력 범위는 기존 전송 계약과
프로브 제품 범위의 교집합인 -20~120°C다.

광/개폐/AI의 유효성과 분리하여 시작 직후부터 2초마다 SnapshotPacked와
Matter endpoint 2 TemperatureMeasurement에 온도를 발행한다. 60초 모델 이력을 기다리지 않는다.
온도 입력 또는 공급 측정이 무효이면 다음 보고에서 온도를 unknown으로 바꾼다.
다른 센서는 미확인 상태를 유지하고 출처는 test다.

설치·복구 이미지와 실기 로그는 `local-data/deployments/2026-10-06-temperature/`에 보관한다.
검사는 `python -m firmware.a08.verify <log> --temperature --output <new-json>`로 실행한다.
부팅 자체 검사에는 25°C 기준, 온도 변화 방향, 잘못된 입력 거부, 다른 센서 없이 즉시 온도 보고,
단선 입력에서 기존 온도를 보존하지 않는 경우를 포함한다.

실기 결과: 통합/드라이버 오류 0, 80초 연속 48~52ms 취득 및 환산 검사 통과,
온도 보고 45회 모두 유효(30.59~31.06°C), SnapshotPacked/이력 발행 결과 모두 성공.
이는 기준 온도계와 비교한 정확도 보증이나 HA 화면의 별도 수신 확인은 아니다.


## 개폐센서 독립 활성화 — 2026-10-06

`3.4.1-a08-contact-1+10`은 `CONTACT_ENABLED=true`로 P1.10 접점을 독립 활성화한다.
광센서 보정용 전역 `SENSORS_CONFIRMED=false`와 온도 활성화는 유지한다.
사용자가 배선을 바로잡은 뒤 실제 GPIO에서 자석 부착 LOW, 분리 HIGH를 확인했다.
닫힘은 SnapshotPacked의 lid=0, 열림은 lid=1이며, 시작 시 50ms 안정화 전에는 unknown이다.
기존 50ms 안정화 및 상태 변화 즉시 보고를 유지하고 endpoint 1 BooleanState에도 반영한다.
통합 자체 검사에 광센서가 미확인인 상태의 닫힘/열림 보고와 접점 무효 시 unknown 전환을 추가했다.

설치 자료는 `local-data/deployments/2026-10-06-contact/`에 보관한다.
부팅 검사는 `python -m firmware.a08.verify <log> --temperature --contact --output <new-json>`로 실행한다.
물리 접점 양방향 확인, 설치 후 펌웨어 보고, HA 화면 수신 확인은 서로 구분한다.


설치 후 실제 자석 분리 상태 lid=1 및 부착 상태 lid=0의 SnapshotPacked/이력 발행 성공을 확인했다.
부팅 자체 검사 및 센서 드라이버 오류는 0이다. 다만 수집 간격이 40~60ms를 벗어날 때
기존 파이프라인이 접점을 잠깐 unknown으로 무효화한 뒤 회복하는 현상이 관찰됐다.
따라서 활성화 및 양방향 물리 보고는 확인했지만, 이번 연속 20Hz 안정성 검사는 통과로 기록하지 않는다.
HA 화면의 수신 상태는 별도 확인하지 않았다.

## 부착 광센서 보정과 연속 UART 기록 — 2026-10-07

`fix/a08-adc-input-range`의 `3.4.1-a08-optical-log-1+11`을 설치했다.
CRP-G1020MP 부착 후 표시등 조작과 첫 예약→취사→보온→취소 회차의
측정 범위를 사용해 광센서 4개를 보정·활성화했다. 보온/백미 OFF 기준은
인접 표시등 빛 간섭을 포함하며 `raw/VDD_raw*4095` 단위다.
보정 버전은 `g1020-mounted-20261007-v1`이고, 온도·개폐 활성화는 유지한다.
예약 채널은 관찰한 첫 시간 표시등 위치에 해당하며 모든 예약 시간 설정을 보증하지 않는다.
이 회차는 개발용 보정 자료이고 독립 평가 회차로 재사용하지 않는다.

기존 동기 UART 로그가 측정 루프를 지연시키는 문제를 줄이기 위해 deferred
로그 처리를 적용했다. 측정 루프에서 BootId·순번·uptime·ADC·VDD·개폐를
20Hz로 출력하고 PC가 UART로 저장한다. 초기 shell 큐 512 B에서는 표본 순번
누락과 메시지 누락 경고가 발생했다. UART-only 시도에서는 새 A08 로그가
수신되지 않아 기존 shell 경로로 복원하고 shell 메시지 큐를 8192 B로 늘렸다.
로그 버퍼도 8192 B이고 처리 스레드 대기 간격은 10ms다.

최종 설치 후 자체 검사/드라이버 오류 0, 80초(200표본씩 8구간)의 50ms 취득,
원시 기록 1,641표본의 순번 연속성과 UART 누락 경고 0을 확인했다.
개폐는 부팅 초기 안정화 때만 unknown이고 이후 닫힘으로 유지됐다.
잠금·보온 ON, 백미·예약 OFF 보고를 확인했으며 사용자가 실제 보온 ON을 확인했다.
이 검사는 장시간 안정성, 모든 표시등 조합, 온도 정확도 또는 HA/B 수신 검증을 대신하지 않는다.
모델은 기존 후보이며 출처는 test를 유지한다.

앱 HEX 영역은 `[0xd000, 0xce2d8)`이고 FLASH 791,048 B/RAM 231,388 B다.
이미지·생성 소스·복구본·검증 로그/JSON은
`local-data/deployments/2026-10-07-optical/`에 보존한다.
기록 실행·재시작 방법은 [연속 기록 설명](../../tools/a08_capture/README.md)을 따른다.
실측 검사는 `python -m firmware.a08.verify <log> --temperature --contact --optical --output <new-json>`이다.
