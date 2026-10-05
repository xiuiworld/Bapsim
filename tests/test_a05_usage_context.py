"""Verified live board estimates explain an opening without claiming a meal."""

from datetime import datetime, timedelta, timezone
from unittest import TestCase

from service.a05_contract_records import A05ContractRecords
from service.a05_usage_context import A05UsageContext
from service.ha_analysis_flow import add_board_context_evidence
from tests.test_a05_snapshot_input import report


class A05UsageContextTests(TestCase):
    def test_context_is_explanatory_and_never_changes_decision_or_future_history(self):
        at = datetime(2026, 10, 6, tzinfo=timezone.utc)
        context = {"observed_at": at.isoformat(), "classification": "warming_possible_use",
                   "lid_record_id": "stream:7"}
        decision = {"as_of": (at + timedelta(hours=1)).isoformat(),
                    "routine_status": "check_required", "score": 0.8,
                    "evidence": {"model": "personal_usage_v1"}}
        add_board_context_evidence(decision, context)
        self.assertEqual(decision["routine_status"], "check_required")
        self.assertEqual(decision["score"], 0.8)
        self.assertEqual(decision["evidence"]["last_accepted_open_context"]["lid_record_id"],
                         "stream:7")
        self.assertEqual(decision["evidence"]["board_context_role"], "explanatory_only")
        older = {"as_of": (at - timedelta(minutes=1)).isoformat(), "evidence": {}}
        add_board_context_evidence(older, context)
        self.assertEqual(older["evidence"], {})

    def test_warming_recent_cooking_and_unknown_keep_distinct_evidence(self):
        at = datetime(2026, 10, 6, tzinfo=timezone.utc)
        tracker = A05UsageContext()
        recorder = A05ContractRecords("home", "board", "sensor.a05_reportcounter")

        def feed(counter, phase, lid, quality=1):
            now = at + timedelta(seconds=counter * 15)
            raw = report(counter, counter, lid, "live")[1].attributes["raw_snapshot"]
            raw.update(CookerPhase=phase if quality else None, InferenceMethod=1,
                       ModelVersion="a07-v1", InferenceScore=9000 if quality else None,
                       TemperatureCentiC=6500)
            raw["SignalQuality"]["CookerPhase"] = quality
            rows = recorder.accept(raw, now, now)
            tracker.observe("board", "sensor.a05_reportcounter", raw, rows, now)
            return now, rows

        feed(1, 2, False)  # actual model-bearing cooking estimate
        opened, rows = feed(2, 3, True)
        tracker.stage_open("board", opened.isoformat())
        warming = tracker.take_open("board", opened.isoformat())
        self.assertEqual(warming["classification"], "warming_possible_use")
        self.assertEqual(warming["model_version"], "a07-v1")
        self.assertEqual(warming["temperature_c"], 65.0)
        self.assertIn(warming["lid_record_id"], {row["record_id"] for row in rows})
        self.assertFalse(warming["meal_confirmed"])

        feed(3, 1, False)
        opened, _ = feed(4, 1, True)
        tracker.stage_open("board", opened.isoformat())
        recent = tracker.take_open("board", opened.isoformat())
        self.assertEqual(recent["classification"], "recent_cooking_possible_use")
        self.assertIsNotNone(recent["recent_cooking_record_id"])

        tracker.reset("board")
        feed(5, 0, False, quality=0)
        opened, _ = feed(6, 0, True, quality=0)
        tracker.stage_open("board", opened.isoformat())
        unknown = tracker.take_open("board", opened.isoformat())
        self.assertEqual(unknown["classification"], "unclassified_open")
        self.assertIsNone(unknown["phase"])
        self.assertIsNone(unknown["model_version"])
        self.assertFalse(unknown["meal_confirmed"])

    def test_stream_gap_discards_old_cooking_context(self):
        at = datetime(2026, 10, 6, tzinfo=timezone.utc)
        tracker = A05UsageContext()
        recorder = A05ContractRecords("home", "board", "sensor.a05_reportcounter")
        for counter, phase, lid in ((1, 2, False), (3, 1, False), (4, 1, True)):
            now = at + timedelta(seconds=counter * 15)
            raw = report(counter, counter, lid, "live")[1].attributes["raw_snapshot"]
            raw.update(CookerPhase=phase, InferenceMethod=1,
                       ModelVersion="a07-v1", InferenceScore=9000)
            raw["SignalQuality"]["CookerPhase"] = 1
            rows = recorder.accept(raw, now, now)
            tracker.observe("board", "sensor.a05_reportcounter", raw, rows, now)
            if lid:
                tracker.stage_open("board", now.isoformat())
                self.assertEqual(tracker.take_open("board", now.isoformat())["classification"],
                                 "unclassified_open")

    def test_rule_estimate_is_recorded_but_not_called_board_ai_context(self):
        at = datetime(2026, 10, 6, tzinfo=timezone.utc)
        raw = report(1, 1, True, "live")[1].attributes["raw_snapshot"]
        raw.update(CookerPhase=3, InferenceMethod=2,
                   ModelVersion="test-fixture-only", InferenceScore=9000)
        raw["SignalQuality"]["CookerPhase"] = 1
        rows = A05ContractRecords("home", "board", "sensor.a05_reportcounter").accept(raw, at, at)
        tracker = A05UsageContext()
        tracker.observe("board", "sensor.a05_reportcounter", raw, rows, at)
        tracker.stage_open("board", at.isoformat())
        context = tracker.take_open("board", at.isoformat())
        self.assertEqual(context["classification"], "unclassified_open")
        self.assertEqual(context["inference_method"], "rule")
