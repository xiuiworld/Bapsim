"""Caregiver response, pause, acknowledgement, and mobile-action HA handlers."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable, NamedTuple


class CaregiverHandlers(NamedTuple):
    respond: Callable
    set_notification_control: Callable
    dashboard_control: Callable
    on_mobile_action: Callable


def bind_caregiver_commands(
        callback, hass, workflow, trend_workflow, controls, usage_model, analysis_mode,
        output, external_service, show, send_controls, publish_workflow, save_now, save_later, logger
) -> CaregiverHandlers:
    _LOGGER = logger
    control_lock = asyncio.Lock()

    async def respond(call) -> None:
        response = call.data["response"]
        source = call.data.get("source")
        device_id = call.data.get("device_id")
        if source is None and device_id is None:
            active = [
                item for item in (*workflow.dump_state(), *trend_workflow.dump_state())
                if item["status"] in {"check_required", "notified", "checking"}
            ]
            if len(active) != 1:
                _LOGGER.warning("Caregiver response needs exactly one active incident or an explicit source and device_id")
                return
            source, device_id = active[0]["source"], active[0]["device_id"]
        elif not isinstance(source, str) or not isinstance(device_id, str):
            raise ValueError("source and device_id must be supplied together")
        incident_id = call.data.get("incident_id")
        if incident_id is None and sum(
            item["status"] in {"check_required", "notified", "checking"}
            and item["source"] == source and item["device_id"] == device_id
            for item in (*workflow.dump_state(), *trend_workflow.dump_state())
        ) > 1:
            raise ValueError("incident_id is required when both assessments are active")
        manager = trend_workflow if incident_id and any(
            item["incident_id"] == incident_id for item in trend_workflow.dump_state()
        ) else workflow
        update = manager.caregiver_response(source, device_id, response,
                                            datetime.now(timezone.utc).isoformat(), incident_id)
        if update is not None:
            publish_workflow(update)

    async def set_notification_control(call) -> None:
        command = dict(call.data)
        if command.get("household_id", output.household_id) != output.household_id:
            raise ValueError("household_id does not match this Bapsim instance")
        if external_service:
            if not hass.services.has_service("mqtt", "publish"):
                raise ValueError("MQTT broker is required for external analysis service")
            command["schema_version"] = "0.1.0"
            command["household_id"] = output.household_id
            command["created_at"] = datetime.now(timezone.utc).isoformat()
            await hass.services.async_call("mqtt", "publish", {
                "topic": output.topic("matter", "control"),
                "payload": json.dumps(command, ensure_ascii=False),
                "qos": 1, "retain": False,
            }, blocking=True)
            show("notification_control_result", "submitted", {"command_id": command.get("command_id")})
            return
        if command.get("action") == "acknowledge":
            incident_id = command.get("assessment_id", command.get("incident_id"))
            active = [item for item in (*workflow.dump_state(), *trend_workflow.dump_state())
                      if item.get("incident_id") == incident_id and item["status"] in
                      {"check_required", "notified", "checking"}]
            if len(active) != 1:
                raise ValueError("acknowledge requires one active incident_id")
        now = datetime.now(timezone.utc).isoformat()
        async with control_lock:
            before = deepcopy(controls.dump_state())
            result = controls.apply(command, now)
            show("notification_control_result", "applying", {"command_id": result["command_id"]})
            try:
                await save_now()
            except Exception:
                controls.load_state(before)
                show("notification_control_result", "failed", {
                    "command_id": result["command_id"], "reason": "storage_failed"})
                raise
        show("notification_controls", controls.revision, controls.dump_state())
        show("notification_control_result", result["status"], result)
        if hass.services.has_service("mqtt", "publish"):
            hass.async_create_task(send_controls())
        if command.get("action") == "acknowledge":
            incident_id = command.get("assessment_id", command.get("incident_id"))
            for manager in (workflow, trend_workflow):
                for item in manager.dump_state():
                    if item.get("incident_id") == incident_id:
                        update = manager.caregiver_response(item["source"], item["device_id"],
                                                            "checking", now, incident_id)
                        if update is not None:
                            publish_workflow(update)
        if controls.is_paused(now):
            if not analysis_mode.model_away:
                usage_model.set_away(True, now)
                analysis_mode.model_away = True
            for manager in (workflow, trend_workflow):
                for item in manager.dump_state():
                    for update in manager.ingest({
                        "source": item["source"], "device_id": item["device_id"],
                        "as_of": now, "routine_status": "away", "reason_codes": [],
                    }):
                        publish_workflow(update)
        save_later()

    async def dashboard_control(call) -> None:
        action = call.data.get("action")
        if action not in {"acknowledge", "pause_lifestyle", "cancel_pause"}:
            raise ValueError("unsupported dashboard action")
        now = datetime.now(timezone.utc)
        command: dict[str, object] = {"command_id": str(uuid.uuid4()), "action": action}
        if action in {"acknowledge", "pause_lifestyle"}:
            picker = hass.states.get("input_datetime.bapsim_control_until")
            if picker is None:
                raise ValueError("Set the notification end date and time first")
            until = datetime.fromisoformat(picker.state).replace(tzinfo=timezone(timedelta(hours=9)))
            if until <= now:
                raise ValueError("Notification end time must be in the future")
            command["ends_at"] = until.isoformat()
        if action == "acknowledge":
            active = [item for item in (*workflow.dump_state(), *trend_workflow.dump_state())
                      if item["incident_id"] and item["status"] in {"check_required", "notified", "checking"}]
            if external_service:
                entity = hass.states.get("sensor.bapsim_output_state")
                active = [item for item in (entity.attributes.get("assessments", []) if entity else [])
                          if item.get("status") in {"watch", "notified"}]
                if len(active) == 1:
                    command["assessment_id"] = active[0]["assessment_id"]
            elif len(active) == 1:
                command["assessment_id"] = active[0]["incident_id"]
            if len(active) != 1:
                raise ValueError("Acknowledge requires exactly one active assessment")
        elif action == "pause_lifestyle":
            command.update({"pause_id": str(uuid.uuid4()), "starts_at": now.isoformat()})
        else:
            pause_id = call.data.get("pause_id")
            if not isinstance(pause_id, str) or not pause_id:
                raise ValueError("pause_id is required to cancel")
            command["pause_id"] = pause_id
        await set_notification_control(SimpleNamespace(data=command))

    @callback
    def on_mobile_action(event: Event) -> None:
        action = event.data.get("action", "")
        if not isinstance(action, str):
            return
        parts = action.split("|", 4)
        if len(parts) != 5 or parts[0] != "BAPSIM" or parts[1] not in {"checking", "resolved"}:
            return
        _, response, source, device_id, incident_id = parts
        manager = trend_workflow if any(
            item["incident_id"] == incident_id for item in trend_workflow.dump_state()
        ) else workflow
        update = manager.caregiver_response(source, device_id, response,
                                            datetime.now(timezone.utc).isoformat(), incident_id)
        if update is not None:
            publish_workflow(update)

    return CaregiverHandlers(respond, set_notification_control, dashboard_control, on_mobile_action)
