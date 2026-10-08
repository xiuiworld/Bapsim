"""Bounded Windows display/system request alongside an existing UART recorder.

Does not touch COM ports or change persistent power settings. Explicit sleep,
lid closure and power loss can still interrupt recording. Stop with Ctrl+C or
create STOP_AWAKE in the session directory; requests clear when this exits.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    args = parser.parse_args()
    status_path = args.session / "status.json"
    initial = json.loads(status_path.read_text(encoding="utf-8"))
    deadline = datetime.fromisoformat(initial["until"])
    if deadline.tzinfo is None:
        parser.error("session deadline requires timezone")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetThreadExecutionState.argtypes = [ctypes.c_uint]
    kernel.SetThreadExecutionState.restype = ctypes.c_uint
    try:
        while datetime.now(timezone.utc) < deadline:
            if (args.session / "STOP_AWAKE").exists():
                break
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(2)
                continue
            if status["pid"] != initial["pid"] or status["state"] in {"complete", "failed"}:
                break
            if (datetime.now(timezone.utc) - datetime.fromisoformat(status["utc"])).total_seconds() > 120:
                raise RuntimeError("Recorder status is stale; release awake request")
            if not kernel.SetThreadExecutionState(0x80000003):
                raise ctypes.WinError(ctypes.get_last_error())
            print(json.dumps({"utc": datetime.now(timezone.utc).isoformat(),
                              "pid": os.getpid(), "recorder_pid": status["pid"],
                              "display_and_system_requested": True}), flush=True)
            time.sleep(30)
    finally:
        kernel.SetThreadExecutionState(0x80000000)


if __name__ == "__main__":
    main()
