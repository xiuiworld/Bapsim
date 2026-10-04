"""Bridge HA's raw event bus to the transport-independent cooker normalizer."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    from .runtime.workflow import DeliveryLedger, DetectionStateManager, NotificationControls, ObservationIssueTracker, WorkflowUpdate
    from .runtime.workflow.quiet_hours import QuietHours
    from .runtime.workflow.evidence_boundary import cooker_only
    from .runtime.workflow.notification_policy import (ALERT_ACTIONS, current_request, queue_quiet,
                                                       release_quiet, suppressed)
    from .runtime.inputs.matter import MatterBoardSignalAdapter, MatterContactAdapter, MatterContextAdapter, MatterCookingStateAdapter
    from .runtime.inputs.base.journal import RawEventJournal
    from .runtime.service import ObservationJournal, StatusOutput, AuxiliaryDeferral
    from .runtime.service.analysis_policy import assess_personal_use
    from .runtime.service.ha_notification_transport import notification_copy, send_local, send_mobile
    from .runtime.service.ha_notification_flow import make_notification_handlers
    from .runtime.service.ha_dashboard import HADashboard
    from .runtime.service.ha_mqtt_output import HAMQTTOutput
    from .runtime.service.ha_matter_inputs import bind_matter_inputs, register_matter_services
    from .runtime.service.ha_journal import HAJournal
    from .runtime.service.ha_analysis_flow import AnalysisMode, bind_analysis
    from .runtime.service.ha_cooker_events import bind_cooker_events
    from .runtime.service.ha_periodic import PeriodicState, bind_periodic
    from .runtime.service.ha_mqtt_subscriptions import bind_mqtt_subscriptions
    from .runtime.service.ha_service_registry import HAServiceRegistry
    from .runtime.service.ha_caregiver_commands import bind_caregiver_commands
else:
    from domain.events.normalizer import CookerEventNormalizer, NormalizedCookerEvent
    from domain.baseline import PersonalBaselineEngine
    from domain.detection import RuleDetector
    from domain.features import CookerFeatureExtractor
    from domain.sessions import CookingSessionBuilder
    from domain.personal_model import PersonalUsageModel
    from workflow import DeliveryLedger, DetectionStateManager, NotificationControls, ObservationIssueTracker, WorkflowUpdate
    from workflow.quiet_hours import QuietHours
    from workflow.evidence_boundary import cooker_only
    from workflow.notification_policy import (ALERT_ACTIONS, current_request, queue_quiet,
                                              release_quiet, suppressed)
    from inputs.matter import MatterBoardSignalAdapter, MatterContactAdapter, MatterContextAdapter, MatterCookingStateAdapter
    from inputs.base.journal import RawEventJournal
    from service import ObservationJournal, StatusOutput, AuxiliaryDeferral
    from service.analysis_policy import assess_personal_use
    from service.ha_notification_transport import notification_copy, send_local, send_mobile
    from service.ha_notification_flow import make_notification_handlers
    from service.ha_dashboard import HADashboard
    from service.ha_mqtt_output import HAMQTTOutput
    from service.ha_matter_inputs import bind_matter_inputs, register_matter_services
    from service.ha_journal import HAJournal
    from service.ha_analysis_flow import AnalysisMode, bind_analysis
    from service.ha_cooker_events import bind_cooker_events
    from service.ha_periodic import PeriodicState, bind_periodic
    from service.ha_mqtt_subscriptions import bind_mqtt_subscriptions
    from service.ha_service_registry import HAServiceRegistry
    from service.ha_caregiver_commands import bind_caregiver_commands

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
    trend_workflow = DetectionStateManager(min_consecutive=1, candidate_window=timedelta(days=1),
                                           reminder_cadence=timedelta(days=7), resumption_alerts=False)
    controls = NotificationControls()
    observation_issues = ObservationIssueTracker()
    delivery = DeliveryLedger()
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
    last_baseline_record_day: dict[tuple[str, str], str] = {}
    stored = await store.async_load()
    routine_updated_at: dict[str, str] = dict(stored.get("routine_updated_at", {})) if isinstance(stored, dict) else {}
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
            observation_issues.load_state(stored.get("observation_issues", []))
            delivery.load_state(stored.get("delivery", []))
            quiet_hours.configure(*stored.get("quiet_hours", ["22:00", "08:00"]))
            pending_quiet.update({item.get("notification_id", item["incident_id"]): item
                                  for item in stored.get("pending_quiet", [])})
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
            forwarded_devices.update(
                (item["source"], item["device_id"])
                for item in stored.get("forwarded_devices", [])
                if isinstance(item, dict) and item.get("source") in {"matter", "simulator"}
                and isinstance(item.get("device_id"), str) and item["device_id"]
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
                    item.setdefault("action", "legacy_mobile_retry")
                    pending_mobile[item.get("notification_id", item["incident_id"])] = item
                    notification_id = item.get("notification_id", item["incident_id"])
                    if delivery.get(notification_id) is None:
                        now = datetime.now(timezone.utc).isoformat()
                        record = delivery.prepare(notification_id, item["incident_id"], item["source"],
                                                  item["device_id"], item["action"],
                                                  item["reason_codes"], now, destination="mobile_legacy",
                                                  title=item.get("title", ""), message=item["message"])
                        record.update(status="failed", attempts=1, next_retry_at=now,
                                      expires_at=item.get("expires_at", record["expires_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            _LOGGER.warning("Could not restore some Bapsim pipeline state: %s", exc)
    model_away = away or controls.is_paused(datetime.now(timezone.utc).isoformat())
    for record in delivery.records.values():
        if record["status"] == "uncertain":
            pending_mobile.pop(str(record["notification_id"]), None)
            manager = trend_workflow if "DECLINING_USAGE" in record["reason_codes"] else workflow
            manager.cancel_request(str(record["source"]), str(record["device_id"]),
                                   str(record["updated_at"]))
        elif record["status"] in {"ha_accepted", "cancelled"}:
            pending_mobile.pop(str(record["notification_id"]), None)
    if external_service:
        now = datetime.now(timezone.utc).isoformat()
        for notification_id in tuple(pending_mobile):
            if delivery.get(notification_id) is not None:
                delivery.cancel(notification_id, now, "external_worker_mode")
            pending_mobile.pop(notification_id, None)
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
            "routine_updated_at": dict(routine_updated_at),
            "workflow": workflow.dump_state(),
            "trend_workflow": trend_workflow.dump_state(),
            "notification_controls": controls.dump_state(),
            "observation_issues": observation_issues.dump_state(),
            "delivery": delivery.dump_state(),
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
            "forwarded_devices": [
                {"source": source, "device_id": device_id}
                for source, device_id in sorted(forwarded_devices)
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

    async def save_now() -> None:
        await store.async_save(state_snapshot())

    @callback
    def delivery_failure(exc: Exception) -> str:
        # A timeout or unknown service error may have occurred after HA accepted the call.
        return "failed" if isinstance(exc, ConnectionError) else "uncertain"

    dashboard = HADashboard(hass, usage_model, routine_updated_at)
    show = dashboard.show
    show_initial_routines = dashboard.show_initial_routines

    # Restore dashboard cards without treating old detections as new evidence.
    show("workflow_status", "normal")
    show("decline_status", "waiting")
    show("decline_detection", "waiting")
    show("interval_detection", "waiting")
    show("baseline", "waiting")
    show_initial_routines()
    show("detection_status", "waiting")
    show("features", "waiting")
    show("notification_delivery", "not_sent")
    show("observation_issue_status", "unknown")
    show("mqtt_output", "waiting")
    show("auxiliary_deferral", "waiting")
    show("matter_input", "not_configured")
    show("matter_board", "not_configured")
    show("board_history", len(board_history), {"latest": board_history[-3:]})
    show("away", "away" if away else "home")
    show("notification_controls", controls.revision, controls.dump_state())
    if external_service:
        show("output_state", "awaiting_matter", {"mode": "external_service"})
        show("simulation_output_state", "waiting", {"mode": "simulation"})

    mqtt_output = HAMQTTOutput(hass, output, controls, show, _LOGGER)
    send_mqtt = mqtt_output.send_state
    send_controls = mqtt_output.send_controls

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
        if update.status == "resolved":
            for pending in (pending_mobile, pending_quiet):
                for key, item in tuple(pending.items()):
                    if item["incident_id"] == update.incident_id:
                        pending.pop(key, None)
            for notification_id, record in delivery.records.items():
                if record["incident_id"] == update.incident_id and record["status"] in {"pending", "failed"}:
                    delivery.cancel(notification_id, update.as_of, "incident_resolved")
        hass.bus.async_fire(WORKFLOW_EVENT, payload)
        if update.action == "observation_issue_requested":
            show("observation_issue_status", "alert_requested", payload)
        elif "DECLINING_USAGE" in update.reason_codes or "DECLINE_RESOLVED" in update.reason_codes:
            show("decline_status", update.status, payload)
        else:
            show("workflow_status", update.status, payload)
        save_later()

    create_notification, retry_mobile_notifications = make_notification_handlers(
        hass, workflow, trend_workflow, controls, observation_issues, delivery, quiet_hours,
        availability_by_device, pending_mobile, pending_quiet, retrying_mobile,
        lambda: notification_target, show,
        lambda category, payload, at: archive_service_record(category, payload, at),
        save_now, save_later, publish_workflow, delivery_failure, _LOGGER, WorkflowUpdate,
        current_request, queue_quiet, suppressed, notification_copy, send_local, send_mobile)

    @callback
    def dispatch_workflow(update, manager=workflow) -> None:
        publish_workflow(update)
        if update.action in ALERT_ACTIONS:
            hass.async_create_task(create_notification(update, manager))

    analysis_mode = AnalysisMode(model_away)
    evaluate_model = bind_analysis(
        callback, hass, usage_model, workflow, trend_workflow, controls, observation_issues,
        auxiliary, output, availability_by_device, last_baseline_record_day, analysis_mode,
        lambda: away, lambda category, payload, at: archive_service_record(category, payload, at),
        dispatch_workflow, publish_workflow,
        show, send_mqtt, save_later, assess_personal_use, cooker_only,
        BASELINE_EVENT, DETECTION_EVENT)

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

    journal_io = HAJournal(hass, journal, observations, service_records, _LOGGER)
    archive_raw = journal_io.archive_raw
    write_observation = journal_io.write_observation
    archive_service_record = journal_io.archive_service_record
    prune_observations = journal_io.prune_observations

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

    unsubscribe_sessions = bind_cooker_events(
        hass, callback, COOKER_EVENT, SESSION_EVENT, FEATURE_EVENT, observations,
        write_observation, external_service, forwarded_devices, save_later, output, show,
        usage_model, availability_by_device, workflow, dispatch_workflow,
        archive_service_record, sessions, features, latest_features, evaluate_model, _LOGGER)

    matter_inputs = bind_matter_inputs(
        hass, er, callback, EVENT_HOMEASSISTANT_STARTED, RAW_EVENT, CONTEXT_RAW_EVENT,
        matter_contacts, matter_cooking_states, matter_contexts, matter_board_signals,
        matter_report_at, availability_by_device, usage_model, pending_mobile,
        retry_mobile_notifications, show, MatterContactAdapter, MatterCookingStateAdapter)
    (is_matter_entity, matter_inputs_available, connection_deadline, show_matter_readiness,
     show_context_readiness, show_board_readiness, forward_matter_state,
     forward_context_state, forward_board_state, publish_matter_offline,
     publish_cooking_input_offline, unsubscribe_matter, unsubscribe_registry,
     on_started) = matter_inputs

    periodic_state = PeriodicState()
    on_hourly_tick = bind_periodic(
        callback, hass, periodic_state, show_initial_routines, delivery, external_service,
        forwarded_devices, matter_inputs_available, connection_deadline,
        lambda device_id: any(adapter.device_id == device_id for adapter in matter_contacts.values()),
        publish_matter_offline, show_matter_readiness,
        output, quiet_hours, pending_quiet,
        workflow, trend_workflow, controls, observation_issues, availability_by_device,
        lambda: away, create_notification, release_quiet, save_later, auxiliary,
        usage_model, show, cooker_only, dispatch_workflow, observations,
        prune_observations, latest_features, features, FEATURE_EVENT, evaluate_model,
        pending_mobile, retry_mobile_notifications)

    unsubscribe_tick = async_track_time_interval(hass, on_hourly_tick, timedelta(seconds=60))

    service_registry = HAServiceRegistry(hass, DOMAIN)
    register_matter_services(
        hass, service_registry.register, RAW_EVENT, CONTEXT_RAW_EVENT, matter_contacts, matter_cooking_states,
        matter_contexts, matter_board_signals, is_matter_entity, publish_matter_offline,
        publish_cooking_input_offline, forward_matter_state, forward_context_state,
        forward_board_state, show_matter_readiness, show_context_readiness,
        show_board_readiness, save_later, MatterContactAdapter, MatterCookingStateAdapter,
        MatterContextAdapter, MatterBoardSignalAdapter)


    async def set_notification_target(call) -> None:
        nonlocal notification_target
        target = call.data.get("service")
        if target and (not isinstance(target, str) or not target.startswith("mobile_app_") or not hass.services.has_service("notify", target)):
            raise ValueError("A registered notify.mobile_app_* service is required")
        notification_target = target or None
        save_later()
        if notification_target and pending_mobile:
            hass.async_create_task(retry_mobile_notifications())

    service_registry.register("set_notification_target", set_notification_target)

    async def set_away(call) -> None:
        nonlocal away
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
        analysis_mode.model_away = away or controls.is_paused(datetime.now(timezone.utc).isoformat())
        usage_model.set_away(analysis_mode.model_away, datetime.now(timezone.utc).isoformat())
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

    service_registry.register("set_away", set_away)

    async def set_initial_routine(call) -> None:
        device_id = call.data.get("device_id")
        windows = call.data.get("windows")
        if external_service and not hass.services.has_service("mqtt", "publish"):
            raise ValueError("MQTT broker is required for external analysis service")
        previous = usage_model.initial_routine("matter", device_id) if isinstance(device_id, str) else []
        usage_model.configure_initial_routine("matter", device_id, windows)
        if external_service:
            routines = [{"source": "matter", "device_id": current_device,
                         "windows": usage_model.initial_routine("matter", current_device)}
                        for source, current_device in usage_model.device_keys
                        if source == "matter" and usage_model.initial_routine(source, current_device)]
            try:
                await hass.services.async_call("mqtt", "publish", {
                    "topic": f"bapsim/v1/{output.household_id}/routine_config",
                    "payload": json.dumps({"schema_version": "0.1.0",
                                           "household_id": output.household_id,
                                           "routines": routines}, ensure_ascii=False),
                    "qos": 1, "retain": True,
                }, blocking=True)
            except Exception:
                usage_model.configure_initial_routine("matter", device_id, previous)
                raise
        if windows:
            routine_updated_at[device_id] = datetime.now(timezone.utc).isoformat()
        else:
            routine_updated_at.pop(device_id, None)
        show_initial_routines()
        save_later()

    service_registry.register("set_initial_routine", set_initial_routine)

    caregiver = bind_caregiver_commands(
        callback, hass, workflow, trend_workflow, controls, usage_model, analysis_mode,
        output, external_service, show, send_controls, publish_workflow, save_later, _LOGGER)
    service_registry.register("respond", caregiver.respond)
    service_registry.register("set_notification_control", caregiver.set_notification_control)
    service_registry.register("dashboard_control", caregiver.dashboard_control)
    set_notification_control = caregiver.set_notification_control
    unsubscribe_mobile_action = hass.bus.async_listen(
        "mobile_app_notification_action", caregiver.on_mobile_action)



    stop_mqtt_subscriptions = bind_mqtt_subscriptions(
        hass, output, set_notification_control, show, _LOGGER, external_service)

    async def record_simulated_activity(call) -> None:
        device_id = call.data.get("device_id")
        signal = call.data.get("signal")
        if not isinstance(device_id, str) or not device_id or not isinstance(signal, str):
            raise ValueError("device_id and signal are required")
        at = call.data.get("at") or datetime.now(timezone.utc).isoformat()
        if not isinstance(at, str):
            raise ValueError("at must be a timezone-aware timestamp")
        if external_service and not hass.services.has_service("mqtt", "publish"):
            raise ValueError("MQTT broker is required for external simulated activity")
        auxiliary.record(device_id, at, signal)
        if external_service:
            await hass.services.async_call("mqtt", "publish", {
                "topic": output.topic("simulator", "auxiliary_activity"),
                "payload": json.dumps({"schema_version": "0.1.0",
                                       "household_id": output.household_id,
                                       "source": "simulator", "device_id": device_id,
                                       "at": at, "signal": signal}),
                "qos": 1, "retain": False,
            }, blocking=True)
        show("auxiliary_activity", signal, {"device_id": device_id, "as_of": at, "mode": "simulation"})
        save_later()

    service_registry.register("record_simulated_activity", record_simulated_activity)


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
        stop_mqtt_subscriptions()
        service_registry.close()
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
        "delivery": delivery,
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
    if external_service:
        for entity in ("workflow_status", "decline_status", "decline_detection",
                       "interval_detection", "baseline", "detection_status", "features",
                       "notification_delivery", "observation_issue_status"):
            show(entity, "external_worker", {"mode": "external_service"})
    show_matter_readiness()
    show_context_readiness()
    show_board_readiness()
    # Rewrite restored state using the current cooker-only schema.
    save_later()
    _LOGGER.info("Bapsim pipeline listening for %s and %s", RAW_EVENT, COOKER_EVENT)
    return True
