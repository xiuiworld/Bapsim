"""Exercise the A05 HA bridge with the actual raw-event listener."""

import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from domain.personal_model import usage_group_id
from test_a05_snapshot_input import report
from test_ha_service_integration import COMPONENT, FakeHass, ha_modules


class A05HAIntegrationTests(TestCase):
    def test_buffered_live_phase_context_survives_one_ha_poll(self):
        async def scenario(root):
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_a05_buffered_context", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.async_track_point_in_utc_time = (
                    lambda hass, callback, deadline: setattr(hass, "deadline_callback", callback) or (lambda: None)
                )
                hass = FakeHass()
                hass.config = SimpleNamespace(path=lambda name: str(Path(root) / name))
                entity_id = "sensor.a05_reportcounter"
                entry, initial = report(0, 0, False, "test")
                hass.states.async_set(entity_id, initial.state, initial.attributes)
                hass.registry_entries[entity_id] = entry
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_a05_snapshot", {
                    "device_id": "cooker", "entity_id": entity_id,
                    "matter_entry_id": "entry-1", "node_id": 4,
                })

                def buffered_state(numbers):
                    rows = []
                    for number in numbers:
                        _, state = report(number, number, number == 3, "live")
                        raw = state.attributes["raw_snapshot"]
                        raw.update(UptimeMs=number * 5000,
                                   CookerPhase={1: 1, 2: 2, 3: 3}[number],
                                   InferenceMethod=1, ModelVersion="a07-v1",
                                   InferenceScore=9000, TemperatureCentiC=6500)
                        raw["SignalQuality"]["CookerPhase"] = 1
                        rows.append(raw)
                    state.attributes.update(read_method="buffered_read_5s",
                                            history_path="1/4294048769/4293984261",
                                            raw_history=rows)
                    return state

                def change(numbers):
                    old = hass.states.get(entity_id)
                    state = buffered_state(numbers)
                    hass.states.async_set(entity_id, state.state, state.attributes)
                    hass.bus.async_fire("state_changed", {
                        "entity_id": entity_id, "old_state": old,
                        "new_state": hass.states.get(entity_id),
                    })

                class Clock:
                    now_at = datetime(2026, 10, 5, tzinfo=timezone.utc)

                    def now(self, tz):
                        return self.now_at.astimezone(tz)

                clock = Clock()
                with patch.object(module, "datetime", clock):
                    change([1])  # establish the verified live baseline
                    clock.now_at += timedelta(seconds=10)
                    change([1, 2, 3])  # two board reports arrive in one HA poll
                self.assertIsNotNone(hass.data["bapsim"]["normalizer"].next_deadline)
                hass.deadline_callback(hass.data["bapsim"]["normalizer"].next_deadline)
                context = hass.states.get("sensor.bapsim_usage_context")
                self.assertEqual(context.state, "warming_possible_use")
                self.assertEqual(context.attributes["report_counter"], 3)
                self.assertEqual(context.attributes["model_version"], "a07-v1")
                episodes = hass.data["bapsim"]["usage_model"]._device("matter", "cooker")["raw_opens"]
                self.assertEqual(len(episodes), 1)
                if hass.tasks:
                    await asyncio.gather(*hass.tasks)

        with TemporaryDirectory() as root:
            asyncio.run(scenario(root))

    def test_verified_live_phase_explains_opening_without_learning_from_test(self):
        async def scenario(root):
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_a05_context_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.async_track_point_in_utc_time = (
                    lambda hass, callback, deadline: setattr(hass, "deadline_callback", callback) or (lambda: None)
                )
                hass = FakeHass()
                hass.config = SimpleNamespace(path=lambda name: str(Path(root) / name))
                entity_id = "sensor.a05_reportcounter"
                _, initial = report(0, 0, False, "test")
                hass.states.async_set(entity_id, initial.state, initial.attributes)
                hass.registry_entries[entity_id] = report()[0]
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_a05_snapshot", {
                    "device_id": "cooker", "entity_id": entity_id,
                    "matter_entry_id": "entry-1", "node_id": 4,
                })

                def feed(counter, lid, phase, origin="live"):
                    old = hass.states.get(entity_id)
                    _, state = report(counter, counter, lid, origin)
                    raw = state.attributes["raw_snapshot"]
                    raw.update(CookerPhase=phase, InferenceMethod=1,
                               ModelVersion="a07-v1", InferenceScore=9000,
                               TemperatureCentiC=6500)
                    raw["SignalQuality"]["CookerPhase"] = 1
                    hass.states.async_set(entity_id, state.state, state.attributes)
                    hass.bus.async_fire("state_changed", {
                        "entity_id": entity_id, "old_state": old,
                        "new_state": hass.states.get(entity_id),
                    })

                feed(1, False, 2)  # baseline cooking estimate
                feed(2, True, 3)   # verified warming estimate and real lid transition
                self.assertIsNotNone(hass.data["bapsim"]["normalizer"].next_deadline)
                hass.deadline_callback(hass.data["bapsim"]["normalizer"].next_deadline)
                context = hass.states.get("sensor.bapsim_usage_context")
                self.assertEqual(context.state, "warming_possible_use")
                self.assertEqual(context.attributes["temperature_c"], 65.0)
                self.assertEqual(context.attributes["model_version"], "a07-v1")
                self.assertFalse(context.attributes["meal_confirmed"])
                self.assertEqual(len(hass.data["bapsim"]["usage_model"]._device(
                    "matter", "cooker")["raw_opens"]), 1)
                saved = hass.data["bapsim"]["store"].saved
                self.assertEqual(saved["latest_usage_context"][0]["classification"],
                                 "warming_possible_use")

                feed(3, False, 2, "test")
                self.assertEqual(hass.states.get("sensor.bapsim_usage_context").state,
                                 "warming_possible_use")
                self.assertEqual(len(hass.data["bapsim"]["usage_model"]._device(
                    "matter", "cooker")["raw_opens"]), 1)
                if hass.tasks:
                    await asyncio.gather(*hass.tasks)
                files = list((Path(root) / "bapsim_records").glob("records-*.jsonl"))
                rows = [json.loads(line) for path in files
                        for line in path.read_text(encoding="utf-8").splitlines()]
                episode = next(row for row in rows if row["category"] == "usage_episode")
                group = next(row for row in rows if row["category"] == "usage_group")
                self.assertEqual(episode["classification"], "warming_possible_use")
                self.assertEqual(episode["phase_quality"], "good")
                self.assertTrue(episode["lid_record_id"])
                self.assertEqual(group["record_id"], usage_group_id(
                    "matter", "cooker", group["group_started_at"]))
                self.assertEqual(group["board_usage_context"]["lid_record_id"],
                                 episode["lid_record_id"])

        with TemporaryDirectory() as root:
            asyncio.run(scenario(root))

    def test_ha_callback_archives_only_verified_live_contract_records(self):
        async def scenario(root):
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_a05_contract_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.config = SimpleNamespace(path=lambda name: str(Path(root) / name))
                entity_id = "sensor.a05_reportcounter"
                _, initial = report(0, 0, False, "test")
                hass.states.async_set(entity_id, initial.state, initial.attributes)
                hass.registry_entries[entity_id] = report()[0]
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_a05_snapshot", {
                    "device_id": "cooker", "entity_id": entity_id,
                    "matter_entry_id": "entry-1", "node_id": 4,
                })

                def change(counter, sequence, lid, origin, invalid=False):
                    old = hass.states.get(entity_id)
                    _, new = report(counter, sequence, lid, origin)
                    if invalid:
                        new.attributes["raw_snapshot"]["WarmLed"] = 99
                    hass.states.async_set(entity_id, new.state, new.attributes)
                    hass.bus.async_fire("state_changed", {
                        "entity_id": entity_id, "old_state": old,
                        "new_state": hass.states.get(entity_id),
                    })

                change(1, 1, False, "test")
                change(2, 2, False, "live")
                change(3, 3, True, "live")
                change(4, 4, False, "live", invalid=True)
                if hass.tasks:
                    await asyncio.gather(*hass.tasks)
                files = list((Path(root) / "bapsim_contract_observations").glob("contract-*.jsonl"))
                self.assertEqual(len(files), 1)
                rows = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
                self.assertEqual(len(rows), 23)  # initial 8, next 7, offline 8
                self.assertEqual([row["value"] for row in rows if row["signal"] == "lid_open"],
                                 [False, True, None])
                self.assertEqual([row["value"] for row in rows if row["signal"] == "device_connection"],
                                 ["online", "offline"])
                self.assertTrue(all(row["source_ref"] == entity_id and row["mode"] == "live"
                                    for row in rows))
        with TemporaryDirectory() as root:
            asyncio.run(scenario(root))

    def test_expired_report_closes_coverage_at_last_report(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_a05_expiry_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                entity_id = "sensor.a05_reportcounter"
                _, initial = report(0, 0, False, "test")
                hass.states.async_set(entity_id, initial.state, initial.attributes)
                hass.registry_entries[entity_id] = report()[0]
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_a05_snapshot", {
                    "device_id": "cooker", "entity_id": entity_id,
                    "matter_entry_id": "entry-1", "node_id": 4,
                })
                old = hass.states.get(entity_id)
                _, current = report(1, 1, False, "live")
                hass.states.async_set(entity_id, current.state, current.attributes)
                hass.bus.async_fire("state_changed", {
                    "entity_id": entity_id, "old_state": old,
                    "new_state": hass.states.get(entity_id),
                })
                model = hass.data["bapsim"]["usage_model"]
                state = model._device("matter", "cooker")
                last_report = state["online_since"]
                self.assertIsNotNone(last_report)
                hass.hourly_callback(last_report + timedelta(minutes=4))
                self.assertIsNone(state["online_since"])
                self.assertEqual(state["coverage"][-1][1],
                                 last_report + timedelta(microseconds=1))
                if hass.tasks:
                    await asyncio.gather(*hass.tasks)

        asyncio.run(scenario())

    def test_test_origin_is_excluded_and_live_sequence_is_forwarded(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_a05_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                entity_id = "sensor.a05_reportcounter"
                _, state = report(0, 0, False, "test")
                hass.states.async_set(entity_id, state.state, state.attributes)
                hass.registry_entries[entity_id] = report()[0]
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_a05_snapshot", {
                    "device_id": "cooker", "entity_id": entity_id,
                    "matter_entry_id": "entry-1", "node_id": 4,
                })

                def change(counter, sequence, lid, origin):
                    old = hass.states.get(entity_id)
                    _, new = report(counter, sequence, lid, origin)
                    hass.states.async_set(entity_id, new.state, new.attributes)
                    hass.bus.async_fire("state_changed", {
                        "entity_id": entity_id, "old_state": old,
                        "new_state": hass.states.get(entity_id),
                    })

                class ReportClock:
                    tick = 0

                    def now(self, tz):
                        # A05 reads arrive on a five-second poll. A controlled
                        # clock keeps the strict last >= now rejection meaningful.
                        at = datetime(2026, 10, 5, tzinfo=timezone.utc)
                        self.tick += 1
                        return (at + timedelta(seconds=5 * self.tick)).astimezone(tz)

                with patch.object(module, "datetime", ReportClock()):
                    change(1, 1, False, "test")
                    self.assertFalse([event for name, event in hass.bus.events
                                      if name == module.RAW_EVENT and event["device_id"] == "cooker"])
                    change(2, 2, False, "live")
                    change(3, 3, True, "live")
                    events = [(event["event_type"], event["value"])
                              for name, event in hass.bus.events
                              if name == module.RAW_EVENT and event["device_id"] == "cooker"]
                    self.assertEqual(events, [("availability", "online"),
                                              ("lid_state", "closed"),
                                              ("lid_state", "open")])
                    change(5, 5, False, "live")
                    events = [(event["event_type"], event["value"])
                              for name, event in hass.bus.events
                              if name == module.RAW_EVENT and event["device_id"] == "cooker"]
                    self.assertEqual(events[-3:], [("availability", "offline"),
                                                   ("availability", "online"),
                                                   ("lid_state", "closed")])
                    gap_offline = [event for name, event in hass.bus.events
                                   if name == module.RAW_EVENT and event["device_id"] == "cooker"
                                   and event["event_type"] == "availability"
                                   and event["value"] == "offline"][-1]
                    model_state = hass.data["bapsim"]["usage_model"]._device("matter", "cooker")
                    self.assertEqual(model_state["coverage"][-1][1].isoformat(),
                                     gap_offline["observed_at"])
                    change(6, 6, True, "test")
                    events = [(event["event_type"], event["value"])
                              for name, event in hass.bus.events
                              if name == module.RAW_EVENT and event["device_id"] == "cooker"]
                    self.assertEqual(events[-1], ("availability", "offline"))
                if hass.tasks:
                    await asyncio.gather(*hass.tasks)

        asyncio.run(scenario())
