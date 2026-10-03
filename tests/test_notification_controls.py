from datetime import datetime, timedelta, timezone
from unittest import TestCase

from workflow.notification_controls import NotificationControls


NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def at(hours: int) -> str:
    return (NOW + timedelta(hours=hours)).isoformat()


class NotificationControlsTests(TestCase):
    def test_pause_is_bounded_idempotent_and_restored(self):
        controls = NotificationControls()
        command = {"command_id": "cmd-1", "action": "pause_lifestyle", "pause_id": "travel-1",
                   "starts_at": at(1), "ends_at": at(25)}
        self.assertEqual(controls.apply(command, at(0))["status"], "accepted")
        self.assertEqual(controls.apply(command, at(0))["status"], "accepted")
        self.assertEqual(controls.revision, 1)
        with self.assertRaises(ValueError):
            controls.apply({**command, "ends_at": at(26)}, at(0))
        restored = NotificationControls()
        restored.load_state(controls.dump_state())
        self.assertFalse(restored.is_paused(at(0)))
        self.assertTrue(restored.is_paused(at(1)))
        self.assertFalse(restored.is_paused(at(25)))
        restored.apply({"command_id": "cmd-2", "action": "cancel_pause", "pause_id": "travel-1"}, at(2))
        self.assertFalse(restored.is_paused(at(3)))

    def test_acknowledgement_expires_and_rejects_unbounded_control(self):
        controls = NotificationControls()
        controls.apply({"command_id": "cmd-3", "action": "acknowledge",
                        "incident_id": "case-1", "ends_at": at(12)}, at(0))
        self.assertTrue(controls.is_acknowledged("case-1", at(11)))
        self.assertFalse(controls.is_acknowledged("case-1", at(12)))
        with self.assertRaises(ValueError):
            controls.apply({"command_id": "cmd-4", "action": "acknowledge",
                            "incident_id": "case-1", "ends_at": at(0)}, at(0))
