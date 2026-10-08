"""The offline replay must preserve independent labels and abstention boundaries."""

from datetime import date, timedelta
from unittest import TestCase
from unittest.mock import patch

from service.auxiliary import AuxiliaryDeferral
from ai.usage_record_pipeline import moment

from ai.demo_usage_records import EVALUATION_START, generate
from ai.usage_record_pipeline import evaluate, validate_label, validate_observation


class UsageRecordPipelineTests(TestCase):
    def test_demo_replays_two_isolated_households_and_abstains_only_on_unknown_days(self):
        observations, labels = generate()
        result = evaluate(observations, labels, EVALUATION_START)
        self.assertEqual(len(result["days"]), 28)
        self.assertEqual(result["personal"]["eligible_days"], 24)
        self.assertEqual(result["personal"]["abstained_days"], 4)
        self.assertEqual(result["fixed_24h"]["eligible_days"], 24)
        self.assertEqual(result["notification_request"]["eligible_days"], 24)
        self.assertEqual(result["fixed_24h_notification_request"]["eligible_days"], 24)
        self.assertEqual(tuple(result["fixed_24h_notification_request"][key]
                               for key in ("tp", "fp", "fn", "tn")), (4, 2, 2, 16))
        self.assertLess(result["notification_request"]["fp"], result["personal"]["fp"])
        self.assertLess(result["notification_request"]["tp"], result["personal"]["tp"])
        personal_episodes = result["first_request_episodes"]["personal"]
        fixed_episodes = result["first_request_episodes"]["fixed_24h"]
        self.assertEqual(personal_episodes["episodes"], 4)
        self.assertEqual(fixed_episodes["episodes"], 4)
        self.assertEqual(personal_episodes["captured"] + personal_episodes["missed"], 4)
        self.assertEqual(fixed_episodes["captured"] + fixed_episodes["missed"], 4)
        self.assertEqual([item["start_date"] for item in personal_episodes["details"]],
                         [item["start_date"] for item in fixed_episodes["details"]])
        self.assertTrue(all(item["delay_from_label_midnight_hours"] >= 0
                            for item in personal_episodes["details"]
                            if item["first_request_at"] is not None))
        self.assertEqual({row["household_id"] for row in result["days"]},
                         {"demo_morning", "demo_twice"})
        self.assertEqual({row["abstain_reason"] for row in result["days"] if row["review"] == "unknown"},
                         {"away", "no_connection_evidence"})
        for row in result["days"]:
            self.assertEqual(row["abstain_reason"] is not None, row["review"] == "unknown")
            if row["review"] == "unknown":
                self.assertIsNone(row["personal_candidate"])
                self.assertIsNone(row["fixed_24h_candidate"])
                self.assertIsNone(row["notification_request"])
                self.assertIsNone(row["fixed_24h_notification_request"])
            elif row["notification_request"]:
                self.assertTrue(row["personal_candidate"])
            if row["abstain_reason"] is None:
                self.assertEqual(row["notification_request"], bool(row["notification_request_at"]))
                self.assertEqual(row["fixed_24h_notification_request"],
                                 bool(row["fixed_24h_notification_request_at"]))
                self.assertFalse(row["fixed_24h_notification_request"] and
                                 not row["fixed_24h_candidate"])

    def test_missing_connection_evidence_abstains_instead_of_counting_normal(self):
        observations, labels = generate()
        target = "2026-02-06"
        observations = [row for row in observations if not (
            row["household_id"] == "demo_morning" and row["event_type"] == "device_online"
            and row["observed_at"].startswith(target) and "T08:" <= row["observed_at"][10:14] <= "T16:")]
        result = evaluate(observations, labels, EVALUATION_START)
        row = next(row for row in result["days"] if row["household_id"] == "demo_morning"
                   and row["local_date"] == target)
        self.assertEqual(row["review"], "no_check_needed")
        self.assertEqual(row["abstain_reason"], "stale_connection_evidence")
        self.assertIsNone(row["personal_candidate"])

    def test_rejects_scripted_real_label_and_naive_observation(self):
        observations, labels = generate()
        with self.assertRaisesRegex(ValueError, "scripted labels"):
            validate_label({**labels[0], "mode": "live", "source": "matter"})
        with self.assertRaisesRegex(ValueError, "timezone"):
            validate_observation({**observations[0], "observed_at": "2026-01-05T00:00:00"})
        with self.assertRaisesRegex(ValueError, "earlier calendar days"):
            evaluate(observations, labels, EVALUATION_START - timedelta(days=1))
        activity = {key: observations[0][key] for key in
                    ("schema_version", "mode", "household_id", "device_id", "source")}
        activity.update({"observed_at": observations[0]["observed_at"], "signal": "motion_detected"})
        with self.assertRaisesRegex(ValueError, "matching simulation household"):
            evaluate(observations, labels, EVALUATION_START,
                     simulated_activity=[{**activity, "mode": "live", "source": "matter"}])
        self.assertIsInstance(date.fromisoformat(labels[0]["local_date"]), date)


    def test_auxiliary_deadlines_never_rewind_or_exceed_two_hours(self):
        from tools.compare_auxiliary_scenarios import compare
        original = AuxiliaryDeferral.due
        released = []
        calls = {}

        def trace(instance, at):
            previous = calls.get(instance)
            if previous is not None:
                self.assertGreaterEqual(moment(at), moment(previous))
            calls[instance] = at
            result = original(instance, at)
            released.extend(result)
            return result

        with patch.object(AuxiliaryDeferral, "due", trace):
            result = compare()
        self.assertTrue(released)
        for item in released:
            delay = moment(item["as_of"]) - moment(item["deferred_from"])
            self.assertGreaterEqual(delay, timedelta(minutes=30))
            self.assertLessEqual(delay, timedelta(hours=2))
        households = result["household_comparison"]
        self.assertEqual(households["cooker_only"]["personal"],
                         households["with_simulated_activity"]["personal"])


    def test_auxiliary_timer_crossing_midnight_is_recorded_on_release_day(self):
        from tools.compare_auxiliary_scenarios import compare
        original = AuxiliaryDeferral.due
        released = []
        def trace(instance, at):
            result = original(instance, at)
            released.extend(result)
            return result
        with patch("ai.usage_record_pipeline.CHECKS", ((23, 30),)), patch.object(AuxiliaryDeferral, "due", trace):
            result = compare()
        from ai.usage_record_pipeline import KST
        crossing = [item for item in released if
                    moment(item["as_of"]).astimezone(KST).date() >
                    moment(item["deferred_from"]).astimezone(KST).date()]
        self.assertTrue(crossing)
        self.assertTrue(all(moment(item["as_of"]) - moment(item["deferred_from"]) <= timedelta(hours=2)
                            for item in crossing))
