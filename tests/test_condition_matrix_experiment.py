from unittest import TestCase

from ai.condition_matrix_experiment import (
    FUTURE_DAYS, HOLDOUT_CONDITIONS, HOLDOUT_SEEDS, SETTINGS,
    TRAIN_CONDITIONS, TRAIN_SEEDS, household, score,
)


class ConditionMatrixTests(TestCase):
    def test_target_episodes_each_remove_two_planned_openings(self):
        for rhythm in ("older_morning", "older_twice"):
            rows = household(3101, rhythm, "stable", SETTINGS["current"],
                             opportunity_interruptions=True, future_days=14)
            for episode in (1, 2):
                self.assertEqual(sum(row["missed_opportunities"] for row in rows
                                     if row["episode_id"] == episode), 2)
                self.assertEqual(sum(len(row["missed_opportunity_times"]) for row in rows
                                     if row["episode_id"] == episode), 2)

    def test_training_and_holdout_have_distinct_conditions_and_homes(self):
        self.assertFalse(set(TRAIN_CONDITIONS) & set(HOLDOUT_CONDITIONS))
        self.assertFalse(set(TRAIN_SEEDS) & set(HOLDOUT_SEEDS))

    def test_labels_are_fixed_before_candidate_settings(self):
        current = household(809, "older_alternate", "gradual_drift", SETTINGS["current"])
        proposed = household(809, "older_alternate", "gradual_drift",
                             SETTINGS["grace_and_cadence"])
        self.assertEqual(len(current), FUTURE_DAYS)
        self.assertEqual([(row["day"], row["label"], row["reason"]) for row in current],
                         [(row["day"], row["label"], row["reason"]) for row in proposed])
        self.assertEqual(sum(row["label"] is None for row in current), 2)
        self.assertEqual(sum(row["label"] == 1 for row in current), 4)
        self.assertEqual(score(current)["alerted_away_or_offline"], 0)
        self.assertEqual(score(proposed)["alerted_away_or_offline"], 0)
        self.assertEqual(score(current, "notification_request")["alerted_away_or_offline"], 0)
        self.assertEqual(score(proposed, "notification_request")["alerted_away_or_offline"], 0)
