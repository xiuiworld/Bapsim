"""Single-owner serial recovery, durable before ACK; no HA/live replay."""
import csv
from bisect import bisect_left
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from tools.a08_storage.format import FIELDS, SLOT_SIZE, crc, decode_slot, normalized_crc, salvage_prefix


class Receiver:
    def __init__(self, archive, device="cooker", clock=time.monotonic):
        self.root = Path(archive)
        self.root.mkdir(parents=True, exist_ok=True)
        self.device = device
        self.clock = clock
        self.db = sqlite3.connect(self.root / "archive.sqlite3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS slots(device TEXT, id INTEGER, data BLOB NOT NULL,
                crc INTEGER NOT NULL, saved_utc TEXT NOT NULL, error TEXT, PRIMARY KEY(device,id));
            CREATE TABLE IF NOT EXISTS samples(device TEXT, boot INTEGER, seq INTEGER,
                value TEXT NOT NULL, slot INTEGER NOT NULL, PRIMARY KEY(device,boot,seq));
            CREATE TABLE IF NOT EXISTS anchors(device TEXT, boot INTEGER, ms INTEGER,
                utc REAL, uncertainty_s REAL, PRIMARY KEY(device,boot,ms));
        """)
        self.pending = None
        self.next_poll = 0
        self.slot = 0
        self.offset = 0
        self.data = bytearray()
        self.after = 0
        self.last_status = {}
        self.last_error = None
        self.recovered_samples = 0
        self.recovered_slots = 0
        self.token = secrets.randbits(32)

    def close(self):
        self.db.close()

    def disconnected(self):
        # A reconnect may follow a reset. Discover status again; never combine
        # bytes from two transfers without confirming the slot identity.
        self.pending = None
        self.slot = self.offset = 0
        self.data.clear()
        self.next_poll = 0

    def _send(self, port, kind, command):
        payload = (command + "\r\n").encode("ascii")
        if port.write(payload) != len(payload):
            raise ConnectionError("short serial command write")
        self.pending = (kind, command, self.clock(), time.time())

    def tick(self, port):
        now = self.clock()
        if self.pending:
            if now - self.pending[2] > 8:
                kind, command, _, _ = self.pending
                if kind == "status":
                    self.pending = None
                    self.next_poll = 0  # Fresh token: reject pre-suspend responses.
                else:
                    self._send(port, kind, command)
            return
        if now >= self.next_poll:
            self.token = (self.token + 1) & 0xffffffff
            self._send(port, "status", f"baplog status {self.after} {self.token}")

    def _get(self, port):
        self._send(port, "get", f"baplog get {self.slot} {self.offset}")

    def _ack(self, port, checksum):
        self._send(port, "ack", f"baplog ack {self.slot} {checksum:08x}")

    def _export(self, decoded):
        path = self.root / f"{self.device}-slot-{decoded['slot_id']:010d}.csv"
        temp = path.with_suffix(".tmp")
        anchor_cache = {}
        with temp.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["utc_estimated", "time_uncertainty_s", "time_basis", *FIELDS, "config_hash"])
            writer.writeheader()
            for row in decoded["rows"]:
                boot = row["boot"]
                if boot not in anchor_cache:
                    anchors = self.db.execute("SELECT ms,utc,uncertainty_s FROM anchors WHERE device=? AND boot=? ORDER BY ms",
                                              (self.device, boot)).fetchall()
                    anchor_cache[boot] = ([a[0] for a in anchors], anchors)
                times, anchors = anchor_cache[boot]
                index = bisect_left(times, row["uptime_ms"])
                nearby = anchors[max(0, index-1):index+1]
                anchor = min(nearby, key=lambda a: abs(a[0]-row["uptime_ms"])) if nearby else None
                utc, uncertainty, basis = "", "", "unanchored_device_uptime"
                if anchor:
                    delta = (row["uptime_ms"] - anchor[0]) / 1000
                    utc = datetime.fromtimestamp(anchor[1] + delta, timezone.utc).isoformat()
                    # Clock drift is not calibrated; retain explicit uncertainty
                    # and the original uptime instead of inventing receipt times.
                    uncertainty = anchor[2] + abs(delta) * 0.0001
                    basis = "device_uptime_anchor_estimate_100ppm_assumption"
                writer.writerow(dict(row, utc_estimated=utc, time_uncertainty_s=uncertainty,
                                     time_basis=basis, config_hash=decoded["config_hash"]))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)

    def _save(self, data):
        decoded = decode_slot(data, self.slot)
        # Include whole immutable slot and raw sample values in one durable
        # transaction; CSV is a regenerable view, not the ACK authority.
        with self.db:
            existing = self.db.execute("SELECT data FROM slots WHERE device=? AND id=?", (self.device, self.slot)).fetchone()
            if existing and normalized_crc(existing[0]) != normalized_crc(data):
                raise ValueError("slot id reused with different contents")
            for row in decoded["rows"]:
                value = json.dumps(row, sort_keys=True, separators=(",", ":"))
                previous = self.db.execute("SELECT value FROM samples WHERE device=? AND boot=? AND seq=?",
                                           (self.device, row["boot"], row["sequence"])).fetchone()
                if previous and previous[0] != value:
                    raise ValueError("sample identity conflict")
                self.db.execute("INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?)",
                                (self.device, row["boot"], row["sequence"], value, self.slot))
            self.db.execute("INSERT OR IGNORE INTO slots VALUES(?,?,?,?,?,NULL)",
                            (self.device, self.slot, data, decoded["crc"], datetime.now(timezone.utc).isoformat()))
        self._export(decoded)
        self.recovered_samples += len(decoded["rows"])
        self.recovered_slots += 1
        return decoded["crc"]

    def _quarantine(self, data, error):
        self.last_error = str(error)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO slots VALUES(?,?,?,?,?,?)",
                            (self.device, self.slot, data, normalized_crc(data), datetime.now(timezone.utc).isoformat(), str(error)))
        try:
            prefix = salvage_prefix(data, self.slot)
        except ValueError:
            prefix = None
        if prefix:
            with self.db:
                for row in prefix["rows"]:
                    value = json.dumps(row, sort_keys=True, separators=(",", ":"))
                    self.db.execute("INSERT OR IGNORE INTO samples VALUES(?,?,?,?,?)",
                                    (self.device, row["boot"], row["sequence"], value, self.slot))
            self._export(prefix)
        (self.root / f"{self.device}-slot-{self.slot:010d}.error.json").write_text(
            json.dumps({"error": str(error), "acknowledged": False,
                        "verified_prefix_samples": len(prefix["rows"]) if prefix else 0}, indent=2))
        self.after = self.slot  # Retrieve later slots without deleting this one.
        self.pending = None
        self.next_poll = self.clock() + 2

    def consume(self, line, port):
        if not self.pending or "BAPLOG " not in line:
            return
        kind = self.pending[0]
        if kind == "status" and "BAPLOG STATUS " in line:
            match = re.search(r"BAPLOG STATUS (.+) crc=([0-9a-fA-F]{8})(?:\s|$)", line)
            if not match:
                return
            try:
                status_bytes = match[1].encode("ascii")
            except UnicodeEncodeError:
                return
            if crc(status_bytes) != int(match[2], 16):
                return
            values = {k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", match[1])}
            required = {"ready", "oldest", "max", "closed", "free", "blocked", "lost", "boot", "ms", "token"}
            if not required <= values.keys():
                return
            if values["token"] != self.token:
                return
            self.last_status = values
            # Status was sampled between command transmission and receipt.
            elapsed = max(0, self.clock() - self.pending[2])
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO anchors VALUES(?,?,?,?,?)",
                                (self.device, values["boot"], values["ms"], self.pending[3] + elapsed / 2, elapsed / 2))
            self.pending = None
            if not values["ready"] or not values["oldest"]:
                self.after = 0
                self.next_poll = self.clock() + 3
                return
            self.slot = values["oldest"]
            existing = self.db.execute("SELECT data,crc,error FROM slots WHERE device=? AND id=?", (self.device, self.slot)).fetchone()
            if existing:
                if existing[2]:
                    self.after = self.slot
                    self.next_poll = self.clock() + 1
                else:
                    self._export(decode_slot(existing[0], self.slot))
                    self._ack(port, existing[1])
                return
            self.data.clear()
            self.offset = 0
            self._get(port)
        elif kind == "get":
            match = re.search(r"BAPLOG DATA id=(\d+) offset=(\d+) crc=([0-9a-fA-F]{8}) hex=([0-9a-fA-F]{512})(?:\s|$)", line)
            if not match or (int(match[1]), int(match[2])) != (self.slot, self.offset):
                return
            chunk = bytes.fromhex(match[4])
            if crc(chunk) != int(match[3], 16):
                return  # Retry, never acknowledge a corrupt transfer.
            self.data.extend(chunk)
            self.offset += len(chunk)
            if self.offset < SLOT_SIZE:
                self._get(port)
            else:
                data = bytes(self.data)
                try:
                    checksum = self._save(data)
                except ValueError as error:
                    self._quarantine(data, error)
                else:
                    self._ack(port, checksum)
        elif kind == "ack":
            match = re.search(r"BAPLOG ACK id=(\d+) crc=([0-9a-fA-F]{8})", line)
            if match and int(match[1]) == self.slot and match[2].lower() == self.pending[1].split()[-1]:
                self.pending = None
                self.next_poll = self.clock()
                self.after = 0
        if "BAPLOG ERROR " in line:
            self.last_error = line.strip()
            self.pending = None
            self.next_poll = self.clock() + 3

    def status(self):
        return {"recovered_slots": self.recovered_slots, "recovered_samples": self.recovered_samples,
                "board": self.last_status, "last_error": self.last_error, "archive": str(self.root.resolve())}
