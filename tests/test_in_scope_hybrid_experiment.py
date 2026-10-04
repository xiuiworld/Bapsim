from unittest import TestCase

from ai.in_scope_hybrid_experiment import nuisance_normal_days


class InScopeHybridExperimentTests(TestCase):
    def test_followup_request_is_attributed_to_interrupted_episode(self):
        rows = [
            {"household": "a", "episode_id": 1, "label": 1,
             "missed_opportunity_times": ["2026-03-01T08:00:00+09:00",
                                          "2026-03-01T20:00:00+09:00"],
             "notification_request_times": []},
            {"household": "a", "episode_id": None, "label": 0,
             "notification_request_times": ["2026-03-02T09:00:00+09:00"]},
            {"household": "a", "episode_id": None, "label": 0,
             "notification_request_times": ["2026-03-03T09:00:00+09:00"]},
        ]
        self.assertEqual(nuisance_normal_days(rows),
                         {"normal_days": 2, "days_with_unrelated_request": 1})
