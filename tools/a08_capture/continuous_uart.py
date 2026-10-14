"""Daily USB UART records with reconnect, until an explicit ISO deadline.

No debug connection, reset or flash. With --offline-archive, sends only the
baplog status/get/ack protocol. Recovered history remains separate from live CSV.
"""
import argparse
import csv
import ctypes
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import time
import os
import sys

import serial

KST = timezone(timedelta(hours=9))
SAMPLE = re.compile(r"A08 SAMPLE boot=(\d+) seq=(\d+) ms=(\d+) raw=([\d,-]+) supply=(-?\d+) lid=(-?\d+)")


def now():
    return datetime.now(timezone.utc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--until", required=True, help="ISO timestamp with timezone")
    parser.add_argument("--offline-archive", help="Durable archive directory; requires offline-log firmware")
    parser.add_argument("--allow-sleep", action="store_true", help="Do not request Windows sleep prevention")
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--device", default="cooker", help="Stable archive identity; use a different ID for each device")
    args = parser.parse_args()
    deadline = datetime.fromisoformat(args.until)
    if deadline.tzinfo is None or deadline <= now():
        parser.error("until must be a future timestamp with timezone")
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=False)
    (root / "metadata.json").write_text(json.dumps({
        "start_utc": now().isoformat(), "until": deadline.isoformat(),
        "port": args.port, "baud": 115200, "source": "firmware UART 20 Hz; board acquisition 20 Hz",
        "raw_channels": ["lock", "warm", "white_rice", "reservation", "ntc"],
        "usage": "Raw observations only; opening does not prove eating.",
        "temperature": "Nominal 10K B3950 surface temperature; no reference thermometer calibration",
        "origin": "test; optical calibration and model are not independently validated",
        "timing": "UTC host receipt plus device boot ID/sequence/uptime; sample sequence advances by 1 at 20 Hz",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    port = None
    day = None
    log = data = events = None
    pending = b""
    count = lines = reconnects = 0
    last_sample = last_uart = None
    last_flush = 0
    awake = False if args.allow_sleep else bool(ctypes.windll.kernel32.SetThreadExecutionState(0x80000001))
    recovery = None
    if args.offline_archive:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from tools.a08_storage.receiver import Receiver
        recovery = Receiver(args.offline_archive, device=args.device)
    failure = None
    status_warning_active = False

    def status(state, reason=None):
        nonlocal status_warning_active
        payload = {"state": state, "utc": now().isoformat(), "pid": os.getpid(),
            "until": deadline.isoformat(), "samples": count, "uart_lines": lines,
            "reconnects": reconnects, "last_sample_utc": last_sample,
            "last_uart_utc": last_uart, "sleep_prevention": awake, "reason": reason}
        if recovery:
            payload["offline_storage"] = recovery.status()
        temp = root / "status.tmp"
        for attempt in range(5):
            try:
                temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                temp.replace(root / "status.json")
                status_warning_active = False
                return
            except PermissionError as exc:
                # Windows readers can briefly prevent an atomic rename. A locked
                # monitoring file must not interrupt the sensor data stream.
                if attempt < 4:
                    time.sleep(0.05)
                    continue
                if events and not events.closed and not status_warning_active:
                    events.write(json.dumps({"utc": now().isoformat(),
                        "event": "status_update_blocked", "reason": str(exc)}) + "\n")
                    events.flush()
                status_warning_active = True

    try:
        status("connecting")
        while now() < deadline and not (root / "STOP").exists():
            new_day = now().astimezone(KST).strftime("%Y-%m-%d")
            if day != new_day:
                for f in (log, data, events):
                    if f:
                        f.close()
                day = new_day
                folder = root / day
                folder.mkdir(exist_ok=True)
                log = (folder / "uart.jsonl").open("a", encoding="utf-8")
                data_path = folder / "raw.csv"
                needs_header = not data_path.exists() or data_path.stat().st_size == 0
                data = data_path.open("a", encoding="utf-8", newline="")
                writer = csv.writer(data)
                if needs_header:
                    writer.writerow(["utc", "boot", "sequence", "uptime_ms", "lock_raw", "warm_raw", "white_rice_raw", "reservation_raw", "ntc_raw", "vdd_raw", "lid_open"])
                events = (folder / "events.jsonl").open("a", encoding="utf-8")
            if port is None:
                try:
                    port = serial.Serial(args.port, 115200, timeout=0.2, write_timeout=2)
                    if recovery:
                        recovery.disconnected()
                    pending = b""
                    events.write(json.dumps({"utc": now().isoformat(), "event": "serial_connected"}) + "\n")
                except (serial.SerialException, OSError) as exc:
                    status("reconnecting", str(exc))
                    time.sleep(2)
                    continue
            try:
                if recovery:
                    recovery.tick(port)
                block = port.read(max(1, port.in_waiting))
            except (serial.SerialException, OSError) as exc:
                reconnects += 1
                events.write(json.dumps({"utc": now().isoformat(), "event": "serial_disconnected", "reason": str(exc)}) + "\n")
                events.flush()
                port.close()
                port = None
                status("reconnecting", str(exc))
                continue
            if block:
                last_uart = now().isoformat()
                pending += block
                while b"\n" in pending:
                    raw, pending = pending.split(b"\n", 1)
                    line = raw.decode("utf-8", errors="replace").rstrip("\r")
                    line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line)
                    if recovery:
                        try:
                            recovery.consume(line, port)
                        except (serial.SerialException, ConnectionError) as exc:
                            events.write(json.dumps({"utc": now().isoformat(), "event": "recovery_serial_disconnected", "reason": str(exc)}) + "\n")
                            events.flush()
                            port.close()
                            port = None
                            reconnects += 1
                            recovery.disconnected()
                            pending = b""
                            break
                    # Only sensor diagnostics: exclude pairing codes and unrelated secrets.
                    if "A08 " not in line and "BAPLOG " not in line and not re.search(r"dropped|<err>|fault|assert|panic|stack overflow|exception", line, re.I):
                        continue
                    utc = now().isoformat()
                    log.write(json.dumps({"utc": utc, "line": line}) + "\n")
                    lines += 1
                    match = SAMPLE.search(line)
                    if match:
                        boot, seq, ms, adc, supply, lid = match.groups()
                        codes = adc.split(",")
                        if len(codes) == 5:
                            writer.writerow([utc, boot, seq, ms, *codes, supply, lid])
                            count += 1
                            last_sample = utc
                if len(pending) > 65536:
                    pending = b""
                    events.write(json.dumps({"utc": now().isoformat(), "event": "discarded_oversize_partial_line"}) + "\n")
            if time.monotonic()-last_flush >= 1:
                log.flush(); data.flush(); events.flush()
                state = "recording" if last_sample and (now()-datetime.fromisoformat(last_sample)).total_seconds() < 10 else "waiting_for_samples"
                status(state)
                last_flush = time.monotonic()
    except BaseException as exc:
        failure = str(exc)
        raise
    finally:
        if port:
            port.close()
        for f in (log, data, events):
            if f:
                f.close()
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        status("failed" if failure else "complete", failure)
        if recovery:
            recovery.close()


if __name__ == "__main__":
    main()
