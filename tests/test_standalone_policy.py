from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from domain.personal_model import PersonalUsageModel
from service.standalone import AnalysisService
from workflow.quiet_hours import QuietHours


KST = timezone(timedelta(hours=9))
START = datetime(2026, 9, 1, tzinfo=KST)


def at(day: int, hour: int = 0) -> str:
    return (START + timedelta(days=day, hours=hour)).isoformat()


def event(day: int, hour: int, kind: str) -> dict[str, str]:
    return {"source": "matter", "device_id": "cooker_01", "event_type": kind,
            "observed_at": at(day, hour), "availability": "online"}


class PolicyTests(TestCase):
    def test_worker_defers_simulated_activity_and_restores_pending_decision(self):
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            service = AnalysisService("home", state_file, lambda *args: None, lambda update: True)
            activity_at = at(7, 10)
            decision_at = (datetime.fromisoformat(activity_at) + timedelta(minutes=5)).isoformat()
            service.process_simulated_activity({"schema_version": "0.1.0", "household_id": "home",
                                                "source": "simulator", "device_id": "cooker_01",
                                                "at": activity_at, "signal": "door_opened"})
            with self.assertRaises(ValueError):
                service.process_simulated_activity({"schema_version": "0.1.0", "household_id": "home",
                                                    "source": "matter", "device_id": "cooker_01",
                                                    "at": activity_at, "signal": "door_opened"})
            service.availability[("simulator", "cooker_01")] = True
            decision = {"source": "simulator", "device_id": "cooker_01", "as_of": decision_at,
                        "routine_status": "check_required", "reason_codes": ["EXPECTED_USE_MISSING"]}
            with patch.object(service.model, "evaluate", return_value=[decision]), \
                 patch.object(service.model, "evaluate_interval", return_value=None), \
                 patch.object(service.model, "evaluate_trend", return_value=None):
                service.evaluate("simulator", "cooker_01", decision_at)
            self.assertEqual(len(service.auxiliary.pending), 1)
            self.assertEqual(service.gap.dump_state(), [])
            restored = AnalysisService("home", state_file, lambda *args: None, lambda update: True)
            self.assertEqual(len(restored.auxiliary.pending), 1)
            due = (datetime.fromisoformat(decision_at) + timedelta(minutes=31)).isoformat()
            restored.availability[("simulator", "cooker_01")] = True
            restored.last_heartbeat[("simulator", "cooker_01")] = datetime.fromisoformat(due)
            with patch.object(restored.model, "evaluate", return_value=[]), \
                 patch.object(restored.model, "evaluate_interval", return_value=None), \
                 patch.object(restored.model, "evaluate_trend", return_value=None):
                restored.tick(due)
            self.assertEqual(restored.auxiliary.pending, [])
            self.assertEqual(restored.gap.dump_state()[0]["status"], "normal")

    def test_retained_initial_routine_is_validated_replaced_and_persisted(self):
        with TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            service = AnalysisService("home", state_file, lambda *args: None, lambda update: True)
            payload = {"schema_version": "0.1.0", "household_id": "home", "routines": [
                {"source": "matter", "device_id": "cooker_01", "windows": [
                    {"time": "08:00", "weekdays": [0, 1, 2, 3, 4]}]}]}
            service.process_routine_config(payload)
            restored = AnalysisService("home", state_file, lambda *args: None, lambda update: True)
            self.assertEqual(restored.model.initial_routine("matter", "cooker_01"),
                             [{"time": "08:00", "weekdays": [0, 1, 2, 3, 4]}])
            with self.assertRaises(ValueError):
                restored.process_routine_config({**payload, "household_id": "other"})
            with self.assertRaises(ValueError):
                restored.process_routine_config({**payload, "routines": [
                    {"source": "matter", "device_id": "cooker_01", "windows": [
                        {"time": "25:00"}]}]})
            self.assertEqual(len(restored.model.initial_routine("matter", "cooker_01")), 1)
            restored.process_routine_config({**payload, "routines": []})
            self.assertEqual(restored.model.initial_routine("matter", "cooker_01"), [])

    def test_quiet_hours_cross_midnight_and_changeable(self):
        policy = QuietHours()
        self.assertTrue(policy.is_quiet(at(0, 23)))
        self.assertTrue(policy.is_quiet(at(1, 7)))
        self.assertEqual(policy.next_delivery_at(at(0, 23)),
                         datetime.fromisoformat(at(1, 8)).astimezone(timezone.utc).isoformat())
        self.assertFalse(policy.is_quiet(at(1, 8)))
        policy.configure("13:00", "14:00")
        self.assertTrue(policy.is_quiet(at(1, 13)))
        self.assertFalse(policy.is_quiet(at(1, 22)))

    def test_irregular_interval_requires_coverage_and_grace(self):
        model = PersonalUsageModel()
        model.observe(event(0, 0, "device_online"))
        # Alternating gaps spread openings across the clock rather than one daily slot.
        openings = [(0, 1), (1, 6), (2, 12), (3, 19), (5, 3), (6, 12), (7, 22), (9, 9)]
        for day, hour in openings:
            model.observe(event(day, hour, "lid_opened"))
        last = START + timedelta(days=9, hours=9)
        limit = max((START + timedelta(days=b, hours=h) -
                     (START + timedelta(days=a, hours=g)))
                    for (a, g), (b, h) in zip(openings, openings[1:]))
        first = (last + limit + timedelta(hours=1)).isoformat()
        second = (last + limit + timedelta(hours=3)).isoformat()
        watch = model.evaluate_interval("matter", "cooker_01", first, online=True)
        self.assertIsNotNone(watch)
        self.assertEqual(watch["routine_status"], "watch")
        required = model.evaluate_interval("matter", "cooker_01", second, online=True)
        self.assertEqual(required["routine_status"], "check_required")
        model.observe({**event(9, 10, "device_offline"), "observed_at": at(12, 0)})
        self.assertIsNone(model.evaluate_interval("matter", "cooker_01", at(13), online=False))

    def test_worker_control_is_idempotent_and_state_survives_restart(self):
        with TemporaryDirectory() as directory:
            messages = []
            service = AnalysisService("home", Path(directory) / "state.json",
                                      lambda *args: messages.append(args), lambda update: True)
            service.process_event(event(0, 0, "device_online"), at(0))
            command = {"schema_version": "0.1.0", "household_id": "home",
                       "command_id": "pause-1", "action": "pause_lifestyle",
                       "created_at": at(0), "pause_id": "trip-1",
                       "starts_at": at(0), "ends_at": at(1)}
            self.assertEqual(service.process_control(command, at(0))["status"], "accepted")
            self.assertEqual(service.process_control(command, at(0))["status"], "accepted")
            self.assertEqual(service.controls.revision, 1)
            restored = AnalysisService("home", Path(directory) / "state.json",
                                       lambda *args: messages.append(args), lambda update: True)
            self.assertTrue(restored.controls.is_paused(at(0)))
            self.assertEqual(restored.process_control({**command, "command_id": "stale"}, at(3))["status"],
                             "rejected")
            self.assertTrue(any(item[0].endswith("control_result") for item in messages))

    def test_overnight_alert_is_sent_only_if_still_active(self):
        with TemporaryDirectory() as directory:
            sent = []
            service = AnalysisService("home", Path(directory) / "state.json",
                                      lambda *args: None, lambda update: sent.append(update) or True)
            service.availability[("matter", "cooker_01")] = True
            for hour in (22, 23):
                updates = service.gap.ingest({"source": "matter", "device_id": "cooker_01",
                                              "as_of": at(0, hour), "routine_status": "check_required",
                                              "reason_codes": ["EXPECTED_USE_MISSING"]})
                service._handle(updates, service.gap, at(0, hour))
            self.assertEqual(len(service.pending_quiet), 1)
            self.assertEqual(sent, [])
            service.last_heartbeat[("matter", "cooker_01")] = datetime.fromisoformat(at(1, 8))
            service.tick(at(1, 8))
            self.assertEqual(len(sent), 1)
            self.assertEqual(service.pending_quiet, {})

    def test_reminder_and_resumption_are_distinct_and_persisted(self):
        with TemporaryDirectory() as directory:
            sent = []
            state_file = Path(directory) / "state.json"
            service = AnalysisService("home", state_file, lambda *args: None,
                                      lambda update: sent.append(update) or True)
            key = ("matter", "cooker_01")
            service.availability[key] = True

            def missed(day):
                return {"source": key[0], "device_id": key[1], "as_of": at(day, 12),
                        "routine_status": "check_required", "reason_codes": ["EXPECTED_USE_MISSING"],
                        "evidence": {"window_end": at(day, 11)}}

            service.gap.ingest(missed(0))
            service._handle(service.gap.ingest(missed(1)), service.gap, at(1, 12))
            self.assertEqual([item.action for item in sent], ["notify_requested"])
            service._handle(service.gap.ingest(missed(2)), service.gap, at(2, 12))
            self.assertEqual([item.action for item in sent], ["notify_requested", "reminder_requested"])
            self.assertNotEqual(sent[0].notification_id, sent[1].notification_id)
            service.save()

            restored = AnalysisService("home", state_file, lambda *args: None,
                                       lambda update: sent.append(update) or True)
            restored.availability[key] = True
            restored._handle(restored.gap.ingest(missed(2)), restored.gap, at(2, 13))
            self.assertEqual(len(sent), 2)
            restored.process_event(event(2, 13, "lid_opened"), at(2, 13))
            self.assertEqual([item.action for item in sent],
                             ["notify_requested", "reminder_requested", "usage_resumed_requested"])
            self.assertEqual(restored.gap.dump_state()[0]["status"], "resolved")
