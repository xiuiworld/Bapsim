from datetime import datetime, timedelta, timezone
from unittest import TestCase

from domain.personal_model import PersonalUsageModel
from workflow import DetectionStateManager


KST = timezone(timedelta(hours=9))
START = datetime(2026, 9, 1, tzinfo=KST)


def at(day: int, hour: int, minute: int = 0) -> str:
    return (START + timedelta(days=day, hours=hour, minutes=minute)).isoformat()


def event(day: int, hour: int, minute: int = 0, kind: str = "lid_opened") -> dict[str, str]:
    return {
        "source": "matter", "device_id": "cooker_01", "event_type": kind,
        "observed_at": at(day, hour, minute), "availability": "online",
    }


class PersonalUsageModelTests(TestCase):
    def _trained(self) -> PersonalUsageModel:
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        for day in range(7):
            model.observe(event(day, 15, 30))
            model.evaluate("matter", "cooker_01", at(day, 18), online=True)
        return model

    def test_week_of_late_use_moves_expected_time_and_missing_window_is_flagged(self):
        model = self._trained()
        profile = model.profile("matter", "cooker_01", at(7, 12))
        afternoon = profile["windows"][1]
        expected = datetime.fromisoformat(afternoon["expected_time"])
        self.assertGreater(expected.hour, 14)
        self.assertGreaterEqual(afternoon["covered_days"], 7)
        self.assertGreater(afternoon["personal_weight"], 0.7)
        decisions = model.evaluate("matter", "cooker_01", at(7, 18), online=True)
        missing = [item for item in decisions if item["evidence"]["slot"] == "slot_2"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["routine_status"], "check_required")
        self.assertEqual(missing[0]["reason_codes"], ["EXPECTED_USE_MISSING"])
        self.assertEqual(model.evaluate("matter", "cooker_01", at(7, 18, 30), online=True), [])

    def test_weak_initial_prior_waits_for_personal_history(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        decisions = model.evaluate("matter", "cooker_01", at(0, 15, 30), online=True)
        midday = [item for item in decisions if item["evidence"]["slot"] == "slot_2"]
        self.assertEqual(midday[0]["routine_status"], "learning")

    def test_regular_evening_user_is_not_expected_at_midday(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        for day in range(7):
            model.observe(event(day, 20, 30))
            model.evaluate("matter", "cooker_01", at(day, 22), online=True)
        decisions = model.evaluate("matter", "cooker_01", at(7, 15, 30), online=True)
        midday = [item for item in decisions if item["evidence"]["slot"] == "slot_2"]
        self.assertEqual(midday[0]["routine_status"], "no_expectation")
        self.assertLess(midday[0]["score"], model.alert_probability)

    def test_new_routine_gradually_moves_expected_time(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        for day in range(7):
            model.observe(event(day, 13))
            model.evaluate("matter", "cooker_01", at(day, 15, 30), online=True)
        before = datetime.fromisoformat(model.profile("matter", "cooker_01", at(7, 12))["windows"][1]["expected_time"])
        for day in range(7, 14):
            model.observe(event(day, 16))
            model.availability("matter", "cooker_01", True, at(day, 19))
        after = datetime.fromisoformat(model.profile("matter", "cooker_01", at(14, 12))["windows"][1]["expected_time"])
        self.assertGreater((after - before).total_seconds(), 45 * 60)

    def test_offline_window_is_not_counted_as_inactivity_or_training(self):
        model = self._trained()
        model.observe(event(7, 12, kind="device_offline"))
        decisions = model.evaluate("matter", "cooker_01", at(7, 18), online=False)
        missing = [item for item in decisions if item["evidence"]["slot"] == "slot_2"]
        self.assertEqual(missing[0]["routine_status"], "data_unavailable")
        model.observe(event(8, 0, kind="device_online"))
        profile = model.profile("matter", "cooker_01", at(8, 12))
        self.assertEqual(profile["windows"][1]["covered_days"], 7)

    def test_away_window_is_excluded(self):
        model = self._trained()
        model.set_away(True, at(7, 9))
        decisions = model.evaluate("matter", "cooker_01", at(7, 18), online=True, away=True)
        self.assertEqual([item for item in decisions if item["evidence"]["slot"] == "slot_2"][0]["routine_status"], "away")
        model.set_away(False, at(7, 22))
        profile = model.profile("matter", "cooker_01", at(8, 12))
        self.assertEqual(profile["windows"][1]["covered_days"], 7)

    def test_restart_does_not_assume_coverage_during_downtime(self):
        model = self._trained()
        restored = PersonalUsageModel()
        restored.load_state(model.dump_state())
        restored.availability("matter", "cooker_01", True, at(8, 0))
        decisions = restored.evaluate("matter", "cooker_01", at(8, 18), online=True)
        self.assertEqual([item for item in decisions if item["evidence"]["slot"] == "slot_2"][0]["routine_status"], "check_required")
        self.assertEqual(restored.profile("matter", "cooker_01", at(8, 12))["windows"][1]["covered_days"], 7)

    def test_repeated_openings_within_episode_are_one_observation(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        model.observe(event(0, 12))
        model.observe(event(0, 12, 5))
        self.assertEqual(len(model.dump_state()["devices"][0]["opens"]), 1)

    def test_fixed_sixty_minute_group_does_not_extend_with_later_openings(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        for minute in (0, 20, 59, 60, 110):
            model.observe(event(0, 12 + minute // 60, minute % 60))
        stored = model.dump_state()["devices"][0]
        self.assertEqual(len(stored["raw_opens"]), 5)
        self.assertEqual(
            [datetime.fromisoformat(value).astimezone(KST).isoformat() for value in stored["opens"]],
            [at(0, 12), at(0, 13)],
        )
        restored = PersonalUsageModel()
        restored.load_state(model.dump_state())
        self.assertEqual(restored.dump_state()["devices"][0]["opens"], stored["opens"])

    def test_decline_requires_long_reference_and_three_daily_evaluations(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        for day in range(14):
            model.observe(event(day, 8))
            model.observe(event(day, 19))
        alerts = [model.evaluate_trend("matter", "cooker_01", at(day, 0, 1), online=True)
                  for day in range(19, 23)]
        self.assertEqual(sum(item is not None and item["routine_status"] == "check_required"
                             for item in alerts), 1)
        candidate = next(item for item in alerts if item is not None and item["routine_status"] == "check_required")
        self.assertEqual(DetectionStateManager(min_consecutive=1).ingest(candidate)[0].action,
                         "notify_requested")
        self.assertEqual(model.evaluate_trend("matter", "cooker_01", at(22, 1), online=True), None)
        restored = PersonalUsageModel()
        restored.load_state(model.dump_state())
        self.assertEqual(restored.evaluate_trend("matter", "cooker_01", at(22, 2), online=True), None)

    def test_decline_does_not_count_missing_coverage_as_zero_use(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        for day in range(14):
            model.observe(event(day, 8))
            model.observe(event(day, 19))
        model.observe(event(14, 0, kind="device_offline"))
        for day in range(19, 25):
            self.assertIsNone(model.evaluate_trend("matter", "cooker_01", at(day, 1), online=False))
