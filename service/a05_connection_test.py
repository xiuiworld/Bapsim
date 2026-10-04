"""Bounded A05 board-to-B contract test, separate from personal learning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .a05_contract_records import A05ContractRecords


MAX_RUN = timedelta(minutes=10)


class A05ConnectionTest:
    """Require fresh closed then open reports in one verified board stream."""

    def __init__(self, household_id: str, device_id: str, source_ref: str,
                 test_run_id: str, started_at: datetime):
        if not test_run_id or started_at.tzinfo is None:
            raise ValueError("A05 test requires a run ID and timezone")
        self.household_id = household_id
        self.device_id = device_id
        self.source_ref = source_ref
        self.test_run_id = test_run_id
        self.started_at = started_at.astimezone(timezone.utc)
        self.expires_at = self.started_at + MAX_RUN
        self.contract = A05ContractRecords(household_id, device_id, source_ref,
                                           mode="test", test_run_id=test_run_id)
        self.phase = "await_closed"
        self.closed: dict | None = None
        self.result: dict | None = None
        self.mobile_attempted = False
        self.stopped = False

    def status(self) -> dict:
        return {"test_run_id": self.test_run_id, "mode": "test",
                "household_id": self.household_id, "device_id": self.device_id,
                "source_ref": self.source_ref, "started_at": self.started_at.isoformat(),
                "expires_at": self.expires_at.isoformat(), "phase": self.phase,
                "closed_record_id": self.closed["record_id"] if self.closed else None,
                "result": self.result, "mobile_attempted": self.mobile_attempted}

    def stop(self, phase: str = "stopped") -> None:
        self.stopped = True
        if self.phase not in {"complete", "delivery_failed"}:
            self.phase = phase

    def accept(self, raw: dict, now: datetime, source_at: datetime | None) -> tuple[list[dict], dict | None]:
        if self.stopped or self.result is not None:
            return [], None
        now = now.astimezone(timezone.utc)
        if now > self.expires_at:
            self.stop("expired")
            return [], None
        records = self.contract.accept(raw, now, source_at)
        if not records:
            return [], None
        if any(item["signal"] == "device_connection" and item["value"] == "offline"
               for item in records):
            self.closed = None
            self.phase = "await_closed"
        lid = next((item for item in records if item["signal"] == "lid_open"
                    and item["quality"] == "good"), None)
        if lid is None:
            return records, None
        if self.closed is None:
            if lid["value"] is False:
                self.closed = {"record_id": lid["record_id"], "stream_id": lid["stream_id"],
                               "report_counter": raw["ReportCounter"]}
                self.phase = "closed_observed"
            return records, None
        if (lid["value"] is True and lid["kind"] == "change" and
                lid["stream_id"] == self.closed["stream_id"] and
                raw["ReportCounter"] != self.closed["report_counter"]):
            self.result = {
                "kind": "a05_connection_test", "mode": "test", "test_run_id": self.test_run_id,
                "household_id": self.household_id, "device_id": self.device_id,
                "source_ref": self.source_ref, "phase": "board_input_confirmed",
                "closed_record_id": self.closed["record_id"],
                "opened_record_id": lid["record_id"],
                "boot_id": raw["BootId"], "closed_report_counter": self.closed["report_counter"],
                "opened_report_counter": raw["ReportCounter"],
                "observation_sequence": raw["ObservationSequence"],
                "received_at": now.isoformat(),
            }
            self.phase = "board_input_confirmed"
            return records, self.result
        return records, None
