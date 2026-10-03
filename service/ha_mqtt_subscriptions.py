"""MQTT control and worker-state subscription lifecycle for HA."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace


def bind_mqtt_subscriptions(hass, output, set_notification_control, show, logger, external_service):
    _LOGGER = logger
    unsubscribe_control = None
    unsubscribe_worker: list = []

    async def on_mqtt_control(message) -> None:
        if getattr(message, "retain", False):
            return
        command_id = None
        try:
            command = json.loads(message.payload)
            if not isinstance(command, dict):
                raise ValueError("control payload must be an object")
            command_id = command.get("command_id")
            if command.get("schema_version") != "0.1.0":
                raise ValueError("unsupported control schema")
            created = datetime.fromisoformat(command["created_at"].replace("Z", "+00:00"))
            if created.tzinfo is None or abs((datetime.now(timezone.utc) - created).total_seconds()) > 600:
                raise ValueError("stale control command")
            await set_notification_control(SimpleNamespace(data=command))
            result = {"command_id": command_id, "status": "accepted"}
        except (KeyError, TypeError, ValueError) as exc:
            result = {"command_id": command_id, "status": "rejected", "reason": str(exc)}
        if hass.services.has_service("mqtt", "publish"):
            await hass.services.async_call("mqtt", "publish", {
                "topic": output.topic("matter", "control_result"),
                "payload": json.dumps(result), "qos": 1, "retain": False,
            }, blocking=True)

    async def subscribe_controls() -> None:
        nonlocal unsubscribe_control
        try:
            from homeassistant.components import mqtt
        except ImportError:
            return
        if await mqtt.async_wait_for_mqtt_client(hass):
            unsubscribe_control = await mqtt.async_subscribe(
                hass, output.topic("matter", "control"),
                lambda message: hass.async_create_task(on_mqtt_control(message)), qos=1,
            )

    # MQTT can finish loading after this YAML integration. Wait for its client
    # instead of deciding from whether mqtt.publish already exists at setup.
    if not external_service and hasattr(hass, "config"):
        hass.async_create_task(subscribe_controls())

    async def subscribe_worker() -> None:
        try:
            from homeassistant.components import mqtt
        except ImportError:
            return
        if not await mqtt.async_wait_for_mqtt_client(hass):
            return

        def on_state(message) -> None:
            try:
                payload = json.loads(message.payload)
                if not isinstance(payload, dict):
                    raise ValueError("worker state must be an object")
                if payload.get("household_id") == output.household_id:
                    show("output_state", payload.get("summary", ""), payload)
            except (TypeError, ValueError):
                _LOGGER.warning("Ignored invalid worker state")

        def on_simulation_state(message) -> None:
            try:
                payload = json.loads(message.payload)
                if not isinstance(payload, dict):
                    raise ValueError("simulation state must be an object")
                if payload.get("household_id") == output.household_id:
                    show("simulation_output_state", payload.get("summary", ""),
                         {**payload, "mode": "simulation"})
            except (TypeError, ValueError):
                _LOGGER.warning("Ignored invalid simulation worker state")

        def on_control_state(message) -> None:
            try:
                payload = json.loads(message.payload)
                if not isinstance(payload, dict):
                    raise ValueError("worker controls must be an object")
                if payload.get("household_id") == output.household_id:
                    show("notification_controls", payload.get("revision", 0), payload)
            except (TypeError, ValueError):
                _LOGGER.warning("Ignored invalid worker controls")

        def on_control_result(message) -> None:
            try:
                payload = json.loads(message.payload)
                if not isinstance(payload, dict):
                    raise ValueError("worker result must be an object")
                show("notification_control_result", payload.get("status", "unknown"), payload)
            except (TypeError, ValueError):
                _LOGGER.warning("Ignored invalid worker control result")

        for leaf, handler in (("state", on_state), ("notification_controls", on_control_state),
                              ("control_result", on_control_result)):
            unsubscribe_worker.append(await mqtt.async_subscribe(
                hass, output.topic("matter", leaf), handler, qos=1))
        unsubscribe_worker.append(await mqtt.async_subscribe(
            hass, output.topic("simulator", "state"), on_simulation_state, qos=1))

    if external_service and hasattr(hass, "config"):
        hass.async_create_task(subscribe_worker())

    def stop_subscriptions() -> None:
        if unsubscribe_control is not None:
            unsubscribe_control()
        for remove in unsubscribe_worker:
            remove()

    return stop_subscriptions
