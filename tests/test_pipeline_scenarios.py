from datetime import datetime, timedelta, timezone
import unittest

from domain.baseline import PersonalBaselineEngine
from domain.detection import RuleDetector
from workflow import DetectionStateManager
from workflow.evidence_boundary import cooker_only


START = datetime(2026, 9, 20, 9, tzinfo=timezone.utc)


def sample(day: int, hour: int = 0, *, opens: int = 4, delay: float = 20,
           availability: str = "online") -> dict[str, object]:
    return {
        "source": "simulator", "device_id": "cooker_01",
        "as_of": (START + timedelta(days=day, hours=hour)).isoformat(),
        "availability": availability, "has_data_gap": False,
        "cook_count_24h": 2 if availability == "online" else None,
        "lid_open_count_24h": opens if availability == "online" else None,
        "hours_since_last_open": 8.0 if availability == "online" else None,
        "median_first_open_delay_min": delay if availability == "online" else None,
        "latest_first_open_delay_min": delay if availability == "online" else None,
        "cook_interval_hours": 12.0 if availability == "online" else None,
    }


class FullDecisionScenarioTests(unittest.TestCase):
    def test_learning_normal_mismatch_alert_response_and_quality_gates(self):
        baseline = PersonalBaselineEngine()
        detector = RuleDetector()
        workflow = DetectionStateManager()
        for day in range(7):
            current = sample(day)
            result = detector.detect(current, baseline.snapshot("simulator", "cooker_01", current["as_of"]))
            self.assertEqual(result.routine_status, "learning")
            baseline.record(current, result.routine_status)
        normal = sample(7)
        result = detector.detect(normal, baseline.snapshot("simulator", "cooker_01", normal["as_of"]))
        self.assertEqual(result.routine_status, "normal")
        abnormal = sample(7, 1, opens=1, delay=60)
        for hour in (1, 2):
            abnormal = sample(7, hour, opens=1, delay=60)
            result = detector.detect(abnormal, baseline.snapshot("simulator", "cooker_01", abnormal["as_of"]))
            decision = cooker_only(result.as_dict())
            self.assertEqual(decision, result.as_dict())
            updates = workflow.ingest(decision)
        self.assertEqual(updates[0].action, "notify_requested")
        workflow.mark_notified("simulator", "cooker_01", abnormal["as_of"])
        self.assertEqual(workflow.caregiver_response("simulator", "cooker_01", "checking", abnormal["as_of"]).status, "checking")
        self.assertEqual(detector.detect(sample(8, availability="offline"), baseline.snapshot("simulator", "cooker_01", sample(8)["as_of"])).routine_status, "data_unavailable")
        self.assertEqual(detector.detect(sample(8), baseline.snapshot("simulator", "cooker_01", sample(8)["as_of"]), away=True).routine_status, "away")


if __name__ == "__main__":
    unittest.main()
