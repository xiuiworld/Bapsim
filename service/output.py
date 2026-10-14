"""Build a versioned, short-lived HA/MQTT status snapshot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Mapping

REASON_CODES = {
    "EXPECTED_USE_MISSING": "expected_window_missed",
    "UNUSUAL_USE_GAP": "unusual_gap",
    "DECLINING_USAGE": "declining_frequency",
    "COOKER_DATA_UNAVAILABLE": "observation_gap",
    "USAGE_RESUMED": "usage_resumed",
}


class StatusOutput:
    def __init__(self, household_id: str, revision: int = 0) -> None:
        if not household_id or not all(char.isalnum() or char in "_-" for char in household_id):
            raise ValueError("household_id must be a topic-safe identifier")
        self.household_id = household_id
        self.revision = revision

    def topic(self, source: str, leaf: str) -> str:
        prefix = "bapsim/v1" if source == "matter" else "bapsim/simulation/v1"
        return f"{prefix}/{self.household_id}/{leaf}"

    def build(self, at: str, profile: Mapping[str, object], observation: str,
              gap: Mapping[str, object] | None, decline: Mapping[str, object] | None) -> dict[str, object]:
        parsed = datetime.fromisoformat(at.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("output time requires timezone")
        now = parsed.astimezone(timezone.utc)
        self.revision += 1
        assessments = []
        for kind, item in (("usage_gap", gap), ("declining_usage", decline)):
            if item is None or item.get("incident_id") is None:
                continue
            active = item["status"] in {"check_required", "notified", "checking"}
            assessments.append({
                "assessment_id": item["incident_id"], "type": kind,
                "source": item.get("source"), "device_id": item.get("device_id"),
                "caregiver_status": (item.get("caregiver_status") if active else
                                     "resolved" if item.get("resolution_reason") == "caregiver_completed" else None),
                "resolution_reason": item.get("resolution_reason"),
                "status": "notified" if item["status"] in {"notified", "checking"} else
                          "resolved" if item["status"] == "resolved" else "watch",
                "reason_codes": [REASON_CODES[code] for code in item.get("reason_codes", [])
                                 if code in REASON_CODES],
                "evidence_ids": list(item.get("evidence_ids") or ()),
                # The workflow only knows a scheduled policy eligibility for
                # interval and trend reminders. Other assessments have no
                # exact next evaluation time to promise.
                "next_evaluate_at": item.get("next_reminder_at") if active else None,
            })
        if observation != "available":
            summary = "밥솥 관측 상태를 확인해 주세요."
        elif any(item["status"] in {"watch", "notified"} for item in assessments):
            summary = "평소와 다른 밥솥 사용 기록이 있어 안부 확인을 권합니다."
        else:
            summary = "현재 확인된 밥솥 사용 기록을 표시합니다."
        return {
            "schema_version": "0.1.0", "household_id": self.household_id,
            "revision": self.revision, "generated_at": now.isoformat(),
            "valid_until": (now + timedelta(seconds=180)).isoformat(),
            "last_usage_at": profile.get("last_lid_open_at"),
            "learning": ("personalized" if profile.get("ready") else
                         "insufficient" if not profile.get("last_lid_open_at") else "collecting"),
            "initial_basis": bool(profile.get("initial_ready")),
            "observation": observation,
            "sensor_issues": (["cooker_data_unavailable"] if observation == "unavailable" else []),
            "assessments": assessments, "summary": summary,
            "policy_version": "0.1.0", "model_version": str(profile.get("model", "personal_usage_v1")),
        }
