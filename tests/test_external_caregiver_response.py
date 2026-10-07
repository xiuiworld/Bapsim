"""HA responses must reach the worker, its saved workflow, and its output."""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from service.ha_caregiver_commands import bind_caregiver_commands
from service.ha_dashboard import HADashboard
from service.standalone import AnalysisService
from workflow import DetectionStateManager, NotificationControls
from test_ha_service_integration import FakeHass


class ExternalCaregiverResponseTests(TestCase):
    def test_selected_response_changes_worker_output_and_survives_restart(self):
        async def scenario(two):
            with TemporaryDirectory() as directory:
                hass = FakeHass()
                dashboard = HADashboard(hass, None, {})
                messages = []

                def publish(topic, body, retain):
                    messages.append((topic, json.loads(body) if body.startswith("{") else body))
                    if topic == "bapsim/v1/home/state":
                        dashboard.show_output("matter", json.loads(body))

                worker = AnalysisService("home", Path(directory) / "state.json", publish,
                                         lambda update: self.fail("response must not send an alert"))
                worker.gap.load_state([{"source": "matter", "device_id": "cooker",
                                        "incident_id": "gap-case", "status": "notified"},
                                       {"source": "simulator", "device_id": "demo",
                                        "incident_id": "sim-case", "status": "notified"}])
                if two:
                    worker.decline.load_state([{"source": "matter", "device_id": "cooker",
                                                "incident_id": "trend-case", "status": "notified"}])
                selected_id = "trend-case" if two else "gap-case"
                manager = worker.decline if two else worker.gap
                worker.pending_quiet = {"selected": {"incident_id": selected_id},
                                        "simulation": {"incident_id": "sim-case"}}
                now = datetime.now(timezone.utc)
                state = worker.output.build(now.isoformat(), {}, "available",
                                            worker.gap.current_state("matter", "cooker"),
                                            worker.decline.current_state("matter", "cooker"))
                dashboard.show_output("matter", state)
                commands = []

                async def mqtt_publish(call):
                    command = json.loads(call.data["payload"])
                    commands.append(command)
                    result = worker.process_control(command, datetime.now(timezone.utc).isoformat())
                    self.assertEqual(result["status"], "accepted", result)

                hass.services.async_register("mqtt", "publish", mqtt_publish)
                handlers = bind_caregiver_commands(
                    lambda f: f, hass, DetectionStateManager(), DetectionStateManager(),
                    NotificationControls(), None, None, worker.output, True, dashboard.show,
                    None, None, None, None, logging.getLogger(__name__))
                if two:
                    with self.assertRaises(ValueError):
                        await handlers.respond(SimpleNamespace(data={"response": "checking"}))
                    self.assertFalse(commands)
                    hass.states.async_set("input_select.bapsim_active_assessment",
                                          "선택한 사건 | " + selected_id, {})
                for response in ("checking", "resolved"):
                    await handlers.respond(SimpleNamespace(data={"response": response}))
                    self.assertEqual(manager.current_state("matter", "cooker")["status"], response)
                    self.assertEqual(commands[-1]["action"], "caregiver_response")
                    self.assertEqual(commands[-1]["assessment_id"], selected_id)
                    output = hass.states.get("sensor.bapsim_output_state").attributes
                    assessment = next(a for a in output["assessments"] if a["assessment_id"] == selected_id)
                    self.assertEqual(assessment["caregiver_status"], response)
                    restored = AnalysisService("home", worker.state_file, lambda *args: None, lambda u: True)
                    restored_manager = restored.decline if two else restored.gap
                    self.assertEqual(restored_manager.current_state("matter", "cooker")["status"], response)
                    self.assertFalse(restored.controls.acknowledgements)
                    self.assertEqual(restored.process_control(commands[-1], datetime.now(timezone.utc).isoformat())
                                     ["status"], "accepted")
                self.assertEqual(worker.gap.current_state("simulator", "demo")["status"], "notified")
                self.assertNotIn("selected", worker.pending_quiet)
                self.assertIn("simulation", worker.pending_quiet)
                if two:
                    self.assertEqual(worker.gap.current_state("matter", "cooker")["status"], "notified")
                self.assertEqual(worker.process_control(commands[-1], datetime.now(timezone.utc).isoformat())
                                 ["status"], "accepted")  # Retry does not reopen a resolved incident.

                before = len(commands)
                with self.assertRaises(ValueError):
                    await handlers.respond(SimpleNamespace(data={"response": "resolved", "incident_id": "wrong"}))
                expired = {**state, "valid_until": (now - timedelta(seconds=1)).isoformat()}
                hass.states.async_set("sensor.bapsim_output_state", "old", {**expired, "source": "matter"})
                with self.assertRaises(ValueError):
                    await handlers.respond(SimpleNamespace(data={"response": "checking"}))
                self.assertEqual(len(commands), before)
                with self.assertRaises(ValueError):
                    await handlers.respond(SimpleNamespace(data={"response": "resolved", "source": "simulator"}))

        asyncio.run(scenario(False))
        asyncio.run(scenario(True))

    def test_worker_rejects_expired_unknown_and_simulation_response(self):
        with TemporaryDirectory() as directory:
            worker = AnalysisService("home", Path(directory) / "state.json", lambda *a: None, lambda u: True)
            worker.gap.load_state([{"source": "matter", "device_id": "cooker",
                                    "incident_id": "real", "status": "notified"},
                                   {"source": "simulator", "device_id": "demo",
                                    "incident_id": "sim", "status": "notified"}])
            now = datetime.now(timezone.utc)
            base = {"schema_version": "0.1.0", "household_id": "home", "created_at": now.isoformat(),
                    "action": "caregiver_response", "response": "resolved",
                    "result_valid_until": (now + timedelta(seconds=180)).isoformat()}
            for command_id, incident_id, expires in (
                    ("expired", "real", (now - timedelta(seconds=1)).isoformat()),
                    ("unknown", "missing", base["result_valid_until"]),
                    ("simulation", "sim", base["result_valid_until"])):
                result = worker.process_control({**base, "command_id": command_id,
                                                 "assessment_id": incident_id,
                                                 "result_valid_until": expires}, now.isoformat())
                self.assertEqual(result["status"], "rejected")
            self.assertEqual(worker.gap.current_state("matter", "cooker")["status"], "notified")
            self.assertEqual(worker.gap.current_state("simulator", "demo")["status"], "notified")

    def test_worker_response_storage_failure_rolls_back_and_does_not_publish_state(self):
        with TemporaryDirectory() as directory:
            published = []
            worker = AnalysisService("home", Path(directory) / "state.json",
                                     lambda *args: published.append(args), lambda u: True)
            worker.gap.load_state([{"source": "matter", "device_id": "cooker",
                                    "incident_id": "real", "status": "notified"}])
            worker.save()
            now = datetime.now(timezone.utc)
            command = {"command_id": "failure", "schema_version": "0.1.0", "household_id": "home",
                       "created_at": now.isoformat(), "action": "caregiver_response", "response": "resolved",
                       "assessment_id": "real", "result_valid_until": (now + timedelta(seconds=180)).isoformat()}
            with patch.object(worker, "save", side_effect=OSError("test storage failure")):
                result = worker.process_control(command, now.isoformat())
            self.assertEqual(result["status"], "rejected")
            self.assertIn("storage_failed", result["reason"])
            self.assertEqual(worker.gap.current_state("matter", "cooker")["status"], "notified")
            self.assertFalse(worker.controls.commands)
            self.assertFalse(any(topic.endswith("/state") for topic, body, retained in published))
