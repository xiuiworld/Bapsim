"""Group canonical cooker transitions into cooking sessions.

This layer records what was observed. It does not infer meals or risk.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Mapping


@dataclass
class CookingSession:
    session_id: str
    device_id: str
    source: str
    cooking_started_at: str | None = None
    cooking_finished_at: str | None = None
    first_open_at: str | None = None
    lid_open_count: int = 0
    lid_open_times: list[str] = field(default_factory=list)
    data_gap: bool = False
    finalized: bool = False
    closed_at: str | None = None

    @property
    def incomplete(self) -> bool:
        return self.cooking_started_at is None or self.cooking_finished_at is None

    @property
    def first_open_delay_min(self) -> float | None:
        if self.cooking_finished_at is None or self.first_open_at is None:
            return None
        finished = datetime.fromisoformat(self.cooking_finished_at)
        opened = datetime.fromisoformat(self.first_open_at)
        return (opened - finished).total_seconds() / 60

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "incomplete": self.incomplete,
            "first_open_delay_min": self.first_open_delay_min,
        }


@dataclass
class SessionUpdate:
    kind: str  # session_updated | session_finalized
    session: CookingSession

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "session": self.session.as_dict()}


class CookingSessionBuilder:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], CookingSession] = {}

    def dump_state(self) -> list[dict[str, object]]:
        return [session.as_dict() for session in self._active.values()]

    def load_state(self, stored: list[dict[str, object]]) -> None:
        self._active.clear()
        for payload in stored:
            fields = CookingSession.__dataclass_fields__
            session = CookingSession(**{name: payload[name] for name in fields if name in payload})
            self._active[(session.source, session.device_id)] = session

    @staticmethod
    def _time(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("event time must include a timezone")
        return parsed

    @staticmethod
    def _new(source: str, device_id: str, at: str, *, orphan: bool) -> CookingSession:
        return CookingSession(
            session_id=f"{source}:{device_id}:{at}",
            device_id=device_id,
            source=source,
            cooking_finished_at=at if orphan else None,
            cooking_started_at=None if orphan else at,
        )

    @staticmethod
    def _update(kind: str, session: CookingSession) -> SessionUpdate:
        # Published updates are snapshots, not references to mutable live state.
        return SessionUpdate(kind, deepcopy(session))

    def ingest(self, event: Mapping[str, object]) -> list[SessionUpdate]:
        kind = event.get("event_type")
        if kind not in {
            "cooking_started",
            "cooking_finished",
            "lid_opened",
            "device_offline",
        }:
            return []
        source = event.get("source")
        device_id = event.get("device_id")
        at = event.get("observed_at")
        if not all(isinstance(item, str) and item for item in (source, device_id, at)):
            raise ValueError("event is missing source, device_id, or observed_at")
        moment = self._time(at)
        key = (source, device_id)
        session = self._active.get(key)

        if kind == "cooking_started":
            if session is not None and session.cooking_started_at is None:
                if moment < self._time(session.cooking_finished_at):
                    session.cooking_started_at = at
                    session.data_gap = False
                    return [self._update("session_updated", session)]
            if session is not None and session.cooking_started_at is not None:
                if moment <= self._time(session.cooking_started_at):
                    return []
            updates: list[SessionUpdate] = []
            if session is not None:
                session.finalized = True
                session.closed_at = at
                updates.append(self._update("session_finalized", session))
            session = self._new(source, device_id, at, orphan=False)
            self._active[key] = session
            updates.append(self._update("session_updated", session))
            return updates

        if kind == "cooking_finished":
            if session is None:
                session = self._new(source, device_id, at, orphan=True)
                session.data_gap = True
                self._active[key] = session
            elif session.cooking_started_at and moment < self._time(session.cooking_started_at):
                return []
            elif session.cooking_finished_at is not None:
                return []
            else:
                session.cooking_finished_at = at
            return [self._update("session_updated", session)]

        if session is None:
            return []
        if kind == "device_offline":
            if session.cooking_started_at and moment < self._time(session.cooking_started_at):
                return []
            if session.data_gap:
                return []
            session.data_gap = True
            return [self._update("session_updated", session)]

        if session.cooking_finished_at is None or moment < self._time(session.cooking_finished_at):
            return []
        session.lid_open_count += 1
        session.lid_open_times.append(at)
        if session.first_open_at is None:
            session.first_open_at = at
        return [self._update("session_updated", session)]

    def finalize_all(self) -> list[SessionUpdate]:
        """Close active sessions at the end of an offline replay or export."""
        updates = []
        for key, session in list(self._active.items()):
            session.finalized = True
            updates.append(self._update("session_finalized", session))
            del self._active[key]
        return updates
