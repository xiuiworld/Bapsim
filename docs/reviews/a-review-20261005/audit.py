"""Read-only source/model audit; creates evidence only beside this script."""
import hashlib
import json
import sys
from pathlib import Path
import numpy as np

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'AGENTS.md').exists())
sys.path.insert(0, str(ROOT))
from tools.a07_edge.observations import Calibration, extract
from tools.a07_edge.model import tree_predict, metrics, rule_predict
from tools.a07_edge.verify_raw_log import verify

out = {}
local = ROOT / 'local-data/a07'
model = json.loads((local / 'refined-v1/model.json').read_text())
data = np.load(local / 'audit-9811/features.npz')
prior = json.loads((local / 'refined-v1/fresh-audit.json').read_text())
out['reevaluation'] = {}
for name, path in [('baseline', local/'run-7307/model.json'), ('refined', local/'refined-v1/model.json')]:
    m = json.loads(path.read_text())
    pred, _ = tree_predict(m['nodes'], data['x'], data['valid'], m['score_threshold'])
    result = {}
    for condition, mask in [('normal', data['split'] != 'stress'), ('stress', data['split'] == 'stress')]:
        metric = metrics(data['y'][mask], pred[mask], data['valid'][mask])
        result[condition] = metric
        result[condition]['matches_saved_metrics'] = all(prior['results'][name][condition][k] == v for k, v in metric.items())
    out['reevaluation'][name] = result
rule = rule_predict(data['x'], data['valid'])
out['rules'] = {c: metrics(data['y'][mask], rule[mask], data['valid'][mask]) for c, mask in [('normal', data['split']!='stress'), ('stress', data['split']=='stress')]}
out['archived_board_log_recheck'] = verify(local/'raw-board/boot.log', local/'raw-board/manifest.json')
out['app_sha256'] = hashlib.sha256((local/'pr12-app.signed.hex').read_bytes()).hexdigest()

# Independent constructed input: slow warming-lamp blink, constant warm surface.
# A hypothetical reheat-like signal, not a measured cooker episode.
n = 3600
t = np.arange(n)*50
adc = np.tile([3000., 3000., 500., 500., 2048.], (n, 1))
from tools.a07_edge.observations import temperature_adc
adc[:, 4] = temperature_adc(55, Calibration())
adc[:, 1] = np.where((t/1000*.15) % 1 < .5, 3000., 500.)
x, valid, _, led = extract(t, adc, np.zeros(n), np.ones(n), Calibration())
pred, _ = tree_predict(model['nodes'], x, valid, model['score_threshold'])
out['independent_slow_blink'] = {'hz': .15, 'windows':len(pred), 'valid':int(valid.sum()), 'predictions':{s:int((pred==i).sum()) for i,s in enumerate(['inactive','cooking','warming','unknown'])}, 'note':'Constructed reheat-like waveform; not measured ground truth.'}

# Continuity break followed by 60 s at the hysteresis midpoint.
def after_gap(initial):
    a = np.tile([1750.,3000.,500.,500.,2048.], (2400,1))
    a[:1200,0] = initial
    clock = np.arange(2400)*50
    clock[1200:] += 5000
    z,v,_,en = extract(clock,a,np.zeros(2400),np.ones(2400),Calibration())
    return {'last_valid':bool(v[-1]),'last_lock_duty':float(z[-1,0]),'last_lock_enum':int(en[-1,0])}
out['hysteresis_after_gap'] = {'previous_on':after_gap(3000), 'previous_off':after_gap(500)}

# Python accepts infinite beta whereas the C++ reset explicitly rejects it.
cal = Calibration(ntc_beta=float('inf'))
try:
    cal.validate()
    z,v,_,_ = extract(t,np.tile([500.,3000.,500.,500.,2048.],(n,1)),np.zeros(n),np.ones(n),cal)
    out['infinite_beta'] = {'accepted':True,'valid_windows':int(v.sum()),'temperature_c':float(z[0,8])}
except ValueError as e:
    out['infinite_beta'] = {'accepted':False,'error':str(e)}

# Snapshot polling can observe closed both before and after a complete opening.
out['snapshot_sampling'] = {'board_edges_seconds':[[1,'open'],[3,'closed']], 'poll_seconds':[0,5], 'observed':['closed','closed'], 'recoverable_openings_from_values':0}
dest = Path(__file__).with_name('results.json')
dest.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(out,ensure_ascii=False,indent=2))
