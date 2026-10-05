"""Bounded UART capture for board evidence (no reset, no serial writes)."""
import argparse
from pathlib import Path
import time
import serial


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', default='COM8')
    p.add_argument('--seconds', type=float, default=90)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    if a.seconds <= 0:
        p.error('seconds must be positive')
    with serial.Serial(a.port, 115200, timeout=0.2) as port, Path(a.output).open('xb') as out:
        deadline = time.monotonic() + a.seconds
        print('UART capture ready', flush=True)
        while time.monotonic() < deadline:
            block = port.read(max(1, port.in_waiting))
            if block:
                out.write(block)
                out.flush()
    print('UART capture complete', flush=True)


if __name__ == '__main__':
    main()
