"""Replay irregular cooker-use gaps with hourly checks and isolated disruptions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random

from ai.episode_evaluation import episode_score
from domain.personal_model import PersonalUsageModel
from workflow import DetectionStateManager


KST = timezone(timedelta(hours=9))
START = datetime(2026, 2, 2, tzinfo=KST)
HISTORY_DAYS = 28
FUTURE_DAYS = 21
RHYTHMS = ("every_2_days", "every_3_days", "alternating_2_3_days")
DEVELOPMENT_SEEDS = (301, 307)
REPLICATION_SEEDS = (809, 821, 823, 827)
STRESS_SEEDS = (1201, 1213)
CANDIDATES = {
    "current_p95": (0.95, "empirical"),
    "earlier_p75": (0.75, "empirical"),
    "robust_predictive_p95": (0.95, "robust_predictive"),
    "robust_predictive_p90": (0.90, "robust_predictive"),
    "robust_predictive_p85": (0.85, "robust_predictive"),
    "guarded_predictive_p90": (0.90, "guarded_predictive"),
}


def _planned_days(rhythm: str) -> list[int]:
    if rhythm == "every_2_days":
        return list(range(0, HISTORY_DAYS + FUTURE_DAYS, 2))
    if rhythm == "every_3_days":
        return list(range(0, HISTORY_DAYS + FUTURE_DAYS, 3))
    if rhythm == "alternating_2_3_days":
        days = []
        day = 0
        while day < HISTORY_DAYS + FUTURE_DAYS:
            days.append(day)
            day += 2 if len(days) % 2 else 3
        return days
    raise ValueError("unknown rhythm")


def household(seed: int, rhythm: str, *, interrupted: bool,
              quantile: float = 0.95,
              interval_strategy: str = "empirical",
              natural_skip_probability: float = 0.11) -> list[dict[str, object]]:
    planned = _planned_days(rhythm)
    future = [day for day in planned if day >= HISTORY_DAYS]
    episodes = {future[index + offset]: number
                for number, index in enumerate((1, len(future) // 2), 1)
                for offset in (0, 1)}
    rng = Random(f"irregular-hourly-v1:{seed}:{rhythm}")
    device = f"{rhythm}_{seed}_{'incident' if interrupted else 'benign'}"
    model = PersonalUsageModel(interval_quantile=quantile,
                               interval_strategy=interval_strategy)
    workflow = DetectionStateManager(candidate_window=timedelta(hours=36),
                                     min_consecutive=2, resumption_alerts=False)
    # Build the complete event schedule before replay. Candidate settings see
    # the same behavior and never influence the generated ground truth.
    categories = {}
    openings = []
    expected = {}
    for day in range(HISTORY_DAYS + FUTURE_DAYS):
        if interrupted and day in episodes:
            category = "interruption"
        elif day >= HISTORY_DAYS:
            draw = rng.random()
            category = ("outing_declared" if draw < 0.03 else "offline" if draw < 0.05
                        else "natural_skip" if draw < 0.05 + natural_skip_probability
                        and day in planned else "routine")
        else:
            category = "routine"
        categories[day] = category
        if day not in planned:
            continue
        hour = 2 + rng.randrange(20)
        minute = rng.randrange(60)
        expected[day] = START + timedelta(days=day, hours=hour, minutes=minute)
        if category == "routine":
            openings.append(expected[day])
    openings.sort()
    model.observe({"source": "simulator", "device_id": device,
                   "event_type": "device_online", "observed_at": START.isoformat(),
                   "availability": "online"})
    requests: dict[int, list[str]] = {day: [] for day in range(HISTORY_DAYS + FUTURE_DAYS)}
    event_index = 0
    for hour in range((HISTORY_DAYS + FUTURE_DAYS) * 24):
        moment = START + timedelta(hours=hour)
        day = hour // 24
        if hour % 24 == 0:
            away = categories[day] == "outing_declared"
            online = categories[day] != "offline"
            model.set_away(away, moment.isoformat())
            model.observe({"source": "simulator", "device_id": device,
                           "event_type": "device_online" if online else "device_offline",
                           "observed_at": moment.isoformat(), "availability": "online"})
        while event_index < len(openings) and openings[event_index] <= moment:
            opening = openings[event_index]
            model.observe({"source": "simulator", "device_id": device,
                           "event_type": "lid_opened", "observed_at": opening.isoformat(),
                           "availability": "online"})
            workflow.ingest({"source": "simulator", "device_id": device,
                             "as_of": opening.isoformat(), "routine_status": "normal",
                             "reason_codes": ["USAGE_RESUMED"]})
            event_index += 1
        model.availability("simulator", device, online, moment.isoformat())
        decision = model.evaluate_interval("simulator", device, moment.isoformat(),
                                           online=online, away=away)
        if decision is None:
            continue
        for update in workflow.ingest(decision):
            if update.action == "notify_requested":
                requests[day].append(update.as_of)
                workflow.mark_notified("simulator", device, update.as_of)
    rows = []
    for day in range(HISTORY_DAYS, HISTORY_DAYS + FUTURE_DAYS):
        category = categories[day]
        rows.append({"household": device, "rhythm": rhythm, "day": day,
                     "category": category,
                     "label": None if category in {"outing_declared", "offline"} else
                     int(category == "interruption"),
                     "episode_id": episodes.get(day) if category == "interruption" else None,
                     "missed_use_at": expected[day].isoformat() if category == "interruption" else None,
                     "notification_request": int(bool(requests[day])),
                     "notification_request_times": requests[day]})
    return rows


def experiment() -> dict[str, object]:
    def replay(seeds: tuple[int, ...], quantile: float, strategy: str,
               *, natural_skip_probability: float = 0.11) -> dict[str, object]:
        benign = [row for seed in seeds for rhythm in RHYTHMS
                  for row in household(seed, rhythm, interrupted=False, quantile=quantile,
                                       interval_strategy=strategy,
                                       natural_skip_probability=natural_skip_probability)]
        incidents = [row for seed in seeds for rhythm in RHYTHMS
                     for row in household(seed, rhythm, interrupted=True, quantile=quantile,
                                          interval_strategy=strategy,
                                          natural_skip_probability=natural_skip_probability)]
        return {"benign_request_days": sum(row["notification_request"] for row in benign),
                "benign_eligible_days": sum(row["label"] is not None for row in benign),
                "declared_away_or_offline_requests": sum(
                    row["label"] is None and row["notification_request"] for row in benign),
                "episodes": episode_score(incidents),
                "by_rhythm": {rhythm: {
                    "benign_request_days": sum(row["notification_request"] for row in benign
                                               if row["rhythm"] == rhythm),
                    "episodes": episode_score([row for row in incidents if row["rhythm"] == rhythm])}
                    for rhythm in RHYTHMS}}
    return {"scope": "Synthetic irregular cooker use; hourly interval checks; no field labels",
            "development_seeds": DEVELOPMENT_SEEDS, "replication_seeds": REPLICATION_SEEDS,
            "stress_seeds": STRESS_SEEDS,
            "history_days": HISTORY_DAYS, "future_days": FUTURE_DAYS,
            "candidates": CANDIDATES,
            "development": {name: replay(DEVELOPMENT_SEEDS, *settings)
                            for name, settings in CANDIDATES.items()},
            "replication": {name: replay(REPLICATION_SEEDS, *settings)
                            for name, settings in CANDIDATES.items()},
            "frequent_natural_skip_stress": {
                name: replay(STRESS_SEEDS, *CANDIDATES[name], natural_skip_probability=0.30)
                for name in ("current_p95", "robust_predictive_p90", "guarded_predictive_p90")}}


if __name__ == "__main__":
    result = experiment()
    target = Path(__file__).with_name("irregular_interval_result.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: {"benign_request_days": item["benign_request_days"],
                             "caught": item["episodes"]["caught"],
                             "episodes": item["episodes"]["episodes"]}
                      for name, item in result["replication"].items()}, ensure_ascii=False))
