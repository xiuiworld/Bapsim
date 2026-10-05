"""Fail-closed bridge from the A05 HA probe's atomic snapshot to raw cooker events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .a05_contract_records import MAX_AGE, valid_snapshot


PATH = "1/4294048769/4293984260"
HISTORY_PATH = "1/4294048769/4293984261"


def valid_history(rows, latest):
    if type(rows) is not list or not 1 <= len(rows) <= 8 or rows[-1] != latest:
        return False
    for i, row in enumerate(rows):
        if not valid_snapshot(row):
            return False
        if i and (row["BootId"] != rows[i-1]["BootId"] or
                  row["ReportCounter"] != rows[i-1]["ReportCounter"]+1 or
                  row["ObservationSequence"] != rows[i-1]["ObservationSequence"]+1 or
                  row["UptimeMs"] < rows[i-1]["UptimeMs"]):
            return False
    return True


def pending_reports(state, latest, previous):
    """Return unseen reports; first observation/reboot is a latest baseline.

    Retention overflow is NOT filled: existing consumers see a sequence gap.
    No replay after a consumer restart or observed offline interval.
    """
    if state.attributes.get("read_method") != "buffered_read_5s" or previous is None:
        return [latest]
    if latest["BootId"] != previous[0] or latest["ReportCounter"] < previous[2]:
        return [latest]
    return [r for r in state.attributes["raw_history"] if r["ReportCounter"] > previous[2]]


def report_time(raw, latest, now, last=None):
    """Estimated UTC from read time and board elapsed time, not a board UTC clock."""
    age_ms = latest["UptimeMs"] - raw["UptimeMs"]
    # Validate before constructing timedelta: wire uptime is an unsigned uint64.
    if not 0 <= age_ms <= MAX_AGE.total_seconds() * 1000:
        return None
    if last is not None and last >= now:
        return None
    at = now - timedelta(milliseconds=age_ms)
    return max(at, last + timedelta(microseconds=1)) if last is not None else at


def _verified_report(entry, state, matter_entry_id: str, node_id: int,
                     input_kind: str) -> dict | None:
    """Validate one explicit origin without relaxing the A05 provenance checks."""
    if (entry is None or entry.platform != "bapsim_matter_probe" or
            entry.unique_id != f"a05_{matter_entry_id}_{node_id}_ReportCounter" or
            state is None or state.state in {"unknown", "unavailable"}):
        return None
    attrs = state.attributes
    if (attrs.get("input_kind") != input_kind or attrs.get("transport") != "matter" or
            attrs.get("matter_entry_id") != matter_entry_id or
            attrs.get("node_id") != node_id or attrs.get("attribute_path") != PATH or
            attrs.get("read_method") not in ("explicit_read_5s", "buffered_read_5s") or
            attrs.get("schema_version") != 1):
        return None
    raw = attrs.get("raw_snapshot")
    if not valid_snapshot(raw):
        return None
    if attrs.get("read_method") == "buffered_read_5s" and (
            attrs.get("history_path") != HISTORY_PATH or
            not valid_history(attrs.get("raw_history"), raw)):
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
    return raw


def verified_report(entry, state, matter_entry_id: str, node_id: int) -> dict | None:
    """Accept only live reports for personal learning and live notifications."""
    return _verified_report(entry, state, matter_entry_id, node_id, "live")


def verified_test_report(entry, state, matter_entry_id: str, node_id: int) -> dict | None:
    """Accept only test reports for an explicitly armed development run."""
    return _verified_report(entry, state, matter_entry_id, node_id, "test")


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
        self.buffered = False

    def deadline(self) -> datetime | None:
        return self.last_report + MAX_AGE if self.last_report else None

    def available(self, now: datetime) -> bool:
        deadline = self.deadline()
        return deadline is not None and self.last_report <= now <= deadline

    def offline(self, now: datetime) -> list[dict]:
        was_online = self.last_report is not None
        # A skipped report makes the interval after the last confirmed report
        # unobserved. Closing it at the new report would count that interval as
        # continuously covered by the personal usage model.
        coverage_end = min(now, self.last_report + timedelta(microseconds=1)) if was_online else now
        self.previous = self.last_report = self.last_lid = None
        return [self._event("availability", "offline", coverage_end,
                            "offline", "unknown", received_at=now)] if was_online else []

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
               availability: str, quality: str,
               received_at: datetime | None = None) -> dict:
        at = now.astimezone(timezone.utc).isoformat()
        received = (received_at or now).astimezone(timezone.utc).isoformat()
        return {"device_id": self.device_id, "event_type": event_type, "value": value,
                "observed_at": at, "received_at": received, "source": "matter",
                "availability": availability, "quality": quality}
