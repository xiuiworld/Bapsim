"""The development path requires verified, fresh board changes and stays out of live state."""

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from service.a05_connection_test import A05ConnectionTest
from service.a05_snapshot_input import verified_report, verified_test_report
from tests.test_a05_snapshot_input import report
from tests.test_ha_service_integration import COMPONENT, FakeHass, ha_modules


class A05ConnectionTestTests(TestCase):
    def test_test_origin_keeps_full_provenance_checks_and_live_rejection(self):
        entry, state = report(origin="test")
        self.assertIsNotNone(verified_test_report(entry, state, "entry-1", 4))
        self.assertIsNone(verified_report(entry, state, "entry-1", 4))
        for change in (
            lambda: setattr(entry, "platform", "matter"),
            lambda: setattr(entry, "unique_id", "wrong"),
            lambda: state.attributes.update(attribute_path="wrong"),
            lambda: state.attributes.update(matter_entry_id="wrong"),
            lambda: state.attributes.update(node_id=5),
            lambda: state.attributes.update(read_method="wrong"),
            lambda: state.attributes.update(report_counter=3),
        ):
            entry, state = report(origin="test")
            change()
            self.assertIsNone(verified_test_report(entry, state, "entry-1", 4))

    def test_closed_then_open_requires_continuity_and_real_change(self):
        now = datetime.now(timezone.utc)
        run = A05ConnectionTest("household_a_test", "cooker_a_test",
                                "sensor.a05_reportcounter", "run-1", now)
        open_first = report(1, 1, True, "test")[1].attributes["raw_snapshot"]
        records, result = run.accept(open_first, now, now)
        self.assertIsNone(result)
        self.assertTrue(all(r["mode"] == "test" and r["test_run_id"] == "run-1"
                            for r in records))
        closed = report(2, 2, False, "test")[1].attributes["raw_snapshot"]
        _, result = run.accept(closed, now + timedelta(seconds=5), now)
        self.assertIsNone(result)
        self.assertEqual(run.phase, "closed_observed")
        gap_open = report(4, 4, True, "test")[1].attributes["raw_snapshot"]
        _, result = run.accept(gap_open, now + timedelta(seconds=10), now)
        self.assertIsNone(result)
        self.assertEqual(run.phase, "await_closed")
        closed = report(5, 5, False, "test")[1].attributes["raw_snapshot"]
        run.accept(closed, now + timedelta(seconds=15), now)
        opened = report(6, 6, True, "test")[1].attributes["raw_snapshot"]
        _, result = run.accept(opened, now + timedelta(seconds=20), now)
        self.assertEqual(result["closed_report_counter"], 5)
        self.assertEqual(result["opened_report_counter"], 6)
        self.assertEqual(result["test_run_id"], "run-1")
        self.assertNotEqual(result["closed_record_id"], result["opened_record_id"])
        self.assertEqual(run.accept(opened, now + timedelta(seconds=25), now), ([], None))

    def test_ha_callbacks_persist_board_result_and_call_only_chosen_phone_once(self):
        async def scenario():
            with TemporaryDirectory() as directory, patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_a05_test_integration", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.config = SimpleNamespace(path=lambda name: str(Path(directory) / name))
                phone_calls = []

                async def phone(call):
                    phone_calls.append(call.data)

                hass.services.async_register("notify", "mobile_app_test_phone", phone)
                _, initial = report(0, 0, True, "test")
                hass.states.async_set("sensor.a05_reportcounter", initial.state, initial.attributes)
                hass.registry_entries["sensor.a05_reportcounter"] = report()[0]
                self.assertTrue(await module.async_setup(hass, {"bapsim": {"household_id": "household_a_test"}}))
                await hass.services.async_call("bapsim", "register_a05_snapshot", {
                    "entity_id": "sensor.a05_reportcounter", "device_id": "cooker_a_test",
                    "matter_entry_id": "entry-1", "node_id": 4,
                })
                await hass.services.async_call("bapsim", "start_a05_connection_test", {
                    "entity_id": "sensor.a05_reportcounter", "device_id": "cooker_a_test",
                    "phone_service": "notify.mobile_app_test_phone",
                })
                run = hass.data["bapsim"]["a05_test_controller"].run
                self.assertEqual(run.phase, "await_closed")

                async def feed(counter, lid, origin="test"):
                    previous = hass.states.get("sensor.a05_reportcounter")
                    _, state = report(counter, counter, lid, origin)
                    hass.states.async_set("sensor.a05_reportcounter", state.state, state.attributes)
                    hass.bus.async_fire("state_changed", {
                        "entity_id": "sensor.a05_reportcounter", "old_state": previous,
                        "new_state": hass.states.get("sensor.a05_reportcounter"),
                    })
                    tasks, hass.tasks = hass.tasks, []
                    if tasks:
                        await asyncio.gather(*tasks)

                await feed(1, False, "unknown")
                self.assertEqual(run.phase, "await_closed")
                await feed(2, False)
                self.assertEqual(run.phase, "closed_observed")
                self.assertEqual(phone_calls, [])
                await feed(3, True)
                self.assertEqual(run.phase, "ha_accepted")
                self.assertEqual(len(phone_calls), 1)
                self.assertIn("개발용 연결 시험", phone_calls[0]["title"])
                self.assertEqual(phone_calls[0]["data"]["url"], "/bapsim-lab/a05-test")
                self.assertEqual(hass.states.get("sensor.bapsim_a05_connection_test").state,
                                 "ha_accepted")
                await feed(4, False)
                await feed(5, True)
                self.assertEqual(len(phone_calls), 1)
                self.assertFalse(any(data.get("device_id") == "cooker_a_test"
                                     for name, data in hass.bus.events if name == module.RAW_EVENT))
                self.assertFalse(any(item.get("device_id") == "cooker_a_test"
                                     for item in hass.data["bapsim"]["usage_model"].dump_state()["devices"]))
                files = list((Path(directory) / "bapsim_test_runs").glob("test-*.jsonl"))
                self.assertEqual(len(files), 1)
                saved = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
                self.assertTrue(all(item["mode"] == "test" and item["test_run_id"] == run.test_run_id
                                    for item in saved))
                result = next(item for item in saved if item["kind"] == "a05_connection_test")
                self.assertEqual(result["closed_report_counter"], 2)
                self.assertEqual(result["opened_report_counter"], 3)
                self.assertTrue(any(item.get("phase") == "mobile_attempt" for item in saved))
                await hass.services.async_call("bapsim", "stop_a05_connection_test", {})
                self.assertTrue(run.stopped)
                await hass.services.async_call("bapsim", "start_a05_connection_test", {
                    "entity_id": "sensor.a05_reportcounter", "device_id": "cooker_a_test",
                    "phone_service": "notify.mobile_app_test_phone",
                })
                second = hass.data["bapsim"]["a05_test_controller"].run
                hass.data["bapsim"]["a05_test_controller"]._expire(second, datetime.now(timezone.utc))
                await feed(6, False)
                await feed(7, True)
                self.assertEqual(second.phase, "expired")
                self.assertEqual(len(phone_calls), 1)
                await hass.services.async_call("bapsim", "start_a05_connection_test", {
                    "entity_id": "sensor.a05_reportcounter", "device_id": "cooker_a_test",
                    "phone_service": "notify.mobile_app_test_phone",
                })
                controller = hass.data["bapsim"]["a05_test_controller"]
                with patch.object(controller.journal, "write_test", new=AsyncMock(return_value=False)):
                    await feed(8, False)
                self.assertEqual(controller.run.phase, "storage_failed")
                self.assertEqual(len(phone_calls), 1)

        asyncio.run(scenario())
