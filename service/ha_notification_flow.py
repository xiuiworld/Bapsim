"""HA-specific alert lifecycle; policy and service transport are injected by the integration."""

from __future__ import annotations

from datetime import datetime, timezone


def make_notification_handlers(
        hass, workflow, trend_workflow, controls, observation_issues, delivery, quiet_hours,
        availability_by_device, pending_mobile, pending_quiet, retrying_mobile,
        notification_target_getter, show, archive_service_record, save_now, save_later,
        publish_workflow, delivery_failure, logger, workflow_update_type,
        current_request, queue_quiet, suppressed, notification_copy, send_local, send_mobile):
    """Bind persisted alert state, HA callbacks, and transport for one integration instance."""
    _LOGGER = logger
    WorkflowUpdate = workflow_update_type

    async def finish_notification(update, manager, outcome: str, stage: str,
                                  title: str, message: str, error: str | None = None) -> None:
        notification_id = str(update.notification_id or update.incident_id)
        now = datetime.now(timezone.utc).isoformat()
        delivery.finish(notification_id, outcome, now, error)
        record = delivery.get(notification_id)
        if outcome == "failed" and record["next_retry_at"] is not None:
            pending_mobile[notification_id] = {
                "source": update.source, "device_id": update.device_id,
                "incident_id": str(update.incident_id), "notification_id": notification_id,
                "action": update.action, "reason_codes": list(update.reason_codes),
                "title": title, "message": message, "stage": stage,
                "expires_at": record["expires_at"],
            }
        else:
            pending_mobile.pop(notification_id, None)
            if outcome == "ha_accepted" and update.action not in {
                    "usage_resumed_requested", "observation_issue_requested"}:
                sent = manager.mark_notified(update.source, update.device_id, now)
                if sent is not None:
                    publish_workflow(sent)
            elif outcome != "ha_accepted":
                manager.cancel_request(update.source, update.device_id, now)
        show("notification_delivery", outcome, {"source": update.source,
                                                  "incident_id": update.incident_id,
                                                  "notification_id": notification_id,
                                                  "next_retry_at": record["next_retry_at"]})
        archive_service_record("notification", {
            "incident_id": update.incident_id, "notification_id": notification_id,
            "action": update.action, "delivery": outcome, "attempts": record["attempts"],
            "reason_codes": list(update.reason_codes),
        }, now)
        await save_now()

    async def create_notification(update, manager=workflow, retry_at: str | None = None) -> None:
        notification_time = retry_at or datetime.now(timezone.utc).isoformat()
        issue = update.action == "observation_issue_requested"
        notification_id = str(update.notification_id or update.incident_id)
        if issue:
            current_valid = (observation_issues.is_unavailable(update.source, update.device_id) and
                             availability_by_device.get((update.source, update.device_id)) != "online")
        else:
            current = manager.current_state(update.source, update.device_id)
            current_valid = (current is not None and current["incident_id"] == update.incident_id and
                             current_request(update.action, current))
        if not current_valid:
            return
        if suppressed(update.action, update.incident_id, controls, notification_time):
            manager.cancel_request(update.source, update.device_id, notification_time)
            show("notification_delivery", "paused", {"source": update.source,
                                                       "incident_id": update.incident_id})
            return
        if quiet_hours.is_quiet(notification_time):
            queue_quiet(pending_quiet, update)
            show("notification_delivery", "quiet_hours", {
                "source": update.source,
                "incident_id": update.incident_id,
                "next_delivery_at": quiet_hours.next_delivery_at(notification_time),
            })
            save_later()
            return
        pending_quiet.pop(notification_id, None)
        title, message = notification_copy(update.action, update.reason_codes)
        delivery.prepare(notification_id, str(update.incident_id), update.source, update.device_id,
                         update.action, list(update.reason_codes), notification_time,
                         destination=notification_target_getter() or "ha_local", title=title, message=message)
        await save_now()
        if not delivery.begin(notification_id, notification_time):
            return
        await save_now()  # A restart during either service call leaves an uncertain record.
        try:
            await send_local(hass, notification_id, title, message)
        except Exception as exc:
            _LOGGER.warning("Bapsim local notification failed: %s", type(exc).__name__)
            await finish_notification(update, manager, delivery_failure(exc), "local",
                                      title, message, type(exc).__name__)
            return
        if notification_target_getter() and update.source != "simulator":
            try:
                await send_mobile(hass, notification_target_getter(), title, message, update.action,
                                  update.source, update.device_id, str(update.incident_id))
            except Exception as exc:
                _LOGGER.warning("Bapsim mobile notification service failed: %s", type(exc).__name__)
                await finish_notification(update, manager, delivery_failure(exc), "mobile",
                                          title, message, type(exc).__name__)
                return
        await finish_notification(update, manager, "ha_accepted", "complete", title, message)

    async def retry_mobile_notifications(as_of: str | None = None) -> None:
        now = as_of or datetime.now(timezone.utc).isoformat()
        for notification_id, item in tuple(pending_mobile.items()):
            if notification_id in retrying_mobile:
                continue
            record = delivery.get(notification_id)
            if record is None:
                pending_mobile.pop(notification_id, None)
                continue
            incident_id = item["incident_id"]
            resumed = item.get("action") == "usage_resumed_requested"
            issue = item.get("action") == "observation_issue_requested"
            manager = trend_workflow if "DECLINING_USAGE" in item["reason_codes"] else workflow
            expired = datetime.fromisoformat(str(record["expires_at"])) <= datetime.fromisoformat(now)
            if expired or suppressed(str(item["action"]), incident_id, controls, now):
                pending_mobile.pop(notification_id, None)
                delivery.cancel(notification_id, now, "expired" if expired else "control_suppressed")
                if item.get("action") in {"notify_requested", "reminder_requested"}:
                    manager.cancel_request(item["source"], item["device_id"], now)
                await save_now()
                continue
            if quiet_hours.is_quiet(now) or not delivery.due(notification_id, now):
                continue
            current = manager.current_state(item["source"], item["device_id"])
            if current is not None and current["incident_id"] != incident_id:
                current = None
            if not current_request(str(item["action"]), current, observation_unavailable=
                                   observation_issues.is_unavailable(item["source"], item["device_id"])):
                pending_mobile.pop(notification_id, None)
                delivery.cancel(notification_id, now, "incident_not_active")
                manager.cancel_request(item["source"], item["device_id"], now)
                await save_now()
                continue
            online = availability_by_device.get((item["source"], item["device_id"])) == "online"
            if (issue and online) or (not issue and not online and item["source"] != "simulator"):
                continue
            if item.get("stage", "mobile") == "mobile" and not notification_target_getter():
                continue
            retrying_mobile.add(notification_id)
            try:
                update = WorkflowUpdate(item["device_id"], item["source"], incident_id,
                                        current["status"] if current else "data_unavailable",
                                        item.get("action", "notify_requested"), now,
                                        tuple(item["reason_codes"]), notification_id)
                if item.get("stage", "mobile") == "local":
                    await create_notification(update, manager, retry_at=now)
                elif notification_target_getter():
                    if delivery.begin(notification_id, now):
                        await save_now()
                        try:
                            await send_mobile(hass, notification_target_getter(),
                                              item.get("title", "밥심 — 안부 확인 필요"), item["message"],
                                              str(item["action"]), item["source"], item["device_id"],
                                              incident_id)
                        except Exception as exc:
                            await finish_notification(update, manager, delivery_failure(exc), "mobile",
                                                      item.get("title", ""), item["message"],
                                                      type(exc).__name__)
                        else:
                            await finish_notification(update, manager, "ha_accepted", "complete",
                                                      item.get("title", ""), item["message"])
            finally:
                retrying_mobile.discard(notification_id)
            break  # Do not deliver all old requests at once after reconnection.

    return create_notification, retry_mobile_notifications
