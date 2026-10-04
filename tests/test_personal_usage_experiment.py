"""Check the synthetic Colab experiment's time split and abstention rules."""

from unittest import TestCase

from ai.personal_usage_experiment import TRAIN_DAYS, TOTAL_DAYS, run_household, scenario_checks


class PersonalUsageExperimentTests(TestCase):
    def test_future_labels_do_not_turn_away_or_outage_into_normal_days(self):
        rows = run_household(41, "twice_daily", 0.72)
        self.assertEqual(len(rows), TOTAL_DAYS)
        self.assertEqual(sum(row["split"] == "history" for row in rows), TRAIN_DAYS)
        self.assertTrue(all(row["label"] is None for row in rows if row["scenario"] in {"away", "offline"}))
        self.assertTrue(all(row["personal_alert"] == 0 for row in rows
                            if row["scenario"] in {"away", "offline"}))
        self.assertTrue(any(row["label"] == 1 for row in rows if row["scenario"] == "scripted_gap"))
        self.assertTrue(any(row["label"] == 0 for row in rows if row["scenario"] == "natural_skip"))

    def test_irregular_gap_and_gradual_decline_use_active_model_paths(self):
        result = scenario_checks()
        self.assertEqual(result["irregular_gap"]["status"], "check_required")
        self.assertGreater(result["irregular_gap"]["elapsed_hours"],
                           result["irregular_gap"]["p95_hours"] + 2)
        self.assertIn("DECLINING_USAGE", [reason for item in result["gradual_decline"]
                                               for reason in item["reason_codes"]])
