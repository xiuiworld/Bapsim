"""Map HA Matter cooker entities to the unchanged raw event contract."""

from __future__ import annotations

from datetime import datetime
import re

from ..base.contracts import RawInputEvent


class MatterContactAdapter:
    def __init__(self, device_id: str, *, on_is_open: bool) -> None:
        if not device_id:
            raise ValueError("device_id is required")
        self.device_id = device_id
        self.on_is_open = on_is_open

    def translate(self, state: str, observed_at: str, received_at: str) -> RawInputEvent:
        for value in (observed_at, received_at):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Matter timestamps require timezone")
        if state in {"unavailable", "unknown"}:
            event_type, value, availability, quality = "availability", "offline", "offline", "unknown"
        elif state in {"on", "off"}:
            is_open = (state == "on") == self.on_is_open
            event_type, value, availability, quality = "lid_state", "open" if is_open else "closed", "online", "observed"
        else:
            raise ValueError("Matter contact state must be on, off, unavailable, or unknown")
        return {
            "device_id": self.device_id,
            "event_type": event_type,
            "value": value,
            "observed_at": observed_at,
            "received_at": received_at,
            "source": "matter",
            "availability": availability,
            "quality": quality,
        }


class MatterCookingStateAdapter:
    """Forward observed cooking states; the hardware's exact values are registered later."""

    def __init__(self, device_id: str, *, started_state: str, finished_state: str) -> None:
        if not device_id:
            raise ValueError("device_id is required")
        if not started_state or not finished_state or started_state == finished_state:
            raise ValueError("distinct started_state and finished_state are required")
        if {started_state, finished_state} & {"unknown", "unavailable"}:
            raise ValueError("unknown and unavailable are reserved HA states")
        self.device_id = device_id
        self.started_state = started_state
        self.finished_state = finished_state

    def translate(self, state: str, observed_at: str, received_at: str) -> RawInputEvent:
        for value in (observed_at, received_at):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Matter timestamps require timezone")
        if state in {"unknown", "unavailable"}:
            event_type, value, availability, quality = "cooking_input_availability", "offline", "offline", "unknown"
        elif state == self.started_state:
            event_type, value, availability, quality = "cooking_state", "started", "online", "observed"
        elif state == self.finished_state:
            event_type, value, availability, quality = "cooking_state", "finished", "online", "observed"
        else:
            raise ValueError("unmapped Matter cooking state")
        return {
            "device_id": self.device_id,
            "event_type": event_type,
            "value": value,
            "observed_at": observed_at,
            "received_at": received_at,
            "source": "matter",
            "availability": availability,
            "quality": quality,
        }


class MatterContextAdapter:
    """Forward another Matter entity's HA state without inferring activity."""

    def __init__(self, device_id: str, *, signal: str) -> None:
        if not isinstance(device_id, str) or not device_id or not isinstance(signal, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", signal):
            raise ValueError("device_id and a lowercase signal are required")
        if signal in {"lid", "cooking", "availability"}:
            raise ValueError("cooker signals are reserved")
        self.device_id = device_id
        self.signal = signal

    def translate(self, state: str, observed_at: str, received_at: str) -> RawInputEvent:
        for value in (observed_at, received_at):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Matter timestamps require timezone")
        available = state not in {"unknown", "unavailable"}
        return {
            "device_id": self.device_id,
            "event_type": f"{self.signal}_state",
            "value": state,
            "observed_at": observed_at,
            "received_at": received_at,
            "source": "matter",
            "availability": "online" if available else "offline",
            "quality": "observed" if available else "unknown",
        }


class MatterBoardSignalAdapter:
    """Preserve a board observation or inference as raw HA-received data."""

    def __init__(self, device_id: str, *, kind: str, signal: str) -> None:
        if not isinstance(device_id, str) or not device_id:
            raise ValueError("device_id is required")
        if kind not in {"observation", "inference"}:
            raise ValueError("kind must be observation or inference")
        if not isinstance(signal, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", signal):
            raise ValueError("signal must be lowercase alphanumeric with underscores")
        self.device_id = device_id
        self.kind = kind
        self.signal = signal

    def translate(self, state: str, observed_at: str, received_at: str) -> RawInputEvent:
        for value in (observed_at, received_at):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("Matter timestamps require timezone")
        available = state not in {"unknown", "unavailable"}
        return {
            "device_id": self.device_id,
            "event_type": f"board_{self.kind}_{self.signal}",
            "value": state,
            "observed_at": observed_at,
            "received_at": received_at,
            "source": "matter",
            "availability": "online" if available else "offline",
            "quality": "observed" if available else "unknown",
        }
