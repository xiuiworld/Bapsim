"""Compare clock-window and cadence-supported decisions on in-scope scripts.

Each scripted episode removes exactly two planned lid-opening opportunities.
The future replay evaluates every minute; earlier history uses sparse ticks.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from ai.condition_matrix_experiment import household, score
from ai.episode_evaluation import episode_score


RHYTHMS = ("older_morning", "older_twice")
BASE = {"alert_probability": 0.72, "late_grace_hours": 0.0,
        "interval_quantile": 0.95}
CANDIDATES = {
    "current": BASE,
    "cadence_window_support": {**BASE, "cadence_window_support": True},
    "interval_with_clock_windows": {**BASE, "interval_with_clock_windows": True},
}
DEVELOPMENT = {"seeds": (3101, 3119),
               "conditions": ("stable", "natural_skips"),
               "variation": {"name": "base"}}
CHALLENGE = {"seeds": (7103, 7121),
             "conditions": ("gradual_drift", "skip_and_late"),
             "variation": {"name": "unseen_shift_and_missing",
                           "jitter_minutes": 60, "skip_delta": 0.08,
                           "missed_open_probability": 0.05}}


def nuisance_normal_days(rows: list[dict[str, object]]) -> dict[str, int]:
    """Exclude normal-day requests that still belong to a scripted episode."""
    # The two episodes are separated in these scripts. Build their response
    # windows individually so a later normal day is not hidden indefinitely.
    episodes = defaultdict(list)
    for row in rows:
        if isinstance(row.get("episode_id"), int):
            episodes[(str(row["household"]), int(row["episode_id"]))].extend(
                datetime.fromisoformat(at) for at in row.get("missed_opportunity_times", []))
    windows = defaultdict(list)
    for (household, _), times in episodes.items():
        if times:
            windows[household].append((min(times), max(times) + timedelta(days=1)))
    normal = [row for row in rows if row["label"] == 0]
    nuisance = sum(any(not any(start <= datetime.fromisoformat(at) < end
                                for start, end in windows[str(row["household"])])
                       for at in row["notification_request_times"]) for row in normal)
    return {"normal_days": len(normal), "days_with_unrelated_request": nuisance}


def evaluate(setting: dict[str, object], cohort: dict[str, object],
             *, tick_minutes: int) -> dict[str, object]:
    rows = [row for condition in cohort["conditions"]
            for seed in cohort["seeds"] for rhythm in RHYTHMS
            for row in household(seed, rhythm, condition, setting,
                                 variation=cohort["variation"],
                                 opportunity_interruptions=True, runtime_ticks=True,
                                 tick_minutes=tick_minutes, future_days=14)]
    return {"notification_days": score(rows, "notification_request"),
            "nuisance_normal_days": nuisance_normal_days(rows),
            "candidate_days": score(rows, "candidate"),
            "episodes": episode_score(rows),
            "by_rhythm": {rhythm: {
                "notification_days": score([row for row in rows if row["rhythm"] == rhythm],
                                           "notification_request"),
                "episodes": episode_score([row for row in rows if row["rhythm"] == rhythm]),
            } for rhythm in RHYTHMS}}


def experiment() -> dict[str, object]:
    development = {}
    for name, setting in CANDIDATES.items():
        development[name] = evaluate(setting, DEVELOPMENT, tick_minutes=5)
        print(f"development {name} complete", flush=True)
    baseline = development["current"]
    eligible = [name for name, item in development.items()
                if item["nuisance_normal_days"]["days_with_unrelated_request"] <=
                baseline["nuisance_normal_days"]["days_with_unrelated_request"]]
    selected = min(eligible, key=lambda name: (
        -development[name]["episodes"]["caught"],
        development[name]["nuisance_normal_days"]["days_with_unrelated_request"],
        name != "current"))
    challenge_results = {}
    for name, setting in CANDIDATES.items():
        challenge_results[name] = evaluate(setting, CHALLENGE, tick_minutes=1)
        print(f"challenge {name} complete", flush=True)
    challenge_current = challenge_results["current"]
    challenge_selected = challenge_results[selected]
    adopted = (challenge_selected["episodes"]["caught"] > challenge_current["episodes"]["caught"]
               and challenge_selected["nuisance_normal_days"]["days_with_unrelated_request"] <=
               challenge_current["nuisance_normal_days"]["days_with_unrelated_request"])
    return {
        "scope": "Synthetic age-65-plus daily cooker-use opportunities only",
        "labels": "Two scripted missed openings per episode, not meals or health outcomes",
        "development": {"cohort": DEVELOPMENT, "tick_minutes": 5, "results": development},
        "selected": selected,
        "adopted": adopted,
        "operating_setting": selected if adopted else "current",
        "challenge": {"cohort": CHALLENGE, "tick_minutes": 1,
                      "results": challenge_results},
    }


if __name__ == "__main__":
    result = experiment()
    target = Path(__file__).with_name("in_scope_hybrid_result.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"selected": result["selected"],
                      "operating_setting": result["operating_setting"],
                      "development": {name: {"caught": item["episodes"]["caught"],
                                             "fp": item["notification_days"]["fp"]}
                                      for name, item in result["development"]["results"].items()},
                      "challenge": {name: {"caught": item["episodes"]["caught"],
                                           "raw_normal_day_requests": item["notification_days"]["fp"],
                                           "nuisance_days": item["nuisance_normal_days"]["days_with_unrelated_request"],
                                           "early_only": item["episodes"]["early_request_only"]}
                                    for name, item in result["challenge"]["results"].items()}},
                     ensure_ascii=False))
