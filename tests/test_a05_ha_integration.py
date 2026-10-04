"""Exercise the A05 HA bridge with the actual raw-event listener."""

import asyncio
import importlib.util
import sys
from datetime import timedelta
from unittest import TestCase
from unittest.mock import patch

from test_a05_snapshot_input import report
from test_ha_service_integration import COMPONENT, FakeHass, ha_modules


class A05HAIntegrationTests(TestCase):
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
