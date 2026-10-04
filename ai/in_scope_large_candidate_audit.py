"""Compare optional model candidates on the precision audit's same households."""

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ai.condition_matrix_experiment import household
from ai.in_scope_hybrid_experiment import CANDIDATES
from ai.in_scope_sample_audit import RHYTHMS, TARGET_CONDITIONS, VARIATIONS, _summary


BASELINE_PATH = Path(__file__).with_name("in_scope_precision_audit_result.json")
CANDIDATE_NAMES = ("cadence_window_support", "interval_with_clock_windows")


def _one_home(spec: tuple[int, str, str, str]) -> list[dict[str, object]]:
    seed, rhythm, condition, candidate = spec
    return household(seed, rhythm, condition, CANDIDATES[candidate],
                     variation=VARIATIONS[condition], opportunity_interruptions=True,
                     runtime_ticks=True, tick_minutes=1, future_days=14)


def _numbers(summary: dict[str, object]) -> tuple[int, int]:
    return (summary["episodes"]["caught"],
            summary["nuisance_normal_days"]["days_with_unrelated_request"])


def experiment() -> dict[str, object]:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    if (baseline["setting"] != "current" or baseline["tick_minutes"] != 1 or
            not baseline["precision_met"] or
            tuple(baseline["conditions"]) != TARGET_CONDITIONS or
            tuple(baseline["rhythms"]) != RHYTHMS or
            baseline["variations"] != {name: VARIATIONS[name]
                                       for name in TARGET_CONDITIONS}):
        raise ValueError("precision baseline is incompatible")
    seeds = tuple(baseline["seeds_used"])
    candidates = {}
    with ProcessPoolExecutor(max_workers=min(6, os.cpu_count() or 1)) as pool:
        for name in CANDIDATE_NAMES:
            specs = [(seed, rhythm, condition, name)
                     for condition in TARGET_CONDITIONS for seed in seeds for rhythm in RHYTHMS]
            homes = []
            for index, rows in enumerate(pool.map(_one_home, specs), 1):
                homes.append(rows)
                if index % 16 == 0:
                    print(f"{name}: {index}/{len(specs)} homes", flush=True)
            by_condition = {condition: _summary([rows for rows in homes
                                                  if rows[0]["condition"] == condition])
                            for condition in TARGET_CONDITIONS}
            summary = _summary(homes)
            if (summary["households"] != baseline["target"]["households"] or
                    summary["episodes"]["episodes"] !=
                    baseline["target"]["episodes"]["episodes"] or
                    summary["nuisance_normal_days"]["normal_days"] !=
                    baseline["target"]["nuisance_normal_days"]["normal_days"]):
                raise ValueError("candidate and baseline denominators differ")
            gain = _numbers(summary)[0] - _numbers(baseline["target"])[0]
            nuisance_change = _numbers(summary)[1] - _numbers(baseline["target"])[1]
            condition_guard = all(
                _numbers(by_condition[condition])[0] >=
                _numbers(baseline["by_condition"][condition])[0] and
                _numbers(by_condition[condition])[1] <=
                _numbers(baseline["by_condition"][condition])[1]
                for condition in TARGET_CONDITIONS)
            candidates[name] = {"target": summary, "by_condition": by_condition,
                                "caught_change": gain,
                                "nuisance_day_change": nuisance_change,
                                "adoptable": gain > 0 and nuisance_change <= 0 and condition_guard}
    return {"baseline_result": BASELINE_PATH.name, "seeds": seeds,
            "conditions": TARGET_CONDITIONS, "rhythms": RHYTHMS,
            "tick_minutes": 1,
            "baseline": {"target": baseline["target"],
                         "by_condition": baseline["by_condition"]},
            "candidates": candidates,
            "operating_setting": (next((name for name in CANDIDATE_NAMES
                                        if candidates[name]["adoptable"]), "current"))}


if __name__ == "__main__":
    result = experiment()
    target = Path(__file__).with_name("in_scope_large_candidate_audit_result.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"operating_setting": result["operating_setting"],
                      "baseline": _numbers(result["baseline"]["target"]),
                      "candidates": {name: {"numbers": _numbers(item["target"]),
                                            "adoptable": item["adoptable"]}
                                     for name, item in result["candidates"].items()}},
                     ensure_ascii=False))
