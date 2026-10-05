"""Fail closed when a raw replay run is incomplete or differs from the oracle."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def verify(log_path, manifest_path):
    raw = Path(log_path).read_bytes()
    log = raw.decode('utf-8', errors='replace')
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8-sig'))
    begins = re.findall(r'A07 RAW BEGIN version=(\S+) state_bytes=(\d+) model=(\S+)', log)
    if len(begins) != 1 or begins[0][0] != manifest['preprocess'] or begins[0][2] != manifest['model']:
        raise ValueError('missing, ambiguous or incorrect run identity')
    rows = re.findall(r'A07 RAW CASE (\S+) windows=(\d+)/(\d+) features=(\d+) valid=(\d+) led=(\d+) model=(\d+)', log)
    expected = {c['case']: c['windows'] for c in manifest['cases']}
    if len(rows) != len(expected) or {r[0] for r in rows} != set(expected):
        raise ValueError('incomplete or duplicated case results')
    for name, actual, want, *errors in rows:
        if int(actual) != expected[name] or int(want) != expected[name] or any(map(int, errors)):
            raise ValueError(f'parity failure: {name}')
    ends = re.findall(r'A07 RAW END cases=(\d+) windows=(\d+) failures=(\d+) window_mean_ns=(\d+) window_max_ns=(\d+) stack_rc=(-?\d+) unused=(\d+)', log)
    if len(ends) != 1:
        raise ValueError('missing or ambiguous final result')
    cases, windows, failures, mean, maximum, rc, unused = map(int, ends[0])
    if cases != len(expected) or windows != sum(expected.values()) or failures or rc:
        raise ValueError('failed summary or stack measurement')
    return dict(model=manifest['model'], preprocessing=manifest['preprocess'],
                cases=cases, samples=sum(c['samples'] for c in manifest['cases']), windows=windows,
                failures=failures, state_bytes=int(begins[0][1]), window_mean_ns=mean,
                window_max_ns=maximum, stack_reserved_bytes=4096, stack_unused_bytes=unused,
                log_sha256=hashlib.sha256(raw).hexdigest(), synthetic_only=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('log')
    p.add_argument('manifest')
    p.add_argument('--output', required=True)
    a = p.parse_args()
    result = verify(a.log, a.manifest)
    text = json.dumps(result, indent=2)
    Path(a.output).write_text(text+'\n')
    print(text)
