"""Bridge HA's raw event bus to the transport-independent cooker normalizer."""

from __future__ import annotations

import asyncio
import logging
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
    from .runtime.workflow import DetectionStateManager
    from .runtime.workflow.evidence_boundary import cooker_only
    from .runtime.inputs.matter import MatterBoardSignalAdapter, MatterContactAdapter, MatterContextAdapter, MatterCookingStateAdapter
    from .runtime.inputs.base.journal import RawEventJournal
else:
    from domain.events.normalizer import CookerEventNormalizer, NormalizedCookerEvent
    from domain.baseline import PersonalBaselineEngine
    from domain.detection import RuleDetector
    from domain.features import CookerFeatureExtractor
    from domain.sessions import CookingSessionBuilder
    from domain.personal_model import PersonalUsageModel
    from workflow import DetectionStateManager
    from workflow.evidence_boundary import cooker_only
    from inputs.matter import MatterBoardSignalAdapter, MatterContactAdapter, MatterContextAdapter, MatterCookingStateAdapter
    from inputs.base.journal import RawEventJournal

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
    matter_contacts: dict[str, MatterContactAdapter] = {}
    matter_cooking_states: dict[str, MatterCookingStateAdapter] = {}
    matter_contexts: dict[str, MatterContextAdapter] = {}
    matter_board_signals: dict[str, MatterBoardSignalAdapter] = {}
    matter_report_at: dict[str, datetime] = {}
    availability_by_device: dict[tuple[str, str], str] = {}
    latest_features: dict[tuple[str, str], dict[str, object]] = {}
    board_history: list[dict[str, str]] = []
    pending_mobile: dict[str, dict[str, object]] = {}
    retrying_mobile: set[str] = set()
    away = False
    notification_target: str | None = None
    cancel_timer = None
    store = Store(hass, 1, "bapsim.pipeline", atomic_writes=True)
    journal = RawEventJournal(Path(hass.config.path("bapsim_events"))) if hasattr(hass, "config") else None
    journal_lock = asyncio.Lock()
    stored = await store.async_load()
    if isinstance(stored, dict):
        try:
            normalizer.load_state(stored.get("normalizer", []))
            sessions.load_state(stored.get("sessions", []))
            features.load_state(stored.get("features", []))
            baseline.load_state(stored.get("baseline", []))
            usage_model.load_state(stored.get("usage_model", {}))
            workflow.load_state(stored.get("workflow", []))
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

    @callback
    def state_snapshot() -> dict[str, object]:
        return {
            "normalizer": normalizer.dump_state(),
            "sessions": sessions.dump_state(),
            "features": features.dump_state(),
            "baseline": baseline.dump_state(),
            "usage_model": usage_model.dump_state(),
            "workflow": workflow.dump_state(),
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
    for item in workflow.dump_state():
        show("workflow_status", item["status"], item)
    for source, device_id in usage_model.device_keys:
        profile = usage_model.profile(source, device_id, datetime.now(timezone.utc).isoformat())
        show("baseline", "ready" if profile["ready"] else "learning", profile)
    for snapshot in latest_features.values():
        show("features", "available" if snapshot.get("availability") == "online" else "unavailable", snapshot)

    @callback
    def publish_workflow(update) -> None:
        payload = update.as_dict()
        if update.status == "resolved" and update.incident_id in pending_mobile:
            pending_mobile.pop(update.incident_id, None)
        hass.bus.async_fire(WORKFLOW_EVENT, payload)
        show("workflow_status", update.status, payload)
        save_later()

    async def create_notification(update) -> None:
        title = "밥심 — 안부 확인 필요"
        message = "평소와 다른 밥솥 사용 패턴이 관측되었습니다. 밥심 실험실에서 확인 상태를 기록하세요.\n\n근거: " + ", ".join(update.reason_codes)
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
        if notification_target:
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
                }
                delivery = "mobile_retry_pending"
                save_later()
        show("notification_delivery", delivery, {"incident_id": update.incident_id, "as_of": datetime.now(timezone.utc).isoformat()})
        sent = workflow.mark_notified(update.source, update.device_id, datetime.now(timezone.utc).isoformat())
        if sent is not None:
            publish_workflow(sent)

    async def retry_mobile_notifications() -> None:
        if not notification_target:
            return
        for incident_id, item in tuple(pending_mobile.items()):
            if incident_id in retrying_mobile:
                continue
            current = next((state for state in workflow.dump_state()
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
        profile = usage_model.profile(source, device_id, as_of)
        hass.bus.async_fire(BASELINE_EVENT, profile)
        show("baseline", "ready" if profile["ready"] else "learning", profile)
        online = availability_by_device.get((source, device_id)) == "online"
        if not online or away:
            status = "away" if away else "data_unavailable"
            reason = "AWAY_DECLARED" if away else "COOKER_DATA_UNAVAILABLE"
            provisional = {
                "source": source, "device_id": device_id, "as_of": as_of,
                "routine_status": status, "severity": "info", "reason_codes": [reason],
                "score": None, "evidence": {"model": "personal_usage_v1"},
            }
            hass.bus.async_fire(DETECTION_EVENT, provisional)
            show("detection_status", status, provisional)
            for update in workflow.ingest(provisional):
                publish_workflow(update)
        decisions = usage_model.evaluate(
            source, device_id, as_of,
            online=online,
            away=away,
        )
        for decision in decisions:
            hass.bus.async_fire(DETECTION_EVENT, decision)
            show("detection_status", decision["routine_status"], decision)
            for update in workflow.ingest(cooker_only(decision)):
                publish_workflow(update)
                if update.action == "notify_requested":
                    hass.async_create_task(create_notification(update))
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
        key = (source, device_id)
        if event.data.get("event_type") in {"device_online", "device_offline"}:
            availability_by_device[key] = event.data["value"]
        try:
            usage_model.observe(event.data)
        except ValueError as exc:
            _LOGGER.warning("Ignored invalid usage-model event: %s", exc)
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
        as_of = now.isoformat()
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

    unsubscribe_tick = async_track_time_interval(hass, on_hourly_tick, timedelta(hours=1))

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
                item for item in workflow.dump_state()
                if item["status"] in {"check_required", "notified", "checking"}
            ]
            if len(active) != 1:
                _LOGGER.warning("Caregiver response needs exactly one active incident or an explicit source and device_id")
                return
            source, device_id = active[0]["source"], active[0]["device_id"]
        elif not isinstance(source, str) or not isinstance(device_id, str):
            raise ValueError("source and device_id must be supplied together")
        update = workflow.caregiver_response(source, device_id, response, datetime.now(timezone.utc).isoformat())
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
        nonlocal away
        value = call.data.get("away")
        if not isinstance(value, bool):
            raise ValueError("away must be true or false")
        away = value
        usage_model.set_away(away, datetime.now(timezone.utc).isoformat())
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

    @callback
    def on_mobile_action(event: Event) -> None:
        action = event.data.get("action", "")
        if not isinstance(action, str):
            return
        parts = action.split("|", 4)
        if len(parts) != 5 or parts[0] != "BAPSIM" or parts[1] not in {"checking", "resolved"}:
            return
        _, response, source, device_id, incident_id = parts
        update = workflow.caregiver_response(source, device_id, response, datetime.now(timezone.utc).isoformat(), incident_id)
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
