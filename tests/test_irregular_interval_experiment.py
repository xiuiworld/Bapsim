from unittest import TestCase

from ai.irregular_interval_experiment import FUTURE_DAYS, household


class IrregularIntervalExperimentTests(TestCase):
    def test_seeded_hourly_replay_is_reproducible_and_keeps_coverage_exclusions(self):
        first = household(809, "every_2_days", interrupted=True)
        self.assertEqual(first, household(809, "every_2_days", interrupted=True))
        self.assertEqual(len(first), FUTURE_DAYS)
        self.assertEqual(len({row["episode_id"] for row in first if row["episode_id"]}), 2)
        self.assertFalse(any(row["notification_request"] for row in first
                             if row["category"] in {"outing_declared", "offline"}))
