"""Extract quantitative behavior from session snapshots.

Missing measurements are ``None``; an unavailable sensor is never treated as
zero activity by this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Mapping


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("feature time must include a timezone")
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class CookerFeatures:
    device_id: str
    source: str
    as_of: str
    availability: str
    cook_count_24h: int | None
    lid_open_count_24h: int | None
    hours_since_last_open: float | None
    median_first_open_delay_min: float | None
    latest_first_open_delay_min: float | None
    pending_first_open_delay_min: float | None
    cooking_in_progress: bool
    cook_interval_hours: float | None
    has_data_gap: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class CookerFeatureExtractor:
    def __init__(self, history_days: int = 14) -> None:
        if history_days < 1:
            raise ValueError("history_days must be positive")
        self.history_days = history_days
        self._sessions: dict[tuple[str, str, str], dict[str, object]] = {}

    def dump_state(self) -> list[dict[str, object]]:
        return [dict(session) for session in self._sessions.values()]

    def load_state(self, stored: list[dict[str, object]]) -> None:
        self._sessions.clear()
        for session in stored:
            self.ingest({"session": session})

    def ingest(self, update: Mapping[str, object]) -> None:
        session = update.get("session")
        if not isinstance(session, Mapping):
            raise ValueError("session update must include a session object")
        source = session.get("source")
        device_id = session.get("device_id")
        session_id = session.get("session_id")
        if not all(isinstance(item, str) and item for item in (source, device_id, session_id)):
            raise ValueError("session is missing source, device_id, or session_id")
        self._sessions[(source, device_id, session_id)] = dict(session)

    def extract(self, source: str, device_id: str, as_of: str, availability: str) -> CookerFeatures:
        if availability not in {"online", "offline", "unknown"}:
            raise ValueError("invalid availability")
        now = _time(as_of)
        day_start = now - timedelta(hours=24)
        history_start = now - timedelta(days=self.history_days)
        sessions = [
            session
            for (s, d, _), session in self._sessions.items()
            if s == source and d == device_id
        ]

        starts: list[datetime] = []
        delays: list[float] = []
        latest_delay: tuple[datetime, float] | None = None
        opens: list[datetime] = []
        gap = False
        active_session: tuple[datetime, Mapping[str, object]] | None = None
        for session in sessions:
            raw_start = session.get("cooking_started_at")
            started = _time(raw_start) if isinstance(raw_start, str) else None
            raw_finish = session.get("cooking_finished_at")
            finished = _time(raw_finish) if isinstance(raw_finish, str) else None
            raw_first = session.get("first_open_at")
            first = _time(raw_first) if isinstance(raw_first, str) else None
            anchor = started or finished
            if anchor is not None and anchor <= now and not session.get("finalized"):
                if active_session is None or anchor > active_session[0]:
                    active_session = (anchor, session)
            if started is not None and history_start < started <= now:
                starts.append(started)
            if finished is not None and first is not None and history_start < finished <= first <= now:
                delay = (first - finished).total_seconds() / 60
                if delay >= 0:
                    delays.append(delay)
                    if latest_delay is None or finished > latest_delay[0]:
                        latest_delay = (finished, delay)
            raw_opens = session.get("lid_open_times", [])
            if not isinstance(raw_opens, list):
                raise ValueError("lid_open_times must be a list")
            for item in raw_opens:
                if isinstance(item, str):
                    opened = _time(item)
                    if opened <= now:
                        opens.append(opened)
            raw_closed = session.get("closed_at")
            closed = _time(raw_closed) if isinstance(raw_closed, str) else None
            gap_anchor = started or finished
            if session.get("data_gap") and gap_anchor is not None and gap_anchor <= now and (
                closed is None or closed > day_start
            ):
                gap = True

        starts.sort()
        opens.sort()
        intervals = [
            (later - earlier).total_seconds() / 3600
            for earlier, later in zip(starts, starts[1:])
        ]
        reliable = availability == "online"
        pending_delay = None
        cooking_in_progress = False
        if active_session is not None:
            current = active_session[1]
            finished_at = current.get("cooking_finished_at")
            if isinstance(finished_at, str):
                first_at = current.get("first_open_at")
                if first_at is None and reliable:
                    pending_delay = max(0.0, (now - _time(finished_at)).total_seconds() / 60)
            else:
                cooking_in_progress = True
        return CookerFeatures(
            device_id=device_id,
            source=source,
            as_of=as_of,
            availability=availability,
            cook_count_24h=sum(day_start < item <= now for item in starts) if reliable else None,
            lid_open_count_24h=sum(day_start < item <= now for item in opens) if reliable else None,
            hours_since_last_open=(now - opens[-1]).total_seconds() / 3600 if opens and reliable else None,
            median_first_open_delay_min=float(median(delays)) if delays else None,
            latest_first_open_delay_min=latest_delay[1] if latest_delay else None,
            pending_first_open_delay_min=pending_delay,
            cooking_in_progress=cooking_in_progress,
            cook_interval_hours=float(median(intervals)) if intervals else None,
            has_data_gap=gap,
        )
