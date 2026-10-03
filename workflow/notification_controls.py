"""Persist bounded caregiver notification controls without treating them as safety evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping


def _time(value: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("control times require a timezone")
    return moment.astimezone(timezone.utc)


class NotificationControls:
    def __init__(self) -> None:
        self.pauses: dict[str, dict[str, str]] = {}
        self.acknowledgements: dict[str, str] = {}
        self.commands: dict[str, dict[str, str]] = {}
        self.command_requests: dict[str, dict[str, object]] = {}
        self.revision = 0

    def load_state(self, stored: Mapping[str, object]) -> None:
        self.pauses = {str(key): dict(value) for key, value in stored.get("pauses", {}).items()}
        self.acknowledgements = dict(stored.get("acknowledgements", {}))
        self.commands = {str(key): dict(value) for key, value in stored.get("commands", {}).items()}
        self.command_requests = {str(key): dict(value) for key, value in stored.get("command_requests", {}).items()}
        self.revision = int(stored.get("revision", 0))

    def dump_state(self) -> dict[str, object]:
        return {"pauses": self.pauses, "acknowledgements": self.acknowledgements,
                "commands": self.commands, "command_requests": self.command_requests,
                "revision": self.revision}

    def apply(self, command: Mapping[str, object], now: str) -> dict[str, str]:
        moment = _time(now)
        command_id = command.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            raise ValueError("command_id is required")
        if command_id in self.commands:
            if command_id in self.command_requests and dict(command) != self.command_requests[command_id]:
                raise ValueError("command_id was already used for another command")
            return self.commands[command_id]
        action = command.get("action")
        if action == "acknowledge":
            incident_id, ends_at = command.get("assessment_id", command.get("incident_id")), command.get("ends_at")
            if not isinstance(incident_id, str) or not incident_id or not isinstance(ends_at, str):
                raise ValueError("incident_id and ends_at are required")
            if _time(ends_at) <= moment:
                raise ValueError("acknowledgement end must be in the future")
            self.acknowledgements[incident_id] = ends_at
        elif action == "pause_lifestyle":
            pause_id, starts_at, ends_at = (command.get(key) for key in ("pause_id", "starts_at", "ends_at"))
            if not all(isinstance(value, str) and value for value in (pause_id, starts_at, ends_at)):
                raise ValueError("pause_id, starts_at and ends_at are required")
            if _time(starts_at) >= _time(ends_at) or _time(ends_at) <= moment:
                raise ValueError("pause interval must have a future end")
            self.pauses[pause_id] = {"starts_at": starts_at, "ends_at": ends_at}
        elif action == "cancel_pause":
            pause_id = command.get("pause_id")
            if not isinstance(pause_id, str) or pause_id not in self.pauses:
                raise ValueError("known pause_id is required")
            del self.pauses[pause_id]
        else:
            raise ValueError("unsupported control action")
        self.revision += 1
        result = {"command_id": command_id, "status": "accepted", "action": str(action)}
        self.commands[command_id] = result
        self.command_requests[command_id] = dict(command)
        return result

    def is_paused(self, at: str) -> bool:
        moment = _time(at)
        return any(_time(item["starts_at"]) <= moment < _time(item["ends_at"])
                   for item in self.pauses.values())

    def is_acknowledged(self, incident_id: str | None, at: str) -> bool:
        if not incident_id or incident_id not in self.acknowledgements:
            return False
        return _time(at) < _time(self.acknowledgements[incident_id])
