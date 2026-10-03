"""Stress caregiver-request logic with randomized benign cooker-use changes.

All labels are scripted cooker-use situations, not meals or health outcomes.
"""

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
HISTORY_DAYS = 14
FUTURE_DAYS = 28
INCIDENTS = {21: 1, 22: 1, 34: 2, 35: 2}
RHYTHMS = {"once_daily": (8,), "twice_daily": (8, 19),
           "weekend_lunch": (8, 13)}
REPLICATION_SEEDS = (809, 821, 823, 827, 829, 839, 853, 857)
DEVELOPMENT_SEEDS = (301, 307, 313, 317)
SETTINGS = {
    "current": {"late_grace_hours": 0.0, "min_consecutive": 2},
    "one_hour_grace": {"late_grace_hours": 1.0, "min_consecutive": 2},
    "three_confirmations": {"late_grace_hours": 0.0, "min_consecutive": 3},
}
CATEGORIES = ("routine", "meal_out", "late_use", "outing_declared",
              "outing_undeclared", "offline")


def _at(day: int, hour: float = 0) -> datetime:
    return START + timedelta(days=day, hours=hour)


def _event(device: str, kind: str, at: datetime) -> dict[str, str]:
    return {"source": "simulator", "device_id": device, "event_type": kind,
            "observed_at": at.isoformat(), "availability": "online"}


def _windows(rhythm: str) -> list[dict[str, object]]:
    if rhythm == "weekend_lunch":
        return [{"time": "08:00"}, {"time": "13:00", "weekdays": [5, 6]}]
    return [{"time": f"{hour:02}:00"} for hour in RHYTHMS[rhythm]]


def _category(rng: Random) -> str:
    draw = rng.random()
    for threshold, name in ((0.55, "routine"), (0.76, "meal_out"),
                            (0.87, "late_use"), (0.92, "outing_declared"),
                            (0.97, "outing_undeclared"), (1.0, "offline")):
        if draw < threshold:
            return name
    raise AssertionError("random draw outside [0, 1)")


def household(seed: int, rhythm: str, *, incidents: bool,
              setting_name: str = "current") -> list[dict[str, object]]:
    if rhythm not in RHYTHMS or setting_name not in SETTINGS:
        raise ValueError("unknown rhythm or setting")
    setting = SETTINGS[setting_name]
    rng = Random(f"benign-disruption-v1:{seed}:{rhythm}")
    device = f"{rhythm}_{seed}_{'incident' if incidents else 'benign'}"
    model = PersonalUsageModel(late_grace_hours=setting["late_grace_hours"])
    model.configure_initial_routine("simulator", device, _windows(rhythm))
    notifications = DetectionStateManager(candidate_window=timedelta(hours=36),
                                          min_consecutive=int(setting["min_consecutive"]),
                                          resumption_alerts=False)
    rows: list[dict[str, object]] = []
    carry_actions: list[tuple[datetime, str]] = []
    for day in range(HISTORY_DAYS + FUTURE_DAYS):
        start = _at(day)
        scheduled = [hour for hour in RHYTHMS[rhythm]
                     if rhythm != "weekend_lunch" or hour == 8 or start.weekday() >= 5]
        scripted = _category(rng) if day >= HISTORY_DAYS else "routine"
        category = "interruption" if incidents and day in INCIDENTS else scripted
        away = category == "outing_declared"
        offline = category == "offline"
        model.set_away(away, start.isoformat())
        model.observe(_event(device, "device_offline" if offline else "device_online", start))
        actions: list[tuple[datetime, str]] = carry_actions
        omitted = rng.choice(scheduled) if category == "meal_out" else None
        for hour in scheduled:
            if category in {"interruption", "outing_declared", "outing_undeclared", "offline"}:
                continue
            if hour == omitted:
                continue
            jitter = max(-45, min(45, rng.gauss(0, 20))) / 60
            shift = 2.5 if category == "late_use" else 0
            actions.append((_at(day, hour + jitter + shift), "open"))
        windows = model.profile("simulator", device, start.isoformat())["windows"]
        actions.extend((datetime.fromisoformat(window["expected_time"]) +
                        timedelta(hours=2.5 + setting["late_grace_hours"]), "evaluate")
                       for window in windows)
        carry_actions = [item for item in actions if item[0] >= _at(day + 1)]
        actions = [item for item in actions if item[0] < _at(day + 1)]
        request_times: list[str] = []
        request_reasons: list[str] = []
        request_windows: list[str | None] = []
        for moment, kind in sorted(actions):
            if kind == "open":
                model.observe(_event(device, "lid_opened", moment))
                notifications.ingest({"source": "simulator", "device_id": device,
                                      "as_of": moment.isoformat(), "routine_status": "normal",
                                      "reason_codes": ["USAGE_RESUMED"]})
            else:
                for decision in model.evaluate("simulator", device, moment.isoformat(),
                                               online=not offline, away=away):
                    for update in notifications.ingest(decision):
                        if update.action == "notify_requested":
                            request_times.append(update.as_of)
                            request_reasons.extend(decision["reason_codes"])
                            request_windows.append(decision["evidence"].get("window_end"))
                            notifications.mark_notified("simulator", device, update.as_of)
        end = _at(day, 23 + 59 / 60)
        if not offline:
            model.availability("simulator", device, True, end.isoformat())
        interval = model.evaluate_interval("simulator", device, end.isoformat(),
                                           online=not offline, away=away)
        if interval:
            for update in notifications.ingest(interval):
                if update.action == "notify_requested":
                    request_times.append(update.as_of)
                    request_reasons.extend(interval["reason_codes"])
                    request_windows.append(None)
                    notifications.mark_notified("simulator", device, update.as_of)
        if day >= HISTORY_DAYS:
            rows.append({"household": device, "rhythm": rhythm, "day": day,
                         "category": category, "label": None if away or offline else
                         int(category == "interruption"),
                         "episode_id": INCIDENTS.get(day) if category == "interruption" else None,
                         "missed_use_at": _at(day, scheduled[0]).isoformat()
                         if category == "interruption" else None,
                         "notification_request": int(bool(request_times)),
                         "notification_request_times": request_times,
                         "request_reasons": request_reasons,
                         "request_windows": request_windows})
    return rows


def experiment() -> dict[str, object]:
    def replay(seeds: tuple[int, ...], setting_name: str
               ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        benign = [row for seed in seeds for rhythm in RHYTHMS
                  for row in household(seed, rhythm, incidents=False,
                                       setting_name=setting_name)]
        challenged = [row for seed in seeds for rhythm in RHYTHMS
                      for row in household(seed, rhythm, incidents=True,
                                           setting_name=setting_name)]
        return benign, challenged

    benign, challenged = replay(REPLICATION_SEEDS, "current")
    def summary(rows: list[dict[str, object]], incidents: list[dict[str, object]]
                ) -> dict[str, object]:
        episodes = episode_score(incidents)
        return {"benign_request_days": sum(row["notification_request"] for row in rows),
                "benign_eligible_days": sum(row["label"] is not None for row in rows),
                "declared_away_request_days": sum(row["category"] == "outing_declared"
                                                  and row["notification_request"] for row in rows),
                "offline_request_days": sum(row["category"] == "offline"
                                            and row["notification_request"] for row in rows),
                "incident_episodes": episodes["episodes"],
                "incident_caught": episodes["caught"],
                "median_detection_delay_hours": episodes["median_delay_hours"]}

    sensitivity = {"replication": {"current": summary(benign, challenged)},
                   "development": {}}
    for name in SETTINGS:
        dev_benign, dev_challenged = replay(DEVELOPMENT_SEEDS, name)
        sensitivity["development"][name] = summary(dev_benign, dev_challenged)
        if name != "current":
            holdout_benign, holdout_challenged = replay(REPLICATION_SEEDS, name)
            sensitivity["replication"][name] = summary(holdout_benign, holdout_challenged)
    groups = {name: [row for row in benign if row["category"] == name]
              for name in CATEGORIES}
    rhythm_groups = {rhythm: {name: [row for row in groups[name] if row["rhythm"] == rhythm]
                              for name in CATEGORIES}
                     for rhythm in RHYTHMS}
    return {
        "scope": "Randomized synthetic cooker-use replay only; no meal or safety labels",
        "development_seeds": DEVELOPMENT_SEEDS, "replication_seeds": REPLICATION_SEEDS,
        "rhythms": list(RHYTHMS), "candidate_settings": SETTINGS,
        "history_days": HISTORY_DAYS, "future_days": FUTURE_DAYS,
        "benign_by_category": {name: {
            "days": len(rows), "notification_request_days": sum(
                row["notification_request"] for row in rows)}
            for name, rows in groups.items()},
        "benign_by_rhythm_category": {rhythm: {name: {
            "days": len(rows), "notification_request_days": sum(
                row["notification_request"] for row in rows)}
            for name, rows in categories.items()}
            for rhythm, categories in rhythm_groups.items()},
        "benign_total": {"households": len(REPLICATION_SEEDS) * len(RHYTHMS),
                         "eligible_days": sum(row["label"] is not None for row in benign),
                         "notification_request_days": sum(row["notification_request"] for row in benign),
                         "away_or_offline_request_days": sum(
                             row["label"] is None and row["notification_request"] for row in benign)},
        "scripted_interruption_episodes": episode_score(challenged),
        "challenged_benign_request_days": sum(
            row["label"] == 0 and row["notification_request"] for row in challenged),
        "sensitivity": sensitivity,
    }


if __name__ == "__main__":
    result = experiment()
    target = Path(__file__).with_name("benign_disruption_result.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
