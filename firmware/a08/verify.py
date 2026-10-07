"""Verify the uncommissioned A08 board boot log, not physical sensor accuracy."""
import argparse
import hashlib
import json
from pathlib import Path
import re

def verify(path, temperature=False, contact=False, optical=False):
    raw=Path(path).read_bytes(); text=raw.decode('utf-8',errors='replace')
    assert re.findall(r'A08 INTEGRATION END failures=(\d+)',text)==['0'], 'integration failed/missing/duplicate'
    # Capture begins before flashing; disregard reports from the previous image.
    text=text[text.index('A08 INTEGRATION END'):]
    assert re.findall(r'A08 ADC config gain=1/4 reference_mv=(\d+) resolution=12',text)==['900'], 'ADC range configuration missing/mismatched'
    starts=re.findall(r'A08 START .*confirmed=(\d+) cal_valid=(\d+) driver_errors=(\d+)',text)
    assert starts==[('1' if optical else '0','1','0')], 'driver startup/calibration failed'
    reports=re.findall(r'A08 R c=(\d+) p=(\d+) h=(\d+) l=(\d+) f=(\d+) t=(-?\d+) led=(\d+),(\d+),(\d+),(\d+)',text)
    assert len(reports)>=3, 'insufficient reports'
    if temperature:
        assert all(r[1:3]==('0','0') for r in reports), 'publish failed'
        if optical:
            assert all(all(0<=int(v)<=3 for v in r[6:]) for r in reports), 'invalid LED enum'
            assert any(all(int(v)>0 for v in r[6:]) for r in reports), 'no complete calibrated LED observation'
            samples=re.findall(r'A08 SAMPLE boot=(\d+) seq=(\d+) ms=(\d+) raw=([\d,-]+) supply=(-?\d+) lid=(-?\d+)',text)
            assert len(samples)>=1200, 'less than 60 seconds of raw 20 Hz records'
            assert all(len(r[3].split(','))==5 for r in samples), 'missing raw channel'
            assert all(a[0]==b[0] and int(b[1])==int(a[1])+1 for a,b in zip(samples,samples[1:])), 'raw log sequence gap/reboot'
        else:
            assert all(r[4]=='0' and r[6:]==('0','0','0','0') for r in reports), 'unconfirmed light became valid'
        if contact:
            assert 'A08 CONTACT enabled=1 high_is_open=1 pin=P1.10' in text, 'contact configuration missing'
            assert all(r[3] in ('0','1','255') for r in reports), 'invalid contact code'
            unknown=[i for i,r in enumerate(reports[1:],1) if r[3]=='255']
            gaps=sum(map(int,re.findall(r'A08 TIMING samples=200 min_ms=\d+ max_ms=\d+ gaps=(\d+)',text)))
            assert len(unknown)<=gaps, 'unknown contact without an acquisition gap'
            assert all(any(r[3] in ('0','1') for r in reports[i+1:i+3]) for i in unknown), 'contact did not recover after a gap'
            assert reports[-1][3] in ('0','1'), 'contact not valid at capture end'
        else:
            assert all(r[3]=='255' for r in reports), 'unconfirmed contact became valid'
        assert all(-2000<=int(r[5])<=12000 for r in reports), 'temperature missing/out of supported range'
        supply=re.findall(r'A08 SUPPLY raw=(\d+) mv=(\d+) temperature_enabled=1',text)
        assert supply and all(1700<=int(mv)<=3600 and abs(int(mv)-int(raw)*3600/4096)<=1 for raw,mv in supply), 'supply measurement invalid'
    else:
        assert all(r[1:]==('0','0','255','0','-32768','0','0','0','0') for r in reports), 'unconfirmed sensor became valid or publish failed'
    assert all(int(a[0])<int(b[0]) for a,b in zip(reports,reports[1:])), 'counter did not advance'
    adc=re.findall(r'A08 ADC raw=.* status=(-?\d+),(-?\d+),(-?\d+),(-?\d+),(-?\d+)',text)
    assert adc and all(r==('0','0','0','0','0') for r in adc), 'ADC read failed'
    voltage_pairs=re.findall(r'A08 ADC raw=([\d,-]+) status=[\d,-]+\s+[^\n]*?A08 ADC mv=([\d,-]+)',text)
    assert voltage_pairs, 'ADC voltage diagnostics missing'
    for codes,voltages in voltage_pairs:
        codes=list(map(int,codes.split(','))); voltages=list(map(int,voltages.split(',')))
        assert len(codes)==len(voltages)==5, 'missing ADC channel'
        for code,mv in zip(codes,voltages):
            if code<=4 or code>=4091:
                assert mv==-1, 'clipped sample converted as valid'
            else:
                assert abs(mv-code*3600/4096)<=1, 'ADC conversion gain mismatch'
    timing=re.findall(r'A08 TIMING samples=200 min_ms=(\d+) max_ms=(\d+) gaps=(\d+)',text)
    run=best=0
    for lo,hi,gaps in timing:
        run=run+1 if int(lo)>=40 and int(hi)<=60 and int(gaps)==0 else 0
        best=max(best,run)
    assert best>=6, 'no 60 second continuous 20 Hz observation'
    assert not re.search(r'HardFault|FATAL ERROR|Stack overflow|acquisition blocked',text,re.I), 'board fault'
    assert not re.search(r'\d+ messages dropped',text,re.I), 'UART diagnostics dropped'
    return {'integration_failures':0,'driver_errors':0,'reports':len(reports),
            'unknown_reports':sum(int(r[5])==-32768 for r in reports),
            'adc_read_checks':len(adc),'clean_timing_windows':best,'timing':timing,
            'adc_gain':'1/4','adc_reference_mv':900,'voltage_checks':len(voltage_pairs),
            'origin':'test','temperature_enabled':temperature,'contact_enabled':contact,'optical_enabled':optical,'contact_states':sorted({int(r[3]) for r in reports}),'contact_unknown_reports':sum(r[3]=='255' for r in reports),'physical_sensors_verified':False,
            'sha256':hashlib.sha256(raw).hexdigest()}

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('log'); parser.add_argument('--output',required=True)
    parser.add_argument('--temperature',action='store_true')
    parser.add_argument('--contact',action='store_true')
    parser.add_argument('--optical',action='store_true')
    args=parser.parse_args(); result=verify(args.log,args.temperature,args.contact,args.optical)
    with open(args.output,'x',encoding='utf-8') as f: json.dump(result,f,indent=2)
    print(json.dumps(result,indent=2))
