"""Validation-only size/coverage search, followed by a separately seeded audit.

Never tune with original test/stress or the new audit. Existing models stay intact.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .model import export_c, fit_tree, metrics, tree_predict


def select(root, output):
    root, output = Path(root), Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('use a fresh selection directory')
    original = json.loads((root/'model.json').read_text())
    data = np.load(root/'features.npz')
    split = data['split']; train = (split == 'train') & data['valid']; val = split == 'validation'
    for key in ('session','profile'):
        if set(data[key][train]) & set(data[key][val]):
            raise ValueError('train/validation group leakage')
    candidates, best = [], None
    for depth in (6, 8, 10, 12):
        for leaf in (40, 80):
            nodes = fit_tree(data['x'][train], data['y'][train], depth=depth, min_leaf=leaf)
            for threshold in (.9, .95, .975, .99, .995, .999):
                pred, _ = tree_predict(nodes, data['x'][val], data['valid'][val], threshold)
                score = metrics(data['y'][val], pred, data['valid'][val])
                item = dict(depth=depth, min_leaf=leaf, nodes=len(nodes), threshold=threshold, **score)
                # This is an experimental selection target, not deployment approval.
                item['eligible'] = (score['accepted_accuracy'] or 0) >= .995 and (score['unknown_false_acceptance'] or 0) <= .005
                candidates.append(item)
                rank = (score['coverage'], -len(nodes), score['accepted_accuracy'] or 0)
                if item['eligible'] and (best is None or rank > best[0]):
                    best = (rank, nodes, item)
            print(f"validation-only: depth={depth}, leaf={leaf}, nodes={len(nodes)}", flush=True)
    if best is None:
        raise ValueError('no candidate met stricter validation target; baseline preserved')
    _, nodes, chosen = best
    model = dict(original, nodes=nodes, score_threshold=chosen['threshold'])
    fingerprint = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()[:12]
    model['version'] = 'a07-refined-v1-'+fingerprint
    output.mkdir(parents=True, exist_ok=True)
    (output/'model.json').write_text(json.dumps(model, indent=2))
    export_c(model, output/'a07_model.h')
    report = {'selection_data':'original train/validation only', 'original_test_used':False,
              'audit_used_for_selection':False, 'target':{'accepted_accuracy':.995,'unknown_false_acceptance_max':.005},
              'chosen':chosen,'candidates':candidates,
              'training_manifest_sha256':hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()}
    (output/'selection.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'version':model['version'],'chosen':chosen},indent=2))


def audit(original_root, refined_root, audit_root):
    original_root, refined_root, audit_root = map(Path,(original_root,refined_root,audit_root))
    source = json.loads((original_root/'manifest.json').read_text())
    fresh = json.loads((audit_root/'manifest.json').read_text())
    if source['seed'] == fresh['seed']:
        raise ValueError('audit needs a new seed after candidate freeze')
    old_sessions = [json.loads(s) for s in (original_root/'sessions.jsonl').read_text().splitlines()]
    new_sessions = [json.loads(s) for s in (audit_root/'sessions.jsonl').read_text().splitlines()]
    if {s['seed'] for s in old_sessions} & {s['seed'] for s in new_sessions}:
        raise ValueError('audit episode seeds overlap')
    f = np.load(audit_root/'features.npz')
    result = {'synthetic_only':True,'fresh_seed':fresh['seed'],'frozen_before_audit':True,
              'group_identity':'(dataset seed, profile/session); local IDs repeat between datasets',
              'all_nonstress_partitions_used_as_audit':True,'results':{}}
    metadata = {r['session_id']:r for r in new_sessions}
    scenarios = np.asarray([metadata[s]['scenario'] for s in f['session']])
    for name, path in [('baseline',original_root/'model.json'),('refined',refined_root/'model.json')]:
        model = json.loads(path.read_text())
        pred, _ = tree_predict(model['nodes'], f['x'], f['valid'],model['score_threshold'])
        result['results'][name] = {'model_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'version':model['version']}
        for condition, mask in [('normal',f['split']!='stress'),('stress',f['split']=='stress')]:
            m = metrics(f['y'][mask],pred[mask],f['valid'][mask])
            m['sessions'] = len(set(f['session'][mask].tolist()))
            m['per_scenario'] = {s:metrics(f['y'][mask & (scenarios==s)],pred[mask & (scenarios==s)],f['valid'][mask & (scenarios==s)]) for s in sorted(set(scenarios[mask]))}
            result['results'][name][condition] = m
    (refined_root/'fresh-audit.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({name:{c:{k:v for k,v in result['results'][name][c].items() if k not in ('per_scenario','confusion_true_rows_pred_columns','label_order')} for c in ('normal','stress')} for name in result['results']},indent=2))


def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('select'); s.add_argument('original'); s.add_argument('output')
    a=sub.add_parser('audit'); a.add_argument('original'); a.add_argument('refined'); a.add_argument('fresh')
    args=p.parse_args()
    if args.command=='select': select(args.original,args.output)
    else: audit(args.original,args.refined,args.fresh)


if __name__=='__main__': main()
