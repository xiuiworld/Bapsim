"""Record the installed A08 contact firmware without changing or halting it.

Addresses are specific to the ELF verified for this test, not a general API.
Create STOP in the output directory to end recording early.
"""
import argparse
import csv
import ctypes
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import threading
import time

import serial


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--serial", required=True, help="J-Link serial of the explicitly selected board")
    parser.add_argument("--elf", required=True, type=Path, help="Exact historical ELF whose addresses are verified below")
    parser.add_argument("--port", default="COM8")
    parser.add_argument("--seconds", type=int, default=14400)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    elf = args.elf
    nm = "C:/ncs/toolchains/4f5b6ad6dd/opt/zephyr-sdk/gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-nm.exe"
    symbols = subprocess.check_output([nm, "-C", str(elf)], text=True)
    if not re.search(r"^20000560\s+d\s+\(anonymous namespace\)::sSensors$", symbols, re.M):
        raise RuntimeError("ELF sensor address differs; do not use stale addresses")
    metadata = {
        "start_utc": stamp(), "duration_seconds": args.seconds,
        "firmware": "3.4.1-a08-contact-1+10", "elf_sha256": hashlib.sha256(elf.read_bytes()).hexdigest(),
        "source": "live SWD reads plus UART; no reset, halt, flash or serial writes",
        "nominal_interval_ms": 100, "time_basis": "Host UTC at receipt; J-Link may buffer output. Sampling interval is 100 ms plus SWD overhead; receipt timestamps are not exact acquisition times.",
        "channels": ["lock", "warm", "white_rice", "reservation", "ntc"],
        "gpio": "P1.10: 0 closed, 1 open", "temperature": "nominal 10K B3950; uncalibrated surface temperature",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    commands = output / "read.jlink"
    with commands.open("w", encoding="ascii") as f:
        f.write("connect\n")
        for _ in range(args.seconds * 10):
            f.write("mem16 0x20000570 5\nmem16 0x200005A4 1\nmem32 0x500D820C 1\nsleep 100\n")
        f.write("q\n")
    stopped = threading.Event()
    errors = []
    uart_count = [0]
    port = serial.Serial(args.port, 115200, timeout=0.2)

    def uart_reader():
        try:
            with (output / "uart.log").open("xb") as raw, (output / "uart-timed.jsonl").open("w", encoding="utf-8") as timed:
                pending = b""
                while not stopped.is_set():
                    block = port.read(max(1, port.in_waiting))
                    if not block:
                        continue
                    raw.write(block)
                    raw.flush()
                    pending += block
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        timed.write(json.dumps({"utc": stamp(), "line": line.decode("utf-8", errors="replace")}) + "\n")
                        uart_count[0] += 1
                    timed.flush()
        except Exception as exc:
            errors.append("UART: " + str(exc))
            stopped.set()

    thread = threading.Thread(target=uart_reader, daemon=True)
    thread.start()
    if not ctypes.windll.kernel32.SetThreadExecutionState(0x80000001):
        errors.append("Could not prevent system sleep")
    jlink = subprocess.Popen([
        "C:/Program Files/SEGGER/JLink_V924a/JLink.exe", "-Device", "nRF54LM20A_M33",
        "-If", "SWD", "-Speed", "4000", "-SelectEmuBySN", args.serial,
        "-NoGui", "1", "-ExitOnError", "1", "-CommandFile", str(commands.resolve()),
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
       creationflags=subprocess.CREATE_NO_WINDOW)
    start = time.monotonic()
    count = 0
    values = None
    supply = None
    status_time = 0

    def status(state):
        data = {"state": state, "utc": stamp(), "elapsed_seconds": round(time.monotonic()-start, 1),
                "samples": count, "uart_lines": uart_count[0], "errors": errors,
                "last_raw": values, "pid": __import__("os").getpid()}
        temp = output / "status.tmp"
        temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp.replace(output / "status.json")

    try:
        with (output / "raw.csv").open("w", newline="", encoding="utf-8") as data, (output / "jlink.log").open("w", encoding="utf-8") as trace:
            writer = csv.writer(data)
            writer.writerow(["utc", "elapsed_s", "lock_raw", "warm_raw", "white_rice_raw", "reservation_raw", "ntc_raw", "vdd_raw", "lid_open"])
            status("starting")
            while not stopped.is_set() and not (output / "STOP").exists() and time.monotonic()-start < args.seconds:
                for line in jlink.stdout:
                    trace.write(line)
                    match = re.match(r"20000570 = ((?:[0-9A-Fa-f]{4}\s*){5})", line)
                    if match:
                        values = [int(v, 16) for v in match[1].split()]
                    match = re.match(r"200005A4 = ([0-9A-Fa-f]{4})", line, re.I)
                    if match:
                        supply = int(match[1], 16)
                    match = re.match(r"500D820C = ([0-9A-Fa-f]{8})", line, re.I)
                    if match and values is not None and supply is not None:
                        writer.writerow([stamp(), round(time.monotonic()-start, 3), *values, supply, (int(match[1],16)>>10)&1])
                        count += 1
                        data.flush()
                    if time.monotonic()-status_time >= 5:
                        trace.flush()
                        status("recording" if count else "starting")
                        status_time = time.monotonic()
                    if stopped.is_set() or (output / "STOP").exists() or time.monotonic()-start >= args.seconds:
                        break
                if stopped.is_set() or (output / "STOP").exists() or time.monotonic()-start >= args.seconds:
                    break
                trace.write(stamp() + " SWD disconnected; retrying without reset or halt\n")
                trace.flush()
                status("reconnecting")
                old_args = jlink.args
                if jlink.poll() is None:
                    jlink.terminate()
                jlink.wait(timeout=10)
                time.sleep(2)
                values = supply = None
                jlink = subprocess.Popen(old_args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
            if count == 0:
                errors.append("No sensor samples received")
    finally:
        if jlink.poll() is None:
            jlink.terminate()
        jlink.wait(timeout=10)
        stopped.set()
        thread.join(timeout=3)
        port.close()
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        status("failed" if errors else "complete")


if __name__ == "__main__":
    main()
