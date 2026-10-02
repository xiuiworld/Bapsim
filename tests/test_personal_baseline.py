from datetime import date, datetime, timedelta, timezone
import unittest

from domain.baseline import PersonalBaselineEngine


START = date(2026, 9, 20)


def features(day: int, opens: int = 2, *, gap: bool = False, availability: str = "online") -> dict[str, object]:
    observed = datetime.combine(START + timedelta(days=day), datetime.min.time(), timezone.utc)
    return {
        "source": "simulator",
        "device_id": "cooker_01",
        "as_of": (observed + timedelta(hours=14)).isoformat(),
        "availability": availability,
        "has_data_gap": gap,
        "cook_count_24h": 1 if availability == "online" else None,
        "lid_open_count_24h": opens if availability == "online" else None,
        "hours_since_last_open": 4.0,
        "median_first_open_delay_min": 20.0,
        "cook_interval_hours": 24.0,
    }


class PersonalBaselineTests(unittest.TestCase):
    def test_unique_normal_days_make_personal_baseline(self) -> None:
        engine = PersonalBaselineEngine(min_days=3)
        for day, opens in enumerate((2, 2, 4)):
            self.assertTrue(engine.record(features(day, opens), "learning"))
        engine.record(features(2, 3), "learning")  # Replace same day's sample.
        snapshot = engine.snapshot("simulator", "cooker_01", features(3)["as_of"])
        self.assertTrue(snapshot.ready)
        self.assertEqual(snapshot.sample_days, 3)
        self.assertEqual(snapshot.metrics["lid_open_count_24h"].median, 2)
        self.assertEqual(snapshot.metrics["lid_open_count_24h"].samples, 3)

    def test_abnormal_and_offline_days_do_not_contaminate(self) -> None:
        engine = PersonalBaselineEngine(min_days=2)
        engine.record(features(0), "normal")
        engine.record(features(1), "normal")
        engine.record(features(1, 0), "check_required")
        self.assertFalse(engine.record(features(2, 0, gap=True), "normal"))
        self.assertFalse(engine.record(features(3, availability="offline"), "normal"))
        snapshot = engine.snapshot("simulator", "cooker_01", features(4)["as_of"])
        self.assertEqual(snapshot.sample_days, 1)
        self.assertFalse(snapshot.ready)

    def test_today_is_excluded_from_comparison(self) -> None:
        engine = PersonalBaselineEngine(min_days=1)
        engine.record(features(0), "learning")
        self.assertEqual(engine.snapshot("simulator", "cooker_01", features(0)["as_of"]).sample_days, 0)
        self.assertEqual(engine.snapshot("simulator", "cooker_01", features(1)["as_of"]).sample_days, 1)


if __name__ == "__main__":
    unittest.main()
