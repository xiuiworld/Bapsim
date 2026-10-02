"""Exercise the same cooker core path that the HA event callbacks connect."""

from datetime import datetime, timedelta, timezone
import unittest

from domain.baseline import PersonalBaselineEngine
from domain.detection import RuleDetector
from domain.events.normalizer import CookerEventNormalizer
from domain.features import CookerFeatureExtractor
from domain.sessions import CookingSessionBuilder
from workflow import DetectionStateManager
from workflow.evidence_boundary import cooker_only


DAY = datetime(2026, 9, 20, tzinfo=timezone.utc)


class RawPipelineIntegrationTests(unittest.TestCase):
    def test_simulator_raw_events_reach_alert_and_response(self):
        normalizer = CookerEventNormalizer()
        sessions = CookingSessionBuilder()
        features = CookerFeatureExtractor()
        baseline = PersonalBaselineEngine()
        detector = RuleDetector()
        workflow = DetectionStateManager()
        latest = None
        updates = []

        def process(events):
            nonlocal latest
            for event in events:
                session_updates = sessions.ingest(event.as_dict())
                for update in session_updates:
                    features.ingest(update.as_dict())
                if session_updates or event.event_type in {"device_online", "device_offline"}:
                    latest = features.extract(
                        "simulator", "cooker_01", event.observed_at, event.availability,
                    ).as_dict()
                    personal = baseline.snapshot("simulator", "cooker_01", latest["as_of"])
                    result = detector.detect(latest, personal)
                    updates.extend(workflow.ingest(cooker_only(result.as_dict())))
                    baseline.record(latest, result.routine_status)

        def send(day, hour, minute, field, value, availability="online"):
            at = DAY + timedelta(days=day, hours=hour, minutes=minute)
            raw = {
                "device_id": "cooker_01", "event_type": field, "value": value,
                "observed_at": at.isoformat(), "received_at": at.isoformat(),
                "source": "simulator", "availability": availability, "quality": "simulated",
            }
            process(normalizer.ingest(raw))
            process(normalizer.flush(at + timedelta(seconds=3)))

        send(-1, 6, 0, "availability", "online")
        send(-1, 6, 1, "cooking_state", "finished")
        send(-1, 6, 2, "lid_state", "closed")
        for day in range(7):
            send(day, 7, 0, "cooking_state", "started")
            send(day, 7, 30, "cooking_state", "finished")
            send(day, 8, 0, "lid_state", "open")
            send(day, 8, 5, "lid_state", "closed")

        normal_at = DAY + timedelta(days=7, hours=6)
        self.assertTrue(baseline.snapshot("simulator", "cooker_01", normal_at.isoformat()).ready)
        self.assertEqual(latest["lid_open_count_24h"], 1)
        self.assertEqual(latest["latest_first_open_delay_min"], 30)

        send(7, 7, 0, "cooking_state", "started")
        send(7, 7, 30, "cooking_state", "finished")
        for hour in (8, 9):
            at = DAY + timedelta(days=7, hours=hour, minutes=30)
            current = features.extract("simulator", "cooker_01", at.isoformat(), "online").as_dict()
            personal = baseline.snapshot("simulator", "cooker_01", at.isoformat())
            decision = detector.detect(current, personal)
            self.assertEqual(decision.routine_status, "check_required")
            updates.extend(workflow.ingest(cooker_only(decision.as_dict())))

        requested = [update for update in updates if update.action == "notify_requested"]
        self.assertEqual(len(requested), 1)
        incident = requested[0]
        notified = workflow.mark_notified("simulator", "cooker_01", incident.as_of)
        self.assertEqual(notified.status, "notified")
        response = workflow.caregiver_response(
            "simulator", "cooker_01", "checking", incident.as_of, incident.incident_id,
        )
        self.assertEqual(response.status, "checking")
        resolved = workflow.caregiver_response(
            "simulator", "cooker_01", "resolved", incident.as_of, incident.incident_id,
        )
        self.assertEqual(resolved.status, "resolved")


if __name__ == "__main__":
    unittest.main()
