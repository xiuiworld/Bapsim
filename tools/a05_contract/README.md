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
west flash -d <build> --domain probe --no-rebuild --dev-id BOARD_SERIAL
```

The test application publishes after 10 seconds, then every 15 seconds for accelerated verification. It cycles cooking, warming, temperature-error/unknown-phase, and unsupported-timer/phase. It explicitly sets test origin; the `rule` method and `test-fixture-only` version are test fields, not claims of deployed inference. Button changes update LidOpen and the snapshot too. Standard temperature becomes null in the error case. BootId is a random uint32 on boot (collision possible, not a permanent identity). The production 60-second reporting setting and physical sensors require subsequent integration.

## HA installation

Run `python tools/a05_contract/build_ha_bundle.py`. The generated `ha_bundle/sensor.py` contains the validator, decoder and HA YAML platform. Back up the existing `/homeassistant/custom_components/bapsim_matter_probe/sensor.py`, then replace it through File editor. Existing A04 YAML entry selects the same Matter entry and node; legacy `name`/`attribute_path` are accepted only for installation compatibility. A05 reads the documented fixed SnapshotPacked path. Restart HA Core after configuration validation.

Each field gets an `A05 <FieldName>` sensor. `ReportCounter` also exposes `raw_snapshot` attributes. All sensors share one 5-second explicit read, a 10-second read timeout, the same BootId/sequence, `input_kind`, and quality metadata. Non-good signals display unknown, transport/validation failures unavailable. The raw centidegree sensor is named TemperatureCentiC; the official Matter temperature entity already displays Celsius. A frozen report counter times out after 180 seconds even if reads still succeed. This polling receiver can miss rapid intermediate transitions; sequence-gap diagnostics expose a limitation rather than reconstructing history.

Rollback: restore the backed-up sensor.py and restart HA Core; restore the prior signed application only if needed, without erase/factory-data/storage writes. The old A04 entity may remain unavailable in HA's registry after replacement; it is not the A05 connection source.

## B handoff boundary

Current `service/ha_matter_inputs.py` accepts only registry platform `matter`. These entities truthfully belong to `bapsim_matter_probe`, so they are **not currently accepted**. `input_kind=test` must additionally keep them out of live personal learning/notifications. Do not rename the platform or turn off validation to force acceptance. B must implement a verified extension-source path with entry/node/attribute/schema/origin checks and quality/enum mapping. The numeric ReportCounter is the candidate for B's `snapshot` connection signal; the full raw_snapshot object must not be used as a decimal counter.

Offline tests: `python -m unittest discover -s tools/a05_contract -p 'test_*.py' -v`. Device/HA evidence is recorded separately in `docs/hardware/A05_데이터모델_구현.md`.
