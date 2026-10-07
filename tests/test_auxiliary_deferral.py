from datetime import datetime, timedelta, timezone
from unittest import TestCase

from service import AuxiliaryDeferral
from tools.compare_auxiliary_scenarios import at as comparison_at, compare


START = datetime(2026, 10, 3, tzinfo=timezone.utc)


def at(minutes: int) -> str:
    return (START + timedelta(minutes=minutes)).isoformat()


def decision(source: str = "simulator") -> dict[str, object]:
    return {"source": source, "device_id": "cooker_01", "as_of": at(120),
            "routine_status": "check_required", "reason_codes": ["EXPECTED_USE_MISSING"]}


class AuxiliaryDeferralTests(TestCase):
    def test_same_synthetic_decisions_delay_only_gap_request(self):
        report = compare()
        results = report["results"]
        self.assertEqual(results["cooker_only"]["gap_requests"][0]["at"], comparison_at(60))
        self.assertEqual(results["with_simulated_activity"]["gap_requests"][0]["at"], comparison_at(90))
        self.assertEqual(results["cooker_only"]["decline_requests"],
                         results["with_simulated_activity"]["decline_requests"])
        households = report["household_comparison"]
        self.assertEqual(households["scripted_activity_events"], 144)
        self.assertEqual(households["cooker_only"]["notification_request"]["eligible_days"],
                         households["with_simulated_activity"]["notification_request"]["eligible_days"])
        self.assertNotEqual(households["cooker_only"]["notification_request"]["fp"],
                            households["with_simulated_activity"]["notification_request"]["fp"])

    def test_simulation_activity_defers_once_then_releases_candidate(self):
        helper = AuxiliaryDeferral()
        helper.record("cooker_01", at(110), "door_opened")
        self.assertTrue(helper.defer(decision()))
        restored = AuxiliaryDeferral()
        restored.load_state(helper.dump_state())
        self.assertEqual(restored.due(at(149)), [])
        result = restored.due(at(150))
        self.assertEqual(result[0]["reason_codes"][-1], "AUXILIARY_ACTIVITY")
        self.assertEqual(restored.due(at(151)), [])

    def test_activity_never_defers_live_matter(self):
        helper = AuxiliaryDeferral()
        helper.record("cooker_01", at(110), "motion_detected")
        self.assertFalse(helper.defer(decision("matter")))
        self.assertEqual(helper.pending, [])
