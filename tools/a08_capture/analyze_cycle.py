"""Read-only UART cycle audit. No threshold tuning or inferred ground truth."""
import argparse
import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

KST = timezone(timedelta(hours=9))
CHANNELS = ('lock_raw', 'warm_raw', 'white_rice_raw', 'reservation_raw', 'ntc_raw')
REPORT = re.compile(r'A08 R c=(\d+) p=(\d+) h=(\d+) l=(\d+) f=(\d+) t=(-?\d+) led=([0-3],[0-3],[0-3],[0-3])')


def analyze(session, until):
    bins = defaultdict(lambda: defaultdict(list))
    gaps, discontinuities, boots, report_counts = [], [], Counter(), Counter()
    timings, errors, led_counts = Counter(), Counter(), Counter()
    previous = first = last = None
    peak = None
    samples = reports = 0
    sources = []
    for path in sorted(session.glob('*/raw.csv')):
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for line in source:
                digest.update(line)
        sources.append({'file': str(path.relative_to(session)), 'sha256_at_read': digest.hexdigest()})
        with path.open(newline='', encoding='utf-8') as source:
            for row in csv.DictReader(source):
                at = datetime.fromisoformat(row['utc'])
                if at > until:
                    break
                try:
                    values = {k: int(v) for k, v in row.items() if k != 'utc'}
                except (ValueError, TypeError):
                    continue  # a concurrently written trailing row is not a sample
                point = {'utc': row['utc'], **values}
                first = first or point
                last = point
                samples += 1
                boots[values['boot']] += 1
                if previous and previous['boot'] == values['boot']:
                    ds = values['sequence'] - previous['sequence']
                    dt = values['uptime_ms'] - previous['uptime_ms']
                    if ds != 1:
                        gaps.append({'utc': row['utc'], 'sequence_delta': ds, 'uptime_delta_ms': dt})
                    if ds == 1 and not 40 <= dt <= 60:
                        discontinuities.append({'utc': row['utc'], 'uptime_delta_ms': dt})
                previous = point
                bucket = at.astimezone(KST).replace(second=0, microsecond=0)
                bucket = bucket.replace(minute=bucket.minute // 5 * 5).isoformat()
                for key in CHANNELS:
                    bins[bucket][key].append(values[key])
                bins[bucket]['lid_open'].append(values['lid_open'])
                supply, adc = values['vdd_raw'], values['ntc_raw']
                if 0 < adc < supply:
                    temp = 1 / (1 / 298.15 + math.log(adc / (supply - adc)) / 3950) - 273.15
                    bins[bucket]['nominal_external_c'].append(temp)
                    if peak is None or temp > peak['c']:
                        peak = {'utc': row['utc'], 'c': temp}
                for key in CHANNELS[:4]:
                    if supply > 0:
                        bins[bucket][key + '_normalized'].append(values[key] / supply * 4095)
    for path in sorted(session.glob('*/uart.jsonl')):
        with path.open(encoding='utf-8') as source:
            for line in source:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if datetime.fromisoformat(row['utc']) > until:
                    break
                text = row['line']
                match = REPORT.search(text)
                if match:
                    reports += 1
                    report_counts[f"packed={match[2]},history={match[3]},phase={match[5]}"] += 1
                    led_counts[match[7]] += 1
                match = re.search(r'A08 TIMING samples=200 min_ms=(\d+) max_ms=(\d+) gaps=(\d+)', text)
                if match:
                    timings[f'min={match[1]},max={match[2]},gaps={match[3]}'] += 1
                if '<err>' in text:
                    errors['matter' if '<err> chip:' in text else 'other'] += 1
                if 'dropped' in text.lower():
                    errors['dropped_warning_lines'] += 1
    return {'cutoff_utc': until.isoformat(), 'first': first, 'last': last, 'samples': samples,
            'boots': dict(boots), 'sequence_gaps': gaps,
            'missing_sequences': sum(max(0, g['sequence_delta'] - 1) for g in gaps),
            'consecutive_sample_timing_faults': discontinuities, 'report_count': reports,
            'report_status_and_phase': dict(report_counts), 'led_reports': dict(led_counts),
            'timing_reports': dict(timings), 'error_lines': dict(errors), 'nominal_external_peak': peak,
            'five_minute_medians': {k: {c: median(v) for c, v in channels.items()} for k, channels in sorted(bins.items())},
            'sources': sources, 'ground_truth': 'No exact cooking-start label; medians are observations, not independent labels.',
            'calibration_changed': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('--until', required=True, type=datetime.fromisoformat)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.until.tzinfo is None:
        parser.error('--until needs an explicit timezone')
    result = analyze(args.session, args.until)
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
        output.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('five_minute_medians', 'sources', 'sequence_gaps', 'consecutive_sample_timing_faults')}, ensure_ascii=False, indent=2))
