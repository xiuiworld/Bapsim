"""De-duplicate routine-change alerts and track caregiver responses."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Mapping


@dataclass(frozen=True)
class WorkflowUpdate:
    device_id: str
    source: str
    incident_id: str | None
    status: str
    action: str
    as_of: str
    reason_codes: tuple[str, ...]
    notification_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["reason_codes"] = list(self.reason_codes)
        return result


@dataclass
class _State:
    status: str = "normal"
    candidate_count: int = 0
    candidate_at: str | None = None
    incident_id: str | None = None
    last_notified_at: str | None = None
    reason_codes: tuple[str, ...] = ()
    notification_count: int = 0
    last_notification_window: str | None = None
    pending_window: str | None = None
    pending_action: str | None = None
    interval_hours: float | None = None
    next_reminder_at: str | None = None
    last_lid_open_at: str | None = None


class DetectionStateManager:
    def __init__(self, min_consecutive: int = 2, cooldown: timedelta = timedelta(hours=1),
                 candidate_window: timedelta = timedelta(hours=2), *,
                 reminder_cadence: timedelta | None = None, resumption_alerts: bool = True) -> None:
        if (min_consecutive < 1 or cooldown < timedelta(0) or candidate_window <= timedelta(0)
                or (reminder_cadence is not None and reminder_cadence <= timedelta(0))):
            raise ValueError("invalid state manager settings")
        self.min_consecutive = min_consecutive
        self.cooldown = cooldown
        self.candidate_window = candidate_window
        self.reminder_cadence = reminder_cadence
        self.resumption_alerts = resumption_alerts
        self._states: dict[tuple[str, str], _State] = {}

    @staticmethod
    def _time(value: str) -> datetime:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("workflow time must include a timezone")
        return result

    @staticmethod
    def _update(source: str, device_id: str, state: _State, action: str, as_of: str) -> WorkflowUpdate:
        notification_id = None
        if state.incident_id and action in {"notify_requested", "reminder_requested", "usage_resumed_requested"}:
            suffix = (f":reminder:{state.notification_count + 1}" if action == "reminder_requested"
                      else ":resumed" if action == "usage_resumed_requested" else ":initial")
            notification_id = state.incident_id + suffix
        return WorkflowUpdate(device_id, source, state.incident_id, state.status, action, as_of,
                              state.reason_codes, notification_id)

    @staticmethod
    def _evidence(detection: Mapping[str, object]) -> tuple[str | None, float | None]:
        evidence = detection.get("evidence")
        if not isinstance(evidence, Mapping):
            return None, None
        window = evidence.get("window_end")
        hours = evidence.get("threshold_hours")
        interval = float(hours) if isinstance(hours, (int, float)) and hours > 0 else None
        return window if isinstance(window, str) else None, interval

    def ingest(self, detection: Mapping[str, object]) -> list[WorkflowUpdate]:
        source = str(detection["source"])
        device_id = str(detection["device_id"])
        as_of = str(detection["as_of"])
        now = self._time(as_of)
        kind = detection.get("routine_status")
        key = (source, device_id)
        state = self._states.setdefault(key, _State())
        reasons = tuple(str(item) for item in detection.get("reason_codes", []))
        window, interval = self._evidence(detection)

        if kind == "check_required":
            if state.status in {"notified", "checking"}:
                if state.pending_action is not None or state.notification_count == 0:
                    return []
                if self.reminder_cadence is not None:
                    if state.last_notified_at is None or now < self._time(state.last_notified_at) + self.reminder_cadence:
                        return []
                elif window is not None:
                    previous = state.last_notification_window or state.last_notified_at
                    if previous is None or now <= self._time(previous) or window == state.pending_window:
                        return []
                    # Window decisions can arrive late; compare the actual window, not the tick time.
                    if state.last_notification_window and self._time(window) <= self._time(state.last_notification_window):
                        return []
                elif (state.next_reminder_at is None or now < self._time(state.next_reminder_at)
                      or interval is None):
                    return []
                state.pending_window = window
                state.interval_hours = interval
                state.pending_action = "reminder_requested"
                return [self._update(source, device_id, state, "reminder_requested", as_of)]
            if state.status == "check_required":
                return []
            if state.candidate_at is not None and now <= self._time(state.candidate_at):
                return []
            if state.last_notified_at is not None and now - self._time(state.last_notified_at) < self.cooldown:
                return []
            if state.candidate_at is not None and now - self._time(state.candidate_at) > self.candidate_window:
                state.candidate_count = 0
            state.candidate_count += 1
            state.candidate_at = as_of
            if state.candidate_count < self.min_consecutive:
                return []
            state.status = "check_required"
            state.incident_id = f"{source}:{device_id}:{as_of}"
            state.reason_codes = reasons
            state.candidate_count = 0
            state.notification_count = 0
            state.last_notification_window = None
            state.pending_window = window
            state.pending_action = "notify_requested"
            state.interval_hours = interval
            state.next_reminder_at = None
            return [self._update(source, device_id, state, "notify_requested", as_of)]

        if kind in {"no_expectation", "learning"}:
            return []
        state.candidate_count = 0
        state.candidate_at = None
        if kind == "away" and state.status in {"check_required", "notified", "checking"}:
            state.status = "resolved"
            state.pending_action = None
            return [self._update(source, device_id, state, "away_declared", as_of)]
        if kind == "normal" and state.status in {"check_required", "notified", "checking"}:
            state.status = "resolved"
            state.pending_action = None
            updates = [self._update(source, device_id, state, "auto_resolved", as_of)]
            if state.notification_count and self.resumption_alerts:
                updates.append(self._update(source, device_id, state, "usage_resumed_requested", as_of))
            return updates
        return []

    def ingest_lid_opening(self, source: str, device_id: str, observed_at: str) -> list[WorkflowUpdate]:
        """Resolve a gap only with a new opening after its current evidence."""
        moment = self._time(observed_at)
        state = self._states.setdefault((source, device_id), _State())
        if state.last_lid_open_at and moment <= self._time(state.last_lid_open_at):
            return []
        if state.candidate_at and moment <= self._time(state.candidate_at):
            return []
        if state.status in {"check_required", "notified", "checking"} and state.incident_id:
            prefix = f"{source}:{device_id}:"
            if state.incident_id.startswith(prefix) and moment <= self._time(state.incident_id[len(prefix):]):
                return []
        state.last_lid_open_at = observed_at
        return self.ingest({"source": source, "device_id": device_id,
                            "as_of": observed_at, "routine_status": "normal",
                            "reason_codes": ["USAGE_RESUMED"]})

    def mark_notified(self, source: str, device_id: str, as_of: str) -> WorkflowUpdate | None:
        state = self._states.get((source, device_id))
        if state is None or state.pending_action not in {"notify_requested", "reminder_requested"}:
            return None
        self._time(as_of)
        action = "reminder_sent" if state.pending_action == "reminder_requested" else "notification_sent"
        if state.status == "check_required":
            state.status = "notified"
        state.last_notified_at = as_of
        state.notification_count += 1
        state.last_notification_window = state.pending_window
        state.pending_action = None
        if state.interval_hours is not None:
            state.next_reminder_at = (self._time(as_of) + timedelta(hours=state.interval_hours)).isoformat()
        return self._update(source, device_id, state, action, as_of)

    def cancel_request(self, source: str, device_id: str, as_of: str) -> None:
        state = self._states.get((source, device_id))
        if state is None or state.pending_action not in {"notify_requested", "reminder_requested"}:
            return
        now = self._time(as_of)
        initial = state.pending_action == "notify_requested"
        state.pending_action = None
        if initial and state.status == "check_required":
            state.status = "resolved"
            state.candidate_count = 0
            state.candidate_at = None
        if state.interval_hours is not None:
            state.next_reminder_at = (now + timedelta(hours=state.interval_hours)).isoformat()

    def caregiver_response(self, source: str, device_id: str, response: str, as_of: str,
                           incident_id: str | None = None) -> WorkflowUpdate | None:
        state = self._states.get((source, device_id))
        if state is None:
            return None
        if incident_id is not None and incident_id != state.incident_id:
            return None
        self._time(as_of)
        if response == "checking" and state.status == "notified":
            state.status = "checking"
        elif response == "resolved" and state.status in {"check_required", "notified", "checking"}:
            state.status = "resolved"
        else:
            return None
        return self._update(source, device_id, state, "caregiver_response", as_of)

    def dump_state(self) -> list[dict[str, object]]:
        return [
            {"source": source, "device_id": device_id, **asdict(state)}
            for (source, device_id), state in self._states.items()
        ]

    def current_state(self, source: str, device_id: str) -> dict[str, object] | None:
        """Return one independent snapshot without exposing the storage layout."""
        state = self._states.get((source, device_id))
        return {"source": source, "device_id": device_id, **asdict(state)} if state else None

    def load_state(self, stored: list[dict[str, object]]) -> None:
        self._states.clear()
        for item in stored:
            state = _State(**{name: item[name] for name in _State.__dataclass_fields__ if name in item})
            state.reason_codes = tuple(state.reason_codes)
            if state.notification_count == 0 and state.last_notified_at and state.status in {"notified", "checking"}:
                state.notification_count = 1
            if state.status == "check_required" and state.incident_id and state.pending_action is None:
                state.pending_action = "notify_requested"
            self._states[(str(item["source"]), str(item["device_id"]))] = state
