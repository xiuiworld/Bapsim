import unittest

from domain.baseline import BaselineSnapshot, MetricStats
from domain.detection import RuleDetector


def baseline(ready: bool = True) -> BaselineSnapshot:
    return BaselineSnapshot(
        device_id="cooker_01",
        source="simulator",
        as_of="2026-10-02T07:00:00+00:00",
        sample_days=7 if ready else 2,
        ready=ready,
        metrics={
            "cook_count_24h": MetricStats(2, 1, 3, 7),
            "lid_open_count_24h": MetricStats(4, 3, 5, 7),
            "median_first_open_delay_min": MetricStats(20, 15, 30, 7),
            "hours_since_last_open": MetricStats(8, 5, 12, 7),
        },
        open_count_trend_per_day=0,
    )


def features(**changes: object) -> dict[str, object]:
    result = {
        "source": "simulator",
        "device_id": "cooker_01",
        "as_of": "2026-10-02T07:00:00+00:00",
        "availability": "online",
        "has_data_gap": False,
        "cook_count_24h": 2,
        "lid_open_count_24h": 4,
        "hours_since_last_open": 8.0,
        "latest_first_open_delay_min": 20.0,
    }
    result.update(changes)
    return result


class RuleDetectorTests(unittest.TestCase):
    def test_normal_and_cook_open_mismatch(self) -> None:
        detector = RuleDetector()
        self.assertEqual(detector.detect(features(), baseline()).routine_status, "normal")
        result = detector.detect(features(lid_open_count_24h=1, latest_first_open_delay_min=60), baseline())
        self.assertEqual(result.routine_status, "check_required")
        self.assertIn("COOK_OPEN_MISMATCH", result.reason_codes)
        self.assertIn("FIRST_OPEN_DELAY_HIGH", result.reason_codes)

    def test_quality_away_and_learning_gates(self) -> None:
        detector = RuleDetector()
        self.assertEqual(detector.detect(features(availability="offline"), baseline()).routine_status, "data_unavailable")
        self.assertEqual(detector.detect(features(has_data_gap=True), baseline()).routine_status, "data_unavailable")
        self.assertEqual(detector.detect(features(), baseline(), away=True).routine_status, "away")
        self.assertEqual(detector.detect(features(), baseline(False)).routine_status, "learning")

    def test_sustained_inactivity(self) -> None:
        result = RuleDetector().detect(
            features(cook_count_24h=0, lid_open_count_24h=0, hours_since_last_open=30), baseline()
        )
        self.assertEqual(result.routine_status, "check_required")
        self.assertIn("ACUTE_INACTIVITY", result.reason_codes)

    def test_new_cooking_session_waits_for_first_open(self) -> None:
        detector = RuleDetector()
        fresh = features(
            cook_count_24h=2, lid_open_count_24h=0,
            hours_since_last_open=8, latest_first_open_delay_min=60,
            pending_first_open_delay_min=5, cooking_in_progress=False,
        )
        self.assertEqual(detector.detect(fresh, baseline()).routine_status, "normal")
        self.assertEqual(detector.detect(features(**{**fresh, "pending_first_open_delay_min": 45}), baseline()).routine_status,
                         "check_required")
        self.assertEqual(detector.detect(features(**{**fresh, "pending_first_open_delay_min": None,
                                                    "cooking_in_progress": True}), baseline()).routine_status, "normal")


if __name__ == "__main__":
    unittest.main()
