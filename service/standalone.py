"""Persistent analysis core for the optional MQTT worker."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from domain.personal_model import PersonalUsageModel
from workflow import DetectionStateManager, NotificationControls, WorkflowUpdate
from workflow.quiet_hours import QuietHours
from service.output import StatusOutput


class AnalysisService:
    def __init__(self, household_id: str, state_file: Path,
                 publish: Callable[[str, str, bool], None],
                 notify: Callable[[WorkflowUpdate], bool], *,
                 quiet_start: str = "22:00", quiet_end: str = "08:00") -> None:
        self.state_file = state_file
        self.publish = publish
        self.notify = notify
        self.model = PersonalUsageModel()
        self.gap = DetectionStateManager(candidate_window=timedelta(hours=36))
        self.decline = DetectionStateManager(min_consecutive=1, candidate_window=timedelta(days=1))
        self.controls = NotificationControls()
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
        self.output.revision = int(saved.get("revision", 0))
        self.away = bool(saved.get("away", False))
        self.pending_quiet = {item["incident_id"]: item for item in saved.get("pending_quiet", [])}
        self.availability = {(item["source"], item["device_id"]): False
                             for item in saved.get("devices", [])}

    def save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model.dump_state(), "gap": self.gap.dump_state(),
            "decline": self.decline.dump_state(), "controls": self.controls.dump_state(),
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
            for update in self.gap.ingest({"source": source, "device_id": device_id,
                                           "as_of": str(event["observed_at"]),
                                           "routine_status": "normal", "reason_codes": []}):
                self.pending_quiet.pop(str(update.incident_id), None)
        self.evaluate(source, device_id, received_at)

    def _handle(self, updates: list[WorkflowUpdate], manager: DetectionStateManager, now: str) -> None:
        for update in updates:
            if update.status == "resolved":
                self.pending_quiet.pop(str(update.incident_id), None)
            if update.action != "notify_requested":
                continue
            if self.controls.is_paused(now) or self.controls.is_acknowledged(update.incident_id, now):
                continue
            if self.quiet.is_quiet(now):
                self.pending_quiet[str(update.incident_id)] = {
                    "incident_id": str(update.incident_id), "source": update.source,
                    "device_id": update.device_id,
                }
                continue
            if self.notify(update):
                manager.mark_notified(update.source, update.device_id, now)

    def evaluate(self, source: str, device_id: str, now: str) -> None:
        online = self.availability.get((source, device_id), False)
        paused = self.away or self.controls.is_paused(now)
        self.model.set_away(paused, now)
        if not online or paused:
            status = "away" if paused else "data_unavailable"
            for manager in (self.gap, self.decline):
                self._handle(manager.ingest({"source": source, "device_id": device_id,
                                             "as_of": now, "routine_status": status,
                                             "reason_codes": []}), manager, now)
        for decision in self.model.evaluate(source, device_id, now, online=online, away=paused):
            self._handle(self.gap.ingest(decision), self.gap, now)
        interval = self.model.evaluate_interval(source, device_id, now, online=online, away=paused)
        if interval and interval["routine_status"] in {"normal", "check_required"}:
            self._handle(self.gap.ingest(interval), self.gap, now)
        trend = self.model.evaluate_trend(source, device_id, now, online=online, away=paused)
        if trend:
            self._handle(self.decline.ingest(trend), self.decline, now)
        profile = self.model.profile(source, device_id, now)
        gap = next((item for item in self.gap.dump_state()
                    if (item["source"], item["device_id"]) == (source, device_id)), None)
        decline = next((item for item in self.decline.dump_state()
                        if (item["source"], item["device_id"]) == (source, device_id)), None)
        state = self.output.build(now, profile, "available" if online else "unavailable", gap, decline)
        self.publish(self.output.topic(source, "state"), json.dumps(state, ensure_ascii=False), True)
        self.publish(self.output.topic(source, "availability"), "online" if online else "offline", True)
        self.save()

    def tick(self, now: str) -> None:
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
        for key in tuple(self.availability):
            seen = self.last_heartbeat.get(key)
            if self.availability[key] and (seen is None or current - seen > timedelta(minutes=3)):
                self.availability[key] = False
                self.model.availability(*key, False, now)
            self.evaluate(*key, now)
        if not self.quiet.is_quiet(now):
            active = {item["incident_id"]: item for item in
                      (*self.gap.dump_state(), *self.decline.dump_state())
                      if item["status"] == "check_required" and item["incident_id"]}
            for incident_id, item in tuple(self.pending_quiet.items()):
                self.pending_quiet.pop(incident_id, None)
                current_state = active.get(incident_id)
                if (current_state is None or self.away or self.controls.is_paused(now) or
                        not self.availability.get((item["source"], item["device_id"]), False)):
                    continue
                manager = self.decline if "DECLINING_USAGE" in current_state["reason_codes"] else self.gap
                update = WorkflowUpdate(item["device_id"], item["source"], incident_id,
                                        "check_required", "notify_requested", now,
                                        tuple(current_state["reason_codes"]))
                self._handle([update], manager, now)
        self.save()
