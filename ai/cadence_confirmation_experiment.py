"""Compare interval and confirmation choices on scripted cooker-use interruptions.

Labels and generator parameters are assumptions, not field accuracy estimates.
Development and challenge cohorts use disjoint seeds and conditions.
"""

from __future__ import annotations

import json

from ai.condition_matrix_experiment import RHYTHMS, household, score
from ai.episode_evaluation import episode_score


BASE = {"alert_probability": 0.72, "late_grace_hours": 0.0,
        "interval_quantile": 0.95, "interval_strategy": "empirical"}
CANDIDATES = {
    "current": BASE,
    "sparse_guarded": {**BASE, "interval_strategy": "sparse_guarded_predictive"},
    "guarded_95": {**BASE, "interval_strategy": "guarded_predictive"},
    "guarded_90": {**BASE, "interval_strategy": "guarded_predictive", "interval_quantile": 0.90},
    "confirmation_60": {**BASE, "candidate_window_hours": 60},
    "guarded_95_confirmation_60": {**BASE, "interval_strategy": "guarded_predictive",
                                 "candidate_window_hours": 60},
    "guarded_90_confirmation_60": {**BASE, "interval_strategy": "guarded_predictive",
                                 "interval_quantile": 0.90, "candidate_window_hours": 60},
    "guarded_90_grace": {**BASE, "interval_strategy": "guarded_predictive",
                         "interval_quantile": 0.90, "late_grace_hours": 1.0},
    "guarded_90_strict": {**BASE, "interval_strategy": "guarded_predictive",
                          "interval_quantile": 0.90, "alert_probability": 0.84},
    "guarded_90_grace_strict": {**BASE, "interval_strategy": "guarded_predictive",
                                "interval_quantile": 0.90,
                                "alert_probability": 0.84, "late_grace_hours": 1.0},
}
DEVELOPMENT = {"seeds": (2381, 2399),
               "conditions": ("stable", "natural_skips", "batch_three_day"),
               "variations": ({"name": "wide_jitter", "jitter_minutes": 80},
                              {"name": "missed_reports", "missed_open_probability": 0.09})}
CHALLENGE = {"seeds": (4903, 4931),
             "conditions": ("gradual_drift", "weekend_shift", "skip_and_late"),
             "variations": ({"name": "shift_and_skip", "shift_hours": 0.6,
                             "skip_delta": 0.13, "jitter_minutes": 65},)}


def evaluate(setting: dict[str, object], cohort: dict[str, object]) -> dict[str, object]:
    rows = [row for variation in cohort["variations"]
            for condition in cohort["conditions"] for seed in cohort["seeds"]
            for rhythm in RHYTHMS
            for row in household(seed, rhythm, condition, setting, variation=variation)]
    return {"days": score(rows, "notification_request"),
            "episodes": episode_score(rows),
            "by_rhythm": {rhythm: score(
                [row for row in rows if row["rhythm"] == rhythm], "notification_request")
                for rhythm in RHYTHMS}}


def experiment() -> dict[str, object]:
    development = {name: evaluate(setting, DEVELOPMENT)
                   for name, setting in CANDIDATES.items()}
    baseline = development["current"]["days"]
    # Constrain nuisance requests before choosing the most episodes caught.
    eligible = [name for name, item in development.items()
                if item["days"]["fp"] <= baseline["fp"]]
    selected = min(eligible, key=lambda name: (
        -development[name]["episodes"]["caught"],
        development[name]["days"]["fp"],
        name != "current"))
    return {"scope": "Scripted cooker-use interruptions only", "development": development,
            "selected": selected, "challenge": {
                "current": evaluate(CANDIDATES["current"], CHALLENGE),
                "selected": evaluate(CANDIDATES[selected], CHALLENGE)}}


if __name__ == "__main__":
    result = experiment()
    print(json.dumps(result, ensure_ascii=False, indent=2))
