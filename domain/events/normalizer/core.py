"""Stateful cooker event normalization; no detection or behavior inference.

The first known state establishes a baseline. Only a later change produces a
behavior event. A lid change must remain stable for ``lid_debounce`` before it
is emitted. Availability changes are emitted immediately and reset state.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Mapping


@dataclass(frozen=True)
class NormalizedCookerEvent:
    device_id: str
    event_type: str
    value: str
    observed_at: str
    received_at: str
    source: str
    availability: str
    quality: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class _PendingLid:
    value: str
    due_at: datetime
    raw: dict[str, str]


@dataclass
class _DeviceState:
    availability: str = "unknown"
    availability_observed: datetime | None = None
    lid: str | None = None
    cooking: str | None = None
    pending_lid: _PendingLid | None = None
    last_observed: dict[str, datetime] = field(default_factory=dict)


class CookerEventNormalizer:
    """Normalize raw reports from registered input adapters.

    ``raw_fields`` maps (source, raw event_type) to a cooker state channel.
    ``raw_values`` maps (source, raw event_type, raw value) to a canonical state.
    The future Matter integration can supply its mappings without changing
    normalized event consumers.
    """

    def __init__(
        self,
        lid_debounce: timedelta = timedelta(seconds=2),
        raw_fields: Mapping[tuple[str, str], str] | None = None,
        raw_values: Mapping[tuple[str, str, str], str] | None = None,
    ) -> None:
        if lid_debounce < timedelta(0):
            raise ValueError("lid_debounce must be nonnegative")
        self.lid_debounce = lid_debounce
        self.raw_fields = dict(
            {
                ("simulator", "lid_state"): "lid",
                ("simulator", "cooking_state"): "cooking",
                ("simulator", "availability"): "availability",
            }
            if raw_fields is None
            else raw_fields
        )
        self.raw_values = dict(raw_values or {})
        self._devices: dict[tuple[str, str], _DeviceState] = {}

    def dump_state(self) -> list[dict[str, object]]:
        # A contact candidate still inside its 2-second debounce period is
        # deliberately not restored as a confirmed transition.
        return [
            {
                "source": source,
                "device_id": device_id,
                "availability": state.availability,
                "availability_observed": state.availability_observed.isoformat() if state.availability_observed else None,
                "lid": state.lid,
                "cooking": state.cooking,
                "last_observed": {name: at.isoformat() for name, at in state.last_observed.items()},
            }
            for (source, device_id), state in self._devices.items()
        ]

    def load_state(self, stored: list[dict[str, object]]) -> None:
        self._devices.clear()
        for item in stored:
            source = str(item["source"])
            device_id = str(item["device_id"])
            availability_at = item.get("availability_observed")
            observed = item.get("last_observed", {})
            if not isinstance(observed, dict):
                raise ValueError("invalid stored observation timestamps")
            self._devices[(source, device_id)] = _DeviceState(
                availability=str(item.get("availability", "unknown")),
                availability_observed=self._timestamp(availability_at) if isinstance(availability_at, str) else None,
                lid=item.get("lid"),
                cooking=item.get("cooking"),
                last_observed={name: self._timestamp(at) for name, at in observed.items()},
            )

    @staticmethod
    def _timestamp(value: str) -> datetime:
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("timestamp must be ISO 8601") from exc
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return result.astimezone(timezone.utc)

    @staticmethod
    def _payload(raw: Mapping[str, object]) -> dict[str, str]:
        fields = (
            "device_id",
            "event_type",
            "value",
            "observed_at",
            "received_at",
            "source",
            "availability",
            "quality",
        )
        if any(not isinstance(raw.get(name), str) for name in fields):
            raise ValueError("raw event is missing a string contract field")
        payload = {name: str(raw[name]) for name in fields}
        if not payload["device_id"] or not payload["source"]:
            raise ValueError("device_id and source are required")
        if payload["availability"] not in {"online", "offline", "unknown"}:
            raise ValueError("invalid availability")
        return payload

    @staticmethod
    def _event(raw: dict[str, str], event_type: str, value: str) -> NormalizedCookerEvent:
        return NormalizedCookerEvent(**{**raw, "event_type": event_type, "value": value})

    @property
    def next_deadline(self) -> datetime | None:
        deadlines = (
            state.pending_lid.due_at
            for state in self._devices.values()
            if state.pending_lid is not None
        )
        return min(deadlines, default=None)

    def flush(self, now: datetime) -> list[NormalizedCookerEvent]:
        """Confirm all lid candidates whose debounce time has elapsed."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("flush time must include a timezone")
        emitted: list[NormalizedCookerEvent] = []
        for state in self._devices.values():
            pending = state.pending_lid
            if pending is None or pending.due_at > now:
                continue
            state.pending_lid = None
            if state.lid == pending.value:
                continue
            state.lid = pending.value
            kind = "lid_opened" if pending.value == "open" else "lid_closed"
            emitted.append(self._event(pending.raw, kind, pending.value))
        return emitted

    def ingest(self, raw: Mapping[str, object]) -> list[NormalizedCookerEvent]:
        payload = self._payload(raw)
        observed = self._timestamp(payload["observed_at"])
        received = self._timestamp(payload["received_at"])
        emitted = self.flush(received)

        channel = self.raw_fields.get((payload["source"], payload["event_type"]))
        if channel not in {"lid", "cooking", "availability"}:
            return emitted
        key = (payload["source"], payload["device_id"])
        state = self._devices.setdefault(key, _DeviceState())
        last = state.last_observed.get(channel)
        if last is not None and observed <= last:
            return emitted
        state.last_observed[channel] = observed

        value = self.raw_values.get(
            (payload["source"], payload["event_type"], payload["value"]),
            payload["value"],
        )
        availability = value if channel == "availability" else payload["availability"]
        if availability not in {"online", "offline", "unknown"}:
            return emitted
        if state.availability_observed is not None and observed < state.availability_observed:
            return emitted
        state.availability_observed = observed
        if availability != state.availability:
            state.availability = availability
            state.lid = None
            state.cooking = None
            state.pending_lid = None
            if availability in {"online", "offline"}:
                kind = "device_online" if availability == "online" else "device_offline"
                emitted.append(self._event(payload, kind, availability))
        if channel == "availability" or availability != "online":
            return emitted

        if channel == "lid":
            if value not in {"open", "closed"}:
                state.lid = None
                state.pending_lid = None
            elif state.lid is None:
                state.lid = value
                state.pending_lid = None
            elif value == state.lid:
                state.pending_lid = None
            elif self.lid_debounce == timedelta(0):
                state.lid = value
                state.pending_lid = None
                kind = "lid_opened" if value == "open" else "lid_closed"
                emitted.append(self._event(payload, kind, value))
            elif state.pending_lid is None or state.pending_lid.value != value:
                state.pending_lid = _PendingLid(value, received + self.lid_debounce, payload)
            return emitted

        if value not in {"started", "finished"}:
            state.cooking = None
        elif state.cooking is None:
            state.cooking = value
        elif value != state.cooking:
            state.cooking = value
            kind = "cooking_started" if value == "started" else "cooking_finished"
            emitted.append(self._event(payload, kind, value))
        return emitted
