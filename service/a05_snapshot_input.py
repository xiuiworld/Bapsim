"""Fail-closed bridge from the A05 HA probe's atomic snapshot to raw cooker events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


PATH = "1/4294048769/4293984260"
MAX_AGE = timedelta(minutes=3)


def verified_report(entry, state, matter_entry_id: str, node_id: int) -> dict | None:
    """Accept only a live A05 report from its expected Matter entry and node."""
    if (entry is None or entry.platform != "bapsim_matter_probe" or
            entry.unique_id != f"a05_{matter_entry_id}_{node_id}_ReportCounter" or
            state is None or state.state in {"unknown", "unavailable"}):
        return None
    attrs = state.attributes
    if (attrs.get("input_kind") != "live" or attrs.get("transport") != "matter" or
            attrs.get("matter_entry_id") != matter_entry_id or
            attrs.get("node_id") != node_id or attrs.get("attribute_path") != PATH or
            attrs.get("read_method") != "explicit_read_5s" or
            attrs.get("schema_version") != 1):
        return None
    raw = attrs.get("raw_snapshot")
    if not isinstance(raw, dict) or raw.get("SchemaVersion") != 1:
        return None
    boot, sequence, counter = (raw.get(key) for key in
                               ("BootId", "ObservationSequence", "ReportCounter"))
    if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF
           for value in (boot, sequence, counter)):
        return None
    if (str(counter) != state.state or attrs.get("boot_id") != boot or
            attrs.get("observation_sequence") != sequence or
            attrs.get("report_counter") != counter):
        return None
    quality = raw.get("SignalQuality")
    if (not isinstance(quality, dict) or quality.get("LidOpen") != 1 or
            type(raw.get("LidOpen")) is not bool):
        return None
    return raw


class A05SnapshotInput:
    def __init__(self, device_id: str, matter_entry_id: str, node_id: int):
        if not device_id or not matter_entry_id or type(node_id) is not int or node_id < 1:
            raise ValueError("A05 requires device, Matter entry and positive node ID")
        self.device_id = device_id
        self.matter_entry_id = matter_entry_id
        self.node_id = node_id
        self.previous: tuple[int, int, int] | None = None
        self.last_report: datetime | None = None
        self.last_lid: bool | None = None

    def deadline(self) -> datetime | None:
        return self.last_report + MAX_AGE if self.last_report else None

    def available(self, now: datetime) -> bool:
        deadline = self.deadline()
        return deadline is not None and self.last_report <= now <= deadline

    def offline(self, now: datetime) -> list[dict]:
        was_online = self.last_report is not None
        self.previous = self.last_report = self.last_lid = None
        return [self._event("availability", "offline", now, "offline", "unknown")] if was_online else []

    def accept(self, raw: dict | None, now: datetime) -> list[dict]:
        if raw is None:
            return self.offline(now)
        current = (raw["BootId"], raw["ObservationSequence"], raw["ReportCounter"])
        if self.previous == current:
            return []
        previous = self.previous
        interrupted = (previous is not None and
                       (current[0] != previous[0] or
                        current[1] not in (previous[1], previous[1] + 1) or
                        current[2] != previous[2] + 1 or
                        (current[1] == previous[1] and raw["LidOpen"] != self.last_lid) or
                        not self.available(now)))
        events = self.offline(now) if interrupted else []
        self.previous, self.last_report = current, now
        if previous is None or interrupted:
            events.append(self._event("availability", "online", now, "online", "observed"))
        lid = raw["LidOpen"]
        if self.last_lid is None or self.last_lid != lid:
            events.append(self._event("lid_state", "open" if lid else "closed", now,
                                      "online", "observed"))
        self.last_lid = lid
        return events

    def _event(self, event_type: str, value: str, now: datetime,
               availability: str, quality: str) -> dict:
        at = now.astimezone(timezone.utc).isoformat()
        return {"device_id": self.device_id, "event_type": event_type, "value": value,
                "observed_at": at, "received_at": at, "source": "matter",
                "availability": availability, "quality": quality}
