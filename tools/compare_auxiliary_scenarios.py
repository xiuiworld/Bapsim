"""Compare identical synthetic cooker decisions with optional simulated activity."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path

from ai.demo_usage_records import EVALUATION_START, generate
from ai.usage_record_pipeline import KST, evaluate
from service.auxiliary import AuxiliaryDeferral
from service.analysis_policy import route_personal_decision
from workflow import DetectionStateManager


START = datetime(2026, 10, 7, tzinfo=timezone.utc)


def at(minutes: int) -> str:
    return (START + timedelta(minutes=minutes)).isoformat()


def compare() -> dict[str, object]:
    results = {}
    for activity_enabled in (False, True):
        gap = DetectionStateManager(candidate_window=timedelta(hours=36))
        decline = DetectionStateManager(min_consecutive=1, candidate_window=timedelta(days=1),
                                         reminder_cadence=timedelta(days=7), resumption_alerts=False)
        auxiliary = AuxiliaryDeferral()
        requests = []
        if activity_enabled:
            auxiliary.record("cooker_01", at(50), "door_opened")
        for minutes in (0, 60):
            decision = {"source": "simulator", "device_id": "cooker_01", "as_of": at(minutes),
                        "routine_status": "check_required", "reason_codes": ["EXPECTED_USE_MISSING"]}
            _, updates = route_personal_decision("window", decision, gap, decline,
                                                   deferred=auxiliary.defer(decision))
            requests.extend({"action": update.action, "at": update.as_of} for update in updates
                            if update.action == "notify_requested")
        for decision in auxiliary.due(at(90)):
            requests.extend({"action": update.action, "at": update.as_of} for update in gap.ingest(decision)
                            if update.action == "notify_requested")
        trend = {"source": "simulator", "device_id": "cooker_01", "as_of": at(180),
                 "routine_status": "check_required", "reason_codes": ["USE_DECLINE"]}
        _, updates = route_personal_decision("trend", trend, gap, decline)
        trend_requests = [{"action": update.action, "at": update.as_of} for update in updates
                          if update.action == "notify_requested"]
        results["with_simulated_activity" if activity_enabled else "cooker_only"] = {
            "gap_requests": requests, "decline_requests": trend_requests}
    observations, labels = generate()
    activity = [
        {**{field: label[field] for field in
            ("schema_version", "mode", "household_id", "device_id", "source")},
         "observed_at": datetime.combine(date.fromisoformat(label["local_date"]),
                                         time(hour), KST).isoformat(),
         "signal": "motion_detected"}
        for label in labels if label["review"] == "check_needed" for hour in range(24)
    ]
    cooker = evaluate(observations, labels, EVALUATION_START)
    assisted = evaluate(observations, labels, EVALUATION_START, simulated_activity=activity)
    household_comparison = {
        "households": 2, "scripted_activity_events": len(activity),
        "activity_schedule": "One synthetic motion event per hour on each check_needed day only; stress case",
        "cooker_only": {key: cooker[key] for key in
                        ("personal", "notification_request", "first_request_episodes")},
        "with_simulated_activity": {key: assisted[key] for key in
                                    ("personal", "notification_request", "first_request_episodes")},
    }
    return {"mode": "simulation", "scope": "Identical synthetic assessment stream and workflow settings; "
            "activity changes only the optional simulation deferral. No HA, phone, or real household data.",
            "candidate_times": [at(0), at(60)], "activity_time": at(50), "results": results,
            "household_comparison": household_comparison}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(args.output)


if __name__ == "__main__":
    main()
