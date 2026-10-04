from unittest import TestCase

from ai.episode_evaluation import episode_score


class EpisodeEvaluationTests(TestCase):
    def test_two_opportunity_episode_needs_request_after_second_miss(self):
        row = {"household": "a", "episode_id": 1, "day": 21, "label": 1,
               "missed_use_at": "2026-03-01T08:00:00+09:00",
               "missed_opportunity_times": ["2026-03-01T08:00:00+09:00",
                                            "2026-03-01T18:00:00+09:00"],
               "notification_request_times": ["2026-03-01T12:00:00+09:00"]}
        self.assertEqual(episode_score([row])["caught"], 0)
        self.assertEqual(episode_score([row])["early_request_only"], 1)
        row["notification_request_times"].append("2026-03-01T19:00:00+09:00")
        result = episode_score([row])
        self.assertEqual(result["caught"], 1)
        self.assertEqual(result["median_delay_hours"], 1.0)

    def test_two_opportunity_episode_has_same_24_hour_response_window(self):
        row = {"household": "a", "episode_id": 1, "day": 21, "label": 1,
               "missed_use_at": "2026-03-01T08:00:00+09:00",
               "missed_opportunity_times": ["2026-03-01T08:00:00+09:00",
                                            "2026-03-01T20:00:00+09:00"],
               "notification_request_times": []}
        next_day = {"household": "a", "episode_id": None, "day": 22, "label": 0,
                    "notification_request_times": ["2026-03-02T09:00:00+09:00"]}
        self.assertEqual(episode_score([row, next_day])["caught"], 1)

    def test_request_between_sparse_planned_use_days_counts_for_episode(self):
        rows = [
            {"household": "a", "episode_id": 1, "day": 21, "label": 1,
             "missed_use_at": "2026-03-01T08:00:00+09:00",
             "notification_request_times": []},
            {"household": "a", "episode_id": None, "day": 22, "label": 0,
             "notification_request_times": ["2026-03-02T10:00:00+09:00"]},
            {"household": "a", "episode_id": 1, "day": 23, "label": 1,
             "missed_use_at": "2026-03-03T08:00:00+09:00",
             "notification_request_times": []},
        ]
        self.assertEqual(episode_score(rows)["caught"], 1)

    def test_one_request_catches_two_day_episode_and_reports_delay(self):
        rows = [
            {"household": "a", "episode_id": 1, "day": 21, "label": 1,
             "missed_use_at": "2026-03-01T08:00:00+09:00",
             "notification_request_times": []},
            {"household": "a", "episode_id": 1, "day": 22, "label": 1,
             "missed_use_at": "2026-03-02T08:00:00+09:00",
             "notification_request_times": ["2026-03-02T10:00:00+09:00"]},
            {"household": "b", "episode_id": 1, "day": 21, "label": 1,
             "missed_use_at": "2026-03-01T08:00:00+09:00",
             "notification_request_times": []},
            {"household": "b", "episode_id": None, "day": 23, "label": 0,
             "notification_request_times": ["2026-03-03T10:00:00+09:00"]},
        ]
        result = episode_score(rows)
        self.assertEqual((result["episodes"], result["caught"], result["missed"]), (2, 1, 1))
        self.assertEqual(result["recall"], 0.5)
        self.assertEqual(result["median_delay_hours"], 26.0)
