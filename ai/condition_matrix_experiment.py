"""Exploratory stress test, including profiles outside the product target.

These labels describe scripted cooker-use interruptions, never meals or safety.
The condition parameters are engineering assumptions, not population estimates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random
from typing import Mapping

from domain.personal_model import PersonalUsageModel
from workflow import DetectionStateManager


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
    "longer_confirmation": {"alert_probability": 0.72, "late_grace_hours": 0.0,
                            "interval_quantile": 0.95, "candidate_window_hours": 72},
    "longer_grace_cadence": {"alert_probability": 0.72, "late_grace_hours": 1.0,
                             "interval_quantile": 0.75, "candidate_window_hours": 72},
    "single_high_confidence": {"alert_probability": 0.84, "late_grace_hours": 1.0,
                                "interval_quantile": 0.95, "min_consecutive": 1},
    "single_cadence": {"alert_probability": 0.84, "late_grace_hours": 1.0,
                       "interval_quantile": 0.75, "min_consecutive": 1},
}


def _at(day: int, hour: float = 0) -> datetime:
    return START + timedelta(days=day, hours=hour)


def _event(device: str, kind: str, at: datetime) -> dict[str, str]:
    return {"source": "simulator", "device_id": device, "event_type": kind,
            "observed_at": at.isoformat(), "availability": "online"}


def _planned_days(rhythm: str, condition: str, future_days: int = FUTURE_DAYS) -> list[int]:
    period = 3 if condition == "batch_three_day" else int(RHYTHMS[rhythm]["period"])
    return [day for day in range(HISTORY_DAYS, HISTORY_DAYS + future_days)
            if day % period == 0 and day not in {HISTORY_DAYS + 4, HISTORY_DAYS + 13}]


def household(seed: int, rhythm: str, condition: str,
              setting: dict[str, float], *, variation: Mapping[str, float | str] | None = None,
              opportunity_interruptions: bool = False, runtime_ticks: bool = False,
              tick_minutes: int = 60, future_days: int = FUTURE_DAYS
              ) -> list[dict[str, object]]:
    if rhythm not in RHYTHMS or condition not in TRAIN_CONDITIONS + HOLDOUT_CONDITIONS:
        raise ValueError("unknown synthetic rhythm or condition")
    if tick_minutes < 1 or 1440 % tick_minutes:
        raise ValueError("tick_minutes must divide one day")
    if future_days < 10:
        raise ValueError("future_days needs room for two separate episodes")
    spec = RHYTHMS[rhythm]
    variation = variation or {}
    variation_name = str(variation.get("name", "base"))
    identity = f"condition-v1:{seed}:{rhythm}:{condition}"
    rng = Random(identity if variation_name == "base" else f"{identity}:{variation_name}")
    device = f"{rhythm}_{condition}_{seed}"
    if variation_name != "base":
        device += f"_{variation_name}"
    model = PersonalUsageModel(**{key: setting[key] for key in
                                  ("alert_probability", "late_grace_hours", "interval_quantile")},
                               cadence_window_support=bool(setting.get("cadence_window_support", False)),
                               interval_with_clock_windows=bool(
                                   setting.get("interval_with_clock_windows", False)))
    planned = _planned_days(rhythm, condition, future_days)
    if opportunity_interruptions:
        opportunities = [(day, int(hour)) for day in planned for hour in spec["hours"]]
        interrupted_opportunities = {
            opportunities[index + offset]: number
            for number, index in enumerate((len(opportunities) // 3,
                                            2 * len(opportunities) // 3), 1)
            for offset in (0, 1)
        }
        episodes = {day: number for (day, _), number in interrupted_opportunities.items()}
        interruptions = {day for day, _ in interrupted_opportunities}
    else:
        # Legacy stress replay interrupts two planned-use days, which removes
        # more individual openings for a twice-daily than once-daily home.
        episodes = {planned[index + offset]: number
                    for number, index in enumerate((len(planned) // 3, 2 * len(planned) // 3), 1)
                    for offset in (0, 1)}
        interruptions = set(episodes)
        interrupted_opportunities = {}
    notifications = DetectionStateManager(candidate_window=timedelta(
                                               hours=setting.get("candidate_window_hours", 36)),
                                           min_consecutive=int(setting.get("min_consecutive", 2)),
                                           resumption_alerts=False)
    last_open: datetime | None = None
    rows: list[dict[str, object]] = []
    carry_actions: list[tuple[datetime, str]] = []
    for day in range(HISTORY_DAYS + future_days):
        start = _at(day)
        away = day == HISTORY_DAYS + 4
        offline = day == HISTORY_DAYS + 13
        period = 3 if condition == "batch_three_day" else int(spec["period"])
        scheduled = day % period == 0
        interruption = day in interruptions
        skip_rate = min(0.95, float(spec["skip"]) + float(variation.get("skip_delta", 0)) +
                        (0.18 if condition == "natural_skips" else 0.23
                         if condition == "skip_and_late" else 0))
        natural_skip = (scheduled and not interruption and rng.random() < skip_rate
                        if not opportunity_interruptions else False)
        shift = (2.0 if condition == "late_shift" else
                 3.0 * max(0, day - HISTORY_DAYS) / future_days
                 if condition == "gradual_drift" else
                 3.0 if condition == "weekend_shift" and start.weekday() >= 5 else
                 1.0 if condition == "skip_and_late" else 0) + float(variation.get("shift_hours", 0))
        model.set_away(away, start.isoformat())
        model.observe(_event(device, "device_offline" if offline else "device_online", start))
        actions: list[tuple[datetime, str]] = carry_actions
        if scheduled and not (away or offline or natural_skip):
            for hour in spec["hours"]:
                if interruption and (not opportunity_interruptions or
                                     (day, int(hour)) in interrupted_opportunities):
                    continue
                if opportunity_interruptions and rng.random() < skip_rate:
                    continue
                jitter = max(-90, min(90, rng.gauss(0, float(variation.get("jitter_minutes", 35)))))
                opening = _at(day, int(hour)) + timedelta(hours=shift, minutes=jitter)
                miss_rate = float(variation.get("missed_open_probability", 0))
                if miss_rate == 0 or rng.random() >= miss_rate:
                    actions.append((opening, "open"))
                    if rng.random() < 0.25:
                        actions.append((opening + timedelta(minutes=12), "repeat"))
        windows = model.profile("simulator", device, start.isoformat())["windows"]
        grace = setting["late_grace_hours"]
        if runtime_ticks and day >= HISTORY_DAYS:
            actions.extend((_at(day) + timedelta(minutes=minute), "evaluate")
                           for minute in range(0, 1440, tick_minutes))
            for window in windows:
                due = datetime.fromisoformat(window["expected_time"]) + timedelta(hours=2 + grace)
                minute = due.replace(second=0, microsecond=0)
                actions.append((minute + (timedelta(minutes=1) if due > minute else timedelta()),
                                "evaluate"))
        else:
            actions.extend((datetime.fromisoformat(window["expected_time"]) +
                            timedelta(hours=2.5 + grace), "evaluate") for window in windows)
        carry_actions = [item for item in actions if item[0] >= _at(day + 1)]
        actions = [item for item in actions if item[0] < _at(day + 1)]
        flagged = False
        requested = False
        request_times: list[str] = []
        for moment, action in sorted(actions):
            if action in {"open", "repeat"}:
                model.observe(_event(device, "lid_opened", moment))
                notifications.ingest({"source": "simulator", "device_id": device,
                                      "as_of": moment.isoformat(), "routine_status": "normal",
                                      "reason_codes": ["USAGE_RESUMED"]})
                last_open = moment
            else:
                decisions = model.evaluate("simulator", device, moment.isoformat(),
                                           online=not offline, away=away)
                flagged |= any(item["routine_status"] == "check_required" for item in decisions)
                for decision in decisions:
                    for update in notifications.ingest(decision):
                        if update.action == "notify_requested":
                            requested = True
                            request_times.append(update.as_of)
                            notifications.mark_notified("simulator", device, update.as_of)
                if runtime_ticks and day >= HISTORY_DAYS:
                    interval = model.evaluate_interval("simulator", device, moment.isoformat(),
                                                       online=not offline, away=away)
                    flagged |= bool(interval and interval["routine_status"] == "check_required")
                    if interval:
                        for update in notifications.ingest(interval):
                            if update.action == "notify_requested":
                                requested = True
                                request_times.append(update.as_of)
                                notifications.mark_notified("simulator", device, update.as_of)
        end = _at(day, 23 + 59 / 60)
        if not offline:
            model.availability("simulator", device, True, end.isoformat())
        if not runtime_ticks or day < HISTORY_DAYS:
            interval = model.evaluate_interval("simulator", device, end.isoformat(),
                                               online=not offline, away=away)
            flagged |= bool(interval and interval["routine_status"] == "check_required")
            if interval:
                for update in notifications.ingest(interval):
                    if update.action == "notify_requested":
                        requested = True
                        request_times.append(update.as_of)
                        notifications.mark_notified("simulator", device, update.as_of)
        if day >= HISTORY_DAYS:
            rows.append({"household": device, "rhythm": rhythm, "age_group": spec["age"],
                         "variation": variation_name,
                         "condition": condition, "day": day,
                         **({"window_probabilities": {window["slot"]: window["probability"]
                                                       for window in windows}} if runtime_ticks else {}),
                         "label": None if away or offline else int(interruption),
                         "candidate": int(flagged),
                         "notification_request": int(requested),
                         "notification_request_times": request_times,
                         "episode_id": episodes.get(day) if interruption else None,
                         **({"missed_opportunities": sum(missed_day == day for missed_day, _
                                                         in interrupted_opportunities),
                             "missed_opportunity_times": [
                                 (_at(day, float(hour)) + timedelta(hours=shift)).isoformat()
                                 for missed_day, hour in interrupted_opportunities
                                 if missed_day == day]}
                            if opportunity_interruptions else {}),
                         "missed_use_at": (_at(day, float(min(
                             (hour for (missed_day, hour) in interrupted_opportunities
                              if missed_day == day), default=spec["hours"][0]))) +
                                           timedelta(hours=shift)).isoformat()
                         if interruption else None,
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
    training = {name: score(cohort(TRAIN_SEEDS, TRAIN_CONDITIONS, setting), "notification_request")
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
        "scope": "Exploratory synthetic stress test includes age 50-64 and batch cooking outside product target; no meal, health, or safety ground truth",
        "history_days": HISTORY_DAYS, "future_days": FUTURE_DAYS,
        "rhythms": list(RHYTHMS), "train_conditions": list(TRAIN_CONDITIONS),
        "holdout_conditions": list(HOLDOUT_CONDITIONS),
        "train_seeds": list(TRAIN_SEEDS), "holdout_seeds": list(HOLDOUT_SEEDS),
        "candidate_settings": SETTINGS, "training": training,
        "selected": selected, "selected_settings": SETTINGS[selected],
        "holdout_current": score(current_rows, "notification_request"),
        "holdout_selected": score(candidate_rows, "notification_request"),
        "holdout_model_candidates_current": score(current_rows),
        "holdout_model_candidates_selected": score(candidate_rows),
        "holdout_fixed_24h": score(current_rows, "fixed_24h"),
        "by_condition_current": {condition: score([row for row in current_rows if row["condition"] == condition], "notification_request") for condition in HOLDOUT_CONDITIONS},
        "by_condition_selected": {condition: score([row for row in candidate_rows if row["condition"] == condition], "notification_request") for condition in HOLDOUT_CONDITIONS},
        "by_rhythm_current": {rhythm: score([row for row in current_rows if row["rhythm"] == rhythm], "notification_request")
                              for rhythm in RHYTHMS},
        "by_rhythm_selected": {rhythm: score([row for row in candidate_rows if row["rhythm"] == rhythm], "notification_request")
                               for rhythm in RHYTHMS},
        "example_rows": candidate_rows[:8],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(experiment(), ensure_ascii=False, indent=2))
