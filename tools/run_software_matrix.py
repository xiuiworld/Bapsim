"""Record reproducible, synthetic SW scenario evidence for the T08-T13/T17/T20 plan."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import subprocess
import sys
import unittest

from ai.demo_usage_records import EVALUATION_START, generate
from ai.usage_record_pipeline import evaluate


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))
SCENARIOS = {
    "T08": ("tests.test_personal_usage_model.PersonalUsageModelTests.test_fixed_sixty_minute_group_does_not_extend_with_later_openings",),
    "T09": ("tests.test_standalone_policy.PolicyTests.test_overnight_alert_is_sent_only_if_still_active",
            "tests.test_observation_issue.ObservationIssueTests.test_quiet_outage_is_discarded_after_connection_recovers"),
    "T10": ("tests.test_personal_usage_model.PersonalUsageModelTests.test_regular_evening_user_is_not_expected_at_midday",
            "tests.test_standalone_policy.PolicyTests.test_irregular_interval_requires_coverage_and_grace"),
    "T11": ("tests.test_auxiliary_deferral.AuxiliaryDeferralTests.test_simulation_activity_defers_once_then_releases_candidate",
            "tests.test_auxiliary_deferral.AuxiliaryDeferralTests.test_activity_never_defers_live_matter",
            "tests.test_auxiliary_deferral.AuxiliaryDeferralTests.test_same_synthetic_decisions_delay_only_gap_request"),
    "T12": ("tests.test_personal_usage_model.PersonalUsageModelTests.test_decline_requires_long_reference_and_three_daily_evaluations",
            "tests.test_personal_usage_model.PersonalUsageModelTests.test_decline_does_not_count_missing_coverage_as_zero_use"),
    "T13": ("tests.test_delivery_ledger.DeliveryLedgerTests.test_worker_retries_only_after_due_and_does_not_replay_after_restart",
            "tests.test_ha_service_integration.HAServiceIntegrationTests.test_restart_during_notification_call_is_uncertain_and_not_retried"),
    "T20": ("tests.test_notification_controls.NotificationControlsTests.test_pause_is_bounded_idempotent_and_restored",
            "tests.test_personal_usage_model.PersonalUsageModelTests.test_away_window_is_excluded"),
}


def run(output: Path) -> dict[str, object]:
    checks = []
    for scenario_id, names in SCENARIOS.items():
        stream = StringIO()
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in names)
        result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
        checks.append({"id": scenario_id, "status": "passed" if result.wasSuccessful() else "failed",
                       "input_type": "synthetic", "assertions": list(names),
                       "tests_run": result.testsRun, "failures": len(result.failures),
                       "errors": len(result.errors), "log": stream.getvalue().strip()})

    observations, labels = generate()
    comparison = evaluate(observations, labels, EVALUATION_START)
    metrics = {name: comparison[name] for name in (
        "personal", "notification_request", "fixed_24h", "fixed_24h_notification_request")}
    eligible = comparison["notification_request"]["eligible_days"]
    matched = eligible > 0 and eligible == comparison["fixed_24h_notification_request"]["eligible_days"]
    checks.append({"id": "T17", "status": "passed" if matched else "failed",
                   "input_type": "synthetic", "evaluation_start": EVALUATION_START.isoformat(),
                   "households": 2, "metrics": metrics,
                   "comparison_stage": "first workflow notification request on the same eligible household-days",
                   "limitations": "Scripted labels, four daily checkpoints, no quiet-hours release, caregiver controls or phone delivery; no product accuracy approval."})

    commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    report = {"schema_version": 1, "mode": "simulation", "generated_at": datetime.now(timezone.utc).isoformat(),
              "commit": commit, "working_tree_modified": dirty,
              "scope": "Software assertions and synthetic offline evaluation; not real hardware, phone, or household use",
              "overall": "passed" if all(item["status"] == "passed" for item in checks) else "failed",
              "checks": checks}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / "local-data" / f"software-matrix-{stamp}.json"
    report = run(output)
    print(f"{output}: {report['overall']} ({len(report['checks'])} synthetic scenario groups)")
    if report["overall"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
