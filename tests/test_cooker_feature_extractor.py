from datetime import datetime, timedelta, timezone
import unittest

from domain.features import CookerFeatureExtractor


NOW = datetime(2026, 10, 3, 7, tzinfo=timezone.utc)


def at(hours_before: float) -> str:
    return (NOW - timedelta(hours=hours_before)).isoformat()


def session(identity: str, start: float, finish: float, first_open: float, *, gap: bool = False) -> dict[str, object]:
    opened = at(first_open)
    return {
        "kind": "session_finalized",
        "session": {
            "session_id": identity,
            "device_id": "cooker_01",
            "source": "simulator",
            "cooking_started_at": at(start),
            "cooking_finished_at": at(finish),
            "first_open_at": opened,
            "lid_open_times": [opened],
            "data_gap": gap,
            "closed_at": at(first_open - 0.5),
        },
    }


class CookerFeatureExtractorTests(unittest.TestCase):
    def test_window_counts_delays_and_latest_open(self) -> None:
        extractor = CookerFeatureExtractor()
        extractor.ingest(session("old", 25, 24, 23.5))
        extractor.ingest(session("new", 6, 5.5, 5))
        features = extractor.extract("simulator", "cooker_01", NOW.isoformat(), "online")
        self.assertEqual(features.cook_count_24h, 1)
        self.assertEqual(features.lid_open_count_24h, 2)
        self.assertEqual(features.hours_since_last_open, 5)
        self.assertEqual(features.median_first_open_delay_min, 30)
        self.assertEqual(features.latest_first_open_delay_min, 30)
        self.assertEqual(features.cook_interval_hours, 19)

    def test_updated_session_replaces_previous_snapshot(self) -> None:
        extractor = CookerFeatureExtractor()
        item = session("same", 6, 5.5, 5)
        extractor.ingest(item)
        extractor.ingest(item)
        self.assertEqual(extractor.extract("simulator", "cooker_01", NOW.isoformat(), "online").lid_open_count_24h, 1)

    def test_offline_values_are_missing_and_gap_is_exposed(self) -> None:
        extractor = CookerFeatureExtractor()
        extractor.ingest(session("gap", 6, 5.5, 5, gap=True))
        features = extractor.extract("simulator", "cooker_01", NOW.isoformat(), "offline")
        self.assertIsNone(features.cook_count_24h)
        self.assertIsNone(features.lid_open_count_24h)
        self.assertIsNone(features.hours_since_last_open)
        self.assertTrue(features.has_data_gap)

    def test_orphan_finish_is_a_data_gap(self) -> None:
        extractor = CookerFeatureExtractor()
        item = session("orphan", 6, 5.5, 5, gap=True)
        item["session"]["cooking_started_at"] = None
        extractor.ingest(item)
        self.assertTrue(extractor.extract("simulator", "cooker_01", NOW.isoformat(), "online").has_data_gap)

    def test_current_session_exposes_pending_first_open_time(self) -> None:
        extractor = CookerFeatureExtractor()
        current = session("current", 1.5, 1, 0.5)
        current["session"]["first_open_at"] = None
        current["session"]["lid_open_times"] = []
        current["session"]["finalized"] = False
        extractor.ingest(current)
        snapshot = extractor.extract("simulator", "cooker_01", NOW.isoformat(), "online")
        self.assertEqual(snapshot.pending_first_open_delay_min, 60)
        self.assertFalse(snapshot.cooking_in_progress)
        current["session"]["cooking_finished_at"] = None
        extractor.ingest(current)
        snapshot = extractor.extract("simulator", "cooker_01", NOW.isoformat(), "online")
        self.assertIsNone(snapshot.pending_first_open_delay_min)
        self.assertTrue(snapshot.cooking_in_progress)


if __name__ == "__main__":
    unittest.main()
