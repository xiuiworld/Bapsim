"""Check that both supported hosts release the same persisted caregiver request."""

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from service.standalone import AnalysisService
from test_ha_service_integration import COMPONENT, FakeHass, ha_modules
from workflow import DeliveryLedger


class NotificationPathParityTests(TestCase):
    def test_quiet_release_matches_across_hosts(self):
        async def scenario(case: str):
            now = datetime.now(timezone.utc)
            at = now.isoformat()
            ends_at = (now + timedelta(hours=2)).isoformat()
            local = now.astimezone(timezone(timedelta(hours=9)))
            quiet = [(local + timedelta(minutes=2)).strftime("%H:%M"),
                     (local + timedelta(minutes=3)).strftime("%H:%M")]
            active = case != "resolved"
            workflow_state = {"source": "simulator", "device_id": "cooker_01",
                              "status": "check_required" if active else "resolved",
                              "incident_id": "incident-1", "reason_codes": ["EXPECTED_USE_MISSING"]}
            pending = {"source": "simulator", "device_id": "cooker_01",
                       "incident_id": "incident-1", "notification_id": "incident-1",
                       "action": "notify_requested"}
            controls = {}
            if case == "acknowledged":
                controls["acknowledgements"] = {"incident-1": ends_at}
            if case == "paused":
                controls["pauses"] = {"trip": {"starts_at": at, "ends_at": ends_at}}
            seed = {"workflow": [workflow_state], "pending_quiet": [pending],
                    "quiet_hours": quiet, "notification_controls": controls,
                    "availability": [{"source": "simulator", "device_id": "cooker_01",
                                      "availability": "online"}]}
            with TemporaryDirectory() as directory, patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location(f"bapsim_parity_{case}", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                self.assertTrue(await module.async_setup(hass, {}))

                sent = []
                worker = AnalysisService("household_01", Path(directory) / "state.json",
                                         lambda *args: None, lambda update: sent.append(update) or True,
                                         quiet_start=quiet[0], quiet_end=quiet[1])
                worker.gap.load_state([workflow_state])
                worker.controls.load_state(controls)
                worker.pending_quiet[pending["notification_id"]] = dict(pending)
                key = ("simulator", "cooker_01")
                worker.availability[key] = True
                worker.last_heartbeat[key] = now
                worker.evaluate = lambda *args: None  # Compare delivery, independent of model training.

                hass.hourly_callback(now)
                await asyncio.gather(*hass.tasks)
                worker.tick(at)

                ha_sent = [call for call in hass.services.calls
                           if call[:2] == ("persistent_notification", "create")]
                self.assertEqual(len(ha_sent), len(sent), case)
                self.assertEqual(len(sent), 1 if case == "active" else 0, case)
                ha_state = hass.data["bapsim"]["workflow"].current_state(*key)
                worker_state = worker.gap.current_state(*key)
                for field in ("status", "notification_count", "pending_action"):
                    self.assertEqual(ha_state[field], worker_state[field], (case, field))
                self.assertEqual(sorted(record["status"] for record in
                                        hass.data["bapsim"]["delivery"].records.values()),
                                 sorted(record["status"] for record in worker.delivery.records.values()))
                self.assertEqual(hass.data["bapsim"]["store"].saved["pending_quiet"], [])
                self.assertEqual(worker.pending_quiet, {})

        for case in ("active", "acknowledged", "paused", "resolved"):
            with self.subTest(case=case):
                asyncio.run(scenario(case))

    def test_failed_delivery_retries_once_across_hosts(self):
        async def scenario():
            now = datetime.now(timezone.utc)
            at = now.isoformat()
            past = (now - timedelta(minutes=1)).isoformat()
            local = now.astimezone(timezone(timedelta(hours=9)))
            quiet = [(local + timedelta(minutes=2)).strftime("%H:%M"),
                     (local + timedelta(minutes=3)).strftime("%H:%M")]
            state = {"source": "matter", "device_id": "cooker_01",
                     "status": "check_required", "pending_action": "notify_requested",
                     "incident_id": "incident-1", "reason_codes": ["EXPECTED_USE_MISSING"]}
            ledger = DeliveryLedger()
            ledger.prepare("notice-1", "incident-1", "matter", "cooker_01",
                           "notify_requested", state["reason_codes"], past)
            self.assertTrue(ledger.begin("notice-1", past))
            ledger.finish("notice-1", "failed", past)
            seed = {"workflow": [state], "delivery": ledger.dump_state(),
                    "pending_mobile": [{"source": "matter", "device_id": "cooker_01",
                                        "incident_id": "incident-1", "notification_id": "notice-1",
                                        "action": "notify_requested", "stage": "mobile",
                                        "reason_codes": state["reason_codes"],
                                        "title": "밥심", "message": "확인 필요"}],
                    "availability": [{"source": "matter", "device_id": "cooker_01",
                                      "availability": "online"}],
                    "notification_target": "mobile_app_caregiver", "quiet_hours": quiet}
            with TemporaryDirectory() as directory, patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_retry_parity", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                ha_sent = []

                async def mobile(call):
                    ha_sent.append(call.data)

                hass.services.async_register("notify", "mobile_app_caregiver", mobile)
                self.assertTrue(await module.async_setup(hass, {}))
                await asyncio.gather(*hass.tasks)

                sent = []
                worker = AnalysisService("household_01", Path(directory) / "state.json",
                                         lambda *args: None, lambda update: sent.append(update) or True,
                                         quiet_start=quiet[0], quiet_end=quiet[1])
                worker.gap.load_state([state])
                worker.delivery.load_state(ledger.dump_state())
                key = ("matter", "cooker_01")
                worker.availability[key] = True
                worker.last_heartbeat[key] = now
                worker.evaluate = lambda *args: None
                worker.tick(at)

                self.assertEqual((len(ha_sent), len(sent)), (1, 1))
                self.assertEqual(hass.data["bapsim"]["delivery"].get("notice-1")["status"],
                                 worker.delivery.get("notice-1")["status"])
                self.assertEqual(hass.data["bapsim"]["delivery"].get("notice-1")["attempts"],
                                 worker.delivery.get("notice-1")["attempts"])
                self.assertEqual(hass.data["bapsim"]["workflow"].current_state(*key)["notification_count"],
                                 worker.gap.current_state(*key)["notification_count"])

        asyncio.run(scenario())
