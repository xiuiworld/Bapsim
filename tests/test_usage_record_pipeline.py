"""The offline replay must preserve independent labels and abstention boundaries."""

from datetime import date, timedelta
from unittest import TestCase

from ai.demo_usage_records import EVALUATION_START, generate
from ai.usage_record_pipeline import evaluate, validate_label, validate_observation


class UsageRecordPipelineTests(TestCase):
    def test_demo_replays_two_isolated_households_and_abstains_only_on_unknown_days(self):
        observations, labels = generate()
        result = evaluate(observations, labels, EVALUATION_START)
        self.assertEqual(len(result["days"]), 28)
        self.assertEqual(result["personal"]["eligible_days"], 24)
        self.assertEqual(result["personal"]["abstained_days"], 4)
        self.assertEqual(result["fixed_24h"]["eligible_days"], 24)
        self.assertEqual({row["household_id"] for row in result["days"]},
                         {"demo_morning", "demo_twice"})
        self.assertEqual({row["abstain_reason"] for row in result["days"] if row["review"] == "unknown"},
                         {"away", "no_connection_evidence"})
        for row in result["days"]:
            self.assertEqual(row["abstain_reason"] is not None, row["review"] == "unknown")
            if row["review"] == "unknown":
                self.assertIsNone(row["personal_candidate"])
                self.assertIsNone(row["fixed_24h_candidate"])

    def test_missing_connection_evidence_abstains_instead_of_counting_normal(self):
        observations, labels = generate()
        target = "2026-02-06"
        observations = [row for row in observations if not (
            row["household_id"] == "demo_morning" and row["event_type"] == "device_online"
            and row["observed_at"].startswith(target) and "T08:" <= row["observed_at"][10:14] <= "T16:")]
        result = evaluate(observations, labels, EVALUATION_START)
        row = next(row for row in result["days"] if row["household_id"] == "demo_morning"
                   and row["local_date"] == target)
        self.assertEqual(row["review"], "no_check_needed")
        self.assertEqual(row["abstain_reason"], "stale_connection_evidence")
        self.assertIsNone(row["personal_candidate"])

    def test_rejects_scripted_real_label_and_naive_observation(self):
        observations, labels = generate()
        with self.assertRaisesRegex(ValueError, "scripted labels"):
            validate_label({**labels[0], "mode": "live", "source": "matter"})
        with self.assertRaisesRegex(ValueError, "timezone"):
            validate_observation({**observations[0], "observed_at": "2026-01-05T00:00:00"})
        with self.assertRaisesRegex(ValueError, "earlier calendar days"):
            evaluate(observations, labels, EVALUATION_START - timedelta(days=1))
        self.assertIsInstance(date.fromisoformat(labels[0]["local_date"]), date)
