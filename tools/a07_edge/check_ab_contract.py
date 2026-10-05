"""Offline A codec / A07 metadata / selected B implementation compatibility check.

Uses B's implementation directly; does not copy normalization into A or call HA.
This checks candidate snapshots, not the not-yet-implemented board publisher.
"""
import argparse
import importlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

from tools.a05_contract.wire import encode, decode, PATH
from .model import phase_fields


def check(root):
    package = ModuleType('_a07_contract_review_b')
    package.__path__ = [str(Path(root).resolve()/'service')]
    sys.modules[package.__name__] = package
    records = importlib.import_module(package.__name__+'.a05_contract_records')
    origin = importlib.import_module(package.__name__+'.a05_snapshot_input')
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    results = []
    for label, wanted in enumerate(('inactive', 'cooking', 'warming', None)):
        meta = phase_fields(label, .9876, version='a07-contract-check')
        quality = dict.fromkeys(records.FIELDS, 1)
        quality['CookerPhase'] = meta.pop('CookerPhaseQuality')
        assert meta.pop('input_kind') == 'test'
        raw = dict(SchemaVersion=1, BootId=77, ObservationSequence=1, UptimeMs=60000,
                   ReportCounter=1, LidOpen=False, TemperatureCentiC=6500,
                   LockLed=1, WarmLed=2, WhiteRiceLed=1, TimerFirstLed=3,
                   SignalQuality=quality, **meta)
        decoded, test = decode(encode(raw, test_input=True))
        assert test and records.valid_snapshot(decoded)
        state = SimpleNamespace(state='1', attributes=dict(input_kind='test', transport='matter',
            matter_entry_id='review-entry', node_id=4, attribute_path=PATH,
            read_method='explicit_read_5s', schema_version=1, boot_id=77,
            observation_sequence=1, report_counter=1, raw_snapshot=decoded))
        entry = SimpleNamespace(platform='bapsim_matter_probe', unique_id='a05_review-entry_4_ReportCounter')
        assert origin.verified_test_report(entry, state, 'review-entry', 4) is not None
        assert origin.verified_report(entry, state, 'review-entry', 4) is None
        assert origin.verified_test_report(entry, state, 'wrong-entry', 4) is None
        mapper = records.A05ContractRecords('review-home', 'review-board', 'sensor.review',
                                            mode='test', test_run_id='review-run')
        output = mapper.accept(decoded, now, now)
        phase = next(r for r in output if r['signal']=='cooker_phase')
        temp = next(r for r in output if r['signal']=='surface_temperature_c')
        assert phase['value'] == wanted and temp['value'] == 65.0
        assert all(r['mode']=='test' and r['test_run_id']=='review-run' for r in output)
        if wanted is None:
            assert phase['quality']=='unknown' and phase['inference'] is None
        else:
            assert phase['inference']==dict(method='edge_model', version='a07-contract-check', score=.9876)
        assert mapper.accept(decoded, now+timedelta(seconds=5), now)==[]
        # Invalid temperature preserves independent LED observations.
        decoded['ReportCounter']=decoded['ObservationSequence']=2
        decoded['UptimeMs']+=15000
        decoded['TemperatureCentiC']=None
        decoded['SignalQuality']['TemperatureCentiC']=2
        decoded, test = decode(encode(decoded, test_input=True))
        assert records.valid_snapshot(decoded)
        output = mapper.accept(decoded, now+timedelta(seconds=15), now)
        assert next(r for r in output if r['signal']=='surface_temperature_c')['value'] is None
        assert next(r for r in output if r['signal']=='warm_led')['value']=='on'
        results.append(dict(label=label, phase=wanted, codec=True, b_mapping=True,
                            live_rejected=True, duplicate_ignored=True, independent_quality=True))
    return {'scope':'offline candidate snapshots, not deployed publisher or full B analysis', 'cases':results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--b-root', required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.b_root), indent=2))
