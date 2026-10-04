"""A05 input must not turn test data or missing reports into usage evidence."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase

from service.a05_snapshot_input import A05SnapshotInput, PATH, verified_report


def report(counter=1, sequence=1, lid=False, origin="live", boot=7):
    raw = {"SchemaVersion": 1, "BootId": boot,
           "ObservationSequence": sequence, "ReportCounter": counter,
           "LidOpen": lid, "SignalQuality": {"LidOpen": 1}}
    state = SimpleNamespace(state=str(counter), attributes={
        "input_kind": origin, "transport": "matter", "matter_entry_id": "entry-1",
        "node_id": 4, "attribute_path": PATH, "schema_version": 1,
        "read_method": "explicit_read_5s",
        "boot_id": boot, "observation_sequence": sequence,
        "report_counter": counter, "raw_snapshot": raw,
    })
    entry = SimpleNamespace(platform="bapsim_matter_probe",
                            unique_id="a05_entry-1_4_ReportCounter")
    return entry, state


class A05SnapshotInputTests(TestCase):
    def test_accepts_only_matching_live_probe_report(self):
        entry, state = report()
        self.assertIsNotNone(verified_report(entry, state, "entry-1", 4))
        state.attributes["input_kind"] = "test"
        self.assertIsNone(verified_report(entry, state, "entry-1", 4))
        state.attributes["input_kind"] = "live"
        self.assertIsNone(verified_report(entry, state, "other-entry", 4))
        state.attributes["raw_snapshot"]["SignalQuality"]["LidOpen"] = 2
        self.assertIsNone(verified_report(entry, state, "entry-1", 4))

    def test_new_report_baselines_then_detects_only_observed_opening(self):
        bridge = A05SnapshotInput("cooker", "entry-1", 4)
        now = datetime(2026, 10, 4, tzinfo=timezone.utc)
        first = bridge.accept(report()[1].attributes["raw_snapshot"], now)
        self.assertEqual([(e["event_type"], e["value"]) for e in first],
                         [("availability", "online"), ("lid_state", "closed")])
        self.assertEqual(bridge.accept(report()[1].attributes["raw_snapshot"],
                                       now + timedelta(seconds=5)), [])
        opened = bridge.accept(report(2, 2, True)[1].attributes["raw_snapshot"],
                               now + timedelta(seconds=15))
        self.assertEqual([(e["event_type"], e["value"]) for e in opened],
                         [("lid_state", "open")])

    def test_gap_and_stale_report_break_continuity(self):
        bridge = A05SnapshotInput("cooker", "entry-1", 4)
        now = datetime(2026, 10, 4, tzinfo=timezone.utc)
        bridge.accept(report()[1].attributes["raw_snapshot"], now)
        gap = bridge.accept(report(3, 3, True)[1].attributes["raw_snapshot"],
                            now + timedelta(seconds=30))
        self.assertEqual([(e["event_type"], e["value"]) for e in gap],
                         [("availability", "offline"), ("availability", "online"),
                          ("lid_state", "open")])
        self.assertFalse(bridge.available(now + timedelta(minutes=4)))
        self.assertEqual(bridge.offline(now + timedelta(minutes=4))[0]["value"], "offline")
