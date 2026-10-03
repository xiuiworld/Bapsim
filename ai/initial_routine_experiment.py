"""Paired synthetic replay of optional starting routines and caregiver requests.

Scripted cooker use is not evidence of meals, wellbeing, or field accuracy.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random

from ai.condition_matrix_experiment import score
from ai.episode_evaluation import episode_score
from domain.personal_model import PersonalUsageModel
from workflow import DetectionStateManager


KST = timezone(timedelta(hours=9))
START = datetime(2026, 2, 2, tzinfo=KST)  # Monday
HISTORY_DAYS = 5
FUTURE_DAYS = 14
INCIDENTS = {6: 1, 7: 1, 12: 2, 13: 2}
AWAY_DAY = 10
OFFLINE_DAY = 16
SEEDS = (301, 307, 313, 317)
REPLICATION_SEEDS = (809, 821, 823, 827)
SCENARIOS = (
    "accurate_daily", "weekday_lunch_away", "wrong_lunch_weekdays", "wrong_time",
    "wrong_weekdays", "irregular_skips",
)


def _at(day: int, hour: float = 0) -> datetime:
    return START + timedelta(days=day, hours=hour)


def _event(device: str, kind: str, at: datetime) -> dict[str, str]:
    return {"source": "simulator", "device_id": device, "event_type": kind,
            "observed_at": at.isoformat(), "availability": "online"}


def _starting_windows(scenario: str) -> list[dict[str, object]]:
    morning = {"time": "08:00", "weekdays": list(range(7))}
    if scenario == "weekday_lunch_away":
        return [morning, {"time": "13:00", "weekdays": [5, 6]}]
    if scenario == "wrong_lunch_weekdays":
        return [morning, {"time": "13:00", "weekdays": list(range(7))}]
    if scenario == "wrong_weekdays":
        return [{"time": "08:00", "weekdays": [5, 6]}]
    return [morning]


def household(seed: int, scenario: str, *, configured: bool) -> list[dict[str, object]]:
    if scenario not in SCENARIOS:
        raise ValueError("unknown synthetic scenario")
    rng = Random(f"initial-routine-v1:{scenario}:{seed}")
    device = f"{scenario}_{seed}"
    model = PersonalUsageModel()
    if configured:
        model.configure_initial_routine("simulator", device, _starting_windows(scenario))
    notifications = DetectionStateManager(candidate_window=timedelta(hours=36),
                                          min_consecutive=2, resumption_alerts=False)
    rows: list[dict[str, object]] = []
    for day in range(HISTORY_DAYS + FUTURE_DAYS):
        start = _at(day)
        weekday = start.weekday()
        away = day == AWAY_DAY
        offline = day == OFFLINE_DAY
        interruption = day in INCIDENTS
        actual_hour = 17 if scenario == "wrong_time" else 8
        model.set_away(away, start.isoformat())
        model.observe(_event(device, "device_offline" if offline else "device_online", start))
        actions: list[tuple[datetime, str]] = []
        skip = scenario == "irregular_skips" and rng.random() < 0.3
        if not (away or offline or interruption or skip):
            jitter = max(-35, min(35, rng.gauss(0, 15))) / 60
            actions.append((_at(day, actual_hour + jitter), "open"))
            if scenario in {"weekday_lunch_away", "wrong_lunch_weekdays"} and weekday >= 5:
                actions.append((_at(day, 13 + jitter), "open"))
        windows = model.profile("simulator", device, start.isoformat())["windows"]
        actions.extend((datetime.fromisoformat(window["expected_time"]) +
                        timedelta(hours=2.5), "evaluate") for window in windows)
        request_times: list[str] = []
        candidate = False
        for moment, kind in sorted(actions):
            if kind == "open":
                model.observe(_event(device, "lid_opened", moment))
                continue
            for decision in model.evaluate("simulator", device, moment.isoformat(),
                                           online=not offline, away=away):
                candidate |= decision["routine_status"] == "check_required"
                for update in notifications.ingest(decision):
                    if update.action == "notify_requested":
                        request_times.append(update.as_of)
                        notifications.mark_notified("simulator", device, update.as_of)
        end = _at(day, 23 + 59 / 60)
        if not offline:
            model.availability("simulator", device, True, end.isoformat())
        interval = model.evaluate_interval("simulator", device, end.isoformat(),
                                           online=not offline, away=away)
        if interval:
            candidate |= interval["routine_status"] == "check_required"
            for update in notifications.ingest(interval):
                if update.action == "notify_requested":
                    request_times.append(update.as_of)
                    notifications.mark_notified("simulator", device, update.as_of)
        if day >= HISTORY_DAYS:
            rows.append({"household": device, "scenario": scenario, "day": day,
                         "label": None if away or offline else int(interruption),
                         "episode_id": INCIDENTS.get(day),
                         "missed_use_at": _at(day, actual_hour).isoformat()
                         if interruption else None,
                         "candidate": int(candidate),
                         "notification_request": int(bool(request_times)),
                         "notification_request_times": request_times})
    return rows


def experiment() -> dict[str, object]:
    def day_score(rows: list[dict[str, object]], days: int) -> dict[str, object]:
        result = score(rows, "notification_request")
        result["false_requests_per_home"] = round(result["fp"] / result["households"], 3)
        result["observation_days"] = days
        del result["nuisance_per_home_21d"]
        return result

    def metrics(rows: list[dict[str, object]]) -> dict[str, object]:
        early = [row for row in rows if row["day"] < 12]
        return {"days": day_score(rows, FUTURE_DAYS),
                "episodes": episode_score(rows),
                "early_days": day_score(early, 7),
                "early_episodes": episode_score(early)}
    def replay(seeds: tuple[int, ...]) -> dict[str, object]:
        pairs = {scenario: {
            mode: [row for seed in seeds for row in household(
                seed, scenario, configured=(mode == "configured"))]
            for mode in ("baseline", "configured")}
            for scenario in SCENARIOS}
        return {
            "scenario_results": {scenario: {mode: metrics(rows)
                                            for mode, rows in modes.items()}
                                 for scenario, modes in pairs.items()},
            "overall": {mode: metrics([row for modes in pairs.values() for row in modes[mode]])
                        for mode in ("baseline", "configured")},
        }

    return {
        "scope": "Paired synthetic cooker-use replay; no meal or field-accuracy labels",
        "history_days": HISTORY_DAYS, "future_days": FUTURE_DAYS,
        "development_seeds": SEEDS, "replication_seeds": REPLICATION_SEEDS,
        "scenarios": SCENARIOS,
        "development": replay(SEEDS), "replication": replay(REPLICATION_SEEDS),
    }


if __name__ == "__main__":
    target = Path(__file__).with_name("initial_routine_result.json")
    result = experiment()
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["replication"]["overall"], ensure_ascii=False))
