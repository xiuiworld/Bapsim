"""Compare clock-window and cadence-supported decisions on in-scope scripts.

Each scripted episode removes exactly two planned lid-opening opportunities.
The future replay evaluates every minute; earlier history uses sparse ticks.
"""

from __future__ import annotations

import json
from pathlib import Path

from ai.condition_matrix_experiment import household, score
from ai.episode_evaluation import episode_score


RHYTHMS = ("older_morning", "older_twice")
BASE = {"alert_probability": 0.72, "late_grace_hours": 0.0,
        "interval_quantile": 0.95}
CANDIDATES = {
    "current": BASE,
    "cadence_window_support": {**BASE, "cadence_window_support": True},
}
DEVELOPMENT = {"seeds": (3101, 3119),
               "conditions": ("stable", "natural_skips"),
               "variation": {"name": "base"}}
CHALLENGE = {"seeds": (7103, 7121),
             "conditions": ("gradual_drift", "skip_and_late"),
             "variation": {"name": "unseen_shift_and_missing",
                           "jitter_minutes": 60, "skip_delta": 0.08,
                           "missed_open_probability": 0.05}}


def evaluate(setting: dict[str, object], cohort: dict[str, object],
             *, tick_minutes: int) -> dict[str, object]:
    rows = [row for condition in cohort["conditions"]
            for seed in cohort["seeds"] for rhythm in RHYTHMS
            for row in household(seed, rhythm, condition, setting,
                                 variation=cohort["variation"],
                                 opportunity_interruptions=True, runtime_ticks=True,
                                 tick_minutes=tick_minutes, future_days=14)]
    return {"notification_days": score(rows, "notification_request"),
            "candidate_days": score(rows, "candidate"),
            "episodes": episode_score(rows),
            "by_rhythm": {rhythm: {
                "notification_days": score([row for row in rows if row["rhythm"] == rhythm],
                                           "notification_request"),
                "episodes": episode_score([row for row in rows if row["rhythm"] == rhythm]),
            } for rhythm in RHYTHMS}}


def experiment() -> dict[str, object]:
    development = {name: evaluate(setting, DEVELOPMENT, tick_minutes=5)
                   for name, setting in CANDIDATES.items()}
    baseline = development["current"]
    eligible = [name for name, item in development.items()
                if item["notification_days"]["fp"] <= baseline["notification_days"]["fp"]]
    selected = min(eligible, key=lambda name: (
        -development[name]["episodes"]["caught"],
        development[name]["notification_days"]["fp"], name != "current"))
    challenge_current = evaluate(CANDIDATES["current"], CHALLENGE, tick_minutes=1)
    challenge_selected = (challenge_current if selected == "current" else
                          evaluate(CANDIDATES[selected], CHALLENGE, tick_minutes=1))
    adopted = (challenge_selected["episodes"]["caught"] > challenge_current["episodes"]["caught"]
               and challenge_selected["notification_days"]["fp"] <=
               challenge_current["notification_days"]["fp"])
    return {
        "scope": "Synthetic age-65-plus daily cooker-use opportunities only",
        "labels": "Two scripted missed openings per episode, not meals or health outcomes",
        "development": {"cohort": DEVELOPMENT, "tick_minutes": 5, "results": development},
        "selected": selected,
        "adopted": adopted,
        "operating_setting": selected if adopted else "current",
        "challenge": {"cohort": CHALLENGE, "tick_minutes": 1,
                      "current": challenge_current, "selected": challenge_selected},
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
                      "challenge": {name: {"caught": result["challenge"][name]["episodes"]["caught"],
                                           "fp": result["challenge"][name]["notification_days"]["fp"]}
                                    for name in ("current", "selected")}}, ensure_ascii=False))
