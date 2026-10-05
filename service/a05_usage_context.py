"""Attach verified live board context to an observed lid opening.

This is explanatory evidence. It never creates a use event or changes the
personal model's alert thresholds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


RECENT_COOKING = timedelta(hours=2)


class A05UsageContext:
    def __init__(self) -> None:
        self.current: dict[str, dict] = {}
        self.cooking: dict[str, dict] = {}
        self.pending: dict[tuple[str, str], dict] = {}

    def reset(self, device_id: str) -> None:
        self.current.pop(device_id, None)
        self.cooking.pop(device_id, None)
        for key in tuple(self.pending):
            if key[0] == device_id:
                self.pending.pop(key)

    def observe(self, device_id: str, source_ref: str, raw: dict,
                records: list[dict], at: datetime) -> None:
        if any(row["signal"] == "device_connection" and row["value"] == "offline"
               for row in records):
            self.reset(device_id)
        fields = {row["signal"]: row for row in records}
        phase = fields.get("cooker_phase")
        lid = fields.get("lid_open")
        if phase is None or lid is None:
            return
        stream = phase["stream_id"]
        previous = self.current.get(device_id)
        if previous is not None and previous["stream_id"] != stream:
            self.cooking.pop(device_id, None)
        temperature = fields.get("surface_temperature_c")
        warm_led = fields.get("warm_led")
        context = {
            "source": "matter", "device_id": device_id, "source_ref": source_ref,
            "observed_at": at.astimezone(timezone.utc).isoformat(),
            "stream_id": stream, "boot_id": raw["BootId"],
            "report_counter": raw["ReportCounter"],
            "lid_record_id": lid["record_id"],
            "phase": phase["value"], "phase_quality": phase["quality"],
            "phase_record_id": phase["record_id"],
            "model_version": raw["ModelVersion"] if phase["quality"] == "good" else None,
            "inference_method": phase["inference"]["method"] if phase["inference"] else None,
            "temperature_c": temperature["value"] if temperature else None,
            "temperature_quality": temperature["quality"] if temperature else "unknown",
            "temperature_record_id": temperature["record_id"] if temperature else None,
            "warm_led": warm_led["value"] if warm_led else None,
            "warm_led_quality": warm_led["quality"] if warm_led else "unknown",
            "warm_led_record_id": warm_led["record_id"] if warm_led else None,
        }
        self.current[device_id] = context
        if (phase["quality"] == "good" and phase["value"] == "cooking" and
                context["inference_method"] == "edge_model"):
            self.cooking[device_id] = {"at": context["observed_at"],
                                       "record_id": phase["record_id"], "stream_id": stream}

    def stage_open(self, device_id: str, observed_at: str) -> None:
        context = self.current.get(device_id)
        if context is None or context["observed_at"] != observed_at:
            return
        evidence = dict(context)
        evidence["classification"] = "unclassified_open"
        cooking = self.cooking.get(device_id)
        if (context["phase_quality"] == "good" and context["phase"] == "warming" and
                context["inference_method"] == "edge_model"):
            evidence["classification"] = "warming_possible_use"
        elif (cooking is not None and cooking["stream_id"] == context["stream_id"] and
              context["inference_method"] == "edge_model"):
            age = datetime.fromisoformat(observed_at) - datetime.fromisoformat(cooking["at"])
            if timedelta(0) <= age <= RECENT_COOKING:
                evidence["classification"] = "recent_cooking_possible_use"
                evidence["recent_cooking_at"] = cooking["at"]
                evidence["recent_cooking_record_id"] = cooking["record_id"]
        evidence["meal_confirmed"] = False
        self.pending[(device_id, observed_at)] = evidence
        if len(self.pending) > 128:
            self.pending.pop(next(iter(self.pending)))

    def take_open(self, device_id: str, observed_at: str) -> dict | None:
        return self.pending.pop((device_id, observed_at), None)
