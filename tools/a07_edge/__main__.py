import argparse
import json
from pathlib import Path

import numpy as np

from .dataset import export_jsonl, generate
from .model import train
from .measured import import_measured
from .observations import Calibration, extract, read_jsonl, record_jsonl


def main():
    p = argparse.ArgumentParser(description="A07 offline synthetic sensor experiment; never writes HA/board")
    sub = p.add_subparsers(dest='command', required=True)
    g = sub.add_parser('generate'); g.add_argument('output', type=Path)
    g.add_argument('--profiles', type=int, default=120); g.add_argument('--per-profile', type=int, default=20)
    g.add_argument('--stress-profiles', type=int, default=30); g.add_argument('--seed', type=int, default=7307)
    t = sub.add_parser('train'); t.add_argument('dataset', type=Path)
    m = sub.add_parser('import-measured'); m.add_argument('spec', type=Path); m.add_argument('output', type=Path)
    r = sub.add_parser('record'); r.add_argument('source', type=Path); r.add_argument('output', type=Path)
    r = sub.add_parser('replay'); r.add_argument('source', type=Path); r.add_argument('calibration', type=Path)
    e = sub.add_parser('export-log'); e.add_argument('dataset', type=Path); e.add_argument('session'); e.add_argument('output', type=Path)
    args = p.parse_args()
    if args.command == 'generate':
        m = generate(args.output, args.profiles, args.per_profile, args.stress_profiles, args.seed)
        print(json.dumps({k:m[k] for k in ('excerpts','raw_samples','windows')}))
    elif args.command == 'train':
        r = train(args.dataset); print(json.dumps({"nodes":r['nodes'],"splits":r['splits']}, indent=2))
    elif args.command == 'import-measured':
        print(json.dumps(import_measured(args.spec,args.output)))
    elif args.command == 'record':
        print('recorded',record_jsonl(args.source,args.output))
    elif args.command == 'export-log':
        export_jsonl(args.dataset,args.session,args.output)
    else:
        rows = list(read_jsonl(args.source))
        if not rows or len({r['session_id'] for r in rows}) != 1 or len({r['input_kind'] for r in rows}) != 1:
            raise ValueError('replay one session and provenance at a time')
        cal = Calibration(**json.loads(args.calibration.read_text()))
        if any(r['calibration_version'] != cal.version for r in rows):
            raise ValueError('calibration version mismatch')
        adc = np.array([[np.nan if a is None else a for a in r['adc']] for r in rows])
        x, valid, ends, leds = extract([r['uptime_ms'] for r in rows], adc,
                                     [-1 if r['lid'] is None else int(r['lid']) for r in rows],
                                     [r['boot_id'] for r in rows], cal, sequence=[r['sequence'] for r in rows])
        print(json.dumps({'samples':len(rows),'windows':len(x),'valid':int(valid.sum()),
                          'input_kind':rows[0]['input_kind'],'note':'no model/HA/device writes'}))


if __name__ == '__main__':
    main()
