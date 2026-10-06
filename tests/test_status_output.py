from unittest import TestCase

from service import StatusOutput


class StatusOutputTests(TestCase):
    def test_versioned_state_expiry_and_separate_simulation_topic(self):
        output = StatusOutput("home_01", revision=8)
        state = output.build("2026-10-03T00:00:00Z", {
            "ready": True, "model": "personal_usage_v1", "last_lid_open_at": None,
        }, "available", {"incident_id": "gap-1", "status": "notified",
                         "reason_codes": ["EXPECTED_USE_MISSING"]}, None)
        self.assertEqual(state["revision"], 9)
        self.assertEqual(state["valid_until"], "2026-10-03T00:03:00+00:00")
        self.assertEqual(state["assessments"][0]["type"], "usage_gap")
        self.assertEqual(state["assessments"][0]["reason_codes"],
                         ["expected_window_missed"])
        self.assertEqual(output.topic("matter", "state"), "bapsim/v1/home_01/state")
        self.assertEqual(output.topic("simulator", "state"), "bapsim/simulation/v1/home_01/state")

    def test_unavailable_observation_is_visible(self):
        state = StatusOutput("home_01").build("2026-10-03T00:00:00Z", {}, "unavailable", None, None)
        self.assertEqual(state["observation"], "unavailable")
        self.assertEqual(state["sensor_issues"], ["cooker_data_unavailable"])
        self.assertIn("관측", state["summary"])

    def test_linked_evidence_and_known_policy_review_are_visible(self):
        review = "2026-10-10T07:00:00+00:00"
        gap = {"incident_id": "gap-1", "status": "notified",
               "reason_codes": ["UNUSUAL_USE_GAP"],
               "evidence_ids": ("usage_group:abc",), "next_reminder_at": review}
        output = StatusOutput("home_01")
        state = output.build("2026-10-03T00:00:00Z", {}, "available", gap, None)
        self.assertEqual(state["assessments"][0]["evidence_ids"], ["usage_group:abc"])
        self.assertEqual(state["assessments"][0]["next_evaluate_at"], review)
        resolved = output.build("2026-10-03T00:01:00Z", {}, "available",
                                {**gap, "status": "resolved"}, None)
        self.assertIsNone(resolved["assessments"][0]["next_evaluate_at"])

    def test_no_accepted_use_is_insufficient_not_personalized(self):
        state = StatusOutput("home_01").build(
            "2026-10-03T00:00:00Z",
            {"ready": False, "last_lid_open_at": None}, "available", None, None)
        self.assertEqual(state["learning"], "insufficient")
