"""Exercise the HA bridge callbacks and local notification without a HA server."""

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import types
from unittest import TestCase
from unittest.mock import patch

from domain.baseline import PersonalBaselineEngine
from domain.events.normalizer import CookerEventNormalizer
from workflow import DeliveryLedger


COMPONENT = Path(__file__).resolve().parents[1] / "infrastructure/home_assistant/config/custom_components/bapsim/__init__.py"


class FakeBus:
    def __init__(self):
        self.listeners = {}
        self.events = []

    def async_listen(self, name, callback):
        self.listeners.setdefault(name, []).append(callback)
        return lambda: self.listeners[name].remove(callback)

    def async_listen_once(self, name, callback):
        return self.async_listen(name, callback)

    def async_fire(self, name, data):
        self.events.append((name, data))
        for callback in tuple(self.listeners.get(name, [])):
            callback(types.SimpleNamespace(data=data))


class FakeServices:
    def __init__(self):
        self.handlers = {}
        self.calls = []

    def async_register(self, domain, name, handler):
        self.handlers[(domain, name)] = handler

    def async_remove(self, domain, name):
        self.handlers.pop((domain, name), None)

    def has_service(self, domain, name):
        return (domain, name) in self.handlers

    async def async_call(self, domain, name, data, blocking=False):
        self.calls.append((domain, name, data))
        handler = self.handlers.get((domain, name))
        if handler is not None:
            await handler(types.SimpleNamespace(data=data))


class FakeStates:
    def __init__(self, registry_entries):
        self.values = {}
        self.registry_entries = registry_entries

    def async_set(self, entity_id, state, attributes):
        if not entity_id.startswith("sensor.bapsim_"):
            self.registry_entries.setdefault(entity_id, types.SimpleNamespace(platform="matter"))
        self.values[entity_id] = types.SimpleNamespace(
            state=state, attributes=attributes, last_updated=datetime.now(timezone.utc),
        )

    def get(self, entity_id):
        return self.values.get(entity_id)


class FakeHass:
    def __init__(self):
        self.registry_entries = {}
        self.bus = FakeBus()
        self.services = FakeServices()
        self.states = FakeStates(self.registry_entries)
        self.data = {}
        self.tasks = []

    def async_create_task(self, coroutine):
        self.tasks.append(coroutine)

    async def async_add_executor_job(self, function, *args):
        return await asyncio.to_thread(function, *args)


def ha_modules(seed):
    root = types.ModuleType("homeassistant")
    root.__path__ = []
    core = types.ModuleType("homeassistant.core")
    core.EVENT_HOMEASSISTANT_STOP = "homeassistant_stop"
    core.EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"
    core.Event = object
    core.HomeAssistant = object
    core.callback = lambda function: function
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    events = types.ModuleType("homeassistant.helpers.event")
    events.async_track_point_in_utc_time = lambda *args: lambda: None
    def track_interval(hass, callback, interval):
        hass.hourly_callback = callback
        return lambda: None

    events.async_track_time_interval = track_interval
    storage = types.ModuleType("homeassistant.helpers.storage")

    class Store:
        def __init__(self, *args, **kwargs):
            self.saved = None

        async def async_load(self):
            return seed

        def async_delay_save(self, snapshot, delay):
            self.saved = snapshot()

        async def async_save(self, snapshot):
            self.saved = snapshot

    storage.Store = Store
    registry = types.ModuleType("homeassistant.helpers.entity_registry")
    registry.EVENT_ENTITY_REGISTRY_UPDATED = "entity_registry_updated"
    registry.async_get = lambda hass: types.SimpleNamespace(async_get=hass.registry_entries.get)
    return {
        "homeassistant": root, "homeassistant.core": core,
        "homeassistant.helpers": helpers, "homeassistant.helpers.event": events,
        "homeassistant.helpers.storage": storage,
        "homeassistant.helpers.entity_registry": registry,
    }


class HAServiceIntegrationTests(TestCase):
    def test_failed_mobile_check_in_does_not_create_resumption_alert(self):
        now = datetime.now(timezone.utc)
        local = now.astimezone(timezone(timedelta(hours=9)))
        quiet_start = (local + timedelta(minutes=2)).strftime("%H:%M")
        quiet_end = (local + timedelta(minutes=3)).strftime("%H:%M")
        seed = {
            "workflow": [{"source": "matter", "device_id": "cooker_01", "status": "check_required",
                          "incident_id": "incident-1", "reason_codes": ["EXPECTED_USE_MISSING"]}],
            "pending_quiet": [{"source": "matter", "device_id": "cooker_01", "incident_id": "incident-1"}],
            "availability": [{"source": "matter", "device_id": "cooker_01", "availability": "online"}],
            "notification_target": "mobile_app_caregiver",
            "quiet_hours": [quiet_start, quiet_end],
        }

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_failed_mobile_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()

                async def fail_mobile(call):
                    raise ConnectionError("offline")

                hass.services.async_register("notify", "mobile_app_caregiver", fail_mobile)
                self.assertTrue(await module.async_setup(hass, {}))
                hass.hourly_callback(now)
                await asyncio.gather(*hass.tasks)
                self.assertEqual(hass.data["bapsim"]["workflow"].dump_state()[0]["notification_count"], 0)
                self.assertEqual(len(hass.data["bapsim"]["store"].saved["pending_mobile"]), 1)
                record = hass.data["bapsim"]["delivery"].get("incident-1")
                self.assertEqual(record["status"], "failed")
                self.assertEqual(record["attempts"], 1)
                self.assertIsNotNone(record["next_retry_at"])
                hass.bus.async_fire(module.COOKER_EVENT, {
                    "source": "matter", "device_id": "cooker_01", "event_type": "lid_opened",
                    "value": "open", "observed_at": now.isoformat(), "received_at": now.isoformat(),
                    "availability": "online", "quality": "observed",
                })
                await asyncio.gather(*hass.tasks[1:])
                notifications = [body for domain, name, body in hass.services.calls
                                 if (domain, name) == ("persistent_notification", "create")]
                self.assertEqual(len(notifications), 1)
                self.assertEqual(hass.data["bapsim"]["store"].saved["pending_mobile"], [])

        asyncio.run(scenario())

    def test_restart_during_notification_call_is_uncertain_and_not_retried(self):
        now = datetime.now(timezone.utc).isoformat()
        ledger = DeliveryLedger()
        ledger.prepare("notice-1", "incident-1", "matter", "cooker_01",
                       "notify_requested", ["EXPECTED_USE_MISSING"], now)
        ledger.begin("notice-1", now)
        seed = {
            "workflow": [{"source": "matter", "device_id": "cooker_01", "status": "check_required",
                          "incident_id": "incident-1", "reason_codes": ["EXPECTED_USE_MISSING"]}],
            "delivery": ledger.dump_state(),
            "pending_mobile": [{"source": "matter", "device_id": "cooker_01",
                                "incident_id": "incident-1", "notification_id": "notice-1",
                                "reason_codes": ["EXPECTED_USE_MISSING"], "message": "확인 필요",
                                "action": "notify_requested"}],
            "notification_target": "mobile_app_caregiver",
        }

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_uncertain_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()

                async def mobile(call):
                    self.fail("uncertain send must not replay")

                hass.services.async_register("notify", "mobile_app_caregiver", mobile)
                self.assertTrue(await module.async_setup(hass, {}))
                await asyncio.gather(*hass.tasks)
                self.assertEqual(hass.data["bapsim"]["delivery"].get("notice-1")["status"], "uncertain")
                self.assertEqual(hass.data["bapsim"]["store"].saved["pending_mobile"], [])

        asyncio.run(scenario())

    def test_sent_incident_resumption_creates_distinct_local_notification(self):
        now = datetime.now(timezone.utc).isoformat()
        seed = {
            "workflow": [{"source": "simulator", "device_id": "cooker_01",
                          "status": "notified", "incident_id": "incident-1",
                          "last_notified_at": now, "notification_count": 1,
                          "reason_codes": ["EXPECTED_USE_MISSING"]}],
            "quiet_hours": ["00:00", "00:01"],
        }

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_resumption_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                self.assertTrue(await module.async_setup(hass, {}))
                hass.bus.async_fire(module.COOKER_EVENT, {
                    "source": "simulator", "device_id": "cooker_01", "event_type": "lid_opened",
                    "value": "open", "observed_at": now, "received_at": now,
                    "availability": "online", "quality": "simulated",
                })
                await asyncio.gather(*hass.tasks)
                notifications = [body for domain, name, body in hass.services.calls
                                 if (domain, name) == ("persistent_notification", "create")]
                self.assertEqual(len(notifications), 1)
                self.assertIn("다시 사용", notifications[0]["message"])
                self.assertIn(":resumed", notifications[0]["notification_id"])

        asyncio.run(scenario())

    def test_external_worker_mode_forwards_observations_and_controls(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})), patch.dict(os.environ, {"BAPSIM_EXTERNAL_SERVICE": "1"}):
                spec = importlib.util.spec_from_file_location("bapsim_ha_external_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                async def publish(call):
                    pass
                hass.services.async_register("mqtt", "publish", publish)
                self.assertTrue(await module.async_setup(hass, {}))
                hass.bus.async_fire(module.COOKER_EVENT, {
                    "source": "simulator", "device_id": "cooker_01",
                    "event_type": "lid_opened", "observed_at": datetime.now(timezone.utc).isoformat(),
                    "availability": "online",
                })
                await hass.services.async_call("bapsim", "set_notification_control", {
                    "command_id": "pause-test", "action": "pause_lifestyle", "pause_id": "trip-test",
                    "starts_at": datetime.now(timezone.utc).isoformat(),
                    "ends_at": (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
                })
                await asyncio.gather(*hass.tasks)
                sent = [data for domain, name, data in hass.services.calls
                        if (domain, name) == ("mqtt", "publish")]
                self.assertTrue(any(item["topic"].endswith("/observations") and not item["retain"] for item in sent))
                self.assertTrue(any(item["topic"].endswith("/control") and not item["retain"] for item in sent))
                self.assertFalse(hass.data["bapsim"]["usage_model"].device_keys)
                self.assertEqual(hass.data["bapsim"]["store"].saved["forwarded_devices"], [
                    {"source": "simulator", "device_id": "cooker_01"},
                ])

        asyncio.run(scenario())

    def test_external_worker_restores_forwarded_device_heartbeat(self):
        async def scenario():
            seed = {"forwarded_devices": [{"source": "simulator", "device_id": "cooker_01"}]}
            with patch.dict(sys.modules, ha_modules(seed)), patch.dict(os.environ, {"BAPSIM_EXTERNAL_SERVICE": "1"}):
                spec = importlib.util.spec_from_file_location("bapsim_ha_restored_forwarding_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("input_select.bapsim_availability", "online", {})
                async def publish(call):
                    pass
                hass.services.async_register("mqtt", "publish", publish)
                self.assertTrue(await module.async_setup(hass, {}))
                hass.hourly_callback(datetime.now(timezone.utc))
                await asyncio.gather(*hass.tasks)
                sent = [data for domain, name, data in hass.services.calls
                        if (domain, name) == ("mqtt", "publish")]
                self.assertTrue(any(
                    item["topic"] == "bapsim/simulation/v1/household_01/observations"
                    and '"event_type": "device_online"' in item["payload"]
                    for item in sent
                ))

        asyncio.run(scenario())

    def test_bounded_pause_service_is_persisted_and_can_be_cancelled(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_controls_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                self.assertTrue(await module.async_setup(hass, {}))
                now = datetime.now(timezone.utc)
                await hass.services.async_call("bapsim", "set_notification_control", {
                    "command_id": "pause-1", "action": "pause_lifestyle", "pause_id": "trip-1",
                    "starts_at": (now - timedelta(minutes=1)).isoformat(),
                    "ends_at": (now + timedelta(hours=2)).isoformat(),
                })
                self.assertTrue(hass.data["bapsim"]["notification_controls"].is_paused(now.isoformat()))
                self.assertEqual(hass.data["bapsim"]["store"].saved["notification_controls"]["revision"], 1)
                await hass.services.async_call("bapsim", "set_notification_control", {
                    "command_id": "pause-2", "action": "cancel_pause", "pause_id": "trip-1",
                })
                self.assertFalse(hass.data["bapsim"]["notification_controls"].is_paused(now.isoformat()))

        asyncio.run(scenario())

    def test_acknowledge_requires_active_assessment_and_records_expiry(self):
        seed = {"workflow": [{"source": "matter", "device_id": "cooker_01", "status": "notified",
                              "incident_id": "case-1", "reason_codes": ["EXPECTED_USE_MISSING"]}]}

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_ack_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                self.assertTrue(await module.async_setup(hass, {}))
                ends_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
                await hass.services.async_call("bapsim", "set_notification_control", {
                    "command_id": "ack-1", "action": "acknowledge",
                    "assessment_id": "case-1", "ends_at": ends_at,
                })
                self.assertEqual(hass.data["bapsim"]["workflow"].dump_state()[0]["status"], "checking")
                self.assertTrue(hass.data["bapsim"]["notification_controls"].is_acknowledged(
                    "case-1", datetime.now(timezone.utc).isoformat()))

        asyncio.run(scenario())

    def test_simulated_state_uses_separate_mqtt_topic(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_mqtt_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                async def publish(call):
                    pass
                hass.services.async_register("mqtt", "publish", publish)
                self.assertTrue(await module.async_setup(hass, {}))
                at = datetime.now(timezone.utc).isoformat()
                hass.bus.async_fire(module.RAW_EVENT, {
                    "device_id": "cooker_01", "event_type": "availability", "value": "online",
                    "observed_at": at, "received_at": at, "source": "simulator",
                    "availability": "online", "quality": "observed",
                })
                await asyncio.gather(*hass.tasks)
                topics = [data["topic"] for domain, name, data in hass.services.calls
                          if (domain, name) == ("mqtt", "publish")]
                self.assertIn("bapsim/simulation/v1/household_01/state", topics)
                self.assertNotIn("bapsim/v1/household_01/state", topics)

        asyncio.run(scenario())

    def test_mobile_failure_retries_active_incident(self):
        seed = {
            "workflow": [{"source": "matter", "device_id": "cooker_01", "status": "notified",
                          "incident_id": "incident-1", "reason_codes": ["LID_ACTIVITY_DECREASED"]}],
            "notification_target": "mobile_app_caregiver",
            "availability": [{"source": "matter", "device_id": "cooker_01", "availability": "online"}],
            "pending_mobile": [{"source": "matter", "device_id": "cooker_01",
                                "incident_id": "incident-1", "reason_codes": ["LID_ACTIVITY_DECREASED"],
                                "message": "확인 필요"}],
        }

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_retry_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                attempts = []

                async def mobile(call):
                    attempts.append(call.data)
                    if len(attempts) == 1:
                        raise ConnectionError("offline")

                hass.services.async_register("notify", "mobile_app_caregiver", mobile)
                self.assertTrue(await module.async_setup(hass, {}))
                await asyncio.gather(*hass.tasks)
                self.assertEqual(len(attempts), 1)
                self.assertEqual(len(hass.data["bapsim"]["store"].saved["pending_mobile"]), 1)
                hass.hourly_callback(datetime.now(timezone.utc) + timedelta(minutes=2, seconds=1))
                await asyncio.gather(*hass.tasks[1:])
                self.assertEqual(len(attempts), 2)
                self.assertEqual(hass.data["bapsim"]["store"].saved["pending_mobile"], [])
                self.assertEqual(hass.states.values["sensor.bapsim_notification_delivery"].state, "ha_accepted")

        asyncio.run(scenario())

    def test_resolved_incident_is_not_retried_after_restart(self):
        seed = {
            "workflow": [{"source": "matter", "device_id": "cooker_01", "status": "resolved",
                          "incident_id": "incident-1", "reason_codes": []}],
            "notification_target": "mobile_app_caregiver",
            "pending_mobile": [{"source": "matter", "device_id": "cooker_01",
                                "incident_id": "incident-1", "reason_codes": [],
                                "message": "stale"}],
        }

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_stale_retry_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                async def mobile(call):
                    self.fail("resolved incident must not send a mobile notification")
                hass.services.async_register("notify", "mobile_app_caregiver", mobile)
                self.assertTrue(await module.async_setup(hass, {}))
                await asyncio.gather(*hass.tasks)
                self.assertEqual(hass.data["bapsim"]["store"].saved["pending_mobile"], [])

        asyncio.run(scenario())

    def test_declared_away_and_raw_journal_work_without_hardware(self):
        async def scenario(directory):
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_away_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.config = types.SimpleNamespace(path=lambda name: str(Path(directory) / name))
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "set_away", {"away": True})
                self.assertEqual(hass.states.values["sensor.bapsim_away"].state, "away")
                self.assertTrue(hass.data["bapsim"]["store"].saved["away"])
                with self.assertRaises(ValueError):
                    await hass.services.async_call("bapsim", "set_away", {"away": "true"})
                at = "2026-10-02T12:00:00+09:00"
                raw = {"device_id": "cooker_01", "event_type": "board_observation_temperature",
                       "value": "41.5", "observed_at": at, "received_at": at,
                       "source": "matter", "availability": "online", "quality": "observed"}
                hass.bus.async_fire(module.RAW_EVENT, raw)
                hass.bus.async_fire(module.CONTEXT_RAW_EVENT, {**raw, "event_type": "occupancy_state"})
                await asyncio.gather(*hass.tasks)
                files = sorted((Path(directory) / "bapsim_events").glob("*.jsonl"))
                self.assertEqual([path.name for path in files], [
                    "context-2026-10-02.jsonl", "cooker-2026-10-02.jsonl",
                ])
                self.assertIn('"value":"41.5"', files[1].read_text(encoding="utf-8"))

        with TemporaryDirectory() as directory:
            asyncio.run(scenario(directory))

    def test_detection_requests_local_notification_and_caregiver_response(self):
        local = timezone(timedelta(hours=9))
        start = datetime.now(timezone.utc).astimezone(local).replace(
            hour=0, minute=0, second=0, microsecond=0,
        ) - timedelta(days=8)

        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                self.assertTrue(await module.async_setup(hass, {}))
                hass.states.async_set("input_select.bapsim_availability", "online", {})
                def canonical(kind, at):
                    hass.bus.async_fire(module.COOKER_EVENT, {
                        "source": "simulator", "device_id": "cooker_01", "event_type": kind,
                        "value": "online" if kind == "device_online" else "open",
                        "observed_at": at.isoformat(), "received_at": at.isoformat(),
                        "availability": "online", "quality": "simulated",
                    })
                canonical("device_online", start)
                for day in range(7):
                    canonical("lid_opened", start + timedelta(days=day, hours=13))
                    hass.hourly_callback(start + timedelta(days=day, hours=15, minutes=30))
                self.assertEqual(hass.states.values["sensor.bapsim_workflow_status"].state, "normal")
                hass.hourly_callback(start + timedelta(days=7, hours=15, minutes=30))
                self.assertEqual(hass.states.values["sensor.bapsim_baseline"].state, "ready")
                self.assertEqual(hass.states.values["sensor.bapsim_detection_status"].state, "check_required")
                self.assertEqual(len(hass.tasks), 0)
                hass.hourly_callback(start + timedelta(days=8, hours=15, minutes=30))
                self.assertEqual(len(hass.tasks), 1)
                await asyncio.gather(*hass.tasks)
                self.assertEqual(
                    [(domain, name) for domain, name, _ in hass.services.calls],
                    [("persistent_notification", "create")],
                )
                self.assertEqual(hass.states.values["sensor.bapsim_workflow_status"].state, "notified")
                hass.hourly_callback(start + timedelta(days=9, hours=15, minutes=30))
                await asyncio.gather(*hass.tasks[1:])
                notifications = [body for domain, name, body in hass.services.calls
                                 if (domain, name) == ("persistent_notification", "create")]
                self.assertEqual(len(notifications), 2)
                self.assertNotEqual(notifications[0]["notification_id"], notifications[1]["notification_id"])
                await hass.services.async_call("bapsim", "respond", {"response": "checking"})
                self.assertEqual(hass.states.values["sensor.bapsim_workflow_status"].state, "checking")
                await hass.services.async_call("bapsim", "respond", {"response": "resolved"})
                self.assertEqual(hass.states.values["sensor.bapsim_workflow_status"].state, "resolved")

        asyncio.run(scenario())

    def test_dashboard_response_uses_unique_matter_incident(self):
        seed = {"workflow": [{
            "source": "matter", "device_id": "cooker_matter",
            "status": "notified", "incident_id": "matter:incident_1",
            "reason_codes": ["COOK_OPEN_MISMATCH"],
        }]}

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_matter_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "respond", {"response": "checking"})
                self.assertEqual(hass.states.values["sensor.bapsim_workflow_status"].state, "checking")
                self.assertEqual(hass.states.values["sensor.bapsim_workflow_status"].attributes["source"], "matter")

        asyncio.run(scenario())

    def test_matter_contact_registration_reads_current_state(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_contact_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "on", {})
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_matter", "entity_id": "binary_sensor.cooker_lid",
                    "on_is_open": True,
                })
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-1]["source"], "matter")
                self.assertEqual(raw[-1]["event_type"], "lid_state")
                self.assertEqual(raw[-1]["value"], "open")
                self.assertEqual(raw[-1]["availability"], "online")
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "lid_only_ready")
                self.assertFalse(any(
                    name == module.COOKER_EVENT and data["event_type"] == "lid_opened"
                    for name, data in hass.bus.events
                ))

                old = hass.states.get("binary_sensor.cooker_lid")
                hass.states.async_set("binary_sensor.cooker_lid", "off", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "binary_sensor.cooker_lid", "old_state": old,
                    "new_state": hass.states.get("binary_sensor.cooker_lid"),
                })
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-1]["value"], "closed")
                self.assertEqual(raw[-1]["availability"], "online")
                self.assertFalse(any(
                    name == module.COOKER_EVENT and data["event_type"] in {"lid_opened", "lid_closed"}
                    for name, data in hass.bus.events
                ))

        asyncio.run(scenario())

    def test_contact_only_transition_reaches_personal_usage_model(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_lid_model_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.async_track_point_in_utc_time = (
                    lambda hass, callback, deadline: setattr(hass, "deadline_callback", callback) or (lambda: None)
                )
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "off", {})
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_01", "entity_id": "binary_sensor.cooker_lid", "on_is_open": True,
                })
                old = hass.states.get("binary_sensor.cooker_lid")
                hass.states.async_set("binary_sensor.cooker_lid", "on", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "binary_sensor.cooker_lid", "old_state": old,
                    "new_state": hass.states.get("binary_sensor.cooker_lid"),
                })
                self.assertIsNotNone(hass.data["bapsim"]["normalizer"].next_deadline)
                hass.deadline_callback(hass.data["bapsim"]["normalizer"].next_deadline)
                canonical = [data for name, data in hass.bus.events if name == module.COOKER_EVENT]
                self.assertIn("lid_opened", [item["event_type"] for item in canonical])
                model_state = hass.data["bapsim"]["usage_model"].dump_state()
                self.assertEqual(len(model_state["devices"][0]["opens"]), 1)
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "lid_only_ready")

        asyncio.run(scenario())

    def test_matter_registration_rejects_non_matter_entities(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_source_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.template_lid", "off", {})
                hass.states.async_set("sensor.template_phase", "idle", {})
                hass.registry_entries["binary_sensor.template_lid"] = types.SimpleNamespace(platform="template")
                hass.registry_entries["sensor.template_phase"] = types.SimpleNamespace(platform="template")
                self.assertTrue(await module.async_setup(hass, {}))
                with self.assertRaisesRegex(ValueError, "Matter integration"):
                    await hass.services.async_call("bapsim", "register_matter_contact", {
                        "device_id": "cooker_matter", "entity_id": "binary_sensor.template_lid",
                        "on_is_open": True,
                    })
                with self.assertRaisesRegex(ValueError, "Matter integration"):
                    await hass.services.async_call("bapsim", "register_matter_cooking_state", {
                        "device_id": "cooker_matter", "entity_id": "sensor.template_phase",
                        "started_state": "running", "finished_state": "idle",
                    })
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "not_configured")
                self.assertFalse(hass.data["bapsim"]["matter_contacts"])

        asyncio.run(scenario())

    def test_restored_matter_mapping_is_not_ready_after_source_changes(self):
        seed = {"matter_contacts": [{
            "device_id": "cooker_matter", "entity_id": "binary_sensor.cooker_lid",
            "on_is_open": True,
        }], "matter_cooking_states": [{
            "device_id": "cooker_matter", "entity_id": "sensor.cooker_phase",
            "started_state": "running", "finished_state": "idle",
        }]}

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_restored_source_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "off", {})
                hass.states.async_set("sensor.cooker_phase", "idle", {})
                hass.registry_entries["binary_sensor.cooker_lid"] = types.SimpleNamespace(platform="template")
                self.assertTrue(await module.async_setup(hass, {}))
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "source_unverified")
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertTrue(raw)
                self.assertFalse(any(data["event_type"] == "lid_state" for data in raw))

        asyncio.run(scenario())

    def test_live_entity_source_change_offlines_and_restores_matter_input(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_registry_change_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "off", {})
                hass.states.async_set("sensor.cooker_phase", "idle", {})
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_matter", "entity_id": "binary_sensor.cooker_lid",
                    "on_is_open": True,
                })
                await hass.services.async_call("bapsim", "register_matter_cooking_state", {
                    "device_id": "cooker_matter", "entity_id": "sensor.cooker_phase",
                    "started_state": "running", "finished_state": "idle",
                })
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "ready")
                hass.registry_entries["binary_sensor.cooker_lid"] = types.SimpleNamespace(platform="template")
                hass.bus.async_fire("entity_registry_updated", {"entity_id": "binary_sensor.cooker_lid"})
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "source_unverified")
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-1]["value"], "offline")
                before = len([data for data in raw if data["event_type"] == "lid_state"])
                old = hass.states.get("binary_sensor.cooker_lid")
                hass.states.async_set("binary_sensor.cooker_lid", "on", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "binary_sensor.cooker_lid", "old_state": old,
                    "new_state": hass.states.get("binary_sensor.cooker_lid"),
                })
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(len([data for data in raw if data["event_type"] == "lid_state"]), before)

                hass.registry_entries["binary_sensor.cooker_lid"] = types.SimpleNamespace(platform="matter")
                hass.bus.async_fire("entity_registry_updated", {"entity_id": "binary_sensor.cooker_lid"})
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "ready")
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-2]["event_type"], "lid_state")
                self.assertEqual(raw[-2]["value"], "open")
                self.assertEqual(raw[-1]["event_type"], "cooking_state")

        asyncio.run(scenario())

    def test_removed_matter_entity_immediately_offlines_cooker(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_removed_entity_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "off", {})
                hass.states.async_set("sensor.cooker_phase", "idle", {})
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_matter", "entity_id": "binary_sensor.cooker_lid",
                    "on_is_open": True,
                })
                await hass.services.async_call("bapsim", "register_matter_cooking_state", {
                    "device_id": "cooker_matter", "entity_id": "sensor.cooker_phase",
                    "started_state": "running", "finished_state": "idle",
                })
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "ready")
                old = hass.states.values.pop("sensor.cooker_phase")
                hass.bus.async_fire("state_changed", {
                    "entity_id": "sensor.cooker_phase", "old_state": old, "new_state": None,
                })
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "partial_unavailable")
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-1]["event_type"], "cooking_input_availability")
                self.assertEqual(raw[-1]["value"], "offline")
                canonical = [data["event_type"] for name, data in hass.bus.events if name == module.COOKER_EVENT]
                self.assertNotIn("device_offline", canonical)

        asyncio.run(scenario())

    def test_unmapped_matter_state_is_visible_without_creating_cooking_event(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_unmapped_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "off", {})
                hass.states.async_set("sensor.cooker_phase", "idle", {})
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_matter", "entity_id": "binary_sensor.cooker_lid",
                    "on_is_open": True,
                })
                await hass.services.async_call("bapsim", "register_matter_cooking_state", {
                    "device_id": "cooker_matter", "entity_id": "sensor.cooker_phase",
                    "started_state": "running", "finished_state": "idle",
                })
                before = len([data for name, data in hass.bus.events if name == module.COOKER_EVENT
                              and data["event_type"] == "cooking_finished"])
                old = hass.states.get("sensor.cooker_phase")
                hass.states.async_set("sensor.cooker_phase", "keep_warm", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "sensor.cooker_phase", "old_state": old,
                    "new_state": hass.states.get("sensor.cooker_phase"),
                })
                diagnostic = hass.states.values["sensor.bapsim_matter_input"]
                self.assertEqual(diagnostic.state, "partial_state_unmapped")
                self.assertEqual(diagnostic.attributes["devices"][0]["cooking_state"], "keep_warm")
                self.assertTrue(diagnostic.attributes["devices"][0]["cooking_state_unmapped"])
                after = len([data for name, data in hass.bus.events if name == module.COOKER_EVENT
                             and data["event_type"] == "cooking_finished"])
                self.assertEqual(after, before)
                # A newly registered contact has no complete observation window
                # yet. The unmapped cooking value must not disconnect that contact.
                self.assertNotIn("device_offline", [data["event_type"] for name, data in hass.bus.events
                                                    if name == module.COOKER_EVENT])

        asyncio.run(scenario())

    def test_restored_matter_contact_reads_current_state_on_startup(self):
        seed = {"matter_contacts": [{
            "device_id": "cooker_matter", "entity_id": "binary_sensor.cooker_lid",
            "on_is_open": False,
        }]}

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_restored_contact_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "on", {})
                self.assertTrue(await module.async_setup(hass, {}))
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-1]["event_type"], "lid_state")
                self.assertEqual(raw[-1]["value"], "closed")
                self.assertEqual(raw[-1]["availability"], "online")
                self.assertEqual(raw[-1]["source"], "matter")

        asyncio.run(scenario())

    def test_matter_lid_and_cooking_entities_share_raw_pipeline(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_full_matter_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "off", {})
                hass.states.async_set("sensor.cooker_phase", "idle", {})
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_matter", "entity_id": "binary_sensor.cooker_lid",
                    "on_is_open": True,
                })
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "lid_only_ready")
                await hass.services.async_call("bapsim", "register_matter_cooking_state", {
                    "device_id": "cooker_matter", "entity_id": "sensor.cooker_phase",
                    "started_state": "running", "finished_state": "idle",
                })
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "ready")

                def phase(value):
                    old = hass.states.get("sensor.cooker_phase")
                    hass.states.async_set("sensor.cooker_phase", value, {})
                    hass.bus.async_fire("state_changed", {
                        "entity_id": "sensor.cooker_phase", "old_state": old,
                        "new_state": hass.states.get("sensor.cooker_phase"),
                    })

                phase("running")
                phase("idle")
                canonical = [data["event_type"] for name, data in hass.bus.events if name == module.COOKER_EVENT]
                self.assertIn("cooking_started", canonical, repr(hass.bus.events))
                self.assertIn("cooking_finished", canonical)
                self.assertEqual(len(hass.data["bapsim"]["store"].saved["matter_cooking_states"]), 1)

                phase("unavailable")
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "partial_unavailable")
                phase("idle")
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "ready")
                canonical = [data["event_type"] for name, data in hass.bus.events if name == module.COOKER_EVENT]
                self.assertNotIn("device_offline", canonical)
                self.assertEqual(canonical.count("device_online"), 1)

                old = hass.states.get("binary_sensor.cooker_lid")
                hass.states.async_set("binary_sensor.cooker_lid", "unavailable", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "binary_sensor.cooker_lid", "old_state": old,
                    "new_state": hass.states.get("binary_sensor.cooker_lid"),
                })
                self.assertEqual(hass.states.values["sensor.bapsim_detection_status"].state, "data_unavailable")

        asyncio.run(scenario())

    def test_adding_cooking_input_does_not_invent_a_lid_transition(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_contact_upgrade_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "off", {})
                hass.states.async_set("sensor.cooker_phase", "idle", {})
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_matter", "entity_id": "binary_sensor.cooker_lid",
                    "on_is_open": True,
                })
                old = hass.states.get("binary_sensor.cooker_lid")
                hass.states.async_set("binary_sensor.cooker_lid", "on", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "binary_sensor.cooker_lid", "old_state": old,
                    "new_state": hass.states.get("binary_sensor.cooker_lid"),
                })
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "lid_only_ready")

                await hass.services.async_call("bapsim", "register_matter_cooking_state", {
                    "device_id": "cooker_matter", "entity_id": "sensor.cooker_phase",
                    "started_state": "running", "finished_state": "idle",
                })
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "ready")
                canonical = [data["event_type"] for name, data in hass.bus.events if name == module.COOKER_EVENT]
                self.assertFalse({"lid_opened", "lid_closed"} & set(canonical))

        asyncio.run(scenario())

    def test_matter_binding_can_follow_changed_hardware_entities(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_rebind_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                for entity_id, value in (
                    ("binary_sensor.old_lid", "off"), ("binary_sensor.new_lid", "on"),
                    ("sensor.old_phase", "idle"), ("select.new_phase", "complete"),
                ):
                    hass.states.async_set(entity_id, value, {})
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_01", "entity_id": "binary_sensor.old_lid", "on_is_open": True,
                })
                await hass.services.async_call("bapsim", "register_matter_cooking_state", {
                    "device_id": "cooker_01", "entity_id": "sensor.old_phase",
                    "started_state": "running", "finished_state": "idle",
                })
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_01", "entity_id": "binary_sensor.new_lid", "on_is_open": False,
                })
                offline_before_cooking_rebind = sum(
                    data["event_type"] == "device_offline"
                    for name, data in hass.bus.events if name == module.COOKER_EVENT
                )
                await hass.services.async_call("bapsim", "register_matter_cooking_state", {
                    "device_id": "cooker_01", "entity_id": "select.new_phase",
                    "started_state": "heating", "finished_state": "complete",
                })
                self.assertEqual(sum(
                    data["event_type"] == "device_offline"
                    for name, data in hass.bus.events if name == module.COOKER_EVENT
                ), offline_before_cooking_rebind)
                saved = hass.data["bapsim"]["store"].saved
                self.assertEqual([item["entity_id"] for item in saved["matter_contacts"]], ["binary_sensor.new_lid"])
                self.assertEqual([item["entity_id"] for item in saved["matter_cooking_states"]], ["select.new_phase"])
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "ready")
                raw_before = len([1 for name, _ in hass.bus.events if name == module.RAW_EVENT])
                old = hass.states.get("binary_sensor.old_lid")
                hass.states.async_set("binary_sensor.old_lid", "on", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "binary_sensor.old_lid", "old_state": old,
                    "new_state": hass.states.get("binary_sensor.old_lid"),
                })
                raw_after = len([1 for name, _ in hass.bus.events if name == module.RAW_EVENT])
                self.assertEqual(raw_after, raw_before)
                await hass.services.async_call("bapsim", "unregister_matter_input", {
                    "device_id": "cooker_01", "input_type": "cooking",
                })
                self.assertEqual(sum(
                    data["event_type"] == "device_offline"
                    for name, data in hass.bus.events if name == module.COOKER_EVENT
                ), offline_before_cooking_rebind)
                self.assertEqual([
                    data["event_type"] for name, data in hass.bus.events if name == module.RAW_EVENT
                ][-1], "cooking_input_availability")
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "lid_only_ready")
                self.assertEqual(hass.data["bapsim"]["store"].saved["matter_cooking_states"], [])
                await hass.services.async_call("bapsim", "unregister_matter_input", {
                    "device_id": "cooker_01", "input_type": "all",
                })
                self.assertEqual(hass.states.values["sensor.bapsim_matter_input"].state, "not_configured")

        asyncio.run(scenario())

    def test_optional_matter_devices_share_raw_contract_without_cooker_detection(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_context_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.motion", "off", {})
                hass.states.async_set("binary_sensor.front_door", "off", {})
                self.assertTrue(await module.async_setup(hass, {}))
                for entity_id, device_id, signal in (
                    ("binary_sensor.motion", "motion_01", "occupancy"),
                    ("binary_sensor.front_door", "door_01", "contact"),
                ):
                    await hass.services.async_call("bapsim", "register_matter_context", {
                        "device_id": device_id, "entity_id": entity_id, "signal": signal,
                    })
                self.assertEqual(len(hass.data["bapsim"]["store"].saved["matter_contexts"]), 2)
                old = hass.states.get("binary_sensor.motion")
                hass.states.async_set("binary_sensor.motion", "on", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "binary_sensor.motion", "old_state": old,
                    "new_state": hass.states.get("binary_sensor.motion"),
                })
                context = [data for name, data in hass.bus.events if name == module.CONTEXT_RAW_EVENT]
                self.assertEqual(context[-1]["event_type"], "occupancy_state")
                self.assertEqual(context[-1]["value"], "on")
                self.assertEqual(len(context[-1]), 8)
                self.assertFalse([name for name, _ in hass.bus.events if name == module.COOKER_EVENT])
                self.assertEqual(hass.states.values["sensor.bapsim_detection_status"].state, "waiting")

                hass.registry_entries["binary_sensor.motion"] = types.SimpleNamespace(platform="template")
                hass.bus.async_fire("entity_registry_updated", {"entity_id": "binary_sensor.motion"})
                context = [data for name, data in hass.bus.events if name == module.CONTEXT_RAW_EVENT]
                self.assertEqual(context[-1]["availability"], "offline")
                self.assertEqual(context[-1]["quality"], "unknown")
                self.assertEqual(
                    hass.states.values["sensor.bapsim_matter_context"].attributes["devices"][1]["source_verified"],
                    False,
                )

        asyncio.run(scenario())

    def test_restored_optional_matter_mapping_reads_current_state(self):
        seed = {"matter_contexts": [{
            "device_id": "door_01", "entity_id": "binary_sensor.front_door", "signal": "contact",
        }]}

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_restored_context_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.front_door", "on", {})
                self.assertTrue(await module.async_setup(hass, {}))
                events = [data for name, data in hass.bus.events if name == module.CONTEXT_RAW_EVENT]
                self.assertEqual(events[-1]["device_id"], "door_01")
                self.assertEqual(events[-1]["event_type"], "contact_state")
                self.assertEqual(events[-1]["value"], "on")
                self.assertEqual(hass.states.values["sensor.bapsim_matter_context"].state, "ready")

        asyncio.run(scenario())

    def test_board_observation_survives_missing_edge_inference(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_board_signal_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("sensor.board_temperature", "41.5", {})
                hass.states.async_set("sensor.board_phase", "unavailable", {})
                self.assertTrue(await module.async_setup(hass, {}))
                for entity_id, kind, signal in (
                    ("sensor.board_temperature", "observation", "temperature"),
                    ("sensor.board_phase", "inference", "cooker_phase"),
                ):
                    await hass.services.async_call("bapsim", "register_matter_board_signal", {
                        "device_id": "cooker_matter", "entity_id": entity_id,
                        "kind": kind, "signal": signal,
                    })
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-2]["event_type"], "board_observation_temperature")
                self.assertEqual(raw[-2]["value"], "41.5")
                self.assertEqual(raw[-2]["availability"], "online")
                self.assertEqual(raw[-1]["event_type"], "board_inference_cooker_phase")
                self.assertEqual(raw[-1]["availability"], "offline")
                self.assertEqual(hass.states.values["sensor.bapsim_matter_board"].state, "partially_unavailable")
                self.assertFalse([name for name, _ in hass.bus.events if name == module.COOKER_EVENT])

                old = hass.states.get("sensor.board_temperature")
                hass.states.async_set("sensor.board_temperature", "42.0", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "sensor.board_temperature", "old_state": old,
                    "new_state": hass.states.get("sensor.board_temperature"),
                })
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-1]["value"], "42.0")
                self.assertEqual(raw[-1]["availability"], "online")
                hass.registry_entries["sensor.board_temperature"] = types.SimpleNamespace(platform="template")
                hass.bus.async_fire("entity_registry_updated", {"entity_id": "sensor.board_temperature"})
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-1]["event_type"], "board_observation_temperature")
                self.assertEqual(raw[-1]["availability"], "offline")
                self.assertEqual(raw[-1]["quality"], "unknown")
                self.assertEqual(hass.states.values["sensor.bapsim_matter_board"].state, "source_unverified")
                saved_history = hass.data["bapsim"]["store"].saved["board_history"]
                self.assertGreaterEqual(len(saved_history), 3)
                self.assertEqual(saved_history[-1]["event_type"], "board_observation_temperature")
                self.assertEqual(saved_history[-1]["availability"], "offline")

        asyncio.run(scenario())

    def test_board_raw_history_restores_after_restart(self):
        seed = {"board_history": [{
            "device_id": "cooker_matter", "event_type": "board_observation_temperature",
            "value": "41.5", "observed_at": "2026-10-02T10:00:00+09:00",
            "received_at": "2026-10-02T10:00:00+09:00", "source": "matter",
            "availability": "online", "quality": "observed",
        }]}

        async def scenario():
            with patch.dict(sys.modules, ha_modules(seed)):
                spec = importlib.util.spec_from_file_location("bapsim_ha_board_history_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                self.assertTrue(await module.async_setup(hass, {}))
                self.assertEqual(hass.data["bapsim"]["board_history"], seed["board_history"])
                state = hass.states.values["sensor.bapsim_board_history"]
                self.assertEqual(state.state, "1")
                self.assertEqual(state.attributes["latest"][0]["value"], "41.5")

        asyncio.run(scenario())

    def test_verified_board_inference_can_be_mapped_without_losing_raw_value(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_ha_dual_board_signal_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                hass.states.async_set("binary_sensor.cooker_lid", "off", {})
                hass.states.async_set("sensor.board_phase", "complete", {})
                self.assertTrue(await module.async_setup(hass, {}))
                await hass.services.async_call("bapsim", "register_matter_board_signal", {
                    "device_id": "cooker_matter", "entity_id": "sensor.board_phase",
                    "kind": "inference", "signal": "cooker_phase",
                })
                await hass.services.async_call("bapsim", "register_matter_contact", {
                    "device_id": "cooker_matter", "entity_id": "binary_sensor.cooker_lid",
                    "on_is_open": True,
                })
                await hass.services.async_call("bapsim", "register_matter_cooking_state", {
                    "device_id": "cooker_matter", "entity_id": "sensor.board_phase",
                    "started_state": "heating", "finished_state": "complete",
                })
                old = hass.states.get("sensor.board_phase")
                hass.states.async_set("sensor.board_phase", "heating", {})
                hass.bus.async_fire("state_changed", {
                    "entity_id": "sensor.board_phase", "old_state": old,
                    "new_state": hass.states.get("sensor.board_phase"),
                })
                raw = [data for name, data in hass.bus.events if name == module.RAW_EVENT]
                self.assertEqual(raw[-2]["event_type"], "board_inference_cooker_phase")
                self.assertEqual(raw[-2]["value"], "heating")
                self.assertEqual(raw[-1]["event_type"], "cooking_state")
                self.assertEqual(raw[-1]["value"], "started")
                canonical = [data["event_type"] for name, data in hass.bus.events if name == module.COOKER_EVENT]
                self.assertIn("cooking_started", canonical)

        asyncio.run(scenario())
