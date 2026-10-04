"""Larger, household-clustered audit of the current in-scope alert setting."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from random import Random

from ai.condition_matrix_experiment import household
from ai.episode_evaluation import episode_score
from ai.in_scope_hybrid_experiment import BASE, nuisance_normal_days


SEEDS = tuple(9101 + 17 * index for index in range(16))
RHYTHMS = ("older_morning", "older_twice")
TARGET_CONDITIONS = ("stable", "gradual_drift")
STRESS_CONDITIONS = ("skip_and_late",)
VARIATIONS = {
    "stable": {"name": "expanded_target", "jitter_minutes": 60,
               "missed_open_probability": 0.05},
    "gradual_drift": {"name": "expanded_target", "jitter_minutes": 60,
                      "missed_open_probability": 0.05},
    "skip_and_late": {"name": "expanded_high_skip", "jitter_minutes": 60,
                      "skip_delta": 0.08, "missed_open_probability": 0.05},
}


def _one_home(spec: tuple[int, str, str]) -> list[dict[str, object]]:
    seed, rhythm, condition = spec
    return household(seed, rhythm, condition, BASE,
                     variation=VARIATIONS[condition], opportunity_interruptions=True,
                     runtime_ticks=True, tick_minutes=1, future_days=14)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def _cluster_interval(homes: list[list[dict[str, object]]], key: str) -> list[float]:
    """Resample households within each rhythm and condition, keeping both episodes."""
    strata: dict[tuple[str, str], list[tuple[int, int, int, int]]] = defaultdict(list)
    for rows in homes:
        episodes = episode_score(rows)
        nuisance = nuisance_normal_days(rows)
        strata[(str(rows[0]["condition"]), str(rows[0]["rhythm"]))].append(
            (episodes["caught"], episodes["episodes"],
             nuisance["days_with_unrelated_request"], nuisance["normal_days"]))
    rng = Random(20261004)
    estimates = []
    for _ in range(2000):
        sampled = [rng.choice(items) for items in strata.values() for _ in items]
        numerator = sum(item[0 if key == "recall" else 2] for item in sampled)
        denominator = sum(item[1 if key == "recall" else 3] for item in sampled)
        estimates.append(numerator / denominator)
    return [round(_percentile(estimates, 0.025), 4),
            round(_percentile(estimates, 0.975), 4)]


def _summary(homes: list[list[dict[str, object]]]) -> dict[str, object]:
    rows = [row for home in homes for row in home]
    episodes = episode_score(rows)
    nuisance = nuisance_normal_days(rows)
    return {"households": len(homes), "episodes": episodes,
            "nuisance_normal_days": nuisance,
            "household_bootstrap_95pct": {
                "recall": _cluster_interval(homes, "recall"),
                "nuisance_day_rate": _cluster_interval(homes, "nuisance")}}


def experiment() -> dict[str, object]:
    specs = [(seed, rhythm, condition)
             for condition in TARGET_CONDITIONS + STRESS_CONDITIONS
             for seed in SEEDS for rhythm in RHYTHMS]
    homes = []
    with ProcessPoolExecutor(max_workers=min(6, os.cpu_count() or 1)) as pool:
        for index, rows in enumerate(pool.map(_one_home, specs), 1):
            homes.append(rows)
            if index % 8 == 0:
                print(f"evaluated {index}/{len(specs)} households", flush=True)
    by_condition = {condition: _summary([rows for rows in homes
                                          if rows[0]["condition"] == condition])
                    for condition in TARGET_CONDITIONS + STRESS_CONDITIONS}
    target = [rows for rows in homes if rows[0]["condition"] in TARGET_CONDITIONS]
    stress = [rows for rows in homes if rows[0]["condition"] in STRESS_CONDITIONS]
    return {"scope": "Synthetic age-65-plus daily cooker-use opportunities",
            "setting": BASE, "seeds": SEEDS, "rhythms": RHYTHMS,
            "target_conditions": TARGET_CONDITIONS,
            "stress_conditions": STRESS_CONDITIONS,
            "variations": VARIATIONS, "tick_minutes": 1,
            "target": _summary(target), "stress": _summary(stress),
            "by_condition": by_condition}


if __name__ == "__main__":
    result = experiment()
    target = Path(__file__).with_name("in_scope_sample_audit_result.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: {"households": result[name]["households"],
                             "caught": result[name]["episodes"]["caught"],
                             "episodes": result[name]["episodes"]["episodes"],
                             "nuisance_days": result[name]["nuisance_normal_days"]["days_with_unrelated_request"]}
                      for name in ("target", "stress")}, ensure_ascii=False))
