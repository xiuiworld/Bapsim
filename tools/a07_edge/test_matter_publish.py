"""The A07 publisher is explicit, model-derived and confined to a disposable app."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from service.a05_connection_test import A05ConnectionTest
from service.a05_snapshot_input import verified_test_report, verified_report
from tools.a05_contract.wire import PATH, decode, encode
from tools.a07_edge.model import tree_predict
from tools.a07_edge.observations import extract

from .matter_publish import prepare, select_stages


MODEL = {"version": "a07-tree-v1-test", "input_kind": "synthetic",
         "live_qualified": False, "score_threshold": .8,
         "nodes": [
             {"feature": 2, "threshold": .5, "left": 1, "right": 2,
              "class": 3, "score": 1.},
             {"feature": 4, "threshold": .5, "left": 3, "right": 4,
              "class": 3, "score": 1.},
             {"feature": -1, "threshold": 0, "left": -1, "right": -1,
              "class": 2, "score": 1.},
             {"feature": -1, "threshold": 0, "left": -1, "right": -1,
              "class": 0, "score": 1.},
             {"feature": -1, "threshold": 0, "left": -1, "right": -1,
              "class": 1, "score": 1.},
         ]}


class MatterPublisherTests(TestCase):
    def test_model_trace_to_a05_wire_to_b_phase_result(self):
        now = datetime.now(timezone.utc)
        run = A05ConnectionTest("home", "board", "sensor.a05_reportcounter",
                                "run-a07", now, scenario="a07_phase_sequence")
        result = None
        for counter, (_, data) in enumerate(select_stages(MODEL), 1):
            x, valid, _, leds = extract(data["t"], data["adc"], data["lid"],
                                         data["boot"], data["cal"], sequence=data["seq"])
            prediction, scores = tree_predict(MODEL["nodes"], x, valid, MODEL["score_threshold"])
            phase = int(prediction[0])
            names = ("LockLed", "WarmLed", "WhiteRiceLed", "TimerFirstLed")
            snapshot = {"SchemaVersion": 1, "BootId": 77,
                        "ObservationSequence": counter, "UptimeMs": counter * 15000,
                        "ReportCounter": counter, "LidOpen": False,
                        "TemperatureCentiC": (None if not valid[0] else int(round(float(x[0, 8]) * 100))),
                        "CookerPhase": 0 if phase == 3 else phase + 1,
                        "InferenceMethod": 1, "ModelVersion": MODEL["version"],
                        "InferenceScore": None if phase == 3 else int(round(float(scores[0]) * 10000)),
                        "SignalQuality": {"LidOpen": 1, "TemperatureCentiC": 1 if valid[0] else 2,
                                          "CookerPhase": 0 if phase == 3 else 1}}
            for i, name in enumerate(names):
                value = int(leds[0, i])
                snapshot[name] = value
                snapshot["SignalQuality"][name] = 1 if value else 0
            decoded, origin = decode(encode(snapshot, test_input=True))
            self.assertTrue(origin)
            state = SimpleNamespace(state=str(counter), attributes={
                "input_kind": "test", "transport": "matter", "matter_entry_id": "entry-1",
                "node_id": 4, "attribute_path": PATH, "read_method": "explicit_read_5s",
                "schema_version": 1, "boot_id": 77, "observation_sequence": counter,
                "report_counter": counter, "raw_snapshot": decoded})
            entry = SimpleNamespace(platform="bapsim_matter_probe",
                                    unique_id="a05_entry-1_4_ReportCounter")
            self.assertIsNone(verified_report(entry, state, "entry-1", 4))
            raw = verified_test_report(entry, state, "entry-1", 4)
            self.assertIsNotNone(raw)
            _, result = run.accept(raw, now + timedelta(seconds=counter * 15), now)
        self.assertEqual([x["phase"] for x in result["phase_evidence"]],
                         ["inactive", "cooking", "warming", "unknown"])

    def test_selects_four_observed_stages_from_model(self):
        stages = select_stages(MODEL)
        self.assertEqual(len(stages), 4)
        self.assertTrue(all(len(data["t"]) == 1200 for _, data in stages))
        self.assertTrue(all(name != "fixed-result" for name, _ in stages))

    def test_generates_test_only_board_publisher_without_touching_firmware(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "model.json"
            model.write_text(json.dumps(MODEL), encoding="utf-8")
            app = root / "local-data" / "a05-app"
            source = app / "src" / "app_task.cpp"
            source.parent.mkdir(parents=True)
            schema = source.parent / 'default_zap/NordicDevKitCluster.xml'
            schema.parent.mkdir()
            schema.write_text('<attribute name="SnapshotHistory"/>')
            source.write_text('''#include <zephyr/sys/byteorder.h>
uint32_t sReportCounter = 0;
void PublishSnapshot(bool advance) {}
constexpr chip::EndpointId x = 1;
sLidOpen = !pressed;
            PublishSnapshot(false);
k_timer_start(&sProbeTimer, K_SECONDS(10), K_SECONDS(15));
''', encoding="utf-8")
            (app / "CMakeLists.txt").write_text(
                "target_sources(app PRIVATE src/a07/raw_selftest.cpp)\n", encoding="utf-8")
            manifest = prepare(model, app)
            self.assertEqual([x["expected"] for x in manifest["stages"]],
                             ["inactive", "cooking", "warming", "unknown"])
            changed = source.read_text(encoding="utf-8")
            self.assertIn("A07_SYNTHETIC_MATTER", changed)
            self.assertIn("StartA07Replay()", changed)
            self.assertNotIn("PublishSnapshot(false)", changed)
            self.assertNotIn("raw_selftest.cpp", (app / "CMakeLists.txt").read_text())
            self.assertIn("sObservationSequence", changed)
            self.assertIn("u8(1); // schema v1, explicit test origin",
                          (app / "src/a07/a07_snapshot.hpp").read_text())
            with self.assertRaises(ValueError):
                prepare(model, root / "Firmware" / "local-data" / "a05-app")
