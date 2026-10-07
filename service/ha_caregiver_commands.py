"""Caregiver response, pause, acknowledgement, and mobile-action HA handlers."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Callable, Mapping, NamedTuple


class CaregiverHandlers(NamedTuple):
    respond: Callable
    set_notification_control: Callable
    dashboard_control: Callable
    on_mobile_action: Callable


ACTIVE_STATUSES = frozenset({"check_required", "notified", "checking"})


def _selected_id(hass, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    value = getattr(state, "state", None)
    return value.rsplit(" | ", 1)[-1] if isinstance(value, str) and " | " in value else None


def _future_end(value: str, now: datetime) -> bool:
    try:
        end = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return end.tzinfo is not None and end > now
    except ValueError:
        return False


def _active_assessments(hass, workflow, trend_workflow, external_service: bool) -> list[dict[str, str]]:
    if not external_service:
        return [item for item in (*workflow.dump_state(), *trend_workflow.dump_state())
                if item.get("source") == "matter" and item.get("incident_id") and
                item.get("status") in ACTIVE_STATUSES]
    state = hass.states.get("sensor.bapsim_output_state")
    if state is None or state.state == "expired":
        return []
    attributes = state.attributes
    if attributes.get("source") != "matter" or attributes.get("schema_version") != "0.1.0":
        return []
    try:
        valid_until = datetime.fromisoformat(str(attributes["valid_until"]).replace("Z", "+00:00"))
        if valid_until.tzinfo is None or valid_until <= datetime.now(timezone.utc):
            return []
    except (KeyError, TypeError, ValueError):
        return []
    assessments = attributes.get("assessments")
    if not isinstance(assessments, list):
        return []
    return [{"incident_id": item["assessment_id"],
             "device_id": str(item.get("device_id") or item.get("type", "밥솥")),
             "status": "checking" if item.get("caregiver_status") == "checking" else item["status"],
             "source": "matter"}
            for item in assessments if isinstance(item, dict) and
            isinstance(item.get("assessment_id"), str) and item["assessment_id"] and
            item.get("status") in {"watch", "notified"}]


def _active_pauses(hass, controls, external_service: bool) -> Mapping[str, object]:
    if not external_service:
        return controls.pauses
    state = hass.states.get("sensor.bapsim_notification_controls")
    pauses = state.attributes.get("pauses") if state is not None else None
    return pauses if isinstance(pauses, dict) else {}


async def refresh_dashboard_choices(hass, workflow, trend_workflow, controls, logger=None,
                                    *, external_service: bool = False) -> None:
    if not hass.services.has_service("input_select", "set_options"):
        return
    assessments = _active_assessments(hass, workflow, trend_workflow, external_service)
    choices = ["선택"] + [
        f"{item['device_id']} · {item['status']} | {item['incident_id']}"
        for item in assessments
    ]
    now = datetime.now(timezone.utc)
    pauses = ["선택"] + [
        f"{pause_id} (종료 {item['ends_at']}) | {pause_id}"
        for pause_id, item in _active_pauses(hass, controls, external_service).items()
        if isinstance(item, dict) and isinstance(item.get("ends_at"), str) and
        _future_end(item["ends_at"], now)
    ]
    for entity_id, options in (("input_select.bapsim_active_assessment", choices),
                               ("input_select.bapsim_active_pause", pauses)):
        current = hass.states.get(entity_id)
        if current is None or current.attributes.get("options") == options:
            continue
        try:
            await hass.services.async_call("input_select", "set_options", {
                "entity_id": entity_id, "options": options,
            }, blocking=True)
            if (current.state in options and current.state != "선택" and
                    hass.services.has_service("input_select", "select_option")):
                await hass.services.async_call("input_select", "select_option", {
                    "entity_id": entity_id, "option": current.state,
                }, blocking=True)
        except Exception as exc:
            if logger is not None:
                logger.warning("Could not refresh caregiver choice %s: %s", entity_id,
                               type(exc).__name__)


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
        if external_service:
            if response not in {"checking", "resolved"}:
                raise ValueError("unsupported caregiver response")
            if source is not None and source != "matter":
                raise ValueError("external caregiver response requires matter source")
            requested = call.data.get("incident_id") or _selected_id(
                hass, "input_select.bapsim_active_assessment")
            active = [item for item in _active_assessments(hass, workflow, trend_workflow, True)
                      if (requested is None or item["incident_id"] == requested) and
                      (device_id is None or item["device_id"] == device_id)]
            if len(active) != 1:
                raise ValueError("Response requires exactly one current matter assessment")
            state = hass.states.get("sensor.bapsim_output_state")
            await set_notification_control(SimpleNamespace(data={
                "command_id": str(uuid.uuid4()), "action": "caregiver_response",
                "assessment_id": active[0]["incident_id"], "response": response,
                "result_valid_until": state.attributes["valid_until"],
            }))
            return
        selected_incident = None
        if source is None and device_id is None:
            selected = _selected_id(hass, "input_select.bapsim_active_assessment")
            active = [item for item in _active_assessments(hass, workflow, trend_workflow, False)
                      if selected is None or item.get("incident_id") == selected]
            if len(active) != 1:
                _LOGGER.warning("Caregiver response needs exactly one active incident or an explicit source and device_id")
                return
            source, device_id = active[0]["source"], active[0]["device_id"]
            selected_incident = active[0]["incident_id"]
        elif not isinstance(source, str) or not isinstance(device_id, str):
            raise ValueError("source and device_id must be supplied together")
        incident_id = call.data.get("incident_id") or selected_incident
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
        if command.get("action") == "caregiver_response":
            raise ValueError("Use bapsim.respond for internal caregiver responses")
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
        await refresh_dashboard_choices(hass, workflow, trend_workflow, controls, _LOGGER,
                                        external_service=external_service)
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
            active = _active_assessments(hass, workflow, trend_workflow, external_service)
            requested = call.data.get("assessment_id") or _selected_id(
                hass, "input_select.bapsim_active_assessment")
            if requested is not None:
                active = [item for item in active if item["incident_id"] == requested]
            if len(active) == 1:
                command["assessment_id"] = active[0]["incident_id"]
            if len(active) != 1:
                raise ValueError("Acknowledge requires exactly one active assessment")
        elif action == "pause_lifestyle":
            command.update({"pause_id": str(uuid.uuid4()), "starts_at": now.isoformat()})
        else:
            pause_id = call.data.get("pause_id") or _selected_id(
                hass, "input_select.bapsim_active_pause")
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
