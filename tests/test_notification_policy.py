from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import TestCase

from workflow.notification_controls import NotificationControls
from workflow.notification_policy import (current_request, queue_quiet,
                                          quiet_release_allowed, release_quiet, suppressed)
from workflow import DetectionStateManager


NOW = datetime(2026, 10, 3, tzinfo=timezone.utc).isoformat()
LATER = (datetime.fromisoformat(NOW) + timedelta(days=1)).isoformat()


class NotificationPolicyTests(TestCase):
    def test_current_incident_requires_matching_action_and_status(self):
        state = {"status": "check_required", "pending_action": "notify_requested",
                 "notification_count": 0}
        self.assertTrue(current_request("notify_requested", state))
        self.assertFalse(current_request("reminder_requested", state))
        self.assertFalse(current_request("notify_requested", {**state, "pending_action": None}))
        self.assertTrue(current_request("reminder_requested", {**state, "status": "checking",
                                                               "pending_action": "reminder_requested"}))
        self.assertTrue(current_request("usage_resumed_requested", {**state, "status": "resolved",
                                                                     "notification_count": 1}))
        self.assertFalse(current_request("usage_resumed_requested", {**state, "status": "resolved"}))
        self.assertTrue(current_request("observation_issue_requested", None,
                                        observation_unavailable=True))

    def test_pause_acknowledgement_and_quiet_release(self):
        controls = NotificationControls()
        controls.pauses["trip"] = {"starts_at": NOW, "ends_at": LATER}
        controls.acknowledgements["incident"] = LATER
        self.assertTrue(suppressed("notify_requested", "incident", controls, NOW))
        self.assertTrue(suppressed("usage_resumed_requested", "incident", controls, NOW))
        self.assertFalse(suppressed("observation_issue_requested", "incident", controls, NOW))
        state = {"status": "check_required", "pending_action": "notify_requested"}
        self.assertFalse(quiet_release_allowed("notify_requested", state, observation_unavailable=False,
                                               online=True, away=False, controls=controls,
                                               now=NOW, incident_id="incident"))
        self.assertTrue(quiet_release_allowed("observation_issue_requested", None,
                                              observation_unavailable=True, online=False, away=False,
                                              controls=controls, now=NOW, incident_id="incident"))
        self.assertFalse(quiet_release_allowed("observation_issue_requested", None,
                                               observation_unavailable=True, online=True, away=False,
                                               controls=controls, now=NOW, incident_id="incident"))

    def test_new_outage_replaces_stale_queued_outage(self):
        pending = {}
        for incident_id in ("old", "new"):
            queue_quiet(pending, SimpleNamespace(action="observation_issue_requested",
                                                 source="matter", device_id="cooker",
                                                 incident_id=incident_id, notification_id=incident_id))
        self.assertEqual(list(pending), ["new"])

    def test_colliding_assessment_ids_keep_trend_precedence(self):
        gap, decline = DetectionStateManager(), DetectionStateManager()
        shared = {"source": "matter", "device_id": "cooker", "status": "check_required",
                  "pending_action": "notify_requested", "incident_id": "same"}
        gap.load_state([{**shared, "reason_codes": ["EXPECTED_USE_MISSING"]}])
        decline.load_state([{**shared, "reason_codes": ["DECLINING_USAGE"]}])
        pending = {"same": {"source": "matter", "device_id": "cooker",
                            "incident_id": "same", "action": "notify_requested"}}
        ready = release_quiet(pending, gap, decline, NotificationControls(),
                              lambda *_: False, lambda *_: True, False, NOW)
        self.assertEqual(len(ready), 1)
        self.assertIs(ready[0][1], decline)
