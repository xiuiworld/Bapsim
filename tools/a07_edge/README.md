# A07 원시 센서 합성·학습 도구

A의 보드 상태 추정 준비용 합성 학습·보드 대조 도구다. B의 `ai/`, `inputs/simulator/`, 정규화·생활 패턴·알림 코드를 사용하거나 변경하지 않는다. HA 서비스는 호출하지 않는다. `board.py`는 A 소유의 임시 앱에 시험 코드를 생성하며 빌드·플래시는 아래 별도 SDK 명령으로 수행한다. `firmware/`는 수정하지 않는다.

센서 없이 실제 보드 전처리·모델 결과를 A05 Matter 시험 경로로 보내는
명시적 통합은 [`A07_BOARD_TEST.md`](../../deploy/haos/A07_BOARD_TEST.md)를 따른다.
이 경로는 합성 원시 재생과 B의 격리된 시험 처리만 연결한다.

## 실행

Python 3.11+와 NumPy 2.3.5를 사용한다. 이 노트북에서 검증한 Python:

```powershell
$a07Python = 'C:\Users\xiui\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $a07Python -m unittest tools.a07_edge.test_a07 -v
& $a07Python -m tools.a07_edge generate local-data/a07/my-run --seed 7307
& $a07Python -m tools.a07_edge train local-data/a07/my-run
```

다른 환경은 별도 venv에 `python -m pip install -r tools/a07_edge/requirements.txt`를 실행한다. 생성 출력 폴더는 비어 있어야 한다. 기존 평가 결과를 덮어 생성하지 않는다. `train` 재실행은 같은 폴더의 모델·평가 보고서를 갱신한다. 기존 최종 평가 결과를 본 뒤 설정을 조정했다면 새 실험으로 표시하고 새 독립 평가 세트를 확보한다.

기본값은 일반 120개 설치 프로필과 스트레스 30개 프로필, 프로필마다 20개 180초 발췌 시계열이다. 일반 프로필 84/18/18개를 train/validation/test로 먼저 분리하고 이후 윈도우를 만든다. 스트레스 30개는 학습·임계값 선택에 쓰지 않는다. 중복되는 윈도우 18만여 개를 독립 회차 수라고 부르지 않는다. 더 크게 실행하려면 `--profiles`, `--per-profile`, `--stress-profiles`를 지정한다.

## 물리 가정과 상태 의미

- 확정 기기: **CRP-G1020MP**. 보관 설명서 파일명은 G1030MP이며 공통 적용은 미확인이다.
- 광센서 4개 순서: 잠김, 보온, 백미, 첫 시간 LED. 전체 예약 표시를 읽는 구성이 아니다.
- 설명서에서 얻은 후보: 메뉴 선택의 점멸, 취사 완료→보온, 보온 점등/재가열 점멸. 취사 중 백미 LED의 지속 점등은 아직 실물에서 확인하지 않은 **생성 가정**이다.
- `cooking`: 운전상 취사 중. `warming`: 일반 보온 유지. `inactive`: 운전상 취사·일반 보온이 아닌 대기/메뉴 설정/예약 대기/취소 후 냉각. 온도가 높다는 이유만으로 취사로 라벨링하지 않는다.
- `unknown`: 재가열의 기존 계약상 분류 미확정 또는 관측만으로 모호한 사례. 센서 오류/부족/낮은 점수도 추론 시 unknown으로 보류한다. `unknown`은 wire CookerPhase=0, quality=0, score=null이며 inactive로 바꾸지 않는다.
- 재가열은 일반 취사로 묶지 않는다. 예약 대기를 내부 시나리오로 생성하되 예약 유무·완료시각 신호를 새로 만들어 B에 보내지 않는다. 첫 시간 LED 하나의 꺼짐은 예약 없음의 증거가 아니다.
- 합성 정답은 먼저 선택한 운전 시나리오에서 온다. 생성한 센서 온도에 임계값을 적용해 정답을 다시 만들지 않는다. 실제 음식량/압력/내부 가열 제어를 시뮬레이션한 모델은 아니다.

생성 가정: 주변 16~32°C(스트레스 5~40°C), 외부 취사 목표 온도 43~90°C, 보온 32~65°C, 응답 시상수 70~650초, 점멸 0.6~1.8Hz(스트레스 0.15~3.5Hz). **외부 부착 위치에서 측정한 범위가 아니다.** 느린 점멸과 온도 겹침은 구분 실패 가능성을 평가하기 위해 포함했다. 내부 밥 온도로 해석하지 않는다. 12개 시나리오가 프로필당 20개에 순환 배정돼 완전 균등하지 않다.

## 파일과 재현성

| 출력 | 내용 |
|---|---|
| `manifest.json` | seed, 생성 버전, 샘플링, 프로필별 보정·분할 |
| `sessions.jsonl` | 회차·부모 ID, 출처, 시나리오, fault, raw SHA256 |
| `raw/*.npz` | 20Hz uptime/ADC 5채널/개폐/BootId/잠재 정답 |
| `features.npz` | 특징·정답·사용 가능 여부·프로필·회차·분할 |
| `model.json`, `a07_model.h` | 깊이 최대 6 결정트리 후보와 float32 C 추론 함수 |
| `evaluation.json` | 모델·규칙 비교, 판단 비율/판단 중 정확도/보류 포함 재현율/혼동행렬/시나리오별 평가 |
| `parity_vectors.npz` | PC 예상 결과와 C/보드 대조용 벡터. 생성만으로 대조 통과는 아님 |

모델 버전은 트리·선택 임계값·특징·학습 데이터 파일 해시를 반영한다. leaf 빈도 점수는 실제 환경에서 보정된 확률이 아니다. 임계값은 validation에서만 선택하며 후보 중 정확도 95% 이상, unknown 오수락 5% 이하를 만족하는 것의 판단 비율을 최대화한다. 조건 미충족이면 `.99` 보류 기준으로 내보내되 `validation_target_met=false`로 표시한다. 모든 후보는 `live_qualified=false`다.

## 원시 로그 수집·재생

`a07.raw.v1` JSONL은 다음 필드를 필수로 가진다.

```json
{"schema":"a07.raw.v1","input_kind":"measured","session_id":"cook-001","boot_id":1,"uptime_ms":0,"sequence":0,"adc":[500,3000,500,500,2048],"lid":false,"calibration_version":"placement-001","firmware_version":"raw-reader-v1","label":null,"note":"operator has not labelled this sample"}
```

- `adc`: 잠김/보온/백미/첫 시간/NTC 순서, 0~4095 또는 null. `lid`: true가 열림, 오류는 null. 접점 극성은 취득 시 명시적으로 맞춘다.
- LED·접점은 20Hz, NTC는 1Hz로 취득한 최신 값을 같은 초의 행에 유지한다. 현재 구현은 주기가 맞는 로그를 요구하고 불규칙 로그를 임의 보간하지 않는다.
- uptime은 보드 단조 시간이며 UTC가 아니다. sequence는 이 원시 취득 로그의 샘플 순번이며 A05 ObservationSequence와 다르다. BootId 변경과 순번/시간 공백을 구분한다.
- label은 사람의 운전 기록에서 부여한다. null은 무라벨로 보존하고 학습에서 제외한다. unknown과 null을 구분한다. label/note/시나리오/시간 ID는 모델 특징에 넣지 않는다.
- 실측 출처 `measured`는 실제 센서 취득에만 쓴다. A05 버튼 시험/합성 출력을 measured로 위장하지 않는다.

```powershell
& $a07Python -m tools.a07_edge record source.jsonl local-data/a07/captured.jsonl
# 외부 취득 프로그램이 같은 JSONL을 stdout으로 출력할 경우:
# <취득 프로그램> | & $a07Python -m tools.a07_edge record - local-data/a07/captured.jsonl
& $a07Python -m tools.a07_edge replay local-data/a07/captured.jsonl calibration.json
```

`record`는 검증하며 저장하고 기존 파일을 덮어쓰지 않는다. 오류/중단 시 그전까지의 파일이 남으므로 완전한 회차로 간주하지 않는다. 이 도구는 ADC 핀 드라이버·직렬 장치 접속기가 아니다. 실제 보드에서 JSONL을 내보내는 취득 코드는 센서 배선 확인 후 연결한다.

## 실측으로 재학습

`import-measured`의 JSON spec은 `sessions` 목록이다. 각 원시 로그의 `path`, 설치 조건을 묶는 `profile`, `split`(train/validation/test/stress), `scenario`, `calibration`을 지정한다. calibration 필드는 `calibration.example.json`을 참고하되 실제 측정한 off/on 값·분압 방향을 사용한다. path는 spec 위치에 상대적이다.

```json
{"sessions":[{"path":"cook-001.jsonl","profile":"placement-001","split":"train","scenario":"white_cooking","calibration":{"version":"placement-001","led_off":[500,500,500,500],"led_on":[3000,3000,3000,3000]}}]}
```

위는 항목 형식만 보이는 예제다. 실제 실행은 각각 라벨이 있는 train/validation/test 세트를 포함해야 한다. 동일 설치 profile 또는 session을 서로 다른 세트에 넣으면 거부한다. 평가용 실측에서 얻은 정보를 이용해 모델 임계값/생성기를 조정하면 새 평가용 회차가 필요하다. 각 profile의 보정은 독립 off/on 기준 측정으로 수행하고 평가 운전 정답에서 보정값을 역추정하지 않는다.

```powershell
& $a07Python -m tools.a07_edge import-measured local-data/a07/real/spec.json local-data/a07/real-prepared
& $a07Python -m tools.a07_edge train local-data/a07/real-prepared
```

실측 import는 합성과 자동 혼합하지 않는다. 보고서가 입력 출처를 구분하며 충분한 실측 검토 전 자동 live 승인하지 않는다.

## 전처리와 보드 이식 경계

- LED: off/on 보정, 0.4/0.6 히스테리시스, 모델 특징은 최근 2초의 점등 비율/전환 수. v3의 LED 판정은 별도 20초 창을 사용한다(아래 느린 점멸 보완 절).
- NTC: 10kΩ/B3950 후보와 직렬저항, 배선 방향, 오프셋을 설정한다. 레일/비정상 값은 오류다. 모델 입력은 최근 온도와 60초 기울기다.
- 접점: 50ms 안정 유지, 오류/시간 역전/공백에서 상태 재설정. 개방 비율은 특징 하나일 뿐 실제 식사 라벨이 아니다.
- 모델 입력은 60초 연속 이력 후 2초마다 갱신한다. 필수 채널 오류/BootId 변경/누락이면 보류하고 다른 정상 관측 자체는 폐기하지 않는다.
- C 헤더 입력은 **원시 ADC가 아닌 11개 float32 특징**과 valid 플래그다. 특징 입력 대조 및 원시 재생→보드 전처리→모델 대조, 실행 시간·정적 메모리는 확인했다. ADC/GPIO부터 Matter까지의 제품 경로는 [A08 통합](../../firmware/a08/README.md)에 연결했다. 실제 센서 보정·성능 확인은 남아 있다.
- `phase_fields()`는 A05 후보 enum/품질/방법/버전/점수 부분만 반환한다. 완전한 SnapshotPacked나 B 정규화기를 재구현하지 않는다. 합성 시험은 `input_kind=test`로 유지한다.

실행 결과와 현재 완료 범위는 [A07 검증 기록](../../docs/hardware/A07_합성학습_검증.md)을 따른다.

## 보수적인 정확도 개선 후보 비교

```powershell
& $a07Python -m tools.a07_edge.refine select local-data/a07/run-7307 local-data/a07/refined-v1
# 위에서 후보를 먼저 고정한 후, 새 seed로 외부 평가 데이터 생성
& $a07Python -m tools.a07_edge generate local-data/a07/audit-9811 --seed 9811 --profiles 30 --per-profile 20 --stress-profiles 10
& $a07Python -m tools.a07_edge.refine audit local-data/a07/run-7307 local-data/a07/refined-v1 local-data/a07/audit-9811
```

이미 실행된 출력 폴더는 재사용할 수 없다. select는 원래 train/validation만 쓰고 원래 모델을 보존한다. audit의 모든 일반 회차는 외부 평가용이며 학습하지 않는다. 동일 seed 또는 겹치는 원시 회차 seed를 거부한다. 로컬 session/profile ID는 데이터셋마다 재사용되므로 전역 구분에는 dataset seed를 함께 쓴다. 새 평가에서 약점을 본 뒤 고치면 다음 평가에는 또 다른 독립 세트가 필요하다. 개선 후보도 `live_qualified=false`이며 스트레스 unknown 오수락이 더 커진 한계를 보고서에 함께 남겼다.
## 보드에서 모델 대조 시험

`board.py`는 A05의 `local-data` 아래 임시 앱에 모델과 대조 벡터를 추가한다. `firmware/`와 SDK 원본은 수정하지 않는다. 모델은 부팅 20초 뒤 별도 스레드에서 합성 특징 벡터를 평가하고 UART에 `A07 BEGIN/END`를 남긴다. 이 시험은 ADC 전처리나 실측 정확도 검증이 아니며, 모델 결과를 A05 속성으로 발행하지 않는다. 기존 A05 `test-fixture-only` 통신은 계속된다.

```powershell
python -m tools.a07_edge.board --model local-data/a07/refined-v1/model.json --vectors local-data/a07/run-7307/parity_vectors.npz --app local-data/a05/probe
./tools/a07_edge/sdk_command.ps1 -CommandText 'west build -d C:/Users/xiui/AppData/Local/Temp/bapsim-a05-probe'
```

첫 명령은 NumPy 환경에서 실행한다. 새 환경에서는 먼저 `tools/prepare_a05_probe.py`로 앱을 생성하고 A05 README대로 ZAP 생성과 최초 sysbuild를 마친다. `sdk_command.ps1`의 경로는 현재 A 노트북 SDK/툴체인에 맞춰져 있으며 해당 프로세스 환경만 바꾼다.

플래시 전 성공한 기존 `probe/zephyr/zephyr.signed.hex`를 별도 보관하고, 새 HEX의 모든 주소가 앱 슬롯 `[0xd000, 0x1f0000)` 안인지 확인한다. 전체 삭제나 factory data 재기록 없이 `west flash --domain probe --no-rebuild --dev-id <확인한 ID>`로 앱만 기록한다. 최초 등록은 A03 절차이며 이 명령으로 새 보드 전체 초기 설치를 대신하지 않는다.

SDK Python의 pyserial로 아래 명령을 먼저 켜고 다른 터미널에서 플래시하면 부팅 로그를 보존할 수 있다. 출력 파일은 기존 파일 덮어쓰기를 거부한다.

```powershell
& C:/ncs/toolchains/4f5b6ad6dd/opt/bin/python.exe tools/a07_edge/capture_serial.py --port COM8 --seconds 100 --output local-data/a07/board/boot.log
```

합격 기준은 `mismatch=0`, `score_mismatch=0`, 정상 종료와 이후 A05 보고 재개다. 시간은 1 MHz 시스템 카운터로 잰 개별 호출 경과 시간이며 인터럽트/선점을 포함할 수 있다. 스택 잔여량은 이 시험 스레드의 관측값이고 전체 Matter 앱 최대 RAM 사용량이 아니다.

## 원시 입력 전처리의 보드 대조

`preprocess.hpp`는 센서 드라이버와 독립된 C++17 스트리밍 구현이다. `reset(calibration)` 후 `push(sample, result)`를 20Hz 입력마다 호출한다. 1,200개 입력 전에는 false, 이후 40개 입력마다 true를 반환한다. true는 결과 창이 생성됐다는 뜻이며, `result.valid=false`이면 모델은 판단을 보류해야 한다. 광센서·접점의 개별 관측은 별도로 유지된다. 보정이 유효하지 않으면 reset이 false를 반환하고 출력하지 않는다.

- 고정 링 버퍼에는 최근 1,200개의 시간·연속성·광센서 상태/품질·접점과 온도 관측을 보관한다. 동적 할당은 없다.
- 입력은 시간/순번 uint64, BootId uint32, 5채널 ADC 및 접점이다. ADC 누락은 NaN, 접점 누락은 -1이다. 실센서 ADC 드라이버·핀 배정·타이머·직렬 원시 출력은 포함하지 않는다.
- 4개 LED는 0.4/0.6 히스테리시스와 최근 40개의 모델 특징, 별도 400개의 LED 판정 이력을 사용한다. 접점은 50ms debounce를 사용한다.
- NTC는 입력의 0,20,40… 번째만 사용해 1Hz 관측을 만든다. 60개 온도 관측으로 기울기를 계산한다. 실제 드라이버도 동일한 표본 위상을 지켜야 하며, 오래된 값을 새 취득값처럼 채워 넣으면 안 된다.
- 직전 링크의 시간 차 40~60ms, 순번 +1, BootId 동일을 검사한다. 최근 60초 창이 불연속이면 valid=false다. 프레임은 스스로 정렬하거나 누락을 보간하지 않는다. 단일 스트림 인스턴스에 다른 장치의 표본을 섞지 않는다.
- 계산 중 보정/NTC/회귀에는 double, 모델 입력에는 float32를 쓴다. PC 특징값과의 허용 차는 `2e-5 + 2e-6*abs(expected)`이며, 품질·LED enum·최종 판정은 정확히 같아야 한다. 모델 점수 허용 차는 1e-6이다.

```powershell
python -m tools.a07_edge.raw_board --model local-data/a07/refined-v1/model.json --app local-data/a05/probe
./tools/a07_edge/sdk_command.ps1 -CommandText 'west build -d C:/Users/xiui/AppData/Local/Temp/bapsim-a05-probe'
```

첫 명령은 NumPy 환경에서 실행한다. 위 모델 대조 준비에 이어 실행하며, PC의 `observations.extract`를 독립 기대값으로 사용한다. 14가지 합성 원시 시나리오와 PC 기대값은 임시 앱의 읽기 전용 영역에 생성한다. 재생 데이터 저장 시 NaN은 uint16의 65535로 표현했다가 시험 입력에서 NaN으로 복구한다. 물리 ADC는 12비트 범위이므로 65535를 유효 센서값으로 취급하지 않는다.

`A07 RAW CASE`의 features/valid/led/model 오류가 모두 0이고, `A07 RAW END`의 failures=0인지 확인한다. 창 생성 시간은 40번째 입력의 push 호출 경과 시간으로, 직전 39개 입력 처리·ADC 취득·Matter 전송 시간까지 합친 값이 아니다. 전체 정적 메모리에는 검증용 원시 데이터와 기대값도 포함되므로 실제 배포 모델 크기와 구분한다. 이 시험도 A05 전송 값이나 B의 학습·알림 경로를 변경하지 않는다.

기록 종료 후 `python -m tools.a07_edge.verify_raw_log <boot.log> <raw_manifest.json> --output <verification.json>`으로 시나리오 누락·중복·버전·오류·최종 요약을 검사한다. 로그가 불완전하거나 대조에 실패하면 성공 결과 파일을 만들지 않는다.
## 중간점검 후 전처리 변경

`a07-preprocess-v2`는 시간·순번·BootId 불연속 때 광센서의 이전 히스테리시스
상태를 폐기한다. 채널 오류는 해당 채널만 폐기한다. 초기/복구 후 0.4~0.6의
중간 구간만 관측되면 unknown을 유지하고, 정상 입력이 확정 임계값을 넘은 뒤
최근 광센서 창의 품질을 다시 확보해야 모델을 허용한다. 정상 연속 관측의
히스테리시스는 유지한다. 원시 보드 재생 시나리오는 기존 14개에 중간값 복구
4개를 추가한 18개다.

PC와 C++ 모두 ADC 최대값·저항·beta·offset·LED 기준값의 유한성, 양수로
정의된 매개변수의 범위, LED 기준값의 ADC 범위와 최소 대비를 검사한다.
물리 부착·ADC 기준/gain의 적합성까지 이 검사로 확정하지 않는다.

기존 저장 합성 특징과 이전 보드 실기 로그는 v1 결과다. 후속 v2 보드 대조에서
18개 시나리오·578개 창의 오류 0을 확인했다. 별도 안전 검사에서도 보정값
55개 거부·10개 수용, LED 복구 28가지 오류 0을 확인했다.
`raw_manifest.json`의 `safety` 항목이 있으면 로그 검사기가 해당 결과의 누락·중복·오류도 거부한다.
상세 증거와 현재 설치 이미지는
[1차 개선 검증 기록](../../docs/hardware/A_중간점검_1차_개선.md)의 6절에 있다.
기존 모델을 이 합성 시험만으로 실사용 승인하지 않는다.
PC 재현: `python -m unittest tools.a07_edge.test_recovery -v`.

## 느린 점멸 보완 (전처리 v3)

`a07-preprocess-v3`는 20 Hz 기준 최근 400개(약 20초)의 광 관측으로 LED를
분류한다. 연결·해당 채널 품질이 온전한 창에서 전환 0개는 점등/소등,
3개 이상은 반복 점멸, 1~2개는 전환 중 unknown이다. 단일 펄스를 확정 점멸로
분류하지 않는다. 불완전한 창은 unknown이며, 모든 채널의 상태가 확정되고
보온 LED가 점멸이 아닐 때만 기존 모델을 허용한다.

모델의 11개 특징과 2초 특징 창·가중치는 그대로다. 20초 관측은 이를 사용해도
되는지 제한하는 추가 조건이다. 따라서 입력 정보가 충분해도 전환 중에는
`valid=false`일 수 있다. 모델이 학습한 단기 특징을 장기 특징으로 바꾸지 않는다.
각 LED 품질은 해당 채널의 확정 여부이고 온도·접점 관측은 계속 독립적이다.

후보 시험 범위는 0.1~2 Hz, 켜짐 비율 10~90%, 20 Hz 샘플링이다.
주기나 실제 LED 의미를 측정했다는 뜻은 아니다. 20초 안에 드러나지 않는 아주
느린 전환이나 샘플 사이에 끝나는 펄스는 판별을 보장할 수 없다. 상태 전환과
채널 오류 뒤 LED 확정에는 완전한 새 20초 창 및 최대 2초 출력 대기가 필요하다.
새 boot/통신 공백 후 모델의 기존 60초 유효 이력 요구도 유지한다.

```powershell
python -m unittest tools.a07_edge.test_blink tools.a07_edge.test_recovery -v
python -m tools.a07_edge.evaluate_blink --model local-data/a07/refined-v1/model.json --output <new-evaluation.json>
```

평가는 모델 재학습·임계값 탐색 없이 동일한 특징과 기존 모델을 비교한다.
기본 54개 외에 별도 주기·비율·위상의 96개 파형과 후보 밖 18개를 함께 기록한다.
정수/유리수 시간으로 경계를 생성해 부동소수점의 누적 경계 오차를 피한다.
`raw_board`는 기존 18개 PC/보드 대조에 보드 자체 점멸·전환 검사 220개를
추가하며, `verify_raw_log`는 `A07 BLINK END`까지 확인해야 통과한다.
