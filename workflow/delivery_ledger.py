"""Durable logical notification attempts with conservative crash recovery."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping


RETRY_DELAYS = (timedelta(seconds=30), timedelta(minutes=2), timedelta(minutes=5))
TERMINAL = {"ha_accepted", "uncertain", "cancelled"}


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("delivery time must include timezone")
    return result


class DeliveryLedger:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, object]] = {}

    def prepare(self, notification_id: str, incident_id: str, source: str, device_id: str,
                action: str, reason_codes: list[str], now: str, *,
                destination: str = "home_assistant", title: str = "", message: str = "") -> dict[str, object]:
        if notification_id in self.records:
            return self.records[notification_id]
        expires_at = (_time(now) + timedelta(minutes=10)).isoformat()
        record: dict[str, object] = {
            "notification_id": notification_id, "incident_id": incident_id,
            "source": source, "device_id": device_id, "action": action,
            "type": ("observation_issue" if action == "observation_issue_requested" else
                     "usage_resumed" if action == "usage_resumed_requested" else "check_in"),
            "mode": "simulation" if source == "simulator" else "live",
            "detail_path": "/bapsim-lab/status",
            "reason_codes": list(reason_codes), "destination": destination,
            "title": title, "message": message, "created_at": now,
            "expires_at": expires_at, "status": "pending", "attempts": 0,
            "next_retry_at": now, "in_flight": False, "last_attempt_at": None,
            "updated_at": now, "error": None,
        }
        self.records[notification_id] = record
        return record

    def get(self, notification_id: str) -> dict[str, object] | None:
        return self.records.get(notification_id)

    def due(self, notification_id: str, now: str) -> bool:
        record = self.records[notification_id]
        return (record["status"] in {"pending", "failed"} and not record["in_flight"]
                and record["next_retry_at"] is not None
                and _time(str(record["next_retry_at"])) <= _time(now) < _time(str(record["expires_at"])))

    def begin(self, notification_id: str, now: str) -> bool:
        if not self.due(notification_id, now):
            return False
        record = self.records[notification_id]
        record["attempts"] = int(record["attempts"]) + 1
        record["in_flight"] = True
        record["last_attempt_at"] = now
        record["updated_at"] = now
        return True

    def finish(self, notification_id: str, outcome: str, now: str, error: str | None = None) -> None:
        if outcome not in {"ha_accepted", "failed", "uncertain"}:
            raise ValueError("invalid delivery outcome")
        record = self.records[notification_id]
        if not record["in_flight"]:
            raise ValueError("delivery attempt was not started")
        record["in_flight"] = False
        record["status"] = outcome
        record["updated_at"] = now
        record["error"] = error
        attempt = int(record["attempts"])
        record["next_retry_at"] = (
            (_time(now) + RETRY_DELAYS[attempt - 1]).isoformat()
            if outcome == "failed" and attempt <= len(RETRY_DELAYS) else None
        )

    def cancel(self, notification_id: str, now: str, reason: str) -> None:
        record = self.records[notification_id]
        if record["status"] in TERMINAL or (
                record["status"] == "failed" and record["next_retry_at"] is None):
            return
        record.update(status="cancelled", in_flight=False, next_retry_at=None,
                      updated_at=now, error=reason)

    def expire(self, now: str) -> list[str]:
        expired = []
        for notification_id, record in self.records.items():
            if (record["status"] in {"pending", "failed"} and
                    _time(str(record["expires_at"])) <= _time(now)):
                self.cancel(notification_id, now, "expired")
                expired.append(notification_id)
        return expired

    def prune(self, now: str, retention: timedelta = timedelta(days=180)) -> int:
        cutoff = _time(now) - retention
        old = [notification_id for notification_id, record in self.records.items()
               if not record["in_flight"] and
               (record["status"] in TERMINAL or
                (record["status"] == "failed" and record["next_retry_at"] is None)) and
               _time(str(record["updated_at"])) < cutoff]
        for notification_id in old:
            del self.records[notification_id]
        return len(old)

    def dump_state(self) -> list[dict[str, object]]:
        return [dict(record) for record in self.records.values()]

    def load_state(self, stored: list[Mapping[str, object]]) -> None:
        self.records = {}
        for item in stored:
            record = dict(item)
            notification_id = str(record["notification_id"])
            if record.get("in_flight"):
                record.update(status="uncertain", in_flight=False, next_retry_at=None,
                              error="process_restarted_during_delivery")
            self.records[notification_id] = record
