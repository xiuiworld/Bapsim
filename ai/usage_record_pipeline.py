"""Offline, household-isolated evaluation of normalized cooker observations.

Labels are independent human reviews (or explicit scripted demo scenarios).
This module never publishes a notification or changes the live model state.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Iterable

from domain.personal_model import PersonalUsageModel
from workflow import DetectionStateManager


KST = timezone(timedelta(hours=9))
SCHEMA = 1
CHECKS = ((2, 30), (10, 30), (15, 30), (21, 30))
EVENT_TYPES = {"device_online", "device_offline", "lid_opened", "away_state"}
REVIEWS = {"check_needed", "no_check_needed", "unknown"}
BASES = {"scripted_scenario", "user_confirmed", "caregiver_confirmed", "unreviewed"}


def moment(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO 8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp needs a timezone")
    return parsed.astimezone(timezone.utc)


def _identity(row: dict[str, object]) -> tuple[str, str, str, str]:
    if row.get("schema_version") != SCHEMA:
        raise ValueError("unsupported schema_version")
    mode = row.get("mode")
    if mode not in {"simulation", "live", "replay"}:
        raise ValueError("mode must be simulation, live, or replay")
    household, device, source = (row.get(key) for key in ("household_id", "device_id", "source"))
    if not all(isinstance(item, str) and item and not any(char.isspace() for char in item)
               for item in (household, device, source)):
        raise ValueError("household_id, device_id and source need nonempty pseudonymous IDs")
    if mode == "simulation" and source != "simulator":
        raise ValueError("simulation must use source=simulator")
    if mode == "live" and source == "simulator":
        raise ValueError("live observations cannot use source=simulator")
    return mode, household, device, source


def validate_observation(row: dict[str, object]) -> dict[str, object]:
    _identity(row)
    kind = row.get("event_type")
    if kind not in EVENT_TYPES:
        raise ValueError("unsupported normalized event_type")
    moment(row.get("observed_at"))
    if kind == "away_state":
        if type(row.get("away")) is not bool:
            raise ValueError("away_state needs boolean away")
    else:
        expected = "offline" if kind == "device_offline" else "online"
        if row.get("availability") != expected:
            raise ValueError("event availability disagrees with event_type")
    return row


def validate_label(row: dict[str, object]) -> dict[str, object]:
    mode, _, _, _ = _identity(row)
    try:
        date.fromisoformat(str(row["local_date"]))
    except (KeyError, ValueError) as exc:
        raise ValueError("label needs local_date YYYY-MM-DD") from exc
    review, basis = row.get("review"), row.get("basis")
    if review not in REVIEWS or basis not in BASES:
        raise ValueError("invalid review or basis")
    if (review == "unknown") != (basis == "unreviewed"):
        raise ValueError("unknown review must use unreviewed basis, and vice versa")
    if mode == "simulation" and review != "unknown" and basis != "scripted_scenario":
        raise ValueError("simulation labels need scripted_scenario basis")
    if mode != "simulation" and basis == "scripted_scenario":
        raise ValueError("scripted labels cannot describe real observations")
    return row


def read_jsonl(path: Path, validator) -> list[dict[str, object]]:
    result = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("row must be an object")
            result.append(validator(row))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"{path.name}:{number}: {exc}") from exc
    if not result:
        raise ValueError(f"{path.name}: no records")
    return result


def _coverage(events: list[dict[str, object]], day: date, max_age: timedelta) -> str | None:
    """Require fresh connection evidence throughout a full local day."""
    start = datetime.combine(day, time.min, KST).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    state_online = False
    state_away = False
    heartbeats: list[datetime] = []
    for event in events:
        at = moment(event["observed_at"])
        if at >= end:
            break
        kind = event["event_type"]
        if at <= start:
            if kind in {"device_online", "device_offline"}:
                state_online = kind == "device_online"
            elif kind == "away_state":
                state_away = bool(event["away"])
            if kind == "device_online":
                heartbeats = [at]
            continue
        if state_away:
            return "away"
        if not state_online:
            return "no_connection_evidence"
        if kind == "device_offline":
            return "device_offline"
        if kind == "away_state" and event["away"]:
            return "away"
        if kind == "device_online":
            state_online = True
            heartbeats.append(at)
        elif kind == "away_state":
            state_away = False
    if state_away:
        return "away"
    if not state_online or not heartbeats:
        return "no_connection_evidence"
    if heartbeats[0] < start - max_age:
        return "stale_connection_evidence"
    if any(right - left > max_age for left, right in zip([start, *heartbeats], [*heartbeats, end])):
        return "stale_connection_evidence"
    return None


def _metrics(rows: list[dict[str, object]], field: str) -> dict[str, int | float]:
    eligible = [row for row in rows if row["abstain_reason"] is None]
    tp = sum(row["review"] == "check_needed" and row[field] for row in eligible)
    fp = sum(row["review"] == "no_check_needed" and row[field] for row in eligible)
    fn = sum(row["review"] == "check_needed" and not row[field] for row in eligible)
    tn = sum(row["review"] == "no_check_needed" and not row[field] for row in eligible)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": round(tp / (tp + fp), 4) if tp + fp else None,
            "recall": round(tp / (tp + fn), 4) if tp + fn else None,
            "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else None,
            "accuracy": round((tp + tn) / len(eligible), 4) if eligible else None,
            "label_positive_days": tp + fn, "label_negative_days": fp + tn,
            "eligible_days": len(eligible), "abstained_days": len(rows) - len(eligible)}


def _workflow_input(source: str, device: str, at: datetime, status: str) -> dict[str, object]:
    return {"source": source, "device_id": device, "as_of": at.isoformat(),
            "routine_status": status, "reason_codes": []}


def evaluate(observations: Iterable[dict[str, object]], labels: Iterable[dict[str, object]],
             evaluation_start: date, *, history_days: int = 21,
             max_heartbeat_age: timedelta = timedelta(minutes=90)) -> dict[str, object]:
    if history_days < 7 or max_heartbeat_age <= timedelta(0):
        raise ValueError("history_days must be >=7 and max_heartbeat_age positive")
    grouped: dict[tuple[str, str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in observations:
        validate_observation(row)
        grouped[_identity(row)].append(row)
    if not grouped:
        raise ValueError("no observations")
    for events in grouped.values():
        events.sort(key=lambda row: moment(row["observed_at"]))
    seen: set[tuple[tuple[str, str, str, str], str]] = set()
    grouped_labels: dict[tuple[str, str, str, str], list[dict[str, object]]] = defaultdict(list)
    for row in labels:
        validate_label(row)
        key = _identity(row)
        marker = key, str(row["local_date"])
        if marker in seen:
            raise ValueError("duplicate household/device/day label")
        seen.add(marker)
        if key not in grouped:
            raise ValueError("label has no matching observations")
        if date.fromisoformat(str(row["local_date"])) >= evaluation_start:
            grouped_labels[key].append(row)
    if not grouped_labels:
        raise ValueError("no labels on or after evaluation_start")
    report_rows: list[dict[str, object]] = []
    for key, day_labels in grouped_labels.items():
        mode, household, device, source = key
        events = grouped[key]
        first_day = moment(events[0]["observed_at"]).astimezone(KST).date()
        if evaluation_start < first_day + timedelta(days=history_days):
            raise ValueError(f"{household}/{device}: evaluation_start needs {history_days} earlier calendar days")
        last_day = max(date.fromisoformat(str(row["local_date"])) for row in day_labels)
        if any(date.fromisoformat(str(row["local_date"])) >
               moment(events[-1]["observed_at"]).astimezone(KST).date() for row in day_labels):
            raise ValueError("label extends beyond observations")
        indexed_labels = {date.fromisoformat(str(row["local_date"])): row for row in day_labels}
        model = PersonalUsageModel()
        gap = DetectionStateManager(candidate_window=timedelta(hours=36))
        decline = DetectionStateManager(min_consecutive=1, candidate_window=timedelta(days=1))
        cursor = 0
        online = away = False
        last_heartbeat: datetime | None = None
        last_open: datetime | None = None
        day = first_day
        while day <= last_day:
            checks = [datetime.combine(day, time(hour, minute), KST).astimezone(timezone.utc)
                      for hour, minute in CHECKS]
            checks.append(datetime.combine(day, time(23, 59), KST).astimezone(timezone.utc))
            candidates: list[str] = []
            requests: list[str] = []
            for checkpoint in checks:
                while cursor < len(events) and moment(events[cursor]["observed_at"]) <= checkpoint:
                    event = events[cursor]
                    at = moment(event["observed_at"])
                    kind = event["event_type"]
                    if kind == "away_state":
                        away = bool(event["away"])
                        model.set_away(away, at.isoformat())
                        if away:
                            for manager in (gap, decline):
                                manager.ingest(_workflow_input(source, device, at, "away"))
                    else:
                        model.observe(event)
                        if kind == "device_online":
                            online, last_heartbeat = True, at
                        elif kind == "device_offline":
                            online = False
                            for manager in (gap, decline):
                                manager.ingest(_workflow_input(source, device, at, "data_unavailable"))
                        elif kind == "lid_opened" and online and not away:
                            last_open = at
                            gap.ingest(_workflow_input(source, device, at, "normal"))
                    cursor += 1
                fresh = bool(online and last_heartbeat is not None and
                             checkpoint - last_heartbeat <= max_heartbeat_age)
                if online and not fresh and last_heartbeat is not None:
                    model.availability(source, device, False, last_heartbeat.isoformat())
                    online = False
                    for manager in (gap, decline):
                        manager.ingest(_workflow_input(source, device, checkpoint, "data_unavailable"))
                if checkpoint == checks[-1]:
                    continue
                decisions = model.evaluate(source, device, checkpoint.isoformat(), online=fresh, away=away)
                interval = model.evaluate_interval(source, device, checkpoint.isoformat(), online=fresh, away=away)
                trend = model.evaluate_trend(source, device, checkpoint.isoformat(), online=fresh, away=away)
                for decision in [*decisions, *([interval] if interval else []), *([trend] if trend else [])]:
                    if decision["routine_status"] == "check_required":
                        candidates.extend(decision["reason_codes"])
                for decision in decisions:
                    requests.extend(code for update in gap.ingest(decision)
                                    if update.action == "notify_requested" for code in update.reason_codes)
                if interval and interval["routine_status"] in {"normal", "check_required"}:
                    requests.extend(code for update in gap.ingest(interval)
                                    if update.action == "notify_requested" for code in update.reason_codes)
                if trend:
                    requests.extend(code for update in decline.ingest(trend)
                                    if update.action == "notify_requested" for code in update.reason_codes)
            label = indexed_labels.get(day)
            if label is not None:
                reason = _coverage(events, day, max_heartbeat_age)
                if label["review"] == "unknown" and reason is None:
                    reason = "unreviewed"
                end = checks[-1]
                report_rows.append({"mode": mode, "household_id": household, "device_id": device,
                                    "local_date": day.isoformat(), "review": label["review"],
                                    "basis": label["basis"], "abstain_reason": reason,
                                    "personal_candidate": bool(candidates) if reason is None else None,
                                    "notification_request": bool(requests) if reason is None else None,
                                    "fixed_24h_candidate": (bool(last_open is not None and
                                                                 end - last_open > timedelta(hours=24))
                                                            if reason is None else None),
                                    "reason_codes": sorted(set(candidates)) if reason is None else [],
                                    "notification_reason_codes": sorted(set(requests)) if reason is None else []})
            day += timedelta(days=1)
    report_rows.sort(key=lambda row: (row["mode"], row["household_id"], row["device_id"], row["local_date"]))
    return {"schema_version": SCHEMA, "scope": "Offline internal candidates; no meal or health inference or push delivery",
            "evaluation_start": evaluation_start.isoformat(), "history_days": history_days,
            "max_heartbeat_age_minutes": int(max_heartbeat_age.total_seconds() / 60),
            "personal": _metrics(report_rows, "personal_candidate"),
            "notification_request": _metrics(report_rows, "notification_request"),
            "fixed_24h": _metrics(report_rows, "fixed_24h_candidate"), "days": report_rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate pseudonymous normalized usage JSONL locally")
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--evaluation-start", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--history-days", type=int, default=21)
    args = parser.parse_args()
    result = evaluate(read_jsonl(args.observations, validate_observation),
                      read_jsonl(args.labels, validate_label), args.evaluation_start,
                      history_days=args.history_days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(f"{args.output}: {result['personal']['eligible_days']} eligible, "
          f"{result['personal']['abstained_days']} abstained days")


if __name__ == "__main__":
    main()
