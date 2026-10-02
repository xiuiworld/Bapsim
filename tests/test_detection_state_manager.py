from datetime import datetime, timedelta, timezone
import unittest

from workflow import DetectionStateManager


START = datetime(2026, 10, 2, 7, tzinfo=timezone.utc)


def detection(minute: int, status: str) -> dict[str, object]:
    return {
        "source": "simulator",
        "device_id": "cooker_01",
        "as_of": (START + timedelta(minutes=minute)).isoformat(),
        "routine_status": status,
        "reason_codes": ["COOK_OPEN_MISMATCH"] if status == "check_required" else [],
    }


class DetectionStateManagerTests(unittest.TestCase):
    def test_consecutive_alert_and_responses_do_not_repeat(self) -> None:
        manager = DetectionStateManager()
        self.assertEqual(manager.ingest(detection(0, "check_required")), [])
        requested = manager.ingest(detection(1, "check_required"))
        self.assertEqual(requested[0].action, "notify_requested")
        self.assertEqual(manager.ingest(detection(2, "check_required")), [])
        notified = manager.mark_notified("simulator", "cooker_01", detection(2, "normal")["as_of"])
        self.assertEqual(notified.status, "notified")
        checking = manager.caregiver_response("simulator", "cooker_01", "checking", detection(3, "normal")["as_of"])
        self.assertEqual(checking.status, "checking")
        self.assertEqual(manager.ingest(detection(4, "check_required")), [])
        resolved = manager.caregiver_response("simulator", "cooker_01", "resolved", detection(5, "normal")["as_of"])
        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(manager.ingest(detection(6, "check_required")), [])  # cooldown
        self.assertIsNone(manager.caregiver_response("simulator", "cooker_01", "resolved",
                           detection(7, "normal")["as_of"], incident_id="old-incident"))

    def test_new_incident_after_cooldown_and_restore(self) -> None:
        manager = DetectionStateManager()
        manager.ingest(detection(0, "check_required"))
        manager.ingest(detection(1, "check_required"))
        manager.mark_notified("simulator", "cooker_01", detection(2, "normal")["as_of"])
        restored = DetectionStateManager()
        restored.load_state(manager.dump_state())
        self.assertEqual(restored.ingest(detection(3, "normal"))[0].status, "resolved")
        self.assertEqual(restored.ingest(detection(63, "check_required")), [])
        self.assertEqual(restored.ingest(detection(64, "check_required"))[0].action, "notify_requested")

    def test_data_quality_breaks_candidate_sequence(self) -> None:
        manager = DetectionStateManager()
        manager.ingest(detection(0, "check_required"))
        self.assertEqual(manager.ingest(detection(0, "check_required")), [])
        manager.ingest(detection(1, "data_unavailable"))
        self.assertEqual(manager.ingest(detection(2, "check_required")), [])


if __name__ == "__main__":
    unittest.main()
