"""Matter entity source checks, raw forwarding, and readiness presentation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Callable, NamedTuple

CONNECTION_REPORT_MAX_AGE = timedelta(minutes=3)
CONNECTION_SIGNALS = frozenset({"uptime", "snapshot"})


class MatterInputHandlers(NamedTuple):
    is_matter_entity: Callable[..., bool]
    matter_inputs_available: Callable[..., bool]
    connection_deadline: Callable[..., datetime | None]
    show_matter_readiness: Callable[..., None]
    show_context_readiness: Callable[..., None]
    show_board_readiness: Callable[..., None]
    forward_matter_state: Callable[..., None]
    forward_context_state: Callable[..., None]
    forward_board_state: Callable[..., None]
    publish_matter_offline: Callable[..., None]
    publish_cooking_input_offline: Callable[..., None]
    unsubscribe_matter: Callable[..., None]
    unsubscribe_registry: Callable[..., None]
    on_started: Callable[..., None]


def bind_matter_inputs(
        hass, er, callback, started_event, raw_event, context_raw_event,
        matter_contacts, matter_cooking_states, matter_contexts, matter_board_signals,
        matter_report_at, availability_by_device, usage_model, pending_mobile,
        retry_mobile_notifications, show, MatterContactAdapter, MatterCookingStateAdapter
) -> MatterInputHandlers:
    connection_reports: dict[tuple[str, str], datetime] = {}

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
    def connection_deadline(device_id: str) -> datetime | None:
        deadlines = [
            reported + CONNECTION_REPORT_MAX_AGE
            for (entity_id, report_device), reported in connection_reports.items()
            if report_device == device_id
            and (adapter := matter_board_signals.get(entity_id)) is not None
            and adapter.device_id == device_id and adapter.kind == "observation"
            and adapter.signal in CONNECTION_SIGNALS and is_matter_entity(entity_id)
            and (state := hass.states.get(entity_id)) is not None
            and state.state not in {"unknown", "unavailable"}
        ]
        return max(deadlines, default=None)

    @callback
    def matter_inputs_available(device_id: str, as_of: datetime | None = None) -> bool:
        bindings = matter_bindings(device_id)
        contact_ready = any(
            isinstance(adapter, MatterContactAdapter)
            and is_matter_entity(entity_id)
            and (state := hass.states.get(entity_id)) is not None
            and state.state in {"on", "off"}
            for entity_id, adapter in bindings
        )
        if not contact_ready:
            return False
        now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        deadline = connection_deadline(device_id)
        return deadline is not None and deadline - CONNECTION_REPORT_MAX_AGE <= now <= deadline

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
    def show_matter_readiness(as_of: datetime | None = None) -> None:
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
                "inputs_available": matter_inputs_available(device_id, as_of),
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
            status = "connection_unverified"
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
    def next_matter_time(device_id: str, as_of: datetime | None = None) -> str:
        observed = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        previous = matter_report_at.get(device_id)
        if previous is not None and observed <= previous:
            observed = previous + timedelta(microseconds=1)
        matter_report_at[device_id] = observed
        return observed.isoformat()

    @callback
    def publish_matter_offline(device_id: str, as_of: datetime | None = None) -> None:
        received_at = next_matter_time(device_id, as_of)
        hass.bus.async_fire(raw_event, {
            "device_id": device_id, "event_type": "availability", "value": "offline",
            "observed_at": received_at, "received_at": received_at, "source": "matter",
            "availability": "offline", "quality": "unknown",
        })

    @callback
    def publish_cooking_input_offline(device_id: str) -> None:
        received_at = next_matter_time(device_id)
        hass.bus.async_fire(raw_event, {
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
                hass.bus.async_fire(raw_event, raw)
            show_matter_readiness()
            return
        raw = translate_bound(entity_id, adapter, state, received_at)
        hass.bus.async_fire(raw_event, raw)
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
        hass.bus.async_fire(context_raw_event, adapter.translate(value, received_at, received_at))
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
    def forward_board_state(entity_id: str, state, *, fresh_report: bool = False,
                            previous_value: str | None = None) -> None:
        adapter = matter_board_signals.get(entity_id)
        if adapter is None:
            return
        received = datetime.now(timezone.utc)
        is_connection = adapter.kind == "observation" and adapter.signal in CONNECTION_SIGNALS
        valid_report = (fresh_report and is_connection and is_matter_entity(entity_id) and
                        state is not None and state.state not in {"unknown", "unavailable"})
        rebooted = False
        if valid_report and adapter.signal == "uptime":
            try:
                current_uptime = float(state.state)
            except (TypeError, ValueError):
                valid_report = False
            else:
                valid_report = isfinite(current_uptime) and current_uptime >= 0
                try:
                    previous_uptime = float(previous_value) if previous_value is not None else None
                except (TypeError, ValueError):
                    previous_uptime = None
                rebooted = (previous_uptime is not None and isfinite(previous_uptime) and
                            current_uptime < previous_uptime)
        if is_connection and not valid_report:
            if connection_reports.pop((entity_id, adapter.device_id), None) is not None and \
                    availability_by_device.get(("matter", adapter.device_id)) == "online":
                publish_matter_offline(adapter.device_id)
        if valid_report:
            key = entity_id, adapter.device_id
            previous_report = connection_reports.get(key)
            if rebooted or (previous_report is not None and
                            received - previous_report > CONNECTION_REPORT_MAX_AGE):
                publish_matter_offline(adapter.device_id)
            connection_reports[key] = received
        received_at = received.isoformat()
        value = state.state if is_matter_entity(entity_id) and state is not None else "unavailable"
        hass.bus.async_fire(raw_event, adapter.translate(value, received_at, received_at))
        if valid_report and matter_inputs_available(adapter.device_id):
            lids = [bound for bound, bound_adapter in matter_contacts.items()
                    if bound_adapter.device_id == adapter.device_id]
            if lids and availability_by_device.get(("matter", adapter.device_id)) != "online":
                forward_matter_state(lids[0], hass.states.get(lids[0]), snapshot=True)
        show_board_readiness()
        if is_connection:
            show_matter_readiness()

    @callback
    def on_matter_state(event: Event) -> None:
        entity_id = event.data.get("entity_id")
        state = event.data.get("new_state")
        previous = event.data.get("old_state")
        if entity_id in matter_board_signals and (
            state is None or previous is None or previous.state != state.state
        ):
            forward_board_state(entity_id, state, fresh_report=previous is not None and
                                state is not None and previous.state != state.state,
                                previous_value=previous.state if previous is not None else None)
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
        if pending_mobile:
            hass.async_create_task(retry_mobile_notifications())

    hass.bus.async_listen_once(started_event, on_started)

    return MatterInputHandlers(
        is_matter_entity, matter_inputs_available, connection_deadline, show_matter_readiness,
        show_context_readiness, show_board_readiness, forward_matter_state,
        forward_context_state, forward_board_state, publish_matter_offline,
        publish_cooking_input_offline, unsubscribe_matter, unsubscribe_registry, on_started,
    )


def register_matter_services(
        hass, register_service, raw_event, context_raw_event, matter_contacts, matter_cooking_states,
        matter_contexts, matter_board_signals, is_matter_entity, publish_matter_offline,
        publish_cooking_input_offline, forward_matter_state, forward_context_state,
        forward_board_state, show_matter_readiness, show_context_readiness,
        show_board_readiness, save_later, MatterContactAdapter, MatterCookingStateAdapter,
        MatterContextAdapter, MatterBoardSignalAdapter
) -> None:
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

    register_service("register_matter_contact", register_matter_contact)

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

    register_service("register_matter_cooking_state", register_matter_cooking_state)

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

    register_service("unregister_matter_input", unregister_matter_input)

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

    register_service("register_matter_context", register_matter_context)

    async def unregister_matter_context(call) -> None:
        entity_id = call.data["entity_id"]
        adapter = matter_contexts.pop(entity_id, None)
        if adapter is not None:
            received_at = datetime.now(timezone.utc).isoformat()
            hass.bus.async_fire(
                context_raw_event, adapter.translate("unavailable", received_at, received_at)
            )
        show_context_readiness()
        save_later()

    register_service("unregister_matter_context", unregister_matter_context)

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

    register_service("register_matter_board_signal", register_matter_board_signal)

    async def unregister_matter_board_signal(call) -> None:
        entity_id = call.data["entity_id"]
        adapter = matter_board_signals.pop(entity_id, None)
        if adapter is not None:
            received_at = datetime.now(timezone.utc).isoformat()
            hass.bus.async_fire(raw_event, adapter.translate("unavailable", received_at, received_at))
            if adapter.kind == "observation" and adapter.signal in CONNECTION_SIGNALS:
                publish_matter_offline(adapter.device_id)
                show_matter_readiness()
        show_board_readiness()
        save_later()

    register_service("unregister_matter_board_signal", unregister_matter_board_signal)
