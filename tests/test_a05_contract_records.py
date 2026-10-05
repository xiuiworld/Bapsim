"""A05 atomic HA report to agreed one-signal-per-record B contract."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest import TestCase

from service.a05_contract_records import A05ContractRecords, valid_snapshot


CASES = json.loads((Path(__file__).resolve().parents[1] /
                    "tools/a05_contract/snapshots.test.json").read_text(encoding="utf-8"))["cases"]


class A05ContractRecordTests(TestCase):
    def test_every_signal_has_contract_type_quality_and_provenance(self):
        mapper = A05ContractRecords("home_1", "cooker_1", "sensor.a05_reportcounter")
        now = datetime(2026, 10, 4, 10, tzinfo=timezone.utc)
        rows = mapper.accept(deepcopy(CASES[0]["snapshot"]), now, now - timedelta(seconds=1))
        self.assertEqual(len(rows), 8)
        self.assertEqual({row["signal"] for row in rows}, {
            "device_connection", "lid_open", "surface_temperature_c", "lock_led",
            "warm_led", "white_rice_led", "timer_first_led", "cooker_phase"})
        self.assertTrue(all(row["schema_version"] == "0.1.0" and row["mode"] == "live"
                            and row["observed_at"] is None and row["source_ref"] == "sensor.a05_reportcounter"
                            and row["source_at"] == "2026-10-04T09:59:59Z" for row in rows))
        self.assertEqual([row["sequence"] for row in rows], list(range(8)))
        by_signal = {row["signal"]: row for row in rows}
        self.assertEqual(by_signal["surface_temperature_c"]["value"], 21.5)
        self.assertEqual(by_signal["warm_led"]["value"], "off")
        self.assertEqual(by_signal["cooker_phase"]["value"], "cooking")
        self.assertEqual(by_signal["cooker_phase"]["inference"],
                         {"method": "rule", "version": "test-fixture-only", "score": None})
        self.assertIsNone(by_signal["lid_open"]["inference"])
        self.assertEqual(mapper.accept(deepcopy(CASES[0]["snapshot"]), now, now), [])

    def test_partial_quality_gap_and_restart(self):
        mapper = A05ContractRecords("home_1", "cooker_1", "sensor.a05_reportcounter")
        now = datetime(2026, 10, 4, tzinfo=timezone.utc)
        initial = mapper.accept(deepcopy(CASES[1]["snapshot"]), now, now)
        first_stream = initial[0]["stream_id"]
        error = mapper.accept(deepcopy(CASES[2]["snapshot"]), now + timedelta(seconds=15), now)
        by_signal = {row["signal"]: row for row in error}
        self.assertEqual((by_signal["surface_temperature_c"]["quality"],
                          by_signal["surface_temperature_c"]["value"],
                          by_signal["surface_temperature_c"]["kind"]), ("error", None, "quality"))
        self.assertEqual((by_signal["cooker_phase"]["quality"],
                          by_signal["cooker_phase"]["value"],
                          by_signal["cooker_phase"]["inference"]), ("unknown", None, None))
        self.assertEqual(by_signal["lid_open"]["quality"], "good")
        recovered = deepcopy(CASES[0]["snapshot"])
        recovered.update(ObservationSequence=4, ReportCounter=4, UptimeMs=181000)
        good_again = mapper.accept(recovered, now + timedelta(seconds=20), now)
        self.assertEqual(next(row for row in good_again if row["signal"] == "surface_temperature_c")["kind"],
                         "snapshot")
        restarted = mapper.accept(deepcopy(CASES[3]["snapshot"]), now + timedelta(seconds=30), now)
        self.assertEqual(len(restarted), 16)  # old stream quality/offline, then new snapshot
        self.assertEqual(restarted[7]["signal"], "device_connection")
        self.assertEqual(restarted[7]["value"], "offline")
        self.assertNotEqual(restarted[8]["stream_id"], first_stream)
        self.assertEqual(restarted[8]["sequence"], 0)
        self.assertEqual(len({row["record_id"] for row in restarted}), len(restarted))

    def test_invalid_atomic_payload_is_rejected(self):
        raw = deepcopy(CASES[0]["snapshot"])
        raw["TemperatureCentiC"] = "2150"
        self.assertFalse(valid_snapshot(raw))
        raw = deepcopy(CASES[0]["snapshot"])
        raw["SignalQuality"].pop("WarmLed")
        self.assertFalse(valid_snapshot(raw))

    def test_long_silence_starts_new_stream_even_without_counter_gap(self):
        mapper = A05ContractRecords("home_1", "cooker_1", "sensor.a05_reportcounter")
        now = datetime(2026, 10, 4, tzinfo=timezone.utc)
        first = deepcopy(CASES[0]["snapshot"])
        previous_stream = mapper.accept(first, now, now)[0]["stream_id"]
        following = deepcopy(first)
        following.update(ObservationSequence=2, ReportCounter=2, UptimeMs=1000 + 240000)
        rows = mapper.accept(following, now + timedelta(minutes=4), now)
        self.assertEqual(len(rows), 16)
        self.assertNotEqual(rows[-1]["stream_id"], previous_stream)
