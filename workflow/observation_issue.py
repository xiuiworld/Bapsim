"""Track sustained cooker observation outages separately from lifestyle incidents."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping

from .quiet_hours import KST
from .state_manager import WorkflowUpdate


class ObservationIssueTracker:
    def __init__(self, delay: timedelta = timedelta(minutes=10)) -> None:
        if delay <= timedelta(0):
            raise ValueError("observation issue delay must be positive")
        self.delay = delay
        self._states: dict[tuple[str, str], dict[str, str | None]] = {}

    def update(self, source: str, device_id: str, online: bool, as_of: str) -> WorkflowUpdate | None:
        now = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("observation issue time requires timezone")
        state = self._states.setdefault((source, device_id),
                                        {"offline_since": None, "last_alert_day": None})
        if online:
            state["offline_since"] = None
            return None
        if state["offline_since"] is None:
            state["offline_since"] = as_of
            return None
        since = datetime.fromisoformat(str(state["offline_since"]).replace("Z", "+00:00"))
        day = now.astimezone(KST).date().isoformat()
        if now - since < self.delay or state["last_alert_day"] == day:
            return None
        state["last_alert_day"] = day
        incident_id = f"observation:{source}:{device_id}:{day}"
        return WorkflowUpdate(device_id, source, incident_id, "data_unavailable",
                              "observation_issue_requested", as_of,
                              ("COOKER_DATA_UNAVAILABLE",), incident_id)

    def is_unavailable(self, source: str, device_id: str) -> bool:
        state = self._states.get((source, device_id))
        return state is not None and state["offline_since"] is not None

    def dump_state(self) -> list[dict[str, str | None]]:
        return [{"source": source, "device_id": device_id, **state}
                for (source, device_id), state in self._states.items()]

    def load_state(self, stored: list[Mapping[str, object]]) -> None:
        self._states = {
            (str(item["source"]), str(item["device_id"])): {
                "offline_since": str(item["offline_since"]) if item.get("offline_since") else None,
                "last_alert_day": str(item["last_alert_day"]) if item.get("last_alert_day") else None,
            } for item in stored
        }
