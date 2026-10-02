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


class DetectionStateManager:
    def __init__(self, min_consecutive: int = 2, cooldown: timedelta = timedelta(hours=1),
                 candidate_window: timedelta = timedelta(hours=2)) -> None:
        if min_consecutive < 1 or cooldown < timedelta(0) or candidate_window <= timedelta(0):
            raise ValueError("invalid state manager settings")
        self.min_consecutive = min_consecutive
        self.cooldown = cooldown
        self.candidate_window = candidate_window
        self._states: dict[tuple[str, str], _State] = {}

    @staticmethod
    def _time(value: str) -> datetime:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("workflow time must include a timezone")
        return result

    @staticmethod
    def _update(source: str, device_id: str, state: _State, action: str, as_of: str) -> WorkflowUpdate:
        return WorkflowUpdate(device_id, source, state.incident_id, state.status, action, as_of, state.reason_codes)

    def ingest(self, detection: Mapping[str, object]) -> list[WorkflowUpdate]:
        source = str(detection["source"])
        device_id = str(detection["device_id"])
        as_of = str(detection["as_of"])
        now = self._time(as_of)
        kind = detection.get("routine_status")
        key = (source, device_id)
        state = self._states.setdefault(key, _State())
        reasons = tuple(str(item) for item in detection.get("reason_codes", []))

        if kind == "check_required":
            if state.status in {"check_required", "notified", "checking"}:
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
            return [self._update(source, device_id, state, "notify_requested", as_of)]

        if kind == "no_expectation":
            return []
        state.candidate_count = 0
        state.candidate_at = None
        if kind == "away" and state.status in {"check_required", "notified", "checking"}:
            state.status = "resolved"
            return [self._update(source, device_id, state, "away_declared", as_of)]
        if kind == "normal" and state.status in {"check_required", "notified", "checking"}:
            state.status = "resolved"
            return [self._update(source, device_id, state, "auto_resolved", as_of)]
        return []

    def mark_notified(self, source: str, device_id: str, as_of: str) -> WorkflowUpdate | None:
        state = self._states.get((source, device_id))
        if state is None or state.status != "check_required":
            return None
        self._time(as_of)
        state.status = "notified"
        state.last_notified_at = as_of
        return self._update(source, device_id, state, "notification_sent", as_of)

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

    def load_state(self, stored: list[dict[str, object]]) -> None:
        self._states.clear()
        for item in stored:
            state = _State(**{name: item[name] for name in _State.__dataclass_fields__ if name in item})
            state.reason_codes = tuple(state.reason_codes)
            self._states[(str(item["source"]), str(item["device_id"]))] = state
