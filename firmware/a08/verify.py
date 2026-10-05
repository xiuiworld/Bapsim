"""Verify the uncommissioned A08 board boot log, not physical sensor accuracy."""
import argparse
import hashlib
import json
from pathlib import Path
import re

def verify(path):
    raw=Path(path).read_bytes(); text=raw.decode('utf-8',errors='replace')
    assert re.findall(r'A08 INTEGRATION END failures=(\d+)',text)==['0'], 'integration failed/missing/duplicate'
    # Capture begins before flashing; disregard reports from the previous image.
    text=text[text.index('A08 INTEGRATION END'):]
    starts=re.findall(r'A08 START .*confirmed=(\d+) cal_valid=(\d+) driver_errors=(\d+)',text)
    assert starts==[('0','1','0')], 'uncommissioned driver startup failed'
    reports=re.findall(r'A08 R c=(\d+) p=(\d+) h=(\d+) l=(\d+) f=(\d+) t=(-?\d+) led=(\d+),(\d+),(\d+),(\d+)',text)
    assert len(reports)>=3, 'insufficient reports'
    assert all(r[1:]==('0','0','255','0','-32768','0','0','0','0') for r in reports), 'unconfirmed sensor became valid or publish failed'
    assert all(int(a[0])<int(b[0]) for a,b in zip(reports,reports[1:])), 'counter did not advance'
    adc=re.findall(r'A08 ADC raw=.* status=(-?\d+),(-?\d+),(-?\d+),(-?\d+),(-?\d+)',text)
    assert adc and all(r==('0','0','0','0','0') for r in adc), 'ADC read failed'
    timing=re.findall(r'A08 TIMING samples=200 min_ms=(\d+) max_ms=(\d+) gaps=(\d+)',text)
    run=best=0
    for lo,hi,gaps in timing:
        run=run+1 if int(lo)>=40 and int(hi)<=60 and int(gaps)==0 else 0
        best=max(best,run)
    assert best>=6, 'no 60 second continuous 20 Hz observation'
    assert not re.search(r'HardFault|FATAL ERROR|Stack overflow|acquisition blocked',text,re.I), 'board fault'
    return {'integration_failures':0,'driver_errors':0,'unknown_reports':len(reports),
            'adc_read_checks':len(adc),'clean_timing_windows':best,'timing':timing,
            'origin':'test','physical_sensors_verified':False,
            'sha256':hashlib.sha256(raw).hexdigest()}

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('log'); parser.add_argument('--output',required=True)
    args=parser.parse_args(); result=verify(args.log)
    with open(args.output,'x',encoding='utf-8') as f: json.dump(result,f,indent=2)
    print(json.dumps(result,indent=2))
