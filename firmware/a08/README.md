# A08 센서 제품 앱 통합

nRF Connect SDK 3.4.1 / nrf54lm20dk/nrf54lm20b/cpuapp.
제품 경로의 통합 소스다. NTC는 명목 온도 출력을 지원하며 기준 온도계 보정·절대 온도 정확도는 별도 검증이 필요하다.
Nordic SDK 자체를 저장소에 복사하지 않고 A05 데이터 모델 생성기에 제품 코드를 적용한다.

## 연결과 기본값

`sensors.hpp` ADC/GPIO → `pipeline.hpp` → 전처리 v5 → 고정 모델 →
SnapshotPacked + 최근 8개 SnapshotHistory → 기존 HA 수신기.
50 ms 취득 루프에서 50 ms 안정된 접점 변화를 즉시 기록하고, 정기 보고는 2초다.
ADC 센서 다섯 채널과 내부 VDD 채널은 한 번의 sequence로 읽는다. 취득 스레드는 Matter 통신보다 우선하며
서버 초기화 완료를 기다린 뒤 시작한다. 동기식 UART 출력이 관측을 방해하지 않도록
Matter 상세/진행 로그는 제품 루프 시작 때 끄고 오류 로그는 유지한다.
관측 간격은 200개 표본마다 `A08 TIMING`으로 기록한다.
모델은 60초 연속 이력을 요구하고 2초마다 계산한다. 광 판정은 20초 창이다.
늦어진 루프는 과거 표본을 가짜로 채우지 않는다.

### +17 광 간섭 보정

전처리 v5는 설치 프로필의 `warm_white_gain`과 `white_reference`로
`보온 ADC - gain * max(백미 ADC - reference, 0)`를 계산한다.
현재 계수 0.215는 저장된 백미 단독 점멸 구간에서 추정했으며, 보온의
꺼짐/켜짐 판단 경계는 보정 후 1020/1120이다. 자동으로 배경을 따라가며
켜진 표시등을 배경으로 학습하는 방식은 사용하지 않는다.
백미 기준 센서가 결측·포화이거나 보정 잔차가 음수이면 보온도 unknown이다.
실제 보온 점멸, 40–60 ms 연속성 조건, 예약 채널 제외, test 출처는 유지한다.

저장 로그 재생과 인위적 밝기 변화 시험은 현장 정확도 보증이 아니다.
센서 재부착·큰 조명 변화에는 오판 또는 unknown이 남을 수 있다.
`RAW_UART_ENABLED=false`, `RAW_ARCHIVE_ENABLED=false`로 새로운 원시 데이터
수집은 하지 않으며, 기존 외부 플래시 기록은 삭제하지 않는다.

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

## 현재 전송 모드와 설치 프로필

기본 생성은 test 출처를 유지한다. `--live-observations`는 실제 개폐·온도·LED만 live로 전송하고, `--live-edge`는 기존 고정 Edge AI의 추정·품질·모델 버전·점수를 함께 전송한다. 현장 재학습이나 규칙 기반 모델 대체는 하지 않는다. `model_live_qualified=false`는 유지한다.

```powershell
python -m firmware.a08.prepare --app <fresh-local-data-app> --model firmware/a08/model.json --live-edge
```

`site_profile.json`은 설치별 보정 예시다. 다른 부착 위치·조명에서 그대로 정확하다고 가정하지 않는다. `profile.py`가 생성하는 `site_profile.hpp`와 함께 관리한다. 예약 채널은 기본적으로 신뢰하지 않으며 해당 특성을 제외한 트리의 모든 유효 경로가 같은 상태를 지지할 때만 추정한다. 불일치·낮은 점수·입력 품질 문제는 unknown으로 남긴다.

+21은 취득(priority 0)이 처리·OpenThread(priority 1)를 선점한다. 64개 메시지 큐가 넘치면 순번 누락을 보존하고 품질 검사에서 제외한다. 40–60 ms 취득 기준을 완화하거나 결측을 채우지 않는다.

원시 UART와 새 오프라인 기록은 기본 비활성화다. 기존 외부 플래시 자료를 삭제하지 않는다. 저장 기능은 `tools.a08_storage.prepare`로 생성 앱에 추가하며 앱 VERSION을 유지한다.

SDK 3.4.1 환경에서 생성 앱을 sysbuild로 빌드한다. 설치 전 대상 장치와 이미지 주소가 앱 영역 안에 있는지 확인한다. 전체 erase, Matter 초기화, 부트로더·등록 정보 덮어쓰기는 하지 않는다. 설치 이미지·장치 식별자·운영 로그는 공개 소스에 포함하지 않는다.

실제 보온 상태에서 HA 수신을 확인한 범위와 취사·예약 전체 정확도, 장기 안정성은 다르다. 이 소스 공개가 모든 상태의 실측 검증 완료를 의미하지 않는다.
