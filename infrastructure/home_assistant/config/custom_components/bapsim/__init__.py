"""Bridge HA's raw event bus to the transport-independent cooker normalizer."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homeassistant.core import EVENT_HOMEASSISTANT_STARTED, EVENT_HOMEASSISTANT_STOP, Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval
from homeassistant.helpers.storage import Store

if (Path(__file__).resolve().parent / "runtime" / "domain").is_dir():
    from .runtime.domain.events.normalizer import CookerEventNormalizer, NormalizedCookerEvent
    from .runtime.domain.baseline import PersonalBaselineEngine
    from .runtime.domain.detection import RuleDetector
    from .runtime.domain.features import CookerFeatureExtractor
    from .runtime.domain.sessions import CookingSessionBuilder
    from .runtime.domain.personal_model import PersonalUsageModel
    from .runtime.workflow import DetectionStateManager, NotificationControls, WorkflowUpdate
    from .runtime.workflow.quiet_hours import QuietHours
    from .runtime.workflow.evidence_boundary import cooker_only
    from .runtime.inputs.matter import MatterBoardSignalAdapter, MatterContactAdapter, MatterContextAdapter, MatterCookingStateAdapter
    from .runtime.inputs.base.journal import RawEventJournal
    from .runtime.service import ObservationJournal, StatusOutput, AuxiliaryDeferral
else:
    from domain.events.normalizer import CookerEventNormalizer, NormalizedCookerEvent
    from domain.baseline import PersonalBaselineEngine
    from domain.detection import RuleDetector
    from domain.features import CookerFeatureExtractor
    from domain.sessions import CookingSessionBuilder
    from domain.personal_model import PersonalUsageModel
    from workflow import DetectionStateManager, NotificationControls, WorkflowUpdate
    from workflow.quiet_hours import QuietHours
    from workflow.evidence_boundary import cooker_only
    from inputs.matter import MatterBoardSignalAdapter, MatterContactAdapter, MatterContextAdapter, MatterCookingStateAdapter
    from inputs.base.journal import RawEventJournal
    from service import ObservationJournal, StatusOutput, AuxiliaryDeferral

DOMAIN = "bapsim"
RAW_EVENT = "bapsim_raw_event"
CONTEXT_RAW_EVENT = "bapsim_context_raw_event"
COOKER_EVENT = "bapsim_cooker_event"
SESSION_EVENT = "bapsim_session_event"
FEATURE_EVENT = "bapsim_feature_event"
BASELINE_EVENT = "bapsim_baseline_event"
DETECTION_EVENT = "bapsim_detection_event"
WORKFLOW_EVENT = "bapsim_workflow_event"
BOARD_HISTORY_LIMIT = 1000
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    normalizer = CookerEventNormalizer(raw_fields={
        (source, field): channel
        for source in ("simulator", "matter")
        for field, channel in (("lid_state", "lid"), ("cooking_state", "cooking"), ("availability", "availability"))
    })
    sessions = CookingSessionBuilder()
    features = CookerFeatureExtractor()
    baseline = PersonalBaselineEngine()
    detector = RuleDetector()
    usage_model = PersonalUsageModel()
    workflow = DetectionStateManager(candidate_window=timedelta(hours=36))
    trend_workflow = DetectionStateManager(min_consecutive=1, candidate_window=timedelta(days=1))
    controls = NotificationControls()
    quiet_hours = QuietHours()
    auxiliary = AuxiliaryDeferral()
    settings = config.get(DOMAIN) or {}
    if not isinstance(settings, dict):
        settings = {}
    external_service = bool(settings.get("external_service", False)) or os.environ.get("BAPSIM_EXTERNAL_SERVICE") == "1"
    matter_contacts: dict[str, MatterContactAdapter] = {}
    matter_cooking_states: dict[str, MatterCookingStateAdapter] = {}
    matter_contexts: dict[str, MatterContextAdapter] = {}
    matter_board_signals: dict[str, MatterBoardSignalAdapter] = {}
    matter_report_at: dict[str, datetime] = {}
    availability_by_device: dict[tuple[str, str], str] = {}
    forwarded_devices: set[tuple[str, str]] = set()
    latest_features: dict[tuple[str, str], dict[str, object]] = {}
    board_history: list[dict[str, str]] = []
    pending_mobile: dict[str, dict[str, object]] = {}
    pending_quiet: dict[str, dict[str, str]] = {}
    retrying_mobile: set[str] = set()
    away = False
    notification_target: str | None = None
    cancel_timer = None
    store = Store(hass, 1, "bapsim.pipeline", atomic_writes=True)
    journal = RawEventJournal(Path(hass.config.path("bapsim_events"))) if hasattr(hass, "config") else None
    observations = ObservationJournal(Path(hass.config.path("bapsim_observations"))) if hasattr(hass, "config") else None
    service_records = ObservationJournal(Path(hass.config.path("bapsim_records")), 180, "records") if hasattr(hass, "config") else None
    journal_lock = asyncio.Lock()
    mqtt_lock = asyncio.Lock()
    last_discovery_at = None
    last_observation_prune_day = None
    last_baseline_record_day: dict[tuple[str, str], str] = {}
    stored = await store.async_load()
    output = StatusOutput(str(settings.get("household_id", "household_01")),
                          int(stored.get("output_revision", 0)) if isinstance(stored, dict) else 0)
    if isinstance(stored, dict):
        try:
            normalizer.load_state(stored.get("normalizer", []))
            sessions.load_state(stored.get("sessions", []))
            features.load_state(stored.get("features", []))
            baseline.load_state(stored.get("baseline", []))
            usage_model.load_state(stored.get("usage_model", {}))
            workflow.load_state(stored.get("workflow", []))
            trend_workflow.load_state(stored.get("trend_workflow", []))
            controls.load_state(stored.get("notification_controls", {}))
            quiet_hours.configure(*stored.get("quiet_hours", ["22:00", "08:00"]))
            pending_quiet.update({item["incident_id"]: item for item in stored.get("pending_quiet", [])})
            auxiliary.load_state(stored.get("auxiliary", {}))
            for item in stored.get("matter_contacts", []):
                for entity_id, adapter in tuple(matter_contacts.items()):
                    if adapter.device_id == item["device_id"]:
                        matter_contacts.pop(entity_id)
                matter_contacts[item["entity_id"]] = MatterContactAdapter(item["device_id"], on_is_open=item["on_is_open"])
            for item in stored.get("matter_cooking_states", []):
                for entity_id, adapter in tuple(matter_cooking_states.items()):
                    if adapter.device_id == item["device_id"]:
                        matter_cooking_states.pop(entity_id)
                matter_cooking_states[item["entity_id"]] = MatterCookingStateAdapter(
                    item["device_id"], started_state=item["started_state"], finished_state=item["finished_state"]
                )
            for item in stored.get("matter_contexts", []):
                matter_contexts[item["entity_id"]] = MatterContextAdapter(
                    item["device_id"], signal=item["signal"]
                )
            for item in stored.get("matter_board_signals", []):
                matter_board_signals[item["entity_id"]] = MatterBoardSignalAdapter(
                    item["device_id"], kind=item["kind"], signal=item["signal"]
                )
            availability_by_device.update(
                ((item["source"], item["device_id"]), item["availability"])
                for item in stored.get("availability", [])
            )
            latest_features.update(
                ((item["source"], item["device_id"]), item)
                for item in stored.get("latest_features", [])
            )
            stored_board_history = stored.get("board_history", [])
            if not isinstance(stored_board_history, list):
                stored_board_history = []
            for item in stored_board_history[-BOARD_HISTORY_LIMIT:]:
                if isinstance(item, dict) and all(
                    isinstance(item.get(field), str) for field in (
                        "device_id", "event_type", "value", "observed_at", "received_at",
                        "source", "availability", "quality",
                    )
                ) and item["source"] == "matter" and item["event_type"].startswith("board_"):
                    board_history.append({key: item[key] for key in (
                        "device_id", "event_type", "value", "observed_at", "received_at",
                        "source", "availability", "quality",
                    )})
            away = bool(stored.get("away", False))
            if away:
                usage_model.set_away(True, datetime.now(timezone.utc).isoformat())
            notification_target = stored.get("notification_target")
            for item in stored.get("pending_mobile", []):
                if (isinstance(item, dict) and all(isinstance(item.get(key), str) for key in (
                    "source", "device_id", "incident_id", "message",
                )) and isinstance(item.get("reason_codes"), list)):
                    pending_mobile[item["incident_id"]] = item
        except (KeyError, TypeError, ValueError) as exc:
            _LOGGER.warning("Could not restore some Bapsim pipeline state: %s", exc)
    model_away = away or controls.is_paused(datetime.now(timezone.utc).isoformat())
    if model_away:
        usage_model.set_away(True, datetime.now(timezone.utc).isoformat())

    @callback
    def state_snapshot() -> dict[str, object]:
        return {
            "normalizer": normalizer.dump_state(),
            "sessions": sessions.dump_state(),
            "features": features.dump_state(),
            "baseline": baseline.dump_state(),
            "usage_model": usage_model.dump_state(),
            "workflow": workflow.dump_state(),
            "trend_workflow": trend_workflow.dump_state(),
            "notification_controls": controls.dump_state(),
            "quiet_hours": [quiet_hours.start, quiet_hours.end],
            "pending_quiet": list(pending_quiet.values()),
            "auxiliary": auxiliary.dump_state(),
            "output_revision": output.revision,
            "matter_contacts": [
                {"entity_id": entity_id, "device_id": adapter.device_id, "on_is_open": adapter.on_is_open}
                for entity_id, adapter in matter_contacts.items()
            ],
            "matter_cooking_states": [
                {"entity_id": entity_id, "device_id": adapter.device_id,
                 "started_state": adapter.started_state, "finished_state": adapter.finished_state}
                for entity_id, adapter in matter_cooking_states.items()
            ],
            "matter_contexts": [
                {"entity_id": entity_id, "device_id": adapter.device_id, "signal": adapter.signal}
                for entity_id, adapter in matter_contexts.items()
            ],
            "matter_board_signals": [
                {"entity_id": entity_id, "device_id": adapter.device_id,
                 "kind": adapter.kind, "signal": adapter.signal}
                for entity_id, adapter in matter_board_signals.items()
            ],
            "availability": [
                {"source": source, "device_id": device_id, "availability": value}
                for (source, device_id), value in availability_by_device.items()
            ],
            "latest_features": list(latest_features.values()),
            "board_history": board_history[-BOARD_HISTORY_LIMIT:],
            "away": away,
            "notification_target": notification_target,
            "pending_mobile": list(pending_mobile.values()),
        }

    @callback
    def save_later() -> None:
        store.async_delay_save(state_snapshot, 1)

    @callback
    def show(entity: str, value: object, attributes: dict[str, object] | None = None) -> None:
        # HA reserves "unknown" and "unavailable" as entity health states.
        display = {"unknown": "not_observed", "unavailable": "disconnected"}.get(str(value), str(value))
        labels = {
            "detection_status": "밥솥 단독 판정", "workflow_status": "보호자 확인 상태",
            "baseline": "개인 기준", "features": "현재 특징",
            "notification_delivery": "알림 전달 단계", "matter_input": "실물 밥솥 입력 준비",
        }
        hass.states.async_set(f"sensor.bapsim_{entity}", display, {"friendly_name": labels.get(entity, entity), **(attributes or {})})

    # Restore dashboard cards without treating old detections as new evidence.
    show("workflow_status", "normal")
    show("baseline", "waiting")
    show("detection_status", "waiting")
    show("features", "waiting")
    show("notification_delivery", "not_sent")
    show("matter_input", "not_configured")
    show("matter_board", "not_configured")
    show("board_history", len(board_history), {"latest": board_history[-3:]})
    show("away", "away" if away else "home")
    show("notification_controls", controls.revision, controls.dump_state())

    async def send_mqtt(source: str, payload: dict[str, object]) -> None:
        async with mqtt_lock:
            if payload["revision"] < output.revision:
                return
            await send_mqtt_current(source, payload)

    async def send_mqtt_current(source: str, payload: dict[str, object]) -> None:
        nonlocal last_discovery_at
        if not hass.services.has_service("mqtt", "publish"):
            show("mqtt_output", "broker_unavailable", {"topic": output.topic(source, "state")})
            return
        state_topic = output.topic(source, "state")
        availability_topic = output.topic(source, "availability")
        try:
            publish_discovery = source == "matter" and (
                last_discovery_at is None or
                datetime.now(timezone.utc) - last_discovery_at >= timedelta(hours=1)
            )
            if publish_discovery:
                discovery = {
                    "name": "밥심 현재 안내", "unique_id": f"bapsim_{output.household_id}_summary",
                    "state_topic": state_topic, "value_template": "{{ value_json.summary }}",
                    "json_attributes_topic": state_topic, "availability_topic": availability_topic,
                    "expire_after": 180,
                }
                await hass.services.async_call("mqtt", "publish", {
                    "topic": f"homeassistant/sensor/bapsim_{output.household_id}/config",
                    "payload": json.dumps(discovery, ensure_ascii=False), "qos": 1, "retain": True,
                }, blocking=True)
                last_discovery_at = datetime.now(timezone.utc)
            for topic, value in ((availability_topic, "online" if payload["observation"] != "unavailable" else "offline"),
                                 (state_topic, json.dumps(payload, ensure_ascii=False))):
                await hass.services.async_call("mqtt", "publish", {
                    "topic": topic, "payload": value, "qos": 1, "retain": True,
                }, blocking=True)
        except Exception:
            _LOGGER.exception("Bapsim MQTT status publish failed")
            show("mqtt_output", "failed", {"topic": state_topic})
        else:
            show("mqtt_output", "published", {"topic": state_topic, "revision": payload["revision"]})

    async def send_controls() -> None:
        async with mqtt_lock:
            try:
                await hass.services.async_call("mqtt", "publish", {
                    "topic": output.topic("matter", "notification_controls"),
                    "payload": json.dumps({"schema_version": "0.1.0", "household_id": output.household_id,
                                           "revision": controls.revision, "pauses": controls.pauses,
                                           "acknowledgements": controls.acknowledgements}, ensure_ascii=False),
                    "qos": 1, "retain": True,
                }, blocking=True)
            except Exception:
                _LOGGER.exception("Bapsim MQTT controls publish failed")
    for item in workflow.dump_state():
        show("workflow_status", item["status"], item)
    for item in trend_workflow.dump_state():
        show("decline_status", item["status"], item)
    for source, device_id in usage_model.device_keys:
        profile = usage_model.profile(source, device_id, datetime.now(timezone.utc).isoformat())
        show("baseline", "ready" if profile["ready"] else "learning", profile)
    for snapshot in latest_features.values():
        show("features", "available" if snapshot.get("availability") == "online" else "unavailable", snapshot)

    @callback
    def publish_workflow(update) -> None:
        payload = update.as_dict()
        archive_service_record("workflow", payload, update.as_of)
        if update.status == "resolved" and update.incident_id in pending_mobile:
            pending_mobile.pop(update.incident_id, None)
        if update.status == "resolved" and update.incident_id in pending_quiet:
            pending_quiet.pop(update.incident_id, None)
        hass.bus.async_fire(WORKFLOW_EVENT, payload)
        if "DECLINING_USAGE" in update.reason_codes or "DECLINE_RESOLVED" in update.reason_codes:
            show("decline_status", update.status, payload)
        else:
            show("workflow_status", update.status, payload)
        save_later()

    async def create_notification(update, manager=workflow) -> None:
        notification_time = datetime.now(timezone.utc).isoformat()
        if controls.is_paused(notification_time) or controls.is_acknowledged(update.incident_id, notification_time):
            show("notification_delivery", "paused", {"incident_id": update.incident_id})
            return
        if quiet_hours.is_quiet(notification_time):
            pending_quiet[str(update.incident_id)] = {
                "incident_id": str(update.incident_id), "source": update.source,
                "device_id": update.device_id,
            }
            show("notification_delivery", "quiet_hours", {
                "incident_id": update.incident_id,
                "next_delivery_at": quiet_hours.next_delivery_at(notification_time),
            })
            save_later()
            return
        pending_quiet.pop(str(update.incident_id), None)
        declining = "DECLINING_USAGE" in update.reason_codes
        title = "밥심 — 최근 사용 감소" if declining else "밥심 — 안부 확인 필요"
        message = ("최근 밥솥 사용 기록이 평소보다 줄었습니다. 안부를 확인해 보세요."
                   if declining else "평소와 다른 밥솥 사용 패턴이 관측되었습니다. 안부를 확인해 보세요.")
        try:
            await hass.services.async_call(
                "persistent_notification", "create",
                {
                    "title": title,
                    "message": message,
                    "notification_id": "bapsim_" + str(update.incident_id),
                },
                blocking=True,
            )
        except Exception:
            _LOGGER.exception("Bapsim local notification failed")
            return
        delivery = "ha_local_created"
        if notification_target and update.source != "simulator":
            actions = [
                {"action": f"BAPSIM|{response}|{update.source}|{update.device_id}|{update.incident_id}", "title": label}
                for response, label in (("checking", "확인 중"), ("resolved", "안부 확인 완료"))
            ]
            try:
                await hass.services.async_call("notify", notification_target, {
                    "title": title, "message": message, "data": {"actions": actions},
                }, blocking=True)
                delivery = "mobile_service_called"
            except Exception:
                _LOGGER.exception("Bapsim mobile notification service failed")
                pending_mobile[str(update.incident_id)] = {
                    "source": update.source, "device_id": update.device_id,
                    "incident_id": str(update.incident_id), "message": message,
                    "reason_codes": list(update.reason_codes),
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
                }
                delivery = "mobile_retry_pending"
                save_later()
        show("notification_delivery", delivery, {"incident_id": update.incident_id, "as_of": datetime.now(timezone.utc).isoformat()})
        archive_service_record("notification", {
            "incident_id": update.incident_id, "delivery": delivery,
            "reason_codes": list(update.reason_codes),
        }, datetime.now(timezone.utc).isoformat())
        sent = manager.mark_notified(update.source, update.device_id, datetime.now(timezone.utc).isoformat())
        if sent is not None:
            publish_workflow(sent)

    async def retry_mobile_notifications() -> None:
        if not notification_target:
            return
        for incident_id, item in tuple(pending_mobile.items()):
            if incident_id in retrying_mobile:
                continue
            now = datetime.now(timezone.utc).isoformat()
            if (controls.is_paused(now) or controls.is_acknowledged(incident_id, now)
                    or (item.get("expires_at") and datetime.fromisoformat(item["expires_at"]) <= datetime.now(timezone.utc))):
                pending_mobile.pop(incident_id, None)
                save_later()
                continue
            current = next((state for state in (*workflow.dump_state(), *trend_workflow.dump_state())
                            if state["source"] == item["source"] and state["device_id"] == item["device_id"]), None)
            if current is None or current.get("incident_id") != incident_id or current.get("status") not in {"notified", "checking"}:
                pending_mobile.pop(incident_id, None)
                save_later()
                continue
            retrying_mobile.add(incident_id)
            actions = [
                {"action": f"BAPSIM|{response}|{item['source']}|{item['device_id']}|{incident_id}", "title": label}
                for response, label in (("checking", "확인 중"), ("resolved", "안부 확인 완료"))
            ]
            try:
                await hass.services.async_call("notify", notification_target, {
                    "title": "밥심 — 안부 확인 필요", "message": item["message"],
                    "data": {"actions": actions},
                }, blocking=True)
            except Exception:
                _LOGGER.warning("Bapsim mobile notification retry failed for %s", incident_id)
            else:
                pending_mobile.pop(incident_id, None)
                show("notification_delivery", "mobile_service_called", {
                    "incident_id": incident_id, "as_of": datetime.now(timezone.utc).isoformat(),
                })
                save_later()
            finally:
                retrying_mobile.discard(incident_id)

    @callback
    def evaluate_model(source: str, device_id: str, as_of: str) -> None:
        nonlocal model_away
        effective_away = away or controls.is_paused(as_of)
        if effective_away != model_away:
            usage_model.set_away(effective_away, as_of)
            model_away = effective_away
        profile = usage_model.profile(source, device_id, as_of)
        record_day = datetime.fromisoformat(as_of.replace("Z", "+00:00")).date().isoformat()
        if last_baseline_record_day.get((source, device_id)) != record_day:
            archive_service_record("baseline", profile, as_of)
            last_baseline_record_day[(source, device_id)] = record_day
        hass.bus.async_fire(BASELINE_EVENT, profile)
        show("baseline", "ready" if profile["ready"] else "learning", profile)
        online = availability_by_device.get((source, device_id)) == "online"
        if not online or effective_away:
            status = "away" if effective_away else "data_unavailable"
            reason = "AWAY_DECLARED" if effective_away else "COOKER_DATA_UNAVAILABLE"
            provisional = {
                "source": source, "device_id": device_id, "as_of": as_of,
                "routine_status": status, "severity": "info", "reason_codes": [reason],
                "score": None, "evidence": {"model": "personal_usage_v1"},
            }
            hass.bus.async_fire(DETECTION_EVENT, provisional)
            show("detection_status", status, provisional)
            for update in workflow.ingest(provisional):
                publish_workflow(update)
            if effective_away:
                for update in trend_workflow.ingest(provisional):
                    publish_workflow(update)
        decisions = usage_model.evaluate(
            source, device_id, as_of,
            online=online,
            away=effective_away,
        )
        for decision in decisions:
            archive_service_record("assessment", decision, decision["as_of"])
            if decision["routine_status"] == "check_required":
                for item in workflow.dump_state():
                    if (item["source"], item["device_id"]) == (source, device_id) and item["status"] == "checking":
                        incident_id = item.get("incident_id")
                        if incident_id in controls.acknowledgements and not controls.is_acknowledged(incident_id, as_of):
                            resolved = workflow.caregiver_response(source, device_id, "resolved", as_of, incident_id)
                            if resolved is not None:
                                publish_workflow(resolved)
            hass.bus.async_fire(DETECTION_EVENT, decision)
            show("detection_status", decision["routine_status"], decision)
            if auxiliary.defer(decision):
                show("auxiliary_deferral", "deferred", {"device_id": device_id,
                                                       "until": auxiliary.pending[-1]["due_at"]})
                continue
            for update in workflow.ingest(cooker_only(decision)):
                publish_workflow(update)
                if update.action == "notify_requested":
                    hass.async_create_task(create_notification(update))
        interval = usage_model.evaluate_interval(source, device_id, as_of, online=online, away=effective_away)
        if interval is not None:
            archive_service_record("assessment", interval, as_of)
            if interval["routine_status"] in {"watch", "check_required", "normal"}:
                hass.bus.async_fire(DETECTION_EVENT, interval)
                show("interval_detection", interval["routine_status"], interval)
            if interval["routine_status"] in {"normal", "check_required"}:
                for update in workflow.ingest(cooker_only(interval)):
                    publish_workflow(update)
                    if update.action == "notify_requested":
                        hass.async_create_task(create_notification(update))
        trend = usage_model.evaluate_trend(source, device_id, as_of, online=online, away=effective_away)
        if trend is not None:
            archive_service_record("assessment", trend, trend["as_of"])
            hass.bus.async_fire(DETECTION_EVENT, trend)
            show("decline_detection", trend["routine_status"], trend)
            for update in trend_workflow.ingest(cooker_only(trend)):
                publish_workflow(update)
                if update.action == "notify_requested":
                    hass.async_create_task(create_notification(update, trend_workflow))
        gap_state = next((item for item in workflow.dump_state()
                          if item["source"] == source and item["device_id"] == device_id), None)
        decline_state = next((item for item in trend_workflow.dump_state()
                              if item["source"] == source and item["device_id"] == device_id), None)
        state = output.build(as_of, profile, "available" if online else "unavailable",
                             gap_state, decline_state)
        show("output_state", state["summary"], state)
        if hass.services.has_service("mqtt", "publish"):
            hass.async_create_task(send_mqtt(source, state))
        else:
            show("mqtt_output", "broker_unavailable", {"topic": output.topic(source, "state")})
        save_later()

    @callback
    def publish(events: list[NormalizedCookerEvent]) -> None:
        for event in events:
            hass.bus.async_fire(COOKER_EVENT, event.as_dict())

    @callback
    def on_deadline(now) -> None:
        nonlocal cancel_timer
        cancel_timer = None
        publish(normalizer.flush(now))
        schedule()

    @callback
    def schedule() -> None:
        nonlocal cancel_timer
        if cancel_timer is not None:
            cancel_timer()
            cancel_timer = None
        deadline = normalizer.next_deadline
        if deadline is not None:
            cancel_timer = async_track_point_in_utc_time(hass, on_deadline, deadline)

    @callback
    def archive_raw(stream: str, payload: dict[str, object]) -> None:
        if journal is not None:
            hass.async_create_task(write_raw(stream, dict(payload)))

    async def write_raw(stream: str, payload: dict[str, object]) -> None:
        try:
            async with journal_lock:
                await hass.async_add_executor_job(journal.append, stream, payload)
        except (OSError, ValueError) as exc:
            _LOGGER.warning("Could not archive raw %s event: %s", stream, exc)

    async def write_observation(payload: dict[str, object]) -> None:
        try:
            async with journal_lock:
                await hass.async_add_executor_job(observations.append, payload)
        except (OSError, ValueError, TypeError) as exc:
            _LOGGER.warning("Could not archive normalized observation: %s", exc)

    @callback
    def archive_service_record(category: str, payload: dict[str, object], at: str) -> None:
        if service_records is not None:
            hass.async_create_task(write_service_record({"category": category, "as_of": at, **payload}))

    async def write_service_record(payload: dict[str, object]) -> None:
        try:
            async with journal_lock:
                await hass.async_add_executor_job(service_records.append, payload)
        except (OSError, ValueError, TypeError) as exc:
            _LOGGER.warning("Could not archive service record: %s", exc)

    async def prune_observations(now: datetime) -> None:
        try:
            async with journal_lock:
                await hass.async_add_executor_job(observations.prune, now)
                if service_records is not None:
                    await hass.async_add_executor_job(service_records.prune, now)
        except OSError as exc:
            _LOGGER.warning("Could not prune normalized observations: %s", exc)

    @callback
    def on_raw(event: Event) -> None:
        nonlocal away
        if event.data.get("event_type") == "away_state" and event.data.get("source") == "simulator":
            value = event.data.get("value")
            if value in {"away", "home"}:
                away = value == "away"
                usage_model.set_away(away, str(event.data.get("received_at")))
                show("away", value)
                show("detection_status", "away" if away else "monitoring", {
                    "model": "personal_usage_v1", "as_of": event.data.get("received_at"),
                })
                if away:
                    for source, device_id in usage_model.device_keys:
                        for update in workflow.ingest({
                            "source": source, "device_id": device_id,
                            "as_of": str(event.data.get("received_at")),
                            "routine_status": "away", "reason_codes": [],
                        }):
                            publish_workflow(update)
                save_later()
        archive_raw("cooker", event.data)
        if event.data.get("event_type") == "away_state":
            return
        try:
            publish(normalizer.ingest(event.data))
        except ValueError as exc:
            _LOGGER.warning("Ignored invalid raw cooker event: %s", exc)
            return
        if (event.data.get("source") == "matter" and
                isinstance(event.data.get("event_type"), str) and
                event.data["event_type"].startswith("board_") and
                all(isinstance(event.data.get(field), str) for field in (
                    "device_id", "event_type", "value", "observed_at", "received_at",
                    "source", "availability", "quality",
                ))):
            board_history.append({key: event.data[key] for key in (
                "device_id", "event_type", "value", "observed_at", "received_at",
                "source", "availability", "quality",
            )})
            del board_history[:-BOARD_HISTORY_LIMIT]
            show("board_history", len(board_history), {"latest": board_history[-3:]})
        schedule()
        save_later()

    unsubscribe = hass.bus.async_listen(RAW_EVENT, on_raw)

    @callback
    def archive_context(event: Event) -> None:
        archive_raw("context", event.data)

    unsubscribe_context = hass.bus.async_listen(CONTEXT_RAW_EVENT, archive_context)

    @callback
    def on_cooker(event: Event) -> None:
        source = event.data.get("source")
        device_id = event.data.get("device_id")
        observed_at = event.data.get("observed_at")
        if not all(isinstance(item, str) for item in (source, device_id, observed_at)):
            _LOGGER.warning("Ignored canonical cooker event with missing identity or time")
            return
        if observations is not None:
            hass.async_create_task(write_observation(dict(event.data)))
        if external_service:
            forwarded_devices.add((source, device_id))
            if hass.services.has_service("mqtt", "publish"):
                hass.async_create_task(hass.services.async_call("mqtt", "publish", {
                    "topic": output.topic(source, "observations"),
                    "payload": json.dumps(dict(event.data), ensure_ascii=False),
                    "qos": 1, "retain": False,
                }, blocking=True))
                show("mqtt_output", "forwarding", {"topic": output.topic(source, "observations")})
            else:
                show("mqtt_output", "broker_unavailable", {"topic": output.topic(source, "observations")})
            return
        key = (source, device_id)
        prior_groups = {
            opening for item in usage_model.dump_state()["devices"]
            if (item["source"], item["device_id"]) == key for opening in item["opens"]
        } if event.data.get("event_type") == "lid_opened" else set()
        if event.data.get("event_type") in {"device_online", "device_offline"}:
            availability_by_device[key] = event.data["value"]
        try:
            usage_model.observe(event.data)
        except ValueError as exc:
            _LOGGER.warning("Ignored invalid usage-model event: %s", exc)
        if event.data.get("event_type") == "lid_opened":
            for update in workflow.ingest({
                "source": source, "device_id": device_id, "as_of": observed_at,
                "routine_status": "normal", "reason_codes": ["USAGE_RESUMED"],
            }):
                publish_workflow(update)
            for item in usage_model.dump_state()["devices"]:
                if (item["source"], item["device_id"]) == key:
                    for opening in set(item["opens"]) - prior_groups:
                        archive_service_record("usage_group", {
                            "source": source, "device_id": device_id, "group_started_at": opening,
                        }, opening)
        try:
            updates = sessions.ingest(event.data)
        except ValueError as exc:
            _LOGGER.warning("Ignored invalid canonical cooker event: %s", exc)
            return
        for update in updates:
            snapshot = update.as_dict()
            features.ingest(snapshot)
            hass.bus.async_fire(SESSION_EVENT, {**snapshot, "as_of": observed_at})
        if updates or event.data.get("event_type") in {"device_online", "device_offline"}:
            snapshot = features.extract(
                source, device_id, observed_at, availability_by_device.get(key, "unknown")
            )
            payload = snapshot.as_dict()
            latest_features[key] = payload
            hass.bus.async_fire(FEATURE_EVENT, payload)
            show("features", "available" if payload.get("availability") == "online" else "unavailable", payload)
            save_later()
        if event.data.get("event_type") in {"device_online", "device_offline", "lid_opened"}:
            evaluate_model(source, device_id, observed_at)

    unsubscribe_sessions = hass.bus.async_listen(COOKER_EVENT, on_cooker)

    @callback
    def matter_bindings(device_id: str) -> list[tuple[str, MatterContactAdapter | MatterCookingStateAdapter]]:
        return [
            (entity_id, adapter)
            for entity_id, adapter in (*matter_contacts.items(), *matter_cooking_states.items())
            if adapter.device_id == device_id
        ]

    @callback
    def is_matter_entity(entity_id: str) -> bool:
        entry = er.async_get(hass).async_get(entity_id)
        return entry is not None and entry.platform == "matter"

    @callback
    def matter_inputs_available(device_id: str) -> bool:
        bindings = matter_bindings(device_id)
        return any(
            isinstance(adapter, MatterContactAdapter)
            and is_matter_entity(entity_id)
            and (state := hass.states.get(entity_id)) is not None
            and state.state in {"on", "off"}
            for entity_id, adapter in bindings
        )

    @callback
    def matter_cooking_available(device_id: str) -> bool:
        return any(
            isinstance(adapter, MatterCookingStateAdapter)
            and is_matter_entity(entity_id)
            and (state := hass.states.get(entity_id)) is not None
            and state.state in {adapter.started_state, adapter.finished_state}
            for entity_id, adapter in matter_bindings(device_id)
        )

    @callback
    def show_matter_readiness() -> None:
        device_ids = sorted({
            adapter.device_id for adapter in (*matter_contacts.values(), *matter_cooking_states.values())
        })
        devices = []
        for device_id in device_ids:
            lids = [entity_id for entity_id, adapter in matter_contacts.items() if adapter.device_id == device_id]
            cooking = [entity_id for entity_id, adapter in matter_cooking_states.items() if adapter.device_id == device_id]
            lid_state = hass.states.get(lids[0]) if lids else None
            cooking_state = hass.states.get(cooking[0]) if cooking else None
            cooking_adapter = matter_cooking_states[cooking[0]] if cooking else None
            devices.append({
                "device_id": device_id, "lid_entity": lids[0] if lids else None,
                "cooking_entity": cooking[0] if cooking else None,
                "lid_state": lid_state.state if lid_state is not None else None,
                "cooking_state": cooking_state.state if cooking_state is not None else None,
                "lid_state_unmapped": (
                    lid_state is not None and lid_state.state not in {
                        "on", "off", "unknown", "unavailable",
                    }
                ),
                "cooking_state_unmapped": (
                    cooking_state is not None and cooking_adapter is not None
                    and cooking_state.state not in {
                        cooking_adapter.started_state, cooking_adapter.finished_state,
                        "unknown", "unavailable",
                    }
                ),
                "inputs_available": matter_inputs_available(device_id),
                "cooking_available": matter_cooking_available(device_id),
                "source_verified": all(is_matter_entity(entity_id) for entity_id, _ in matter_bindings(device_id)),
            })
        if not devices:
            status = "not_configured"
        elif any(not item["lid_entity"] for item in devices):
            status = "missing_input"
        elif any(not is_matter_entity(item["lid_entity"]) for item in devices):
            status = "source_unverified"
        elif any(item["lid_state_unmapped"] for item in devices):
            status = "state_unmapped"
        elif any(not item["inputs_available"] for item in devices):
            status = "entity_unavailable"
        elif any(not item["cooking_entity"] for item in devices):
            status = "lid_only_ready"
        elif any(not item["source_verified"] for item in devices):
            status = "partial_source_unverified"
        elif any(item["cooking_state_unmapped"] for item in devices):
            status = "partial_state_unmapped"
        elif any(not item["cooking_available"] for item in devices):
            status = "partial_unavailable"
        else:
            status = "ready"
        show("matter_input", status, {"devices": devices})

    @callback
    def next_matter_time(device_id: str) -> str:
        observed = datetime.now(timezone.utc)
        previous = matter_report_at.get(device_id)
        if previous is not None and observed <= previous:
            observed = previous + timedelta(microseconds=1)
        matter_report_at[device_id] = observed
        return observed.isoformat()

    @callback
    def publish_matter_offline(device_id: str) -> None:
        received_at = next_matter_time(device_id)
        hass.bus.async_fire(RAW_EVENT, {
            "device_id": device_id, "event_type": "availability", "value": "offline",
            "observed_at": received_at, "received_at": received_at, "source": "matter",
            "availability": "offline", "quality": "unknown",
        })

    @callback
    def publish_cooking_input_offline(device_id: str) -> None:
        received_at = next_matter_time(device_id)
        hass.bus.async_fire(RAW_EVENT, {
            "device_id": device_id, "event_type": "cooking_input_availability", "value": "offline",
            "observed_at": received_at, "received_at": received_at, "source": "matter",
            "availability": "offline", "quality": "unknown",
        })

    @callback
    def forward_matter_state(entity_id: str, state, *, snapshot: bool = False) -> None:
        adapter = matter_contacts.get(entity_id) or matter_cooking_states.get(entity_id)
        if adapter is None:
            return
        if not matter_inputs_available(adapter.device_id):
            publish_matter_offline(adapter.device_id)
            show_matter_readiness()
            return

        def translate_bound(bound_entity, bound_adapter, current, received_at):
            if isinstance(bound_adapter, MatterContactAdapter):
                return bound_adapter.translate(current.state, received_at, received_at)
            if not is_matter_entity(bound_entity) or current is None:
                return bound_adapter.translate("unavailable", received_at, received_at)
            try:
                return bound_adapter.translate(current.state, received_at, received_at)
            except ValueError:
                # Keep an unrecognized cooking state raw without promoting it
                # to a confirmed cooking transition or disconnecting the lid.
                return {
                    "device_id": bound_adapter.device_id,
                    "event_type": "cooking_input_state", "value": current.state,
                    "observed_at": received_at, "received_at": received_at,
                    "source": "matter", "availability": "online", "quality": "observed",
                }
        received_at = next_matter_time(adapter.device_id)
        if snapshot or availability_by_device.get(("matter", adapter.device_id)) != "online":
            # A current-state read establishes both raw channels after registration/recovery.
            for bound_entity, bound_adapter in matter_bindings(adapter.device_id):
                current = hass.states.get(bound_entity)
                raw = translate_bound(bound_entity, bound_adapter, current, received_at)
                hass.bus.async_fire(RAW_EVENT, raw)
            show_matter_readiness()
            return
        raw = translate_bound(entity_id, adapter, state, received_at)
        hass.bus.async_fire(RAW_EVENT, raw)
        show_matter_readiness()

    @callback
    def show_context_readiness() -> None:
        devices = []
        for entity_id, adapter in sorted(matter_contexts.items()):
            current = hass.states.get(entity_id)
            verified = is_matter_entity(entity_id)
            devices.append({
                "device_id": adapter.device_id, "entity_id": entity_id,
                "signal": adapter.signal,
                "state": current.state if current is not None else None,
                "availability": (
                    "online" if verified and current is not None
                    and current.state not in {"unknown", "unavailable"} else "offline"
                ),
                "source_verified": verified,
            })
        if not devices:
            status = "not_configured"
        elif any(not item["source_verified"] for item in devices):
            status = "source_unverified"
        elif any(item["availability"] != "online" for item in devices):
            status = "entity_unavailable"
        else:
            status = "ready"
        show("matter_context", status, {"devices": devices})

    @callback
    def forward_context_state(entity_id: str, state) -> None:
        adapter = matter_contexts.get(entity_id)
        if adapter is None:
            return
        received_at = datetime.now(timezone.utc).isoformat()
        value = state.state if is_matter_entity(entity_id) and state is not None else "unavailable"
        hass.bus.async_fire(CONTEXT_RAW_EVENT, adapter.translate(value, received_at, received_at))
        show_context_readiness()

    @callback
    def show_board_readiness() -> None:
        signals = []
        for entity_id, adapter in sorted(matter_board_signals.items()):
            current = hass.states.get(entity_id)
            verified = is_matter_entity(entity_id)
            signals.append({
                "device_id": adapter.device_id, "entity_id": entity_id,
                "kind": adapter.kind, "signal": adapter.signal,
                "state": current.state if current is not None else None,
                "source_verified": verified,
                "availability": (
                    "online" if verified and current is not None
                    and current.state not in {"unknown", "unavailable"} else "offline"
                ),
            })
        if not signals:
            status = "not_configured"
        elif any(not item["source_verified"] for item in signals):
            status = "source_unverified"
        elif any(item["availability"] != "online" for item in signals):
            status = "partially_unavailable"
        else:
            status = "ready"
        show("matter_board", status, {"signals": signals})

    @callback
    def forward_board_state(entity_id: str, state) -> None:
        adapter = matter_board_signals.get(entity_id)
        if adapter is None:
            return
        received_at = datetime.now(timezone.utc).isoformat()
        value = state.state if is_matter_entity(entity_id) and state is not None else "unavailable"
        hass.bus.async_fire(RAW_EVENT, adapter.translate(value, received_at, received_at))
        show_board_readiness()

    @callback
    def on_matter_state(event: Event) -> None:
        entity_id = event.data.get("entity_id")
        state = event.data.get("new_state")
        previous = event.data.get("old_state")
        if entity_id in matter_board_signals and (
            state is None or previous is None or previous.state != state.state
        ):
            forward_board_state(entity_id, state)
        if entity_id in matter_contexts:
            if state is None or previous is None or previous.state != state.state:
                forward_context_state(entity_id, state)
            return
        if state is None:
            forward_matter_state(entity_id, None)
            return
        if previous is not None and previous.state == state.state:
            return
        forward_matter_state(entity_id, state)

    unsubscribe_matter = hass.bus.async_listen("state_changed", on_matter_state)

    @callback
    def on_matter_registry_change(event: Event) -> None:
        entity_id = event.data.get("entity_id")
        if entity_id in matter_board_signals:
            forward_board_state(entity_id, hass.states.get(entity_id))
        if entity_id in matter_contexts:
            forward_context_state(entity_id, hass.states.get(entity_id))
            return
        adapter = matter_contacts.get(entity_id) or matter_cooking_states.get(entity_id)
        if adapter is None:
            return
        if matter_inputs_available(adapter.device_id):
            forward_matter_state(entity_id, hass.states.get(entity_id), snapshot=True)
        else:
            publish_matter_offline(adapter.device_id)
            show_matter_readiness()

    unsubscribe_registry = hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, on_matter_registry_change)

    @callback
    def on_started(event: Event) -> None:
        for device_id in {adapter.device_id for adapter in (*matter_contacts.values(), *matter_cooking_states.values())}:
            entity_id = matter_bindings(device_id)[0][0]
            forward_matter_state(entity_id, hass.states.get(entity_id), snapshot=True)
        for entity_id in matter_contexts:
            forward_context_state(entity_id, hass.states.get(entity_id))
        for entity_id in matter_board_signals:
            forward_board_state(entity_id, hass.states.get(entity_id))
        for source, device_id in usage_model.device_keys:
            if source == "matter":
                online = matter_inputs_available(device_id)
            elif source == "simulator":
                current = hass.states.get("input_select.bapsim_availability")
                online = current is not None and current.state == "online"
            else:
                online = False
            availability_by_device[(source, device_id)] = "online" if online else "offline"
            usage_model.availability(source, device_id, online, datetime.now(timezone.utc).isoformat())
        if pending_mobile and notification_target:
            hass.async_create_task(retry_mobile_notifications())

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, on_started)

    @callback
    def on_hourly_tick(now) -> None:
        nonlocal last_observation_prune_day
        as_of = now.isoformat()
        if external_service:
            if hass.services.has_service("mqtt", "publish"):
                for source, device_id in tuple(forwarded_devices):
                    online = (matter_inputs_available(device_id) if source == "matter" else
                              (hass.states.get("input_select.bapsim_availability") is not None and
                               hass.states.get("input_select.bapsim_availability").state == "online"))
                    heartbeat = {"source": source, "device_id": device_id,
                                 "event_type": "device_online" if online else "device_offline",
                                 "observed_at": as_of,
                                 "availability": "online" if online else "offline"}
                    hass.async_create_task(hass.services.async_call("mqtt", "publish", {
                        "topic": output.topic(source, "observations"),
                        "payload": json.dumps(heartbeat), "qos": 1, "retain": False,
                    }, blocking=True))
            return
        if not quiet_hours.is_quiet(as_of) and pending_quiet:
            active = {item["incident_id"]: item for item in
                      (*workflow.dump_state(), *trend_workflow.dump_state())
                      if item["incident_id"] and item["status"] == "check_required"}
            for incident_id, item in tuple(pending_quiet.items()):
                current = active.get(incident_id)
                pending_quiet.pop(incident_id, None)
                if (current is None or away or controls.is_paused(as_of) or
                        availability_by_device.get((item["source"], item["device_id"])) != "online"):
                    continue
                manager = trend_workflow if "DECLINING_USAGE" in current["reason_codes"] else workflow
                update = WorkflowUpdate(item["device_id"], item["source"], incident_id,
                                        "check_required", "notify_requested", as_of,
                                        tuple(current["reason_codes"]))
                hass.async_create_task(create_notification(update, manager))
            save_later()
        if away or controls.is_paused(as_of):
            auxiliary.clear()
        else:
            for decision in auxiliary.due(as_of):
                key = (decision["source"], decision["device_id"])
                if availability_by_device.get(key) != "online":
                    continue
                latest_open = usage_model.profile(*key, as_of)["last_lid_open_at"]
                if latest_open and datetime.fromisoformat(latest_open) > datetime.fromisoformat(decision["deferred_from"]):
                    continue
                show("auxiliary_deferral", "expired", {"device_id": decision["device_id"]})
                for update in workflow.ingest(cooker_only(decision)):
                    publish_workflow(update)
                    if update.action == "notify_requested":
                        hass.async_create_task(create_notification(update))
        if observations is not None and last_observation_prune_day != now.date():
            last_observation_prune_day = now.date()
            hass.async_create_task(prune_observations(now))
        for source, device_id in tuple(latest_features):
            if source == "simulator":
                state = hass.states.get("input_select.bapsim_availability")
                availability = "online" if state is not None and state.state == "online" else "offline"
            elif source == "matter":
                availability = "online" if matter_inputs_available(device_id) else "offline"
            else:
                availability = "offline"
            payload = features.extract(source, device_id, as_of, availability).as_dict()
            latest_features[(source, device_id)] = payload
            show("features", "available" if availability == "online" else "unavailable", payload)
            hass.bus.async_fire(FEATURE_EVENT, payload)
            evaluate_model(source, device_id, as_of)
        for source, device_id in usage_model.device_keys:
            if (source, device_id) not in latest_features:
                evaluate_model(source, device_id, as_of)
        if pending_mobile and notification_target:
            hass.async_create_task(retry_mobile_notifications())

    unsubscribe_tick = async_track_time_interval(hass, on_hourly_tick, timedelta(seconds=60))

    async def register_matter_contact(call) -> None:
        device_id = call.data["device_id"]
        entity_id = call.data["entity_id"]
        if not is_matter_entity(entity_id):
            raise ValueError("Contact entity must belong to the Matter integration")
        if entity_id in matter_cooking_states or entity_id in matter_contexts:
            raise ValueError("One HA entity cannot have multiple Bapsim input roles")
        if entity_id in matter_contacts and matter_contacts[entity_id].device_id != device_id:
            raise ValueError("HA entity already belongs to another logical cooker")
        replacement = MatterContactAdapter(device_id, on_is_open=call.data["on_is_open"])
        existing = [(key, adapter) for key, adapter in matter_contacts.items() if adapter.device_id == device_id]
        if existing and (len(existing) != 1 or existing[0][0] != entity_id or
                         existing[0][1].on_is_open != replacement.on_is_open):
            publish_matter_offline(device_id)
        for key, _ in existing:
            matter_contacts.pop(key)
        matter_contacts[entity_id] = replacement
        forward_matter_state(entity_id, hass.states.get(entity_id), snapshot=True)
        save_later()

    hass.services.async_register(DOMAIN, "register_matter_contact", register_matter_contact)

    async def register_matter_cooking_state(call) -> None:
        device_id = call.data["device_id"]
        entity_id = call.data["entity_id"]
        if not is_matter_entity(entity_id):
            raise ValueError("Cooking entity must belong to the Matter integration")
        if entity_id in matter_contacts or entity_id in matter_contexts:
            raise ValueError("One HA entity cannot have multiple Bapsim input roles")
        if entity_id in matter_cooking_states and matter_cooking_states[entity_id].device_id != device_id:
            raise ValueError("HA entity already belongs to another logical cooker")
        replacement = MatterCookingStateAdapter(
            device_id, started_state=call.data["started_state"],
            finished_state=call.data["finished_state"]
        )
        existing = [(key, adapter) for key, adapter in matter_cooking_states.items() if adapter.device_id == device_id]
        if existing and (len(existing) != 1 or existing[0][0] != entity_id or
                         existing[0][1].started_state != replacement.started_state or
                         existing[0][1].finished_state != replacement.finished_state):
            publish_cooking_input_offline(device_id)
        for key, _ in existing:
            matter_cooking_states.pop(key)
        matter_cooking_states[entity_id] = replacement
        forward_matter_state(entity_id, hass.states.get(entity_id), snapshot=True)
        save_later()

    hass.services.async_register(DOMAIN, "register_matter_cooking_state", register_matter_cooking_state)

    async def unregister_matter_input(call) -> None:
        device_id = call.data["device_id"]
        input_type = call.data["input_type"]
        if input_type not in {"lid", "cooking", "all"}:
            raise ValueError("input_type must be lid, cooking, or all")
        registries = (
            (matter_contacts,) if input_type == "lid" else
            (matter_cooking_states,) if input_type == "cooking" else
            (matter_contacts, matter_cooking_states)
        )
        if any(adapter.device_id == device_id for registry in registries for adapter in registry.values()):
            if input_type == "cooking":
                publish_cooking_input_offline(device_id)
            else:
                publish_matter_offline(device_id)
        for registry in registries:
            for entity_id, adapter in tuple(registry.items()):
                if adapter.device_id == device_id:
                    registry.pop(entity_id)
        show_matter_readiness()
        save_later()

    hass.services.async_register(DOMAIN, "unregister_matter_input", unregister_matter_input)

    async def register_matter_context(call) -> None:
        entity_id = call.data["entity_id"]
        if not is_matter_entity(entity_id):
            raise ValueError("Context entity must belong to the Matter integration")
        if entity_id in matter_contacts or entity_id in matter_cooking_states or entity_id in matter_board_signals:
            raise ValueError("One HA entity cannot have multiple Bapsim input roles")
        adapter = MatterContextAdapter(call.data["device_id"], signal=call.data["signal"])
        matter_contexts[entity_id] = adapter
        forward_context_state(entity_id, hass.states.get(entity_id))
        save_later()

    hass.services.async_register(DOMAIN, "register_matter_context", register_matter_context)

    async def unregister_matter_context(call) -> None:
        entity_id = call.data["entity_id"]
        adapter = matter_contexts.pop(entity_id, None)
        if adapter is not None:
            received_at = datetime.now(timezone.utc).isoformat()
            hass.bus.async_fire(
                CONTEXT_RAW_EVENT, adapter.translate("unavailable", received_at, received_at)
            )
        show_context_readiness()
        save_later()

    hass.services.async_register(DOMAIN, "unregister_matter_context", unregister_matter_context)

    async def register_matter_board_signal(call) -> None:
        entity_id = call.data["entity_id"]
        if not is_matter_entity(entity_id):
            raise ValueError("Board signal entity must belong to the Matter integration")
        if entity_id in matter_contexts:
            raise ValueError("A household context entity cannot also be a board signal")
        adapter = MatterBoardSignalAdapter(
            call.data["device_id"], kind=call.data["kind"], signal=call.data["signal"]
        )
        matter_board_signals[entity_id] = adapter
        forward_board_state(entity_id, hass.states.get(entity_id))
        save_later()

    hass.services.async_register(DOMAIN, "register_matter_board_signal", register_matter_board_signal)

    async def unregister_matter_board_signal(call) -> None:
        entity_id = call.data["entity_id"]
        adapter = matter_board_signals.pop(entity_id, None)
        if adapter is not None:
            received_at = datetime.now(timezone.utc).isoformat()
            hass.bus.async_fire(RAW_EVENT, adapter.translate("unavailable", received_at, received_at))
        show_board_readiness()
        save_later()

    hass.services.async_register(DOMAIN, "unregister_matter_board_signal", unregister_matter_board_signal)

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

    hass.services.async_register(DOMAIN, "respond", respond)

    async def set_notification_target(call) -> None:
        nonlocal notification_target
        target = call.data.get("service")
        if target and (not isinstance(target, str) or not target.startswith("mobile_app_") or not hass.services.has_service("notify", target)):
            raise ValueError("A registered notify.mobile_app_* service is required")
        notification_target = target or None
        save_later()
        if notification_target and pending_mobile:
            hass.async_create_task(retry_mobile_notifications())

    hass.services.async_register(DOMAIN, "set_notification_target", set_notification_target)

    async def set_away(call) -> None:
        nonlocal away, model_away
        value = call.data.get("away")
        if not isinstance(value, bool):
            raise ValueError("away must be true or false")
        if external_service:
            if not hass.services.has_service("mqtt", "publish"):
                raise ValueError("MQTT broker is required for external analysis service")
            await hass.services.async_call("mqtt", "publish", {
                "topic": output.topic("matter", "observations"),
                "payload": json.dumps({"event_type": "away_state", "away": value,
                                       "observed_at": datetime.now(timezone.utc).isoformat(),
                                       "source": "matter"}),
                "qos": 1, "retain": False,
            }, blocking=True)
            show("away", "away" if value else "home")
            return
        away = value
        model_away = away or controls.is_paused(datetime.now(timezone.utc).isoformat())
        usage_model.set_away(model_away, datetime.now(timezone.utc).isoformat())
        show("away", "away" if away else "home")
        show("detection_status", "away" if away else "monitoring", {
            "model": "personal_usage_v1", "as_of": datetime.now(timezone.utc).isoformat(),
        })
        if away:
            for source, device_id in usage_model.device_keys:
                for update in workflow.ingest({
                    "source": source, "device_id": device_id,
                    "as_of": datetime.now(timezone.utc).isoformat(),
                    "routine_status": "away", "reason_codes": [],
                }):
                    publish_workflow(update)
        save_later()

    hass.services.async_register(DOMAIN, "set_away", set_away)

    async def set_notification_control(call) -> None:
        nonlocal model_away
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
        result = controls.apply(command, now)
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
            if not model_away:
                usage_model.set_away(True, now)
                model_away = True
            for manager in (workflow, trend_workflow):
                for item in manager.dump_state():
                    for update in manager.ingest({
                        "source": item["source"], "device_id": item["device_id"],
                        "as_of": now, "routine_status": "away", "reason_codes": [],
                    }):
                        publish_workflow(update)
        save_later()

    hass.services.async_register(DOMAIN, "set_notification_control", set_notification_control)

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

    hass.services.async_register(DOMAIN, "dashboard_control", dashboard_control)

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

    if not external_service and hass.services.has_service("mqtt", "publish"):
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

    if external_service and hass.services.has_service("mqtt", "publish"):
        hass.async_create_task(subscribe_worker())

    async def record_simulated_activity(call) -> None:
        device_id = call.data.get("device_id")
        signal = call.data.get("signal")
        if not isinstance(device_id, str) or not device_id or not isinstance(signal, str):
            raise ValueError("device_id and signal are required")
        at = call.data.get("at") or datetime.now(timezone.utc).isoformat()
        auxiliary.record(device_id, at, signal)
        show("auxiliary_activity", signal, {"device_id": device_id, "as_of": at, "mode": "simulation"})
        save_later()

    hass.services.async_register(DOMAIN, "record_simulated_activity", record_simulated_activity)

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

    unsubscribe_mobile_action = hass.bus.async_listen("mobile_app_notification_action", on_mobile_action)

    @callback
    def on_stop(event: Event) -> None:
        nonlocal cancel_timer
        unsubscribe()
        unsubscribe_context()
        unsubscribe_sessions()
        unsubscribe_matter()
        unsubscribe_registry()
        unsubscribe_tick()
        unsubscribe_mobile_action()
        if unsubscribe_control is not None:
            unsubscribe_control()
        for remove in unsubscribe_worker:
            remove()
        hass.services.async_remove(DOMAIN, "respond")
        hass.services.async_remove(DOMAIN, "register_matter_contact")
        hass.services.async_remove(DOMAIN, "register_matter_cooking_state")
        hass.services.async_remove(DOMAIN, "unregister_matter_input")
        hass.services.async_remove(DOMAIN, "register_matter_context")
        hass.services.async_remove(DOMAIN, "unregister_matter_context")
        hass.services.async_remove(DOMAIN, "register_matter_board_signal")
        hass.services.async_remove(DOMAIN, "unregister_matter_board_signal")
        hass.services.async_remove(DOMAIN, "set_notification_target")
        hass.services.async_remove(DOMAIN, "set_away")
        hass.services.async_remove(DOMAIN, "set_notification_control")
        hass.services.async_remove(DOMAIN, "dashboard_control")
        hass.services.async_remove(DOMAIN, "record_simulated_activity")
        if cancel_timer is not None:
            cancel_timer()
            cancel_timer = None

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_stop)
    hass.data[DOMAIN] = {
        "normalizer": normalizer,
        "sessions": sessions,
        "features": features,
        "baseline": baseline,
        "detector": detector,
        "usage_model": usage_model,
        "workflow": workflow,
        "trend_workflow": trend_workflow,
        "notification_controls": controls,
        "auxiliary": auxiliary,
        "matter_contacts": matter_contacts,
        "matter_cooking_states": matter_cooking_states,
        "matter_contexts": matter_contexts,
        "matter_board_signals": matter_board_signals,
        "board_history": board_history,
        "journal": journal,
        "store": store,
    }
    on_started(None)
    show_matter_readiness()
    show_context_readiness()
    show_board_readiness()
    # Rewrite restored state using the current cooker-only schema.
    save_later()
    _LOGGER.info("Bapsim pipeline listening for %s and %s", RAW_EVENT, COOKER_EVENT)
    return True
