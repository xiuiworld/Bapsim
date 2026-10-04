"""Score first caregiver requests by scripted interruption episode and delay."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import median
from typing import Iterable, Mapping


def episode_score(rows: Iterable[Mapping[str, object]]) -> dict[str, object]:
    episodes: dict[tuple[str, int], list[Mapping[str, object]]] = defaultdict(list)
    requests: dict[str, list[datetime]] = defaultdict(list)
    for row in rows:
        requests[str(row["household"])].extend(
            datetime.fromisoformat(str(at)) for at in row.get("notification_request_times", []))
        if row.get("label") == 1 and isinstance(row.get("episode_id"), int):
            episodes[(str(row["household"]), int(row["episode_id"]))].append(row)
    delays: list[float] = []
    missed = []
    early_only = 0
    for (household, episode_id), members in episodes.items():
        members.sort(key=lambda row: int(row["day"]))
        opportunity_times = [datetime.fromisoformat(str(at)) for member in members
                             for at in member.get("missed_opportunity_times", [])]
        # Opportunity-based episodes are complete only after their last
        # planned opening has been missed. Keep legacy day-based scores intact.
        start = (max(opportunity_times) if opportunity_times else
                 datetime.fromisoformat(str(members[0]["missed_use_at"])))
        last = (max(opportunity_times) if opportunity_times else
                datetime.fromisoformat(str(members[-1]["missed_use_at"])))
        end = (start + timedelta(days=1) if opportunity_times else
               datetime.combine(last.date() + timedelta(days=1), time.min, last.tzinfo))
        matches = [at for at in requests[household] if start <= at < end]
        if matches:
            delays.append(round((min(matches) - start).total_seconds() / 3600, 2))
        else:
            missed.append({"household": household, "episode_id": episode_id})
            if opportunity_times and any(min(opportunity_times) <= at < start
                                         for at in requests[household]):
                early_only += 1
    total = len(episodes)
    ordered = sorted(delays)
    return {"episodes": total, "caught": len(delays), "missed": len(missed),
            "early_request_only": early_only,
            "recall": round(len(delays) / total, 4) if total else 0.0,
            "median_delay_hours": round(median(delays), 2) if delays else None,
            "p90_delay_hours": ordered[(9 * len(ordered) + 9) // 10 - 1] if delays else None,
            "missed_examples": missed[:5]}
