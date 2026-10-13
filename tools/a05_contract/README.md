# A05 SnapshotPacked v1

Development wire format; this is a manufacturer extension, not a standard rice-cooker cluster. Firmware and HA must use the same version. These sources do not change `firmware/`, B normalization, or the simulator.

## Address and encoding

Test VID `0xFFF1`, existing example cluster `0xFFF1FC01`, endpoint 1, attribute `0xFFF10004` (`4293984260`). Read-only, reportable Matter `octet_string`, maximum 110 payload bytes. The existing A04 ReportCounter is retained at `0xFFF10003` for rollback compatibility. No control command is added.

SDK struct attributes require an AttributeAccessInterface implementation (see installed `Accessors.cpp.zapt`). This implementation deliberately uses a single bounded octet-string with an explicit layout, avoiding SDK/Core patches. This is a chosen implementation, not a claim that native structs are unsupported. Values within this attribute are from one update; independently reported standard endpoints are not an atomic transaction with it.

All multibyte integers are little endian. No padding, trailing bytes or implied fields.

| Offset | Bytes | Field |
|---|---:|---|
| 0 | 4 | ASCII `BS05` |
| 4 | 2 | SchemaVersion=1 |
| 6 | 1 | input origin: 0=live, 1=test; other values rejected |
| 7 | 4 | BootId |
| 11 | 4 | ObservationSequence |
| 15 | 8 | UptimeMs |
| 23 | 4 | ReportCounter |
| 27 | 1 | LidOpen: 0=false, 1=true, 255=null |
| 28 | 2 | signed TemperatureCentiC; -32768=null |
| 30 | 5 | LockLed, WarmLed, WhiteRiceLed, TimerFirstLed, CookerPhase |
| 35 | 7 | quality for LidOpen, TemperatureCentiC, four LEDs, CookerPhase |
| 42 | 1 | InferenceMethod |
| 43 | 2 | InferenceScore 0..10000; 65535=null |
| 45 | 1 | ModelVersion UTF-8 byte count, 0..64 |
| 46 | variable | ModelVersion UTF-8 bytes |

LED enum: 0=unknown, 1=off, 2=on, 3=blinking. Phase: 0=unknown, 1=inactive, 2=cooking, 3=warming. Quality: 0=unknown, 1=good, 2=error, 3=unsupported. Method: 0=none, 1=edge_model, 2=rule. Missing observations use null/enum 0 and non-good quality. The receiver never interprets them as a valid zero/off/inactive. This strict profile requires a method and a nonempty model/rule version for a good phase.

## Test producer

`python tools/prepare_a05_probe.py --sdk C:/ncs/v3.4.1 --output local-data/a05/probe`

The output directory must be new. Then, with the existing SDK environment loaded and working directory `C:/ncs/v3.4.1`:

```powershell
west zap-generate --full -z <probe>/src/default_zap/contact_sensor.zap -j <probe>/src/default_zap/zcl.json
west build --sysbuild -b nrf54lm20dk/nrf54lm20b/cpuapp <probe> -d <build> -- -DCONFIG_CHIP_DEVICE_VENDOR_ID=65521
# Only after verifying app HEX bounds and retaining rollback image:
west flash -d <build> --domain probe --no-rebuild --dev-id <BOARD_SERIAL>
```

The test application publishes after 10 seconds, then every 15 seconds for accelerated verification. It cycles cooking, warming, temperature-error/unknown-phase, and unsupported-timer/phase. It explicitly sets test origin; the `rule` method and `test-fixture-only` version are test fields, not claims of deployed inference. Button changes update LidOpen and the snapshot too. Standard temperature becomes null in the error case. BootId is a random uint32 on boot (collision possible, not a permanent identity). The production 60-second reporting setting and physical sensors require subsequent integration.

## HA installation

Run `python tools/a05_contract/build_ha_bundle.py`. The generated `ha_bundle/sensor.py` contains the validator, decoder and HA YAML platform. Back up the existing `/homeassistant/custom_components/bapsim_matter_probe/sensor.py`, then replace it through File editor. Existing A04 YAML entry selects the same Matter entry and node; legacy `name`/`attribute_path` are accepted only for installation compatibility. A05 reads the documented fixed SnapshotPacked path. Restart HA Core after configuration validation.

Each field gets an `A05 <FieldName>` sensor. `ReportCounter` also exposes `raw_snapshot` attributes. All sensors share one 5-second explicit read, a 10-second read timeout, the same BootId/sequence, `input_kind`, and quality metadata. Non-good signals display unknown, transport/validation failures unavailable. The raw centidegree sensor is named TemperatureCentiC; the official Matter temperature entity already displays Celsius. A frozen report counter times out after 180 seconds even if reads still succeed. This polling receiver can miss rapid intermediate transitions; sequence-gap diagnostics expose a limitation rather than reconstructing history.

Rollback: restore the backed-up sensor.py and restart HA Core; restore the prior signed application only if needed, without erase/factory-data/storage writes. The old A04 entity may remain unavailable in HA's registry after replacement; it is not the A05 connection source.

## B handoff boundary

The B service has a separate `bapsim_matter_probe` path in `service/a05_snapshot_input.py`. It verifies registry platform and unique ID, Matter entry/node/attribute path, schema, report counter, and `input_kind=live`. `input_kind=test` stays out of live personal learning, notifications, and contract records; the platform is never relabelled `matter`. The complete atomic `raw_snapshot` is validated before B consumes it. B maps its seven signal values and qualities plus `device_connection` to the agreed one-signal-per-record contract in `service/a05_contract_records.py`, stored separately under `bapsim_contract_observations/contract-YYYY-MM-DD.jsonl`. The existing event normalizer receives only verified lid/availability events. Temperature is converted from centidegrees to Celsius; LED/phase enums and phase inference metadata are preserved. The HA ReportCounter entity ID is the `source_ref`, and HA `last_updated` is `source_at`, not board `observed_at`.

`python -m unittest discover -s tests -p 'test_a05*.py'` covers the HA callback through the separate contract journal with test/live separation, malformed reports, all signals, quality, and restart/gap stream changes. The A05 test-origin device is intentionally excluded from live records; this software integration test does not change the existing 5-second polling loss limit.

Offline tests: `python -m unittest discover -s tools/a05_contract -p 'test_*.py' -v`. Device/HA evidence is recorded separately in `docs/hardware/A05_데이터모델_구현.md`.
## 중간 개폐 보존용 선택적 이력 읽기

`prepare_a05_probe.py`로 새 시험 앱을 생성하면 기존 SnapshotPacked v1과 함께
`SnapshotHistory`를 제공한다. endpoint 1 / cluster `0xFFF1FC01` /
attribute `0xFFF10005` (`1/4294048769/4293984261`), 읽기 전용
`long_octet_string`, 최대 892바이트다. 기존 단일 Snapshot의 형식·경로는 유지한다.

형식은 `BH` 2바이트, 형식 버전 1바이트(1), 개수 1바이트(1~8),
각 보고의 길이 1바이트와 기존 SnapshotPacked bytes의 반복이다.
최근 **8개 보고**를 오래된 순서로 보관한다. 개폐뿐 아니라 주기 보고도 슬롯을
차지한다. 모든 보고는 같은 BootId·출처이고 report counter와 observation
sequence가 각각 +1이며 uptime은 감소하지 않아야 한다. 재부팅 시 RAM 이력은
소멸한다. 카운터 wrap을 연속 이력으로 해석하지 않는다.

새 보드 앱과 재생성한 `ha_bundle/sensor.py`, 이력 처리를 지원하는 B 번들을
설치한 뒤 기존 A05 YAML 센서 항목에 `snapshot_history: true`를 지정한다.
기본 false는 기존 보드와 호환된다. true에서는 이력 속성 읽기 실패를 기존
최신값 읽기로 조용히 대체하지 않고 unavailable로 처리한다. 최신값과 이력을
별도로 읽어 합치지 않고, 이력 속성 한 번의 읽기에서 최신값까지 얻는다.

HA ReportCounter의 `read_method=buffered_read_5s`, `history_path`,
`raw_history`가 B의 추가 검증 대상이다. B는 이미 처리한 보고를 제외하고
순서대로 계약 기록과 기존 8필드 raw 경계에 전달한다. test/live 출처 검증은
그대로 유지한다. 최초 수신·소비자 재시작·관측 offline 이후에는 최신 보고를
기준 상태로 잡으며 보관된 과거를 새 행동으로 재생하지 않는다.

이력 보관 한도를 넘으면 누락된 순번을 복원하지 않는다. 첫 남은 보고에서
공백을 표시하고 새 기준 상태를 잡는다. 보드 uptime 차이로 추정한 사건 시각은
HA 읽기 시각을 기준으로 하며 정확한 보드 UTC가 아니다. 이전 관측보다 시간이
뒤로 가지 않도록 보정하고 실제 수신 시각은 별도로 보존한다. 3분보다 오래된
보관 보고는 행동으로 재생하지 않는다.

이 기능은 **전달된 개폐 기록의 보존**을 개선한다. B 정규화기의 기존 2초
debounce와 60분 사용 묶음 정책은 변경하지 않는다. 따라서 짧은 원시 개폐
기록 수와 최종 사용 행동 수는 다른 지표다. 새로운 물리 센서 드라이버나
무제한 오프라인 복원 기능이 아니다. 실제 버튼·단절·초과·재부팅 시험 결과는
[1차 개선 검증 기록 · 공개 요약](../../docs/IMPLEMENTATION_STATUS.md)의 5·6절을 참고한다.

읽기 오류 중에는 unavailable로 처리하되 마지막 정상 순번은 진단용으로
보존한다. `last_discontinuity`는 읽기 오류·순번 공백·재부팅을 구분하고,
`last_gap`은 가장 최근 누락의 boot·처음/마지막 순번·보고 수를 보존한다.
현재 정상 수신 중이어도 과거의 `last_gap`이 남을 수 있다. 명시적으로 실행한
B 개발 시험의 이력 복구와 live 분석의 offline 이후 최신값 기준 설정은 구분한다.

검증된 buffered 입력의 정규화는 여러 보고가 같은 시각에 도착하더라도 보드
사건 시각 간격으로 2초 debounce를 적용한다. 같은 배치의 3초 개방은 유지하고
1초 개방은 기존 정책에 따라 행동에서 제외하지만 계약 원시 기록에는 남는다.
