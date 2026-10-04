"""Independent synthetic households for the conditional window adaptation."""

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
from ai.in_scope_sample_audit import RHYTHMS, TARGET_CONDITIONS, VARIATIONS, _summary
from ai.in_scope_slot_lock_audit import CANDIDATES


SEEDS = tuple(25001 + 29 * index for index in range(48))
SETTINGS = {"current": BASE, "gated": CANDIDATES["gated"]}


def _one_home(spec: tuple[int, str, str]) -> dict[str, list[dict[str, object]]]:
    seed, rhythm, condition = spec
    return {name: household(seed, rhythm, condition, setting,
                            variation=VARIATIONS[condition],
                            opportunity_interruptions=True, runtime_ticks=True,
                            tick_minutes=1, future_days=14)
            for name, setting in SETTINGS.items()}


def _paired_interval(homes: list[dict[str, list[dict[str, object]]]],
                     candidate: str, metric: str) -> list[float]:
    strata = defaultdict(list)
    for case in homes:
        current = case["current"]
        other = case[candidate]
        label = (current[0]["condition"], current[0]["rhythm"])
        if metric == "recall":
            before = episode_score(current)
            after = episode_score(other)
            strata[label].append((after["caught"] - before["caught"], before["episodes"]))
        else:
            before = nuisance_normal_days(current)
            after = nuisance_normal_days(other)
            strata[label].append((after["days_with_unrelated_request"] -
                                  before["days_with_unrelated_request"], before["normal_days"]))
    rng = Random(20261005)
    values = []
    for _ in range(2000):
        sample = [rng.choice(items) for items in strata.values() for _ in items]
        values.append(sum(numerator for numerator, _ in sample) /
                      sum(denominator for _, denominator in sample))
    values.sort()
    return [round(values[round((len(values) - 1) * fraction)], 4)
            for fraction in (0.025, 0.975)]


def experiment() -> dict[str, object]:
    specs = [(seed, rhythm, condition)
             for condition in TARGET_CONDITIONS for seed in SEEDS for rhythm in RHYTHMS]
    homes = []
    with ProcessPoolExecutor(max_workers=min(6, os.cpu_count() or 1)) as pool:
        for index, case in enumerate(pool.map(_one_home, specs), 1):
            homes.append(case)
            if index % 16 == 0:
                print(f"independent holdout: {index}/{len(specs)} homes", flush=True)
    results = {}
    for name in SETTINGS:
        cohort = [case[name] for case in homes]
        results[name] = {
            "target": _summary(cohort),
            "by_condition": {condition: _summary([rows for rows in cohort
                                                   if rows[0]["condition"] == condition])
                             for condition in TARGET_CONDITIONS},
        }
    baseline = results["current"]["target"]
    decisions = {}
    for name in SETTINGS:
        if name == "current":
            continue
        candidate = results[name]["target"]
        caught = candidate["episodes"]["caught"]
        base_caught = baseline["episodes"]["caught"]
        nuisance = candidate["nuisance_normal_days"]["days_with_unrelated_request"]
        base_nuisance = baseline["nuisance_normal_days"]["days_with_unrelated_request"]
        condition_guard = all(
            results[name]["by_condition"][condition]["episodes"]["caught"] >=
            results["current"]["by_condition"][condition]["episodes"]["caught"] and
            results[name]["by_condition"][condition]
            ["nuisance_normal_days"]["days_with_unrelated_request"] <=
            results["current"]["by_condition"][condition]
            ["nuisance_normal_days"]["days_with_unrelated_request"]
            for condition in TARGET_CONDITIONS)
        decisions[name] = {
            "caught_change": caught - base_caught,
            "nuisance_day_change": nuisance - base_nuisance,
            "paired_bootstrap_95pct_change": {
                "recall": _paired_interval(homes, name, "recall"),
                "nuisance_day_rate": _paired_interval(homes, name, "nuisance"),
            },
            "adoptable": (caught >= base_caught and nuisance <= base_nuisance and
                          (caught > base_caught or nuisance < base_nuisance) and condition_guard),
        }
    return {"seeds": SEEDS, "rhythms": RHYTHMS, "conditions": TARGET_CONDITIONS,
            "variations": {name: VARIATIONS[name] for name in TARGET_CONDITIONS},
            "tick_minutes": 1, "settings": SETTINGS, "results": results,
            "decisions": decisions}


if __name__ == "__main__":
    result = experiment()
    path = Path(__file__).with_name("in_scope_adaptation_holdout_result.json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({name: {
        "caught": value["target"]["episodes"]["caught"],
        "nuisance_days": value["target"]["nuisance_normal_days"]["days_with_unrelated_request"],
    } for name, value in result["results"].items()}))
