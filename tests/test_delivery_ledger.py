from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from service.standalone import AnalysisService
from workflow import DeliveryLedger


START = datetime(2026, 10, 2, 3, tzinfo=timezone.utc)


def at(seconds: int) -> str:
    return (START + timedelta(seconds=seconds)).isoformat()


class DeliveryLedgerTests(TestCase):
    def test_backoff_terminal_failure_and_new_id(self):
        ledger = DeliveryLedger()
        ledger.prepare("n1", "incident", "matter", "cooker", "notify_requested", [], at(0))
        self.assertTrue(ledger.begin("n1", at(0)))
        ledger.finish("n1", "failed", at(0))
        self.assertFalse(ledger.due("n1", at(29)))
        self.assertTrue(ledger.due("n1", at(30)))
        ledger.begin("n1", at(30))
        ledger.finish("n1", "failed", at(30))
        self.assertFalse(ledger.due("n1", at(149)))
        self.assertTrue(ledger.due("n1", at(150)))
        ledger.begin("n1", at(150))
        ledger.finish("n1", "failed", at(150))
        self.assertTrue(ledger.due("n1", at(450)))
        ledger.begin("n1", at(450))
        ledger.finish("n1", "failed", at(450))
        self.assertIsNone(ledger.get("n1")["next_retry_at"])
        self.assertFalse(ledger.due("n1", at(451)))
        self.assertEqual(ledger.prepare("n2", "incident", "matter", "cooker",
                                        "reminder_requested", [], at(451))["status"], "pending")

    def test_in_flight_restore_is_uncertain_and_never_due(self):
        ledger = DeliveryLedger()
        ledger.prepare("n1", "incident", "matter", "cooker", "notify_requested", [], at(0))
        ledger.begin("n1", at(0))
        restored = DeliveryLedger()
        restored.load_state(ledger.dump_state())
        self.assertEqual(restored.get("n1")["status"], "uncertain")
        self.assertFalse(restored.due("n1", at(60)))

    def test_worker_retries_only_after_due_and_does_not_replay_after_restart(self):
        with TemporaryDirectory() as directory:
            attempts = []

            def notify(update):
                attempts.append(update.notification_id)
                return "failed" if len(attempts) == 1 else "ha_accepted"

            state_file = Path(directory) / "state.json"
            service = AnalysisService("home", state_file, lambda *args: None, notify,
                                      quiet_start="00:00", quiet_end="00:01")
            key = ("matter", "cooker")
            service.availability[key] = True
            service.last_heartbeat[key] = START
            base = {"source": key[0], "device_id": key[1], "routine_status": "check_required",
                    "reason_codes": ["EXPECTED_USE_MISSING"]}
            service.gap.ingest({**base, "as_of": at(0)})
            service._handle(service.gap.ingest({**base, "as_of": at(1)}), service.gap, at(1))
            self.assertEqual(len(attempts), 1)
            self.assertEqual(service.delivery.dump_state()[0]["status"], "failed")
            restored = AnalysisService("home", state_file, lambda *args: None, notify,
                                       quiet_start="00:00", quiet_end="00:01")
            restored.availability[key] = True
            restored.last_heartbeat[key] = START + timedelta(seconds=31)
            restored.tick(at(30))
            self.assertEqual(len(attempts), 1)
            restored.tick(at(31))
            self.assertEqual(attempts, [attempts[0], attempts[0]])
            self.assertEqual(restored.delivery.dump_state()[0]["status"], "ha_accepted")
            restored.tick(at(32))
            self.assertEqual(len(attempts), 2)

    def test_worker_timeout_is_uncertain_and_not_retried(self):
        with TemporaryDirectory() as directory:
            attempts = []

            def timeout(update):
                attempts.append(update.notification_id)
                raise TimeoutError("response unknown")

            state_file = Path(directory) / "state.json"
            service = AnalysisService("home", state_file, lambda *args: None, timeout,
                                      quiet_start="00:00", quiet_end="00:01")
            key = ("matter", "cooker")
            service.availability[key] = True
            service.gap.ingest({"source": key[0], "device_id": key[1], "as_of": at(0),
                                "routine_status": "check_required", "reason_codes": []})
            service._handle(service.gap.ingest({"source": key[0], "device_id": key[1],
                                                "as_of": at(1), "routine_status": "check_required",
                                                "reason_codes": []}), service.gap, at(1))
            self.assertEqual(service.delivery.dump_state()[0]["status"], "uncertain")
            restored = AnalysisService("home", state_file, lambda *args: None, timeout,
                                       quiet_start="00:00", quiet_end="00:01")
            restored.availability[key] = True
            restored.last_heartbeat[key] = START + timedelta(minutes=5)
            restored.tick(at(5 * 60))
            self.assertEqual(len(attempts), 1)
