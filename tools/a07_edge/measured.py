"""Import explicitly partitioned, human-labeled physical sensor recordings."""
import hashlib
import json
from pathlib import Path

import numpy as np

from .observations import Calibration, LABELS, extract, read_jsonl


def import_measured(spec_path, output):
    """Spec supplies sessions [{path, profile, split, calibration, scenario}].

    Recording/calibration of one physical placement must stay in one split.
    Unlabeled samples are retained raw but excluded from the training table.
    """
    spec_path, output = Path(spec_path), Path(output)
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    if output.exists() and any(output.iterdir()):
        raise ValueError('output must be empty')
    groups, sessions, prepared = {}, set(), []
    for item in spec['sessions']:
        split, profile = item['split'], str(item['profile'])
        if split not in ('train', 'validation', 'test', 'stress'):
            raise ValueError('explicit split required')
        if profile in groups and groups[profile] != split:
            raise ValueError('placement/profile leaks across splits')
        groups[profile] = split
        source = spec_path.parent/item['path']
        rows = list(read_jsonl(source))
        ids = {r['session_id'] for r in rows}
        if len(ids) != 1 or any(r['input_kind'] != 'measured' for r in rows):
            raise ValueError('one measured session per file required')
        session = ids.pop()
        if session in sessions:
            raise ValueError('session duplicated across files')
        sessions.add(session)
        cal = Calibration(**item['calibration']); cal.validate()
        if any(r['calibration_version'] != cal.version for r in rows):
            raise ValueError('calibration version mismatch')
        adc = np.asarray([[np.nan if a is None else a for a in r['adc']] for r in rows])
        t = np.asarray([r['uptime_ms'] for r in rows]); boot = np.asarray([r['boot_id'] for r in rows])
        lid = np.asarray([-1 if r['lid'] is None else int(r['lid']) for r in rows])
        x, valid, ends, _ = extract(t, adc, lid, boot, cal, sequence=[r['sequence'] for r in rows])
        labelled = np.array([rows[i]['label'] is not None for i in ends], dtype=bool)
        y = np.asarray([LABELS.index(rows[i]['label']) for i in ends if rows[i]['label'] is not None], np.int8)
        prepared.append((item, session, x[labelled], y, valid[labelled], source))
    if not all(any(i[0]['split'] == split and len(i[3]) for i in prepared) for split in ('train', 'validation', 'test')):
        raise ValueError('need labelled train, validation and test sessions')
    output.mkdir(parents=True, exist_ok=True)
    meta = []
    for i, session, x, y, valid, source in prepared:
        meta.append({'session_id':session, 'profile':str(i['profile']), 'split':i['split'],
                     'scenario':i.get('scenario','human-labelled'), 'input_kind':'measured',
                     'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(), 'path':str(source.resolve())})
    np.savez_compressed(output/'features.npz', x=np.concatenate([r[2] for r in prepared]),
                        y=np.concatenate([r[3] for r in prepared]), valid=np.concatenate([r[4] for r in prepared]),
                        split=np.concatenate([np.repeat(r[0]['split'],len(r[3])) for r in prepared]),
                        profile=np.concatenate([np.repeat(str(r[0]['profile']),len(r[3])) for r in prepared]),
                        session=np.concatenate([np.repeat(r[1],len(r[3])) for r in prepared]))
    (output/'sessions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in meta),encoding='utf-8')
    (output/'manifest.json').write_text(json.dumps({'input_kind':'measured','sessions':len(meta),
                                                  'spec':spec},indent=2),encoding='utf-8')
    return {'sessions':len(meta),'labelled_windows':sum(len(r[3]) for r in prepared)}
