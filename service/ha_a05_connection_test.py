"""Explicit, bounded HA adapter for the real A05 test-origin board."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .a05_connection_test import A05ConnectionTest
from .a05_snapshot_input import verified_test_report, pending_reports, report_time


DETAIL_PATH = "/bapsim-lab/a05-test"


class A05TestController:
    def __init__(self, hass, household_id, a05_inputs, registry_get, journal,
                 show, schedule_expiry, logger, external_service):
        self.hass = hass
        self.household_id = household_id
        self.a05_inputs = a05_inputs
        self.registry_get = registry_get
        self.journal = journal
        self.show = show
        self.schedule_expiry = schedule_expiry
        self.logger = logger
        self.external_service = external_service
        self.run: A05ConnectionTest | None = None
        self.phone_service: str | None = None
        self.cancel_expiry = None
        self.show("a05_connection_test", "not_armed", {"mode": "test"})

    @staticmethod
    def _record(run: A05ConnectionTest, phase: str, now: datetime, **extra) -> dict:
        return {"kind": "a05_connection_test_run", "mode": "test",
                "test_run_id": run.test_run_id, "household_id": run.household_id,
                "device_id": run.device_id, "source_ref": run.source_ref,
                "scenario": run.scenario, "phase": phase,
                "received_at": now.astimezone(timezone.utc).isoformat(), **extra}

    def _show(self, run: A05ConnectionTest) -> None:
        self.show("a05_connection_test", run.phase, run.status())

    async def start(self, call) -> None:
        if self.external_service:
            raise ValueError("A05 connection test requires HA internal analysis mode")
        if self.run is not None and not self.run.stopped:
            raise ValueError("Stop the current A05 test before starting another")
        entity_id = call.data.get("entity_id")
        device_id = call.data.get("device_id")
        scenario = call.data.get("scenario", "lid_connection")
        adapter = self.a05_inputs.get(entity_id)
        if adapter is None or device_id != adapter.device_id:
            raise ValueError("Register this A05 entity and matching device ID first")
        entry = self.registry_get(entity_id)
        if (entry is None or entry.platform != "bapsim_matter_probe" or
                entry.unique_id != f"a05_{adapter.matter_entry_id}_{adapter.node_id}_ReportCounter"):
            raise ValueError("A05 probe registry identity changed")
        target = call.data.get("phone_service")
        if isinstance(target, str) and target.startswith("notify."):
            target = target.removeprefix("notify.")
        if (not isinstance(target, str) or not target.startswith("mobile_app_") or
                not self.hass.services.has_service("notify", target)):
            raise ValueError("Choose one registered test phone notify.mobile_app_* service")
        now = datetime.now(timezone.utc)
        run = A05ConnectionTest(self.household_id, device_id, entity_id,
                                uuid4().hex, now, scenario=scenario)
        if not await self.journal.write_test([self._record(run, "armed", now)]):
            raise ValueError("Test journal unavailable; mobile test was not armed")
        self.run = run
        self.phone_service = target
        self.cancel_expiry = self.schedule_expiry(
            self.hass, lambda at: self._expire(run, at), run.expires_at)
        self._show(run)

    async def stop(self, call=None) -> None:
        run = self.run
        if run is None or run.stopped:
            return
        run.stop()
        self.phone_service = None
        if self.cancel_expiry is not None:
            self.cancel_expiry()
            self.cancel_expiry = None
        self._show(run)
        await self.journal.write_test([self._record(run, "stopped", datetime.now(timezone.utc))])

    def _expire(self, run, _now) -> None:
        if self.run is not run or run.stopped:
            return
        run.stop("expired")
        self.phone_service = None
        self.cancel_expiry = None
        self._show(run)
        self.hass.async_create_task(self.journal.write_test([
            self._record(run, "expired", datetime.now(timezone.utc))]))

    def on_unregistered(self, entity_id: str) -> None:
        run = self.run
        if run is not None and run.source_ref == entity_id and not run.stopped:
            # Registry callbacks are synchronous: disarm before queued writes or
            # a pending result task can reach the phone service.
            run.stop()
            self.phone_service = None
            if self.cancel_expiry is not None:
                self.cancel_expiry()
                self.cancel_expiry = None
            self._show(run)
            self.hass.async_create_task(self.journal.write_test([
                self._record(run, "stopped", datetime.now(timezone.utc))]))

    def on_state(self, entity_id, entry, state, previous, now: datetime) -> None:
        run = self.run
        if (run is None or run.stopped or run.result is not None or
                entity_id != run.source_ref or previous is None or state is None):
            return
        adapter = self.a05_inputs.get(entity_id)
        if adapter is None or adapter.device_id != run.device_id:
            return
        raw = verified_test_report(entry, state, adapter.matter_entry_id, adapter.node_id)
        source_at = getattr(state, "last_updated", None)
        if (raw is None or not isinstance(source_at, datetime) or source_at.tzinfo is None or
                source_at < run.started_at or source_at > now):
            return
        records, result = [], None
        for report in pending_reports(state, raw, run.contract.previous):
            at = report_time(report, raw, source_at)
            if at is None or at < run.started_at:
                continue  # retained data preceding the explicit arm is not evidence
            batch, current = run.accept(report, now, at)
            records.extend(batch)
            if current is not None:
                result = current
        if not records:
            self._show(run)
            return
        self._show(run)
        self.hass.async_create_task(self._persist_and_deliver(run, records, result))

    async def _persist_and_deliver(self, run, records, result) -> None:
        batch = records + ([result] if result is not None else [])
        if not await self.journal.write_test(batch):
            run.stop("storage_failed")
            self.phone_service = None
            self._show(run)
            return
        if result is None:
            return
        if (run.stopped or self.run is not run or self.phone_service is None or
                datetime.now(timezone.utc) > run.expires_at or run.mobile_attempted):
            return
        # One attempt per explicit run, including failures and uncertain outcomes.
        run.mobile_attempted = True
        target = self.phone_service
        evidence_ref = (result.get("opened_record_id") or
                        result["phase_evidence"][-1]["record_id"])
        if not await self.journal.write_test([
                self._record(run, "mobile_attempt", datetime.now(timezone.utc),
                             opened_record_id=evidence_ref)]):
            run.stop("storage_failed")
            self.phone_service = None
            self._show(run)
            return
        if run.stopped or self.phone_service != target:
            return
        try:
            description = ("A07 합성 원시 재생의 대기→취사→보온→판단 불가 보고가 B 시험 기록에 반영됐습니다. "
                           if run.scenario == "a07_phase_sequence" else
                           "실제 A05 보드의 닫힘→열림이 B 계약 기록과 시험 결과에 반영됐습니다. ")
            await self.hass.services.async_call("notify", target, {
                "title": "밥심 개발용 연결 시험",
                "message": description + f"실행 ID: {run.test_run_id}",
                "data": {"url": DETAIL_PATH, "clickAction": DETAIL_PATH,
                         "tag": f"bapsim_a05_test_{run.test_run_id}"},
            }, blocking=True)
        except Exception as exc:
            self.logger.warning("A05 test phone call failed: %s", type(exc).__name__)
            run.phase = "delivery_failed"
            outcome = "failed_or_uncertain"
        else:
            run.phase = "ha_accepted"
            outcome = "ha_accepted"
        self.phone_service = None
        if self.cancel_expiry is not None:
            self.cancel_expiry()
            self.cancel_expiry = None
        self._show(run)
        await self.journal.write_test([self._record(run, "mobile_result", datetime.now(timezone.utc),
                                                 delivery=outcome)])

    def close(self) -> None:
        if self.run is not None:
            self.run.stop()
        self.phone_service = None
        if self.cancel_expiry is not None:
            self.cancel_expiry()
            self.cancel_expiry = None
