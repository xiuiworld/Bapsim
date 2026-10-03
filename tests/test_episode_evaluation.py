from unittest import TestCase

from ai.episode_evaluation import episode_score


class EpisodeEvaluationTests(TestCase):
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
