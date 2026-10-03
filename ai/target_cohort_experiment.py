"""Deterministic stress test for single-person cooker-use routines.

Profiles are deliberately balanced test cases, not estimates of Korean
population prevalence. Scripted interruptions are not meal or health labels.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random

from domain.personal_model import PersonalUsageModel


KST = timezone(timedelta(hours=9))
START = datetime(2026, 1, 5, tzinfo=KST)
HISTORY_DAYS = 28
FUTURE_DAYS = 28
TOTAL_DAYS = HISTORY_DAYS + FUTURE_DAYS
TRAIN_SEEDS = (101, 103, 107, 109)
HOLDOUT_SEEDS = (301, 307, 311, 313)
STRESS_SEEDS = (501, 503)
THRESHOLDS = (0.72, 0.84, 0.90)

# These timings and probabilities are stress-test assumptions. The cited
# population studies motivate diversity, not these exact cooker-use rates.
PROFILES = {
    "early_single": {"age": "65_plus", "hours": (7,), "skip": 0.07, "jitter": 25},
    "midday_single": {"age": "65_plus", "hours": (12,), "skip": 0.10, "jitter": 30},
    "evening_single": {"age": "50_64", "hours": (19,), "skip": 0.10, "jitter": 30},
    "breakfast_dinner": {"age": "65_plus", "hours": (8, 18), "skip": 0.08, "jitter": 25},
    "lunch_dinner": {"age": "50_64", "hours": (13, 19), "skip": 0.13, "jitter": 35},
    "three_windows": {"age": "65_plus", "hours": (8, 13, 19), "skip": 0.07, "jitter": 25},
    "batch_every_other_day": {"age": "65_plus", "hours": (17,), "skip": 0.07, "jitter": 40},
    "variable_weekend": {"age": "50_64", "hours": (19,), "skip": 0.14, "jitter": 55},
}


def _at(day: int, hour: float = 0) -> datetime:
    return START + timedelta(days=day, hours=hour)


def _event(device_id: str, kind: str, moment: datetime) -> dict[str, str]:
    return {"source": "simulator", "device_id": device_id,
            "event_type": kind, "observed_at": moment.isoformat(),
            "availability": "offline" if kind == "device_offline" else "online"}


def _planned(profile: str, day: int) -> bool:
    return profile != "batch_every_other_day" or day % 2 == 0


def _interruptions(profile: str, seed: int) -> set[int]:
    eligible = [day for day in range(HISTORY_DAYS, TOTAL_DAYS) if _planned(profile, day)]
    offset = seed % 2
    return {eligible[index + offset] for index in (len(eligible) // 4, len(eligible) // 2,
                                                   3 * len(eligible) // 4)}


def run_household(seed: int, profile: str, threshold: float,
                  *, stress: bool = False) -> list[dict[str, object]]:
    """Replay one home chronologically; labels are set before model scoring."""
    if profile not in PROFILES:
        raise ValueError("unknown synthetic profile")
    spec = PROFILES[profile]
    rng = Random(seed * 1009 + list(PROFILES).index(profile))
    device_id = f"synthetic_{profile}_{seed}"
    model = PersonalUsageModel(alert_probability=threshold)
    gap_days = _interruptions(profile, seed)
    away_days = {HISTORY_DAYS + 5 + seed % 2}
    outage_days = {HISTORY_DAYS + 12 + seed % 2}
    rows: list[dict[str, object]] = []
    last_open: datetime | None = None

    for day in range(TOTAL_DAYS):
        baseline = _at(day)
        away = day in away_days
        online = day not in outage_days
        planned = _planned(profile, day)
        interruption = day in gap_days and not away and online
        # Stress homes have later usage and more legitimate non-use after the
        # historical period. Neither change is shown to the model in advance.
        skip_probability = float(spec["skip"]) + (0.18 if stress and day >= HISTORY_DAYS else 0)
        natural_skip = planned and not interruption and rng.random() < skip_probability
        scenario = ("away" if away else "offline" if not online else
                    "scripted_interruption" if interruption else
                    "ordinary_nonuse" if natural_skip or not planned else
                    "routine_shift" if stress and day >= HISTORY_DAYS else "routine")

        model.set_away(away, baseline.isoformat())
        model.observe(_event(device_id, "device_online" if online else "device_offline", baseline))
        planned_actions: list[tuple[datetime, str]] = []
        if online and not away and planned and not (interruption or natural_skip):
            for hour in spec["hours"]:
                shift = 1.5 if stress and day >= HISTORY_DAYS else 0
                if profile == "variable_weekend" and baseline.weekday() >= 5:
                    shift += 1.5
                jitter = max(-100, min(100, rng.gauss(0, int(spec["jitter"]))))
                opening = _at(day, int(hour)) + timedelta(hours=shift, minutes=jitter)
                planned_actions.append((opening, "open"))
                if rng.random() < 0.22:
                    planned_actions.append((opening + timedelta(minutes=9), "repeat"))

        # Model window centers are computed from prior days only. Evaluate
        # each one shortly after its end, as a periodic worker would.
        centers = model.profile("simulator", device_id, baseline.isoformat())["windows"]
        checks = [datetime.fromisoformat(item["expected_time"]) + timedelta(hours=2.5)
                  for item in centers]
        actions = [(moment, kind) for moment, kind in planned_actions]
        actions += [(moment, "evaluate") for moment in checks]
        flagged = False
        for moment, action in sorted(actions):
            if action in {"open", "repeat"}:
                model.observe(_event(device_id, "lid_opened", moment))
                last_open = moment
            else:
                decisions = model.evaluate("simulator", device_id, moment.isoformat(),
                                           online=online, away=away)
                flagged |= any(item["routine_status"] == "check_required" for item in decisions)
        end = _at(day, 23 + 59 / 60)
        if online:
            model.availability("simulator", device_id, True, end.isoformat())
        interval = model.evaluate_interval("simulator", device_id, end.isoformat(),
                                           online=online, away=away)
        flagged |= bool(interval and interval["routine_status"] == "check_required")
        rows.append({
            "household": device_id, "profile": profile, "age_group": spec["age"],
            "day": day, "split": "history" if day < HISTORY_DAYS else "future",
            "scenario": scenario, "label": (None if day < HISTORY_DAYS or away or not online
                                            else int(interruption)),
            "personal_alert": int(flagged),
            "fixed_24h_alert": int(online and not away and last_open is not None and
                                   end - last_open > timedelta(hours=24)),
        })
    return rows


def cohort(seeds: tuple[int, ...], threshold: float,
           *, stress: bool = False) -> list[dict[str, object]]:
    return [row for seed in seeds for profile in PROFILES
            for row in run_household(seed, profile, threshold, stress=stress)]


def score(rows: list[dict[str, object]], field: str) -> dict[str, int | float]:
    future = [row for row in rows if row["split"] == "future"]
    eligible = [row for row in future if row["label"] is not None]
    tp = sum(row["label"] == 1 and row[field] == 1 for row in eligible)
    fp = sum(row["label"] == 0 and row[field] == 1 for row in eligible)
    fn = sum(row["label"] == 1 and row[field] == 0 for row in eligible)
    tn = sum(row["label"] == 0 and row[field] == 0 for row in eligible)
    return {"households": len({row["household"] for row in future}),
            "eligible_days": len(eligible), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "recall": round(tp / (tp + fn), 4) if tp + fn else 0.0,
            "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0,
            "false_alerts_per_household_28d": round(fp / len({row["household"] for row in future}), 3),
            "abstained_days": len(future) - len(eligible),
            "alerted_away_or_offline": sum(row["label"] is None and row[field] == 1 for row in future)}


def _by(rows: list[dict[str, object]], key: str) -> dict[str, dict[str, int | float]]:
    return {str(value): score([row for row in rows if row[key] == value], "personal_alert")
            for value in sorted({row[key] for row in rows if row["split"] == "future"})}


def experiment() -> dict[str, object]:
    training = []
    for threshold in THRESHOLDS:
        training.append({"threshold": threshold,
                         **score(cohort(TRAIN_SEEDS, threshold), "personal_alert")})
    # Choose on training homes only; favor fewer nuisance alerts for a
    # caregiver-facing workflow. This objective is an experiment assumption.
    selected = max(training, key=lambda item: (
        float(item["recall"]) - 2 * float(item["false_positive_rate"]),
        -abs(float(item["threshold"]) - 0.72)))["threshold"]
    holdout = cohort(HOLDOUT_SEEDS, float(selected))
    stress = cohort(STRESS_SEEDS, float(selected), stress=True)
    return {
        "scope": "Balanced synthetic single-person cooker-use stress test; no meal or health labels.",
        "model": "PersonalUsageModel from domain/personal_model/engine.py",
        "profile_count": len(PROFILES), "profile_names": list(PROFILES),
        "train_seeds": list(TRAIN_SEEDS), "holdout_seeds": list(HOLDOUT_SEEDS),
        "stress_seeds": list(STRESS_SEEDS),
        "history_days_per_household": HISTORY_DAYS, "future_days_per_household": FUTURE_DAYS,
        "training_candidates": training, "selected_threshold": selected,
        "holdout_personal": score(holdout, "personal_alert"),
        "holdout_fixed_24h": score(holdout, "fixed_24h_alert"),
        "holdout_by_profile": _by(holdout, "profile"),
        "holdout_by_age_group": _by(holdout, "age_group"),
        "holdout_by_scenario": _by(holdout, "scenario"),
        "stress_personal": score(stress, "personal_alert"),
        "stress_fixed_24h": score(stress, "fixed_24h_alert"),
        "stress_by_profile": _by(stress, "profile"),
        "example_future_rows": [row for row in holdout if row["household"] ==
                                f"synthetic_lunch_dinner_{HOLDOUT_SEEDS[0]}" and
                                row["split"] == "future"][:10],
    }
