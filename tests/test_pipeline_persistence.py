from datetime import datetime, timedelta, timezone
import unittest

from domain.baseline import PersonalBaselineEngine
from domain.events.normalizer import CookerEventNormalizer
from domain.features import CookerFeatureExtractor
from domain.sessions import CookingSessionBuilder

from test_cooker_event_normalizer import raw
from test_cooking_session_builder import event
from test_personal_baseline import features as daily_features


class PipelinePersistenceTests(unittest.TestCase):
    def test_normalizer_restores_last_stable_state(self) -> None:
        first = CookerEventNormalizer(lid_debounce=timedelta(0))
        first.ingest(raw(0, "lid_state", "closed"))
        restored = CookerEventNormalizer(lid_debounce=timedelta(0))
        restored.load_state(first.dump_state())
        self.assertEqual([item.event_type for item in restored.ingest(raw(1, "lid_state", "open"))], ["lid_opened"])

    def test_session_and_feature_history_survive_round_trip(self) -> None:
        sessions = CookingSessionBuilder()
        extractor = CookerFeatureExtractor()
        for update in sessions.ingest(event(0, "cooking_started")):
            extractor.ingest(update.as_dict())
        for update in sessions.ingest(event(42, "cooking_finished")):
            extractor.ingest(update.as_dict())
        restored_sessions = CookingSessionBuilder()
        restored_sessions.load_state(sessions.dump_state())
        restored_features = CookerFeatureExtractor()
        restored_features.load_state(extractor.dump_state())
        for update in restored_sessions.ingest(event(65, "lid_opened")):
            restored_features.ingest(update.as_dict())
        observed = (datetime(2026, 10, 2, 7, tzinfo=timezone.utc) + timedelta(minutes=70)).isoformat()
        snapshot = restored_features.extract("simulator", "cooker_01", observed, "online")
        self.assertEqual(snapshot.cook_count_24h, 1)
        self.assertEqual(snapshot.lid_open_count_24h, 1)
        self.assertEqual(snapshot.latest_first_open_delay_min, 23)

    def test_personal_baseline_survives_round_trip(self) -> None:
        first = PersonalBaselineEngine(min_days=1)
        first.record(daily_features(0), "learning")
        restored = PersonalBaselineEngine(min_days=1)
        restored.load_state(first.dump_state())
        snapshot = restored.snapshot("simulator", "cooker_01", daily_features(1)["as_of"])
        self.assertTrue(snapshot.ready)
        self.assertEqual(snapshot.metrics["lid_open_count_24h"].median, 2)


if __name__ == "__main__":
    unittest.main()
