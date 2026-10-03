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
    def test_reminder_waits_for_new_expected_window_and_resumption_requires_sent_alert(self) -> None:
        manager = DetectionStateManager(min_consecutive=1)
        first = {**detection(0, "check_required"), "evidence": {"window_end": detection(0, "normal")["as_of"]}}
        requested = manager.ingest(first)[0]
        self.assertEqual(requested.action, "notify_requested")
        self.assertEqual(manager.ingest(detection(1, "normal"))[0].action, "auto_resolved")
        self.assertEqual(manager.ingest(detection(1, "normal")), [])

        second = {**detection(62, "check_required"), "evidence": {"window_end": detection(62, "normal")["as_of"]}}
        manager.ingest(second)
        manager.mark_notified("simulator", "cooker_01", detection(63, "normal")["as_of"])
        self.assertEqual(manager.ingest(second), [])
        next_window = {**detection(122, "check_required"), "evidence": {"window_end": detection(122, "normal")["as_of"]}}
        reminder = manager.ingest(next_window)[0]
        self.assertEqual(reminder.action, "reminder_requested")
        self.assertNotEqual(reminder.notification_id, requested.notification_id)
        self.assertEqual(manager.ingest(next_window), [])
        restored = DetectionStateManager(min_consecutive=1)
        restored.load_state(manager.dump_state())
        self.assertEqual(restored.ingest(next_window), [])
        self.assertEqual(restored.mark_notified("simulator", "cooker_01", detection(123, "normal")["as_of"]).action,
                         "reminder_sent")
        self.assertEqual([item.action for item in restored.ingest(detection(124, "normal"))],
                         ["auto_resolved", "usage_resumed_requested"])
        self.assertEqual(restored.ingest(detection(125, "normal")), [])

    def test_interval_reminder_and_weekly_trend_cadence(self) -> None:
        interval = DetectionStateManager(min_consecutive=1)
        first = {**detection(0, "check_required"), "evidence": {"threshold_hours": 2.0}}
        interval.ingest(first)
        interval.mark_notified("simulator", "cooker_01", detection(1, "normal")["as_of"])
        self.assertEqual(interval.ingest({**first, "as_of": detection(120, "normal")["as_of"]}), [])
        reminder = interval.ingest({**first, "as_of": detection(121, "normal")["as_of"]})[0]
        self.assertEqual(reminder.action, "reminder_requested")
        self.assertEqual(interval.ingest({**first, "as_of": detection(122, "normal")["as_of"]}), [])

        trend = DetectionStateManager(min_consecutive=1, reminder_cadence=timedelta(days=7),
                                      resumption_alerts=False)
        trend.ingest(first)
        trend.mark_notified("simulator", "cooker_01", detection(1, "normal")["as_of"])
        self.assertEqual(trend.ingest({**first, "as_of": detection(60 * 24 * 6, "normal")["as_of"]}), [])
        self.assertEqual(trend.ingest({**first, "as_of": detection(60 * 24 * 7 + 1, "normal")["as_of"]})[0].action,
                         "reminder_requested")
        self.assertEqual([item.action for item in trend.ingest(detection(60 * 24 * 7 + 2, "normal"))],
                         ["auto_resolved"])

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

    def test_other_window_still_learning_does_not_erase_candidate(self) -> None:
        manager = DetectionStateManager()
        self.assertEqual(manager.ingest(detection(0, "check_required")), [])
        self.assertEqual(manager.ingest(detection(1, "learning")), [])
        self.assertEqual(manager.ingest(detection(2, "check_required"))[0].action,
                         "notify_requested")


if __name__ == "__main__":
    unittest.main()
