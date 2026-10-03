"""Train and challenge cooker-use settings on distinct synthetic conditions.

These labels describe scripted cooker-use interruptions, never meals or safety.
The condition parameters are engineering assumptions, not population estimates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random

from domain.personal_model import PersonalUsageModel


KST = timezone(timedelta(hours=9))
START = datetime(2026, 2, 2, tzinfo=KST)
HISTORY_DAYS = 21
FUTURE_DAYS = 21
TRAIN_SEEDS = (113, 127)
HOLDOUT_SEEDS = (809, 821)
TRAIN_CONDITIONS = ("stable", "late_shift", "natural_skips", "batch_three_day")
HOLDOUT_CONDITIONS = ("gradual_drift", "weekend_shift", "skip_and_late")
RHYTHMS = {
    "older_morning": {"age": "65_plus", "hours": (8,), "skip": 0.07, "period": 1},
    "middle_evening": {"age": "50_64", "hours": (19,), "skip": 0.10, "period": 1},
    "older_twice": {"age": "65_plus", "hours": (8, 18), "skip": 0.08, "period": 1},
    "older_alternate": {"age": "65_plus", "hours": (17,), "skip": 0.06, "period": 2},
}
SETTINGS = {
    "current": {"alert_probability": 0.72, "late_grace_hours": 0.0, "interval_quantile": 0.95},
    "higher_threshold": {"alert_probability": 0.84, "late_grace_hours": 0.0, "interval_quantile": 0.95},
    "late_grace": {"alert_probability": 0.72, "late_grace_hours": 1.0, "interval_quantile": 0.95},
    "grace_and_cadence": {"alert_probability": 0.72, "late_grace_hours": 1.0, "interval_quantile": 0.75},
}


def _at(day: int, hour: float = 0) -> datetime:
    return START + timedelta(days=day, hours=hour)


def _event(device: str, kind: str, at: datetime) -> dict[str, str]:
    return {"source": "simulator", "device_id": device, "event_type": kind,
            "observed_at": at.isoformat(), "availability": "online"}


def _planned_days(rhythm: str, condition: str) -> list[int]:
    period = 3 if condition == "batch_three_day" else int(RHYTHMS[rhythm]["period"])
    return [day for day in range(HISTORY_DAYS, HISTORY_DAYS + FUTURE_DAYS)
            if day % period == 0 and day not in {HISTORY_DAYS + 4, HISTORY_DAYS + 13}]


def household(seed: int, rhythm: str, condition: str,
              setting: dict[str, float]) -> list[dict[str, object]]:
    if rhythm not in RHYTHMS or condition not in TRAIN_CONDITIONS + HOLDOUT_CONDITIONS:
        raise ValueError("unknown synthetic rhythm or condition")
    spec = RHYTHMS[rhythm]
    rng = Random(f"condition-v1:{seed}:{rhythm}:{condition}")
    device = f"{rhythm}_{condition}_{seed}"
    model = PersonalUsageModel(**setting)
    planned = _planned_days(rhythm, condition)
    interruptions = {planned[len(planned) // 3], planned[2 * len(planned) // 3]}
    last_open: datetime | None = None
    rows: list[dict[str, object]] = []
    for day in range(HISTORY_DAYS + FUTURE_DAYS):
        start = _at(day)
        away = day == HISTORY_DAYS + 4
        offline = day == HISTORY_DAYS + 13
        period = 3 if condition == "batch_three_day" else int(spec["period"])
        scheduled = day % period == 0
        interruption = day in interruptions
        skip_rate = float(spec["skip"]) + (0.18 if condition == "natural_skips" else 0.23
                                               if condition == "skip_and_late" else 0)
        natural_skip = scheduled and not interruption and rng.random() < skip_rate
        shift = (2.0 if condition == "late_shift" else
                 3.0 * max(0, day - HISTORY_DAYS) / FUTURE_DAYS
                 if condition == "gradual_drift" else
                 3.0 if condition == "weekend_shift" and start.weekday() >= 5 else
                 1.0 if condition == "skip_and_late" else 0)
        model.set_away(away, start.isoformat())
        model.observe(_event(device, "device_offline" if offline else "device_online", start))
        actions: list[tuple[datetime, str]] = []
        if scheduled and not (away or offline or interruption or natural_skip):
            for hour in spec["hours"]:
                jitter = max(-90, min(90, rng.gauss(0, 35)))
                opening = _at(day, int(hour)) + timedelta(hours=shift, minutes=jitter)
                actions.append((opening, "open"))
                if rng.random() < 0.25:
                    actions.append((opening + timedelta(minutes=12), "repeat"))
        windows = model.profile("simulator", device, start.isoformat())["windows"]
        grace = setting["late_grace_hours"]
        actions.extend((datetime.fromisoformat(window["expected_time"]) +
                        timedelta(hours=2.5 + grace), "evaluate") for window in windows)
        flagged = False
        for moment, action in sorted(actions):
            if action in {"open", "repeat"}:
                model.observe(_event(device, "lid_opened", moment))
                last_open = moment
            else:
                decisions = model.evaluate("simulator", device, moment.isoformat(),
                                           online=not offline, away=away)
                flagged |= any(item["routine_status"] == "check_required" for item in decisions)
        end = _at(day, 23 + 59 / 60)
        if not offline:
            model.availability("simulator", device, True, end.isoformat())
        interval = model.evaluate_interval("simulator", device, end.isoformat(),
                                           online=not offline, away=away)
        flagged |= bool(interval and interval["routine_status"] == "check_required")
        if day >= HISTORY_DAYS:
            rows.append({"household": device, "rhythm": rhythm, "age_group": spec["age"],
                         "condition": condition, "day": day,
                         "label": None if away or offline else int(interruption),
                         "candidate": int(flagged),
                         "fixed_24h": int(not away and not offline and last_open is not None and
                                          end - last_open > timedelta(hours=24)),
                         "reason": "away" if away else "offline" if offline else
                                   "interruption" if interruption else
                                   "ordinary_nonuse" if natural_skip or not scheduled else "routine"})
    return rows


def cohort(seeds: tuple[int, ...], conditions: tuple[str, ...],
           setting: dict[str, float]) -> list[dict[str, object]]:
    return [row for condition in conditions for seed in seeds for rhythm in RHYTHMS
            for row in household(seed, rhythm, condition, setting)]


def score(rows: list[dict[str, object]], field: str = "candidate") -> dict[str, int | float]:
    eligible = [row for row in rows if row["label"] is not None]
    tp = sum(row["label"] == 1 and row[field] == 1 for row in eligible)
    fp = sum(row["label"] == 0 and row[field] == 1 for row in eligible)
    fn = sum(row["label"] == 1 and row[field] == 0 for row in eligible)
    tn = sum(row["label"] == 0 and row[field] == 0 for row in eligible)
    homes = len({row["household"] for row in rows})
    return {"households": homes, "eligible_days": len(eligible),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
            "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0,
            "nuisance_per_home_21d": round(fp / homes, 3) if homes else 0.0,
            "excluded_days": len(rows) - len(eligible),
            "alerted_away_or_offline": sum(row["label"] is None and row[field] == 1 for row in rows)}


def by_condition(rows: list[dict[str, object]]) -> dict[str, dict[str, int | float]]:
    return {condition: score([row for row in rows if row["condition"] == condition])
            for condition in sorted({row["condition"] for row in rows})}


def experiment() -> dict[str, object]:
    training = {name: score(cohort(TRAIN_SEEDS, TRAIN_CONDITIONS, setting))
                for name, setting in SETTINGS.items()}
    # Select using training households and training conditions only. The
    # penalty gives nuisance candidates twice the weight of missed scripts.
    selected = max(SETTINGS, key=lambda name: (
        training[name]["recall"] - 2 * training[name]["false_positive_rate"],
        name == "current"))
    current_rows = cohort(HOLDOUT_SEEDS, HOLDOUT_CONDITIONS, SETTINGS["current"])
    candidate_rows = (current_rows if selected == "current" else
                      cohort(HOLDOUT_SEEDS, HOLDOUT_CONDITIONS, SETTINGS[selected]))
    return {
        "scope": "Synthetic cooker-use scenarios only; no meal, health, or safety ground truth",
        "history_days": HISTORY_DAYS, "future_days": FUTURE_DAYS,
        "rhythms": list(RHYTHMS), "train_conditions": list(TRAIN_CONDITIONS),
        "holdout_conditions": list(HOLDOUT_CONDITIONS),
        "train_seeds": list(TRAIN_SEEDS), "holdout_seeds": list(HOLDOUT_SEEDS),
        "candidate_settings": SETTINGS, "training": training,
        "selected": selected, "selected_settings": SETTINGS[selected],
        "holdout_current": score(current_rows),
        "holdout_selected": score(candidate_rows),
        "holdout_fixed_24h": score(current_rows, "fixed_24h"),
        "by_condition_current": by_condition(current_rows),
        "by_condition_selected": by_condition(candidate_rows),
        "by_rhythm_current": {rhythm: score([row for row in current_rows if row["rhythm"] == rhythm])
                              for rhythm in RHYTHMS},
        "by_rhythm_selected": {rhythm: score([row for row in candidate_rows if row["rhythm"] == rhythm])
                               for rhythm in RHYTHMS},
        "example_rows": candidate_rows[:8],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(experiment(), ensure_ascii=False, indent=2))
