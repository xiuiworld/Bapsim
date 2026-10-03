from unittest import TestCase

from ai.benign_disruption_experiment import REPLICATION_SEEDS, household


class BenignDisruptionExperimentTests(TestCase):
    def test_replay_is_reproducible_and_excludes_missing_coverage(self):
        rows = household(REPLICATION_SEEDS[0], "twice_daily", incidents=False)
        self.assertEqual(rows, household(REPLICATION_SEEDS[0], "twice_daily",
                                         incidents=False))
        self.assertEqual(len(rows), 28)
        self.assertGreater(len({row["category"] for row in rows}), 3)
        self.assertTrue(all(row["label"] is None for row in rows
                            if row["category"] in {"outing_declared", "offline"}))
        self.assertFalse(any(row["notification_request"] for row in rows
                             if row["category"] in {"outing_declared", "offline"}))
