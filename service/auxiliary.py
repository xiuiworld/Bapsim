"""Simulation-only activity evidence with a bounded alert deferral."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("auxiliary event needs timezone")
    return result.astimezone(timezone.utc)


class AuxiliaryDeferral:
    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []
        self.pending: list[dict[str, object]] = []

    def dump_state(self) -> dict[str, object]:
        return {"events": self.events, "pending": self.pending}

    def load_state(self, state: Mapping[str, object]) -> None:
        self.events = [dict(item) for item in state.get("events", [])]
        self.pending = [dict(item) for item in state.get("pending", [])]

    def record(self, device_id: str, at: str, signal: str) -> None:
        _time(at)
        if signal not in {"door_opened", "motion_detected", "appliance_used"}:
            raise ValueError("unsupported simulated activity")
        self.events.append({"device_id": device_id, "at": at, "signal": signal})
        cutoff = _time(at) - timedelta(hours=4)
        self.events = [item for item in self.events if _time(item["at"]) >= cutoff]

    def defer(self, decision: Mapping[str, object]) -> bool:
        if decision.get("source") != "simulator" or decision.get("routine_status") != "check_required":
            return False
        at = _time(str(decision["as_of"]))
        device_id = str(decision["device_id"])
        activity = [item for item in self.events if item["device_id"] == device_id
                    and at - timedelta(hours=2) <= _time(item["at"]) <= at]
        if not activity:
            return False
        self.pending.append({"decision": dict(decision), "started_at": at.isoformat(),
                             "due_at": (at + timedelta(minutes=30)).isoformat(),
                             "last_activity_at": max(activity, key=lambda item: _time(item["at"]))["at"]})
        return True

    def clear(self) -> None:
        self.pending.clear()

    def due(self, at: str) -> list[dict[str, object]]:
        now = _time(at)
        ready = []
        remaining = []
        for item in self.pending:
            due_at = _time(item["due_at"])
            if now < due_at:
                remaining.append(item)
                continue
            start = _time(item["started_at"])
            recent = [event for event in self.events if event["device_id"] == item["decision"]["device_id"]
                      and _time(item["last_activity_at"]) < _time(event["at"]) <= now]
            if recent and now < start + timedelta(hours=2):
                item["last_activity_at"] = max(recent, key=lambda event: _time(event["at"]))["at"]
                item["due_at"] = min(now + timedelta(minutes=30), start + timedelta(hours=2)).isoformat()
                remaining.append(item)
            else:
                ready.append({**item["decision"], "as_of": now.isoformat(),
                              "deferred_from": item["decision"]["as_of"],
                              "reason_codes": [*item["decision"].get("reason_codes", []), "AUXILIARY_ACTIVITY"]})
        self.pending = remaining
        return ready
