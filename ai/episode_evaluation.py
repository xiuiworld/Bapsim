"""Score first caregiver requests by scripted interruption episode and delay."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import median
from typing import Iterable, Mapping


def episode_score(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    episodes: dict[tuple[str, int], list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        if row.get("label") == 1 and isinstance(row.get("episode_id"), int):
            episodes[(str(row["household"]), int(row["episode_id"]))].append(row)
    delays: list[float] = []
    missed = []
    for (household, episode_id), members in episodes.items():
        members.sort(key=lambda row: int(row["day"]))
        start = datetime.fromisoformat(str(members[0]["missed_use_at"]))
        last = datetime.fromisoformat(str(members[-1]["missed_use_at"]))
        end = datetime.combine(last.date() + timedelta(days=1), time.min, last.tzinfo)
        times = [datetime.fromisoformat(str(at)) for row in members
                 for at in row.get("notification_request_times", [])]
        matches = [at for at in times if start <= at < end]
        if matches:
            delays.append(round((min(matches) - start).total_seconds() / 3600, 2))
        else:
            missed.append({"household": household, "episode_id": episode_id})
    total = len(episodes)
    ordered = sorted(delays)
    return {"episodes": total, "caught": len(delays), "missed": len(missed),
            "recall": round(len(delays) / total, 4) if total else 0.0,
            "median_delay_hours": round(median(delays), 2) if delays else None,
            "p90_delay_hours": ordered[(9 * len(ordered) + 9) // 10 - 1] if delays else None,
            "missed_examples": missed[:5]}
