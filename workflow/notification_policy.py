"""Transport-independent rules for deciding whether a caregiver alert is still valid."""

from __future__ import annotations

from typing import Mapping

from .notification_controls import NotificationControls


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
