"""Process canonical cooker events after raw input normalization."""

from __future__ import annotations

import json


def bind_cooker_events(
        hass, callback, cooker_event, session_event, feature_event, observations,
        write_observation, external_service, forwarded_devices, save_later, output, show,
        usage_model, availability_by_device, workflow, dispatch_workflow,
        archive_service_record, sessions, features, latest_features, evaluate_model, logger
):
    _LOGGER = logger

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
            if (source, device_id) not in forwarded_devices:
                forwarded_devices.add((source, device_id))
                save_later()
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
        prior_groups = set(usage_model.grouped_open_times(*key)) if event.data.get("event_type") == "lid_opened" else set()
        if event.data.get("event_type") in {"device_online", "device_offline"}:
            availability_by_device[key] = event.data["value"]
        accepted_open = False
        try:
            accepted_open = usage_model.observe(event.data)
        except ValueError as exc:
            _LOGGER.warning("Ignored invalid usage-model event: %s", exc)
        if event.data.get("event_type") == "lid_opened" and accepted_open:
            for update in workflow.ingest_lid_opening(source, device_id, observed_at):
                dispatch_workflow(update)
            for opening in set(usage_model.grouped_open_times(*key)) - prior_groups:
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
            hass.bus.async_fire(session_event, {**snapshot, "as_of": observed_at})
        if updates or event.data.get("event_type") in {"device_online", "device_offline"}:
            snapshot = features.extract(
                source, device_id, observed_at, availability_by_device.get(key, "unknown")
            )
            payload = snapshot.as_dict()
            latest_features[key] = payload
            hass.bus.async_fire(feature_event, payload)
            show("features", "available" if payload.get("availability") == "online" else "unavailable", payload)
            save_later()
        if event.data.get("event_type") in {"device_online", "device_offline", "lid_opened"}:
            evaluate_model(source, device_id, observed_at)

    unsubscribe_sessions = hass.bus.async_listen(cooker_event, on_cooker)

    return unsubscribe_sessions
