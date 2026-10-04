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

    def test_late_tick_recovers_covered_window_without_replaying_resumed_alert(self):
        model = self._trained()
        delayed = model.evaluate("matter", "cooker_01", at(7, 20), online=True)
        midday = [item for item in delayed if item["evidence"]["slot"] == "slot_2"]
        self.assertEqual(len(midday), 1)
        self.assertEqual(midday[0]["routine_status"], "check_required")
        self.assertTrue(midday[0]["evidence"]["replayed_after_delay"])
        self.assertEqual(model.evaluate("matter", "cooker_01", at(7, 20, 30), online=True), [])

        resumed = self._trained()
        resumed.observe(event(7, 19))
        recovered = resumed.evaluate("matter", "cooker_01", at(7, 20), online=True)
        midday = [item for item in recovered if item["evidence"]["slot"] == "slot_2"]
        self.assertEqual(midday, [])

    def test_late_tick_does_not_turn_outage_into_nonuse(self):
        model = self._trained()
        model.observe(event(7, 12, kind="device_offline"))
        model.observe(event(7, 19, kind="device_online"))
        decisions = model.evaluate("matter", "cooker_01", at(7, 20), online=True)
        midday = [item for item in decisions if item["evidence"]["slot"] == "slot_2"]
        self.assertEqual(midday, [])

    def test_weak_initial_prior_waits_for_personal_history(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        decisions = model.evaluate("matter", "cooker_01", at(0, 15, 30), online=True)
        midday = [item for item in decisions if item["evidence"]["slot"] == "slot_2"]
        self.assertEqual(midday[0]["routine_status"], "learning")

    def test_configured_home_time_uses_early_confirmed_history_and_survives_restart(self):
        model = PersonalUsageModel()
        model.configure_initial_routine("matter", "cooker_01", [
            {"time": "07:00", "weekdays": list(range(7))}])
        model.observe(event(0, 0, kind="device_online"))
        for day in (0, 1):
            model.observe(event(day, 7))
        restored = PersonalUsageModel()
        model.availability("matter", "cooker_01", True, at(3, 0))
        restored.load_state(model.dump_state())
        restored.observe(event(3, 0, kind="device_online"))
        decisions = restored.evaluate("matter", "cooker_01", at(3, 9, 30), online=True)
        morning = next(item for item in decisions if item["evidence"]["slot"] == "slot_1")
        self.assertEqual(morning["routine_status"], "check_required")
        self.assertTrue(morning["evidence"]["initial_ready"])
        self.assertEqual(restored.initial_routine("matter", "cooker_01"),
                         [{"time": "07:00", "weekdays": list(range(7))}])

    def test_regular_outing_weekday_suppresses_configured_home_window(self):
        model = PersonalUsageModel()
        model.configure_initial_routine("matter", "cooker_01", [
            {"time": "13:00", "weekdays": [0, 1, 2, 3]}])
        model.observe(event(0, 0, kind="device_online"))
        for day in (0, 1):
            model.observe(event(day, 13))
        # START is Tuesday, so day 3 is Friday and is excluded.
        decisions = model.evaluate("matter", "cooker_01", at(3, 15, 30), online=True)
        midday = next(item for item in decisions if item["evidence"]["slot"] == "slot_2")
        self.assertEqual(midday["routine_status"], "no_expectation")
        self.assertTrue(midday["evidence"]["schedule_excluded"])

    def test_initial_routine_rejects_invalid_weekday_and_time(self):
        model = PersonalUsageModel()
        with self.assertRaises(ValueError):
            model.configure_initial_routine("matter", "cooker_01", [{"time": "8am"}])
        with self.assertRaises(ValueError):
            model.configure_initial_routine("matter", "cooker_01", [{"time": "08:00", "weekdays": [7]}])

    def test_optional_late_grace_waits_for_a_late_opening(self):
        model = PersonalUsageModel(late_grace_hours=1.0)
        model.observe(event(0, 0, kind="device_online"))
        for day in range(7):
            model.observe(event(day, 13))
            model.evaluate("matter", "cooker_01", at(day, 16, 30), online=True)
        expected = datetime.fromisoformat(
            model.profile("matter", "cooker_01", at(7, 12))["windows"][1]["expected_time"])
        self.assertEqual(model.evaluate("matter", "cooker_01",
                                        (expected + timedelta(hours=2, minutes=30)).isoformat(),
                                        online=True), [])
        late_open = expected + timedelta(hours=2, minutes=45)
        model.observe({**event(7, 13), "observed_at": late_open.isoformat()})
        decisions = model.evaluate("matter", "cooker_01",
                                   (expected + timedelta(hours=3, minutes=30)).isoformat(),
                                   online=True)
        midday = [item for item in decisions if item["evidence"]["slot"] == "slot_2"]
        self.assertEqual(midday[0]["routine_status"], "normal")
        self.assertEqual(midday[0]["evidence"]["decision_at"],
                         (expected + timedelta(hours=3)).astimezone(timezone.utc).isoformat())

    def test_optional_interval_quantile_exposes_earlier_candidate(self):
        models = (PersonalUsageModel(), PersonalUsageModel(interval_quantile=0.75))
        for model in models:
            model.observe(event(0, 0, kind="device_online"))
            for day in (0, 2, 4, 6, 8, 10, 12, 16, 18):
                model.observe(event(day, 17))
        later = at(20, 23)
        current = models[0].evaluate_interval("matter", "cooker_01", later, online=True)
        candidate = models[1].evaluate_interval("matter", "cooker_01", later, online=True)
        self.assertEqual(current["routine_status"], "normal")
        self.assertEqual(candidate["routine_status"], "check_required")
        self.assertEqual(current["evidence"]["p95_hours"], candidate["evidence"]["p95_hours"])
        self.assertLess(candidate["evidence"]["threshold_hours"],
                        candidate["evidence"]["p95_hours"])

    def test_robust_interval_prediction_does_not_copy_one_long_gap(self):
        current = PersonalUsageModel()
        predictive = PersonalUsageModel(interval_strategy="robust_predictive",
                                        interval_quantile=0.90)
        guarded = PersonalUsageModel(interval_strategy="guarded_predictive",
                                     interval_quantile=0.90)
        for model in (current, predictive, guarded):
            model.observe(event(0, 0, kind="device_online"))
            for day in (0, 2, 4, 6, 8, 10, 12, 16, 18):
                model.observe(event(day, 17))
        as_of = at(20, 23)
        baseline = current.evaluate_interval("matter", "cooker_01", as_of, online=True)
        candidate = predictive.evaluate_interval("matter", "cooker_01", as_of, online=True)
        combined = guarded.evaluate_interval("matter", "cooker_01", as_of, online=True)
        self.assertEqual(baseline["evidence"]["p95_hours"], 96.0)
        self.assertLess(candidate["evidence"]["threshold_hours"], 96.0)
        self.assertEqual(candidate["evidence"]["interval_strategy"], "robust_predictive")
        self.assertEqual(combined["evidence"]["threshold_hours"],
                         min(baseline["evidence"]["threshold_hours"],
                             candidate["evidence"]["threshold_hours"]))
        with self.assertRaises(ValueError):
            PersonalUsageModel(interval_strategy="unknown")

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
        self.assertEqual(model.grouped_open_times("matter", "cooker_01"),
                         (datetime.fromisoformat(at(0, 12)).astimezone(timezone.utc).isoformat(),))

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

    def test_old_or_out_of_order_opening_never_retrains_current_model(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        model.observe(event(0, 13))
        model.observe(event(1, 13))
        before = model.grouped_open_times("matter", "cooker_01")
        model.observe(event(0, 19))
        self.assertEqual(model.grouped_open_times("matter", "cooker_01"), before)

        restored = PersonalUsageModel()
        restored.load_state(model.dump_state())
        restored.observe(event(0, 20))
        self.assertEqual(restored.grouped_open_times("matter", "cooker_01"), before)

        restored.availability("matter", "cooker_01", True, at(40, 0))
        after_prune = restored.grouped_open_times("matter", "cooker_01")
        restored.observe(event(2, 13))
        self.assertEqual(restored.grouped_open_times("matter", "cooker_01"), after_prune)
        restored.observe(event(39, 13))
        self.assertEqual(restored.grouped_open_times("matter", "cooker_01"),
                         (datetime.fromisoformat(at(39, 13)).astimezone(timezone.utc).isoformat(),))

    def test_opening_after_midnight_counts_for_that_day_even_inside_previous_group(self):
        model = PersonalUsageModel()
        model.observe(event(0, 23, 50))
        model.observe(event(1, 0, 20))
        state = model._device("matter", "cooker_01")
        midnight = datetime.fromisoformat(at(1, 0)).astimezone(timezone.utc)
        self.assertEqual(len(state["opens"]), 1)
        self.assertTrue(model._used(state, midnight, midnight + timedelta(days=1)))
        self.assertEqual(model._daily_group_count(state, midnight,
                                                  midnight + timedelta(days=1)), 1)
        model.observe(event(1, 13))
        self.assertEqual(model._daily_group_count(state, midnight,
                                                  midnight + timedelta(days=1)), 2)

    def test_trend_retries_after_transient_outage_and_resets_nonconsecutive_streak(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, kind="device_online"))
        for day in range(21):
            model.observe(event(day, 8))
            model.observe(event(day, 19))
        model.availability("matter", "cooker_01", True, at(28, 0))
        self.assertIsNone(model.evaluate_trend("matter", "cooker_01", at(24, 0, 1), online=False))
        state = model._device("matter", "cooker_01")
        self.assertNotIn((START + timedelta(days=23)).date().isoformat(), state["trend_evaluations"])
        model.evaluate_trend("matter", "cooker_01", at(24, 0, 10), online=True)
        model.evaluate_trend("matter", "cooker_01", at(25, 0, 10), online=True)
        self.assertEqual(state["decline_streak"], 2)
        model.evaluate_trend("matter", "cooker_01", at(27, 0, 10), online=True)
        self.assertEqual(state["decline_streak"], 1)

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
