"""Home Assistant adapter for shared personal usage assessments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class AnalysisMode:
    model_away: bool


def bind_analysis(
        callback, hass, usage_model, workflow, trend_workflow, controls, observation_issues,
        auxiliary, output, availability_by_device, last_baseline_record_day, mode,
        away_getter, archive_service_record, dispatch_workflow, publish_workflow,
        show, send_mqtt, save_later, assess_personal_use, cooker_only,
        baseline_event, detection_event
):
    @callback
    def evaluate_model(source: str, device_id: str, as_of: str) -> None:
        effective_away = away_getter() or controls.is_paused(as_of)
        if effective_away != mode.model_away:
            usage_model.set_away(effective_away, as_of)
            mode.model_away = effective_away
        profile = usage_model.profile(source, device_id, as_of)
        record_day = datetime.fromisoformat(as_of.replace("Z", "+00:00")).date().isoformat()
        if last_baseline_record_day.get((source, device_id)) != record_day:
            archive_service_record("baseline", profile, as_of)
            last_baseline_record_day[(source, device_id)] = record_day
        hass.bus.async_fire(baseline_event, profile)
        show("baseline", "ready" if profile["ready"] else "learning", profile)
        online = availability_by_device.get((source, device_id)) == "online"
        show("observation_issue_status", "available" if online else "unavailable",
             {"source": source, "device_id": device_id, "as_of": as_of})
        issue = observation_issues.update(source, device_id, online, as_of)
        if issue is not None:
            dispatch_workflow(issue)
        if not online or effective_away:
            status = "away" if effective_away else "data_unavailable"
            reason = "AWAY_DECLARED" if effective_away else "COOKER_DATA_UNAVAILABLE"
            provisional = {
                "source": source, "device_id": device_id, "as_of": as_of,
                "routine_status": status, "severity": "info", "reason_codes": [reason],
                "score": None, "evidence": {"model": "personal_usage_v1"},
            }
            hass.bus.async_fire(detection_event, provisional)
            show("detection_status", status, provisional)
            for update in workflow.ingest(provisional):
                publish_workflow(update)
            if effective_away:
                for update in trend_workflow.ingest(provisional):
                    publish_workflow(update)
        assessments = assess_personal_use(usage_model, source, device_id, as_of,
                                          online=online, away=effective_away)
        for decision in assessments.windows:
            archive_service_record("assessment", decision, decision["as_of"])
            if decision["routine_status"] == "check_required":
                for item in workflow.dump_state():
                    if (item["source"], item["device_id"]) == (source, device_id) and item["status"] == "checking":
                        incident_id = item.get("incident_id")
                        if incident_id in controls.acknowledgements and not controls.is_acknowledged(incident_id, as_of):
                            resolved = workflow.caregiver_response(source, device_id, "resolved", as_of, incident_id)
                            if resolved is not None:
                                publish_workflow(resolved)
            hass.bus.async_fire(detection_event, decision)
            show("detection_status", decision["routine_status"], decision)
            if auxiliary.defer(decision):
                show("auxiliary_deferral", "deferred", {"device_id": device_id,
                                                       "until": auxiliary.pending[-1]["due_at"]})
                continue
            for update in workflow.ingest(cooker_only(decision)):
                dispatch_workflow(update)
        interval = assessments.interval
        if interval is not None:
            archive_service_record("assessment", interval, as_of)
            if interval["routine_status"] in {"watch", "check_required", "normal"}:
                hass.bus.async_fire(detection_event, interval)
                show("interval_detection", interval["routine_status"], interval)
            if interval["routine_status"] in {"normal", "check_required"}:
                for update in workflow.ingest(cooker_only(interval)):
                    dispatch_workflow(update)
        trend = assessments.trend
        if trend is not None:
            archive_service_record("assessment", trend, trend["as_of"])
            hass.bus.async_fire(detection_event, trend)
            show("decline_detection", trend["routine_status"], trend)
            for update in trend_workflow.ingest(cooker_only(trend)):
                dispatch_workflow(update, trend_workflow)
        gap_state = workflow.current_state(source, device_id)
        decline_state = trend_workflow.current_state(source, device_id)
        state = output.build(as_of, profile, "available" if online else "unavailable",
                             gap_state, decline_state)
        show("output_state", state["summary"], state)
        if hass.services.has_service("mqtt", "publish"):
            hass.async_create_task(send_mqtt(source, state))
        else:
            show("mqtt_output", "broker_unavailable", {"topic": output.topic(source, "state")})
        save_later()

    return evaluate_model
