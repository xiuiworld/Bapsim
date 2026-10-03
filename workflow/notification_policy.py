"""Transport-independent rules for deciding whether a caregiver alert is still valid."""

from __future__ import annotations

from typing import Callable, Mapping

from .notification_controls import NotificationControls
from .state_manager import DetectionStateManager, WorkflowUpdate


ALERT_ACTIONS = frozenset({"notify_requested", "reminder_requested",
                           "usage_resumed_requested", "observation_issue_requested"})


def suppressed(action: str, incident_id: str | None,
               controls: NotificationControls, now: str) -> bool:
    if action == "observation_issue_requested":
        return False
    return controls.is_paused(now) or (
        action != "usage_resumed_requested" and controls.is_acknowledged(incident_id, now)
    )


def current_request(action: str, state: Mapping[str, object] | None, *,
                    observation_unavailable: bool = False) -> bool:
    if action == "observation_issue_requested":
        return observation_unavailable
    if state is None:
        return False
    if action == "usage_resumed_requested":
        return state.get("status") == "resolved" and int(state.get("notification_count", 0)) > 0
    if action == "legacy_mobile_retry":
        return state.get("status") in {"notified", "checking"}
    if action == "reminder_requested":
        return (state.get("status") in {"notified", "checking"} and
                state.get("pending_action") == action)
    if action == "notify_requested":
        return state.get("status") == "check_required" and state.get("pending_action") == action
    return False


def quiet_release_allowed(action: str, state: Mapping[str, object] | None, *,
                          observation_unavailable: bool, online: bool, away: bool,
                          controls: NotificationControls, now: str,
                          incident_id: str) -> bool:
    if action == "observation_issue_requested":
        return observation_unavailable and not online
    return (not away and online and not suppressed(action, incident_id, controls, now) and
            current_request(action, state))


def queue_quiet(pending: dict[str, dict[str, str]], update: object) -> None:
    action = str(update.action)
    if action == "observation_issue_requested":
        for key, item in tuple(pending.items()):
            if (item.get("action") == action and
                    (item["source"], item["device_id"]) == (update.source, update.device_id)):
                pending.pop(key, None)
    notification_id = str(update.notification_id or update.incident_id)
    pending[notification_id] = {
        "incident_id": str(update.incident_id), "source": update.source,
        "device_id": update.device_id, "action": action,
        "notification_id": notification_id,
    }


def release_quiet(
        pending: dict[str, dict[str, str]], gap: DetectionStateManager,
        decline: DetectionStateManager, controls: NotificationControls,
        observation_unavailable: Callable[[str, str], bool],
        online: Callable[[str, str], bool], away: bool, now: str,
) -> list[tuple[WorkflowUpdate, DetectionStateManager]]:
    """Consume deferred alerts once and return requests that remain safe to deliver."""
    ready = []
    for notification_id, item in tuple(pending.items()):
        pending.pop(notification_id, None)
        source, device_id = item["source"], item["device_id"]
        incident_id = item["incident_id"]
        action = item.get("action", "notify_requested")
        is_online = online(source, device_id)
        if action == "observation_issue_requested":
            if quiet_release_allowed(action, None, observation_unavailable=
                                     observation_unavailable(source, device_id),
                                     online=is_online, away=away, controls=controls,
                                     now=now, incident_id=incident_id):
                ready.append((WorkflowUpdate(device_id, source, incident_id, "data_unavailable",
                                             action, now, ("COOKER_DATA_UNAVAILABLE",),
                                             item.get("notification_id")), gap))
            continue
        # The older merged snapshot gave the trend manager precedence if IDs collide.
        current = decline.current_state(source, device_id)
        manager = decline
        if current is None or current["incident_id"] != incident_id:
            current = gap.current_state(source, device_id)
            manager = gap
        if current is None or current["incident_id"] != incident_id:
            continue
        if away or not is_online or suppressed(action, incident_id, controls, now):
            continue
        if not quiet_release_allowed(action, current, observation_unavailable=False,
                                     online=is_online, away=away, controls=controls,
                                     now=now, incident_id=incident_id):
            manager.cancel_request(source, device_id, now)
            continue
        ready.append((WorkflowUpdate(device_id, source, incident_id, str(current["status"]),
                                     action, now, tuple(current["reason_codes"]),
                                     item.get("notification_id")), manager))
    return ready
