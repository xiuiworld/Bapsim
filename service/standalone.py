"""Persistent analysis core for the optional MQTT worker."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from domain.personal_model import PersonalUsageModel
from workflow import (DeliveryLedger, DetectionStateManager, NotificationControls,
                      ObservationIssueTracker, WorkflowUpdate)
from workflow.quiet_hours import QuietHours
from workflow.notification_policy import (ALERT_ACTIONS, current_request, queue_quiet,
                                          quiet_release_allowed, suppressed)
from service.output import StatusOutput
from service.analysis_policy import assess_personal_use
from service.auxiliary import AuxiliaryDeferral


class AnalysisService:
    def __init__(self, household_id: str, state_file: Path,
                 publish: Callable[[str, str, bool], None],
                 notify: Callable[[WorkflowUpdate], bool | str], *,
                 quiet_start: str = "22:00", quiet_end: str = "08:00") -> None:
        self.state_file = state_file
        self.publish = publish
        self.notify = notify
        self.model = PersonalUsageModel()
        self.gap = DetectionStateManager(candidate_window=timedelta(hours=36))
        self.decline = DetectionStateManager(min_consecutive=1, candidate_window=timedelta(days=1),
                                             reminder_cadence=timedelta(days=7), resumption_alerts=False)
        self.controls = NotificationControls()
        self.observation_issues = ObservationIssueTracker()
        self.delivery = DeliveryLedger()
        self.auxiliary = AuxiliaryDeferral()
        self.quiet = QuietHours(quiet_start, quiet_end)
        self.output = StatusOutput(household_id)
        self.availability: dict[tuple[str, str], bool] = {}
        self.last_heartbeat: dict[tuple[str, str], datetime] = {}
        self.away = False
        self.pending_quiet: dict[str, dict[str, str]] = {}
        if state_file.exists():
            self._restore(json.loads(state_file.read_text(encoding="utf-8")))

    def _restore(self, saved: Mapping[str, object]) -> None:
        self.model.load_state(saved.get("model", {}))
        self.gap.load_state(saved.get("gap", []))
        self.decline.load_state(saved.get("decline", []))
        self.controls.load_state(saved.get("controls", {}))
        self.observation_issues.load_state(saved.get("observation_issues", []))
        self.delivery.load_state(saved.get("delivery", []))
        self.auxiliary.load_state(saved.get("auxiliary", {}))
        self.output.revision = int(saved.get("revision", 0))
        self.away = bool(saved.get("away", False))
        self.pending_quiet = {item.get("notification_id", item["incident_id"]): item
                              for item in saved.get("pending_quiet", [])}
        self.availability = {(item["source"], item["device_id"]): False
                             for item in saved.get("devices", [])}
        for record in self.delivery.records.values():
            if record["status"] == "uncertain":
                manager = self._manager_for(record)
                manager.cancel_request(str(record["source"]), str(record["device_id"]),
                                       str(record["updated_at"]))

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model.dump_state(), "gap": self.gap.dump_state(),
            "decline": self.decline.dump_state(), "controls": self.controls.dump_state(),
            "observation_issues": self.observation_issues.dump_state(),
            "delivery": self.delivery.dump_state(),
            "auxiliary": self.auxiliary.dump_state(),
            "revision": self.output.revision, "away": self.away,
            "pending_quiet": list(self.pending_quiet.values()),
            "devices": [{"source": source, "device_id": device_id}
                        for source, device_id in self.availability],
        }
        temporary = self.state_file.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state_file)

    def _emit_controls(self) -> None:
        self.publish(self.output.topic("matter", "notification_controls"),
                     json.dumps({"schema_version": "0.1.0", "household_id": self.output.household_id,
                                 "revision": self.controls.revision, "pauses": self.controls.pauses,
                                 "acknowledgements": self.controls.acknowledgements}, ensure_ascii=False), True)

    def process_control(self, command: Mapping[str, object], now: str) -> dict[str, object]:
        command_id = command.get("command_id")
        try:
            if command.get("schema_version") != "0.1.0" or command.get("household_id") != self.output.household_id:
                raise ValueError("schema or household mismatch")
            created = datetime.fromisoformat(str(command["created_at"]).replace("Z", "+00:00"))
            current = datetime.fromisoformat(now.replace("Z", "+00:00"))
            if created.tzinfo is None or abs((current - created).total_seconds()) > 600:
                raise ValueError("stale control")
            if command.get("action") == "acknowledge":
                incident_id = command.get("assessment_id", command.get("incident_id"))
                active = [item for item in (*self.gap.dump_state(), *self.decline.dump_state())
                          if item["incident_id"] == incident_id and item["status"] in
                          {"check_required", "notified", "checking"}]
                if len(active) != 1:
                    raise ValueError("acknowledge requires one active assessment")
            result = self.controls.apply(command, now)
            if command.get("action") == "acknowledge":
                item = active[0]
                manager = self.decline if item in self.decline.dump_state() else self.gap
                manager.caregiver_response(item["source"], item["device_id"], "checking", now, item["incident_id"])
            elif command.get("action") == "pause_lifestyle" and self.controls.is_paused(now):
                self.model.set_away(True, now)
                for manager in (self.gap, self.decline):
                    for item in manager.dump_state():
                        manager.ingest({"source": item["source"], "device_id": item["device_id"],
                                        "as_of": now, "routine_status": "away", "reason_codes": []})
            self._emit_controls()
            self.save()
            response = result
        except (KeyError, TypeError, ValueError) as exc:
            response = {"command_id": command_id, "status": "rejected", "reason": str(exc)}
        self.publish(self.output.topic("matter", "control_result"), json.dumps(response), False)
        return response

    def process_routine_config(self, payload: Mapping[str, object]) -> None:
        if (payload.get("schema_version") != "0.1.0" or
                payload.get("household_id") != self.output.household_id or
                not isinstance(payload.get("routines"), list)):
            raise ValueError("invalid initial routine configuration")
        incoming = {}
        for item in payload["routines"]:
            if not isinstance(item, Mapping):
                raise ValueError("invalid routine entry")
            source, device_id = item.get("source"), item.get("device_id")
            if source != "matter" or not isinstance(device_id, str) or not device_id:
                raise ValueError("routine entry needs matter device_id")
            key = source, device_id
            if key in incoming:
                raise ValueError("duplicate routine device")
            self.model.parse_initial_windows(item.get("windows"))
            incoming[key] = item["windows"]
        for source, device_id in self.model.device_keys:
            if (source == "matter" and (source, device_id) not in incoming and
                    self.model.initial_routine(source, device_id)):
                self.model.configure_initial_routine(source, device_id, [])
        for (source, device_id), windows in incoming.items():
            self.model.configure_initial_routine(source, device_id, windows)
        self.save()

    def process_simulated_activity(self, payload: Mapping[str, object]) -> None:
        """Accept simulation-only activity on its separate MQTT topic."""
        if (payload.get("schema_version") != "0.1.0" or
                payload.get("household_id") != self.output.household_id or
                payload.get("source") != "simulator"):
            raise ValueError("invalid simulated activity envelope")
        device_id, at, signal = (payload.get("device_id"), payload.get("at"),
                                 payload.get("signal"))
        if not all(isinstance(value, str) and value for value in (device_id, at, signal)):
            raise ValueError("simulated activity needs device_id, at and signal")
        self.auxiliary.record(device_id, at, signal)
        self.save()

    def process_event(self, event: Mapping[str, object], received_at: str) -> None:
        if event.get("event_type") == "away_state":
            self.away = bool(event.get("away"))
            self.model.set_away(self.away, received_at)
            self.save()
            return
        source, device_id = event.get("source"), event.get("device_id")
        if source not in {"matter", "simulator"} or not isinstance(device_id, str) or not device_id:
            raise ValueError("invalid observation identity")
        kind = event.get("event_type")
        if kind not in {"device_online", "device_offline", "lid_opened", "lid_closed",
                        "cooking_started", "cooking_finished"}:
            return
        key = source, device_id
        if kind == "device_online":
            self.availability[key] = True
        elif kind == "device_offline":
            self.availability[key] = False
        if kind in {"device_online", "device_offline"}:
            self.last_heartbeat[key] = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
        self.model.observe(event)
        if kind == "lid_opened":
            self._handle(self.gap.ingest({"source": source, "device_id": device_id,
                                          "as_of": str(event["observed_at"]),
                                          "routine_status": "normal", "reason_codes": []}),
                         self.gap, received_at)
        self.evaluate(source, device_id, received_at)

    def _handle(self, updates: list[WorkflowUpdate], manager: DetectionStateManager, now: str) -> None:
        for update in updates:
            if update.status == "resolved":
                for key, item in tuple(self.pending_quiet.items()):
                    if item["incident_id"] == update.incident_id:
                        self.pending_quiet.pop(key, None)
                for notification_id, record in self.delivery.records.items():
                    if record["incident_id"] == update.incident_id and record["status"] in {"pending", "failed"}:
                        self.delivery.cancel(notification_id, now, "incident_resolved")
            if update.action not in ALERT_ACTIONS:
                continue
            if suppressed(update.action, update.incident_id, self.controls, now):
                manager.cancel_request(update.source, update.device_id, now)
                continue
            if self.quiet.is_quiet(now):
                queue_quiet(self.pending_quiet, update)
                continue
            notification_id = str(update.notification_id or update.incident_id)
            self.delivery.prepare(notification_id, str(update.incident_id), update.source,
                                  update.device_id, update.action, list(update.reason_codes), now,
                                  destination="home_assistant", title="", message="")
            self.save()
            self._deliver(update, manager, now)

    def _manager_for(self, record: Mapping[str, object]) -> DetectionStateManager:
        return self.decline if "DECLINING_USAGE" in record["reason_codes"] else self.gap

    def _deliver(self, update: WorkflowUpdate, manager: DetectionStateManager, now: str) -> None:
        notification_id = str(update.notification_id or update.incident_id)
        if not self.delivery.begin(notification_id, now):
            return
        self.save()  # A crash after this point is uncertain; never replay it automatically.
        try:
            result = self.notify(update)
            outcome = ("ha_accepted" if result is True else "failed" if result is False else str(result))
            if outcome not in {"ha_accepted", "failed", "uncertain"}:
                outcome = "uncertain"
        except Exception:
            outcome = "uncertain"
        if outcome == "ha_accepted" and update.action not in {
                "usage_resumed_requested", "observation_issue_requested"}:
            manager.mark_notified(update.source, update.device_id, now)
        self.delivery.finish(notification_id, outcome, now)
        record = self.delivery.get(notification_id)
        if outcome == "uncertain" or (outcome == "failed" and record["next_retry_at"] is None):
            manager.cancel_request(update.source, update.device_id, now)
        self.save()

    def _retry_one(self, now: str) -> None:
        for notification_id in self.delivery.expire(now):
            record = self.delivery.get(notification_id)
            self._manager_for(record).cancel_request(str(record["source"]), str(record["device_id"]), now)
        for notification_id, record in self.delivery.records.items():
            if not self.delivery.due(notification_id, now):
                continue
            source, device_id = str(record["source"]), str(record["device_id"])
            action = str(record["action"])
            manager = self._manager_for(record)
            state = manager.current_state(source, device_id)
            if state is not None and state["incident_id"] != record["incident_id"]:
                state = None
            valid = current_request(action, state, observation_unavailable=
                                    self.observation_issues.is_unavailable(source, device_id))
            if not valid or suppressed(action, str(record["incident_id"]), self.controls, now):
                self.delivery.cancel(notification_id, now, "no_longer_valid")
                manager.cancel_request(source, device_id, now)
                continue
            if self.quiet.is_quiet(now):
                continue
            online = self.availability.get((source, device_id), False)
            if (action == "observation_issue_requested" and online) or (
                    action != "observation_issue_requested" and not online):
                continue
            update = WorkflowUpdate(device_id, source, str(record["incident_id"]),
                                    str(state["status"]) if state else "data_unavailable", action, now,
                                    tuple(record["reason_codes"]), notification_id)
            self._deliver(update, manager, now)
            break  # Never flush a backlog of alerts in one tick.

    def evaluate(self, source: str, device_id: str, now: str) -> None:
        online = self.availability.get((source, device_id), False)
        issue = self.observation_issues.update(source, device_id, online, now)
        if issue is not None:
            self._handle([issue], self.gap, now)
        paused = self.away or self.controls.is_paused(now)
        self.model.set_away(paused, now)
        if not online or paused:
            status = "away" if paused else "data_unavailable"
            for manager in (self.gap, self.decline):
                self._handle(manager.ingest({"source": source, "device_id": device_id,
                                             "as_of": now, "routine_status": status,
                                             "reason_codes": []}), manager, now)
        assessments = assess_personal_use(self.model, source, device_id, now,
                                          online=online, away=paused)
        for decision in assessments.windows:
            if self.auxiliary.defer(decision):
                continue
            self._handle(self.gap.ingest(decision), self.gap, now)
        interval = assessments.interval
        if interval and interval["routine_status"] in {"normal", "check_required"}:
            self._handle(self.gap.ingest(interval), self.gap, now)
        trend = assessments.trend
        if trend:
            self._handle(self.decline.ingest(trend), self.decline, now)
        profile = self.model.profile(source, device_id, now)
        gap = self.gap.current_state(source, device_id)
        decline = self.decline.current_state(source, device_id)
        state = self.output.build(now, profile, "available" if online else "unavailable", gap, decline)
        self.publish(self.output.topic(source, "state"), json.dumps(state, ensure_ascii=False), True)
        self.publish(self.output.topic(source, "availability"), "online" if online else "offline", True)
        self.save()

    def tick(self, now: str) -> None:
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
        self.delivery.prune(now)
        for key in tuple(self.availability):
            seen = self.last_heartbeat.get(key)
            if self.availability[key] and (seen is None or current - seen > timedelta(minutes=3)):
                self.availability[key] = False
                self.model.availability(*key, False, now)
            self.evaluate(*key, now)
        if self.away or self.controls.is_paused(now):
            self.auxiliary.clear()
        else:
            for decision in self.auxiliary.due(now):
                key = str(decision["source"]), str(decision["device_id"])
                if not self.availability.get(key, False):
                    continue
                latest_open = self.model.profile(*key, now)["last_lid_open_at"]
                if (latest_open and datetime.fromisoformat(latest_open) >
                        datetime.fromisoformat(str(decision["deferred_from"]))):
                    continue
                self._handle(self.gap.ingest(decision), self.gap, now)
        if not self.quiet.is_quiet(now):
            active = {item["incident_id"]: item for item in
                      (*self.gap.dump_state(), *self.decline.dump_state()) if item["incident_id"]}
            for notification_id, item in tuple(self.pending_quiet.items()):
                self.pending_quiet.pop(notification_id, None)
                incident_id = item["incident_id"]
                current_state = active.get(incident_id)
                action = item.get("action", "notify_requested")
                online = self.availability.get((item["source"], item["device_id"]), False)
                if action == "observation_issue_requested":
                    if quiet_release_allowed(action, None, observation_unavailable=
                                             self.observation_issues.is_unavailable(item["source"], item["device_id"]),
                                             online=online, away=self.away, controls=self.controls,
                                             now=now, incident_id=incident_id):
                        update = WorkflowUpdate(item["device_id"], item["source"], incident_id,
                                                "data_unavailable", action, now,
                                                ("COOKER_DATA_UNAVAILABLE",), item.get("notification_id"))
                        self._handle([update], self.gap, now)
                    continue
                if current_state is None:
                    continue
                if self.away or not online or suppressed(action, incident_id, self.controls, now):
                    continue
                if not quiet_release_allowed(action, current_state, observation_unavailable=False,
                                             online=online, away=self.away, controls=self.controls,
                                             now=now, incident_id=incident_id):
                    manager = self.decline if "DECLINING_USAGE" in current_state["reason_codes"] else self.gap
                    manager.cancel_request(item["source"], item["device_id"], now)
                    continue
                manager = self.decline if "DECLINING_USAGE" in current_state["reason_codes"] else self.gap
                update = WorkflowUpdate(item["device_id"], item["source"], incident_id,
                                        current_state["status"], action, now,
                                        tuple(current_state["reason_codes"]),
                                        item.get("notification_id"))
                self._handle([update], manager, now)
        self._retry_one(now)
        self.save()
