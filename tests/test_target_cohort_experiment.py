"""Verify cohort separation and scripted labels in the expanded benchmark."""

from unittest import TestCase

from ai.target_cohort_experiment import (FUTURE_DAYS, HISTORY_DAYS, HOLDOUT_SEEDS,
                                         PROFILES, STRESS_SEEDS, TRAIN_SEEDS,
                                         run_household, score)


class TargetCohortExperimentTests(TestCase):
    def test_households_and_time_splits_are_disjoint(self):
        self.assertFalse(set(TRAIN_SEEDS) & set(HOLDOUT_SEEDS))
        self.assertFalse(set(TRAIN_SEEDS) & set(STRESS_SEEDS))
        self.assertFalse(set(HOLDOUT_SEEDS) & set(STRESS_SEEDS))
        for profile in PROFILES:
            rows = run_household(HOLDOUT_SEEDS[0], profile, 0.72)
            self.assertEqual(len(rows), HISTORY_DAYS + FUTURE_DAYS)
            self.assertEqual(sum(row["split"] == "history" for row in rows), HISTORY_DAYS)
            self.assertTrue(all(row["label"] is None for row in rows[:HISTORY_DAYS]))
            self.assertTrue(any(row["label"] == 1 for row in rows[HISTORY_DAYS:]))

    def test_unavailable_days_abstain_and_scoring_is_deterministic(self):
        first = run_household(HOLDOUT_SEEDS[0], "batch_every_other_day", 0.72)
        self.assertEqual(first, run_household(HOLDOUT_SEEDS[0], "batch_every_other_day", 0.72))
        unavailable = [row for row in first if row["scenario"] in {"away", "offline"}]
        self.assertEqual(len(unavailable), 2)
        self.assertTrue(all(row["label"] is None and row["personal_alert"] == 0
                            for row in unavailable))
        result = score(first, "personal_alert")
        self.assertEqual(result["eligible_days"] + result["abstained_days"], FUTURE_DAYS)
        self.assertEqual(result["tp"] + result["fp"] + result["fn"] + result["tn"],
                         result["eligible_days"])
