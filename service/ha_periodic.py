"""Periodic HA housekeeping and deferred assessment delivery."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass
class PeriodicState:
    last_observation_prune_day: date | None = None
    last_routine_review_day: date | None = None


def bind_periodic(
        callback, hass, state, show_initial_routines, delivery, external_service,
        forwarded_devices, matter_inputs_available, connection_deadline, matter_contact_registered,
        publish_matter_offline, show_matter_readiness,
        output, quiet_hours, pending_quiet,
        workflow, trend_workflow, controls, observation_issues, availability_by_device,
        away_getter, create_notification, release_quiet, save_later, auxiliary,
        usage_model, show, cooker_only, dispatch_workflow, observations,
        prune_observations, latest_features, features, feature_event, evaluate_model,
        pending_mobile, retry_mobile_notifications
):
    @callback
    def on_hourly_tick(now) -> None:
        as_of = now.isoformat()
        review_day = now.astimezone(timezone.utc).date()
        if review_day != state.last_routine_review_day:
            show_initial_routines(now)
            state.last_routine_review_day = review_day
        delivery.prune(as_of)
        for (source, device_id), availability in tuple(availability_by_device.items()):
            if (source == "matter" and availability == "online" and
                    matter_contact_registered(device_id) and not matter_inputs_available(device_id, now)):
                availability_by_device[(source, device_id)] = "offline"
                expiry = connection_deadline(device_id)
                closed_at = min(now, expiry) if expiry is not None else now
                usage_model.availability(source, device_id, False, closed_at.isoformat())
                publish_matter_offline(device_id, closed_at)
                show_matter_readiness(now)
        # Reconcile fresh verified reports after restart/reconnection. The normalizer
        # may already hold online and suppress a duplicate transition, while periodic
        # expiry previously marked the analysis state offline. Never backfill coverage.
        if not external_service:
            for source, device_id in set(availability_by_device) | set(usage_model.device_keys):
                if (source == "matter" and availability_by_device.get((source, device_id)) != "online"
                        and matter_contact_registered(device_id) and matter_inputs_available(device_id, now)):
                    availability_by_device[(source, device_id)] = "online"
                    usage_model.availability(source, device_id, True, as_of)
                    show_matter_readiness(now)
                    save_later()
        if external_service:
            if hass.services.has_service("mqtt", "publish"):
                for source, device_id in tuple(forwarded_devices):
                    online = (matter_inputs_available(device_id, now) if source == "matter" else
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
            for update, manager in release_quiet(
                    pending_quiet, workflow, trend_workflow, controls,
                    observation_issues.is_unavailable,
                    lambda source, device_id: availability_by_device.get((source, device_id)) == "online",
                    away_getter(), as_of):
                hass.async_create_task(create_notification(update, manager))
            save_later()
        if away_getter() or controls.is_paused(as_of):
            auxiliary.clear()
        else:
            for decision in auxiliary.due(as_of):
                key = (decision["source"], decision["device_id"])
                if availability_by_device.get(key) != "online":
                    continue
                latest_open = usage_model.profile(*key, as_of)["last_lid_open_at"]
                if latest_open and datetime.fromisoformat(latest_open) > datetime.fromisoformat(decision["deferred_from"]):
                    continue
                show("auxiliary_deferral", "expired", {"source": decision["source"],
                                                       "device_id": decision["device_id"]})
                for update in workflow.ingest(cooker_only(decision)):
                    dispatch_workflow(update)
        if observations is not None and state.last_observation_prune_day != now.date():
            state.last_observation_prune_day = now.date()
            hass.async_create_task(prune_observations(now))
        for source, device_id in tuple(latest_features):
            if source == "simulator":
                availability_state = hass.states.get("input_select.bapsim_availability")
                availability = ("online" if availability_state is not None and
                                availability_state.state == "online" else "offline")
            elif source == "matter":
                availability = "online" if matter_inputs_available(device_id, now) else "offline"
            else:
                availability = "offline"
            payload = features.extract(source, device_id, as_of, availability).as_dict()
            latest_features[(source, device_id)] = payload
            show("features", "available" if availability == "online" else "unavailable", payload)
            hass.bus.async_fire(feature_event, payload)
            evaluate_model(source, device_id, as_of)
        for source, device_id in usage_model.device_keys:
            if (source, device_id) not in latest_features:
                evaluate_model(source, device_id, as_of)
        if pending_mobile:
            hass.async_create_task(retry_mobile_notifications(as_of))

    return on_hourly_tick
