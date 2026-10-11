"""A06 source-specific HA cards and consumer expiry in internal analysis mode."""

import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import types
from unittest import TestCase
from unittest.mock import patch
from jinja2 import Environment

from service.ha_dashboard import HADashboard
from service.ha_mqtt_subscriptions import bind_mqtt_subscriptions, valid_worker_state
from service.ha_mqtt_output import HAMQTTOutput
from service.output import StatusOutput
from test_ha_service_integration import COMPONENT, FakeHass, ha_modules


class A06InternalOutputTests(TestCase):
    def test_caregiver_observation_copy_distinguishes_available_gap_and_initial(self):
        dashboard_file = (Path(__file__).resolve().parents[1] /
                          "infrastructure/home_assistant/config/dashboards/bapsim_lab.yaml")
        content = dashboard_file.read_text(encoding="utf-8")
        start = content.index("          ## 밥솥 사용 확인")
        end = content.index("      - type: markdown", start)
        environment = Environment()
        environment.filters["timestamp_custom"] = lambda value, _format, _local=True: "10월 08일 09:00"
        template = environment.from_string("\n".join(
            line.removeprefix("          ") for line in content[start:end].splitlines()))

        def render(issue, attributes=None):
            output = {"observation": "available", "valid_until": "2099-01-01T00:00:00+00:00",
                      **(attributes or {})}
            def as_timestamp(value, default=0):
                if not value:
                    return default
                return (value if isinstance(value, datetime) else
                        datetime.fromisoformat(value)).timestamp()
            return template.render(
                states=lambda entity: issue if entity == "sensor.bapsim_observation_issue_status"
                else "현재 확인된 밥솥 사용 기록을 표시합니다." if entity == "sensor.bapsim_output_state"
                else "선택",
                state_attr=lambda entity, name: output.get(name)
                if entity == "sensor.bapsim_output_state" else None,
                as_timestamp=as_timestamp,
                now=lambda: datetime(2026, 10, 8, tzinfo=timezone.utc),
            )

        self.assertIn("최근 밥솥 기록을 확인했어요", render("available"))
        self.assertIn("연결 확인이 필요해요", render("disconnected"))
        self.assertIn("새 사용 기록을 기다리는 중이에요", render("unknown"))
        self.assertIn("평소와 비교할 수 없습니다", render("available", {"valid_until": None}))
        self.assertIn("식사 여부나 건강 상태는 알 수 없습니다", render("available"))
        active = render("available", {"learning": "personalized",
                                      "last_usage_at": "2026-10-08T00:00:00+00:00",
                                      "assessments": [{"type": "usage_gap", "status": "notified"}]})
        self.assertIn("10월 08일 09:00", active)
        self.assertIn("평소 시간대 사용 기록 없음", active)
        self.assertIn("확인 필요", active)
        self.assertNotIn("10월 08일 09:00", render("disconnected", {
            "last_usage_at": "2026-10-08T00:00:00+00:00"}))
        initial = render("available", {"learning": "collecting", "initial_basis": True})
        self.assertIn("입력한 사용 시간으로 비교 중", initial)
        self.assertNotIn("입력한 사용 시간으로 비교 중",
                         render("available", {"learning": "personalized", "initial_basis": False}))

    def test_simulator_updates_only_simulation_card_and_expires(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_a06_cards_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                self.assertTrue(await module.async_setup(hass, {"bapsim": {
                    "household_id": "household_a_test"}}))
                self.assertEqual(hass.states.get("sensor.bapsim_output_state").state,
                                 "awaiting_matter")
                self.assertEqual(hass.states.get("sensor.bapsim_simulation_output_state").state,
                                 "waiting")
                at = datetime.now(timezone.utc).isoformat()
                hass.bus.async_fire(module.COOKER_EVENT, {
                    "source": "simulator", "device_id": "cooker_01",
                    "event_type": "device_online", "value": "online",
                    "observed_at": at, "received_at": at,
                    "availability": "online", "quality": "simulated",
                })
                simulation = hass.states.get("sensor.bapsim_simulation_output_state")
                self.assertEqual(simulation.attributes["source"], "simulator")
                self.assertEqual(simulation.attributes["household_id"], "household_a_test")
                self.assertEqual(hass.states.get("sensor.bapsim_output_state").state,
                                 "awaiting_matter")
                self.assertEqual(hass.states.get("sensor.bapsim_baseline").state,
                                 "waiting")
                self.assertEqual(hass.states.get("sensor.bapsim_simulation_baseline").state,
                                 "learning")
                self.assertEqual(hass.states.get("sensor.bapsim_features").state,
                                 "waiting")
                self.assertEqual(hass.states.get("sensor.bapsim_simulation_features").state,
                                 "available")
                if hass.tasks:
                    await asyncio.gather(*hass.tasks)

        asyncio.run(scenario())

    def test_consumer_clears_stale_payload_and_newer_revision_wins(self):
        hass = FakeHass()
        scheduled = []

        def schedule(_hass, callback, at):
            item = {"callback": callback, "at": at, "cancelled": False}
            scheduled.append(item)
            return lambda: item.update(cancelled=True)

        dashboard = HADashboard(hass, None, {}, schedule)
        now = datetime.now(timezone.utc)
        first = {"revision": 1, "summary": "old", "source": "simulator",
                 "valid_until": (now + timedelta(seconds=180)).isoformat()}
        second = {"revision": 2, "summary": "new", "source": "simulator",
                  "valid_until": (now + timedelta(seconds=240)).isoformat()}
        dashboard.show_output("simulator", first)
        dashboard.show_output("simulator", second)
        self.assertTrue(scheduled[0]["cancelled"])
        dashboard.show_output("simulator", first)
        self.assertEqual(len(scheduled), 2)
        scheduled[0]["callback"](scheduled[0]["at"])
        self.assertEqual(hass.states.get("sensor.bapsim_simulation_output_state").state, "new")
        scheduled[1]["callback"](scheduled[1]["at"])
        expired = hass.states.get("sensor.bapsim_simulation_output_state")
        self.assertEqual(expired.state, "expired")
        self.assertNotIn("summary", expired.attributes)

    def test_worker_state_rejects_expired_retained_and_wrong_schema(self):
        now = datetime.now(timezone.utc)
        output = StatusOutput("household_a_test")
        fresh = output.build(now.isoformat(), {"ready": False}, "available", None, None)
        self.assertTrue(valid_worker_state(fresh, "household_a_test", now))
        self.assertFalse(valid_worker_state({**fresh, "revision": True}, "household_a_test", now))
        self.assertFalse(valid_worker_state({**fresh, "schema_version": "old"},
                                            "household_a_test", now))
        self.assertFalse(valid_worker_state(fresh, "other_household", now))
        self.assertFalse(valid_worker_state(fresh, "household_a_test",
                                            now + timedelta(seconds=181)))

    def test_worker_subscription_uses_dashboard_expiry_and_revision_guard(self):
        async def scenario():
            handlers = {}
            components = types.ModuleType("homeassistant.components")
            components.__path__ = []
            mqtt = types.ModuleType("homeassistant.components.mqtt")

            async def wait_for_client(_hass):
                return True

            async def subscribe(_hass, topic, handler, qos):
                handlers[topic] = handler
                return lambda: handlers.pop(topic, None)

            mqtt.async_wait_for_mqtt_client = wait_for_client
            mqtt.async_subscribe = subscribe
            components.mqtt = mqtt
            with patch.dict(sys.modules, {"homeassistant.components": components,
                                          "homeassistant.components.mqtt": mqtt}):
                hass = FakeHass()
                hass.config = SimpleNamespace()
                timers = []

                def schedule(_hass, callback, at):
                    timers.append((callback, at))
                    return lambda: None

                dashboard = HADashboard(hass, None, {}, schedule)
                output = StatusOutput("household_a_test")
                stop = bind_mqtt_subscriptions(
                    hass, output, None, dashboard.show, None, True, dashboard.show_output)
                await asyncio.gather(*hass.tasks)
                handler = handlers[output.topic("matter", "state")]
                now = datetime.now(timezone.utc)
                expired = output.build((now - timedelta(minutes=4)).isoformat(),
                                       {}, "available", None, None)
                handler(SimpleNamespace(payload=json.dumps(expired)))
                self.assertIsNone(hass.states.get("sensor.bapsim_output_state"))
                fresh = output.build(now.isoformat(), {}, "available", None, None)
                handler(SimpleNamespace(payload=json.dumps(fresh)))
                self.assertEqual(hass.states.get("sensor.bapsim_output_state").state,
                                 fresh["summary"])
                self.assertEqual(len(timers), 1)
                handler(SimpleNamespace(payload=json.dumps(expired)))
                self.assertEqual(hass.states.get("sensor.bapsim_output_state").attributes["revision"],
                                 fresh["revision"])
                timers[0][0](timers[0][1])
                self.assertEqual(hass.states.get("sensor.bapsim_output_state").state, "expired")
                stop()

        asyncio.run(scenario())

    def test_mqtt_discovery_and_expiry_cover_simulation_separately(self):
        async def scenario():
            hass = FakeHass()

            async def publish(_call):
                return None

            hass.services.async_register("mqtt", "publish", publish)
            output = StatusOutput("household_a_test")
            sender = HAMQTTOutput(hass, output, SimpleNamespace(revision=0, pauses=[],
                                      acknowledgements=[]), lambda *args: None, None)
            at = datetime.now(timezone.utc).isoformat()
            profile = {"ready": False, "last_lid_open_at": None}
            simulation = output.build(at, profile, "available", None, None)
            await sender.send_state("simulator", simulation)
            matter = output.build(at, profile, "available", None, None)
            await sender.send_state("matter", matter)
            messages = {call[2]["topic"]: call[2]["payload"]
                        for call in hass.services.calls if call[:2] == ("mqtt", "publish")}
            sim_config = json.loads(messages["homeassistant/sensor/bapsim_household_a_test_simulation/config"])
            live_config = json.loads(messages["homeassistant/sensor/bapsim_household_a_test/config"])
            self.assertEqual(sim_config["expire_after"], 180)
            self.assertEqual(live_config["expire_after"], 180)
            self.assertNotEqual(sim_config["unique_id"], live_config["unique_id"])
            self.assertEqual(sim_config["state_topic"],
                             "bapsim/simulation/v1/household_a_test/state")

        asyncio.run(scenario())

    def test_notification_target_accepts_full_and_short_service_names(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_a06_target_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()

                async def mobile(_call):
                    return None

                hass.services.async_register("notify", "mobile_app_test_phone", mobile)
                self.assertTrue(await module.async_setup(hass, {}))
                for name in ("notify.mobile_app_test_phone", "mobile_app_test_phone"):
                    await hass.services.async_call("bapsim", "set_notification_target",
                                                   {"service": name})
                with self.assertRaises(ValueError):
                    await hass.services.async_call("bapsim", "set_notification_target",
                                                   {"service": "notify.mobile_app_other"})

        asyncio.run(scenario())

    def test_expiry_test_service_pauses_only_simulation_publication(self):
        async def scenario():
            with patch.dict(sys.modules, ha_modules({})):
                spec = importlib.util.spec_from_file_location("bapsim_a06_pause_under_test", COMPONENT)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                hass = FakeHass()
                self.assertTrue(await module.async_setup(hass, {}))
                at = datetime.now(timezone.utc)

                def feed(source, kind, moment):
                    hass.bus.async_fire(module.COOKER_EVENT, {
                        "source": source, "device_id": "cooker_01",
                        "event_type": kind, "value": "online" if kind == "device_online" else "open",
                        "observed_at": moment.isoformat(), "received_at": moment.isoformat(),
                        "availability": "online", "quality": "simulated" if source == "simulator" else "observed",
                    })

                feed("simulator", "device_online", at)
                original = hass.states.get("sensor.bapsim_simulation_output_state").attributes["revision"]
                await hass.services.async_call("bapsim", "test_pause_simulation_output",
                                               {"duration_seconds": 240})
                feed("simulator", "lid_opened", at + timedelta(seconds=1))
                self.assertEqual(hass.states.get("sensor.bapsim_simulation_output_state")
                                 .attributes["revision"], original)
                feed("matter", "device_online", at + timedelta(seconds=2))
                self.assertEqual(hass.states.get("sensor.bapsim_output_state").attributes["source"],
                                 "matter")
                if hass.tasks:
                    await asyncio.gather(*hass.tasks)

        asyncio.run(scenario())
