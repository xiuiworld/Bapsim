"""Reproducible synthetic evaluation of the active personal-usage model.

Labels come from scripted behavior, never from the model's score. All model
updates and predictions are processed in timestamp order for each household.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random

from domain.personal_model import PersonalUsageModel


KST = timezone(timedelta(hours=9))
START = datetime(2026, 1, 5, tzinfo=KST)
SCHEDULES = {
    "morning": (8,),
    "midday": (13,),
    "evening": (19,),
    "twice_daily": (8, 19),
}
TRAIN_SEEDS = (11, 17, 23)
HOLDOUT_SEEDS = (41, 47, 53)
THRESHOLDS = (0.72, 0.84, 0.90, 0.95)
TRAIN_DAYS = 21
TOTAL_DAYS = 35


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    return START + timedelta(days=day, hours=hour, minutes=minute)


def _event(device_id: str, kind: str, moment: datetime) -> dict[str, str]:
    return {"source": "simulator", "device_id": device_id, "event_type": kind,
            "observed_at": moment.isoformat(),
            "availability": "offline" if kind == "device_offline" else "online"}


def run_household(seed: int, schedule: str, threshold: float) -> list[dict[str, object]]:
    """Train online on earlier days and score later days without future data."""
    if schedule not in SCHEDULES:
        raise ValueError("unknown synthetic schedule")
    rng = Random(seed * 100 + len(schedule))
    device_id = f"synthetic_{schedule}_{seed}"
    model = PersonalUsageModel(alert_probability=threshold)
    gap_days = {24 + seed % 2, 25 + seed % 2, 32}
    natural_skip_days = {4 + seed % 3, 10 + seed % 2, 17, 22, 33}
    late_use_days = {23, 29}
    away_day, outage_day = 28, 30
    rows: list[dict[str, object]] = []
    last_open: datetime | None = None

    for day in range(TOTAL_DAYS):
        away, online = day == away_day, day != outage_day
        gap = day in gap_days
        natural_skip = day in natural_skip_days
        late_use = day in late_use_days
        baseline = _at(day, 0)
        model.set_away(away, baseline.isoformat())
        model.observe(_event(device_id, "device_online" if online else "device_offline", baseline))
        actions: list[tuple[datetime, str]] = []
        if online and not away:
            for index, hour in enumerate(SCHEDULES[schedule]):
                if (gap or natural_skip) and index == len(SCHEDULES[schedule]) - 1:
                    continue
                jitter = max(-35, min(35, round(rng.gauss(0, 15))))
                opening = _at(day, hour) + timedelta(minutes=jitter)
                if late_use and index == len(SCHEDULES[schedule]) - 1:
                    opening += timedelta(hours=3)
                actions.append((opening, "open"))
                if rng.random() < 0.25:
                    actions.append((opening + timedelta(minutes=8), "repeat_open"))
        for hour, minute in ((2, 30), (10, 30), (15, 30), (21, 30)):
            actions.append((_at(day, hour, minute), "evaluate"))
        flagged = False
        statuses: set[str] = set()
        for moment, action in sorted(actions):
            if action in {"open", "repeat_open"}:
                model.observe(_event(device_id, "lid_opened", moment))
                last_open = moment
            else:
                decisions = model.evaluate("simulator", device_id, moment.isoformat(),
                                           online=online, away=away)
                statuses.update(item["routine_status"] for item in decisions)
                flagged |= any(item["routine_status"] == "check_required" for item in decisions)
        end = _at(day, 23, 59)
        if online:
            model.availability("simulator", device_id, True, end.isoformat())
        profile = model.profile("simulator", device_id, end.isoformat())
        fixed_24h = bool(online and not away and last_open is not None and
                         end - last_open > timedelta(hours=24))
        rows.append({
            "household": device_id, "schedule": schedule, "seed": seed,
            "day": day, "split": "history" if day < TRAIN_DAYS else "future",
            "scenario": "away" if away else "offline" if not online else "scripted_gap" if gap else
                        "natural_skip" if natural_skip else "late_use" if late_use else "normal",
            "label": None if away or not online or day < TRAIN_DAYS else int(gap),
            "personal_alert": int(flagged), "fixed_24h_alert": int(fixed_24h),
            "learning": not bool(profile["ready"]), "statuses": sorted(statuses),
            "last_open_at": profile["last_lid_open_at"],
        })
    return rows


def cohort(seeds: tuple[int, ...], threshold: float) -> list[dict[str, object]]:
    return [row for seed in seeds for schedule in SCHEDULES
            for row in run_household(seed, schedule, threshold)]


def score(rows: list[dict[str, object]], field: str) -> dict[str, int | float]:
    future = [row for row in rows if row["split"] == "future"]
    eligible = [row for row in future if row["label"] is not None]
    tp = sum(row["label"] == 1 and row[field] == 1 for row in eligible)
    fp = sum(row["label"] == 0 and row[field] == 1 for row in eligible)
    fn = sum(row["label"] == 1 and row[field] == 0 for row in eligible)
    tn = sum(row["label"] == 0 and row[field] == 0 for row in eligible)
    recall = tp / (tp + fn) if tp + fn else 0.0
    false_positive_rate = fp / (fp + tn) if fp + tn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "recall": round(recall, 4), "false_positive_rate": round(false_positive_rate, 4),
            "abstained_days": sum(row["label"] is None for row in future),
            "alerted_away_or_offline": sum(row["label"] is None and row[field] == 1 for row in future)}


def scenario_checks() -> dict[str, object]:
    """Exercise the separately agreed irregular-gap and multi-day decline paths."""
    irregular = PersonalUsageModel()
    irregular_id = "synthetic_irregular"
    irregular.observe(_event(irregular_id, "device_online", _at(0, 0)))
    openings = []
    for day in range(16):
        opening = _at(day, (3 + day * 7) % 24)
        irregular.observe(_event(irregular_id, "lid_opened", opening))
        openings.append(opening)
    irregular_at = openings[-1] + timedelta(hours=62)
    irregular_result = irregular.evaluate_interval(
        "simulator", irregular_id, irregular_at.isoformat(), online=True)

    decline = PersonalUsageModel()
    decline_id = "synthetic_decline"
    decline.observe(_event(decline_id, "device_online", _at(0, 0)))
    trend_results = []
    for day in range(28):
        for hour in ((8, 19) if day < 14 else (19,)):
            decline.observe(_event(decline_id, "lid_opened", _at(day, hour)))
        outcome = decline.evaluate_trend("simulator", decline_id,
                                         _at(day + 1, 0, 1).isoformat(), online=True)
        if outcome is not None:
            trend_results.append({"day": day + 1, "status": outcome["routine_status"],
                                  "reason_codes": outcome["reason_codes"]})
    return {
        "irregular_gap": {
            "status": irregular_result["routine_status"] if irregular_result else "insufficient",
            "p95_hours": irregular_result["evidence"]["p95_hours"] if irregular_result else None,
            "elapsed_hours": irregular_result["evidence"]["elapsed_hours"] if irregular_result else None,
        },
        "gradual_decline": trend_results,
    }


def experiment() -> dict[str, object]:
    training = []
    for threshold in THRESHOLDS:
        rows = cohort(TRAIN_SEEDS, threshold)
        result = score(rows, "personal_alert")
        training.append({"threshold": threshold, **result})
    # Select using only training households. Penalize nuisance alerts; ties
    # choose the existing 0.72 setting if it is among the best candidates.
    def objective(item: dict[str, object]) -> tuple[float, float]:
        return (float(item["recall"]) - 2 * float(item["false_positive_rate"]),
                -abs(float(item["threshold"]) - 0.72))
    selected = max(training, key=objective)["threshold"]
    holdout = cohort(HOLDOUT_SEEDS, float(selected))
    return {
        "scope": "Synthetic cooker-use scenarios only; no meal or health outcome labels.",
        "model": "PersonalUsageModel from domain/personal_model/engine.py",
        "train_seeds": list(TRAIN_SEEDS), "holdout_seeds": list(HOLDOUT_SEEDS),
        "history_days_per_household": TRAIN_DAYS, "future_days_per_household": TOTAL_DAYS - TRAIN_DAYS,
        "training_candidates": training, "selected_threshold": selected,
        "holdout_personal": score(holdout, "personal_alert"),
        "holdout_fixed_24h": score(holdout, "fixed_24h_alert"),
        "holdout_by_schedule": {
            schedule: score([row for row in holdout if row["schedule"] == schedule], "personal_alert")
            for schedule in SCHEDULES
        },
        "additional_scenarios": scenario_checks(),
        "example_future_rows": [row for row in holdout if row["seed"] == HOLDOUT_SEEDS[0]
                                and row["schedule"] == "twice_daily" and row["split"] == "future"],
    }
