from unittest import TestCase

from ai.initial_routine_experiment import SCENARIOS, household


class InitialRoutineExperimentTests(TestCase):
    def test_pair_replays_same_scripted_conditions(self):
        for scenario in SCENARIOS:
            baseline = household(301, scenario, configured=False)
            configured = household(301, scenario, configured=True)
            identity = lambda row: (row["day"], row["label"], row["episode_id"],
                                    row["missed_use_at"])
            self.assertEqual([identity(row) for row in baseline],
                             [identity(row) for row in configured])
            self.assertEqual(len(baseline), 14)
            self.assertEqual(sum(row["label"] is None for row in baseline), 2)
