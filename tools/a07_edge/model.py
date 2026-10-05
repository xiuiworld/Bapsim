"""Small CART candidate + conservative rule baseline, with held-out evaluation."""
import json
import hashlib
from pathlib import Path

import numpy as np

from . import VERSION
from .observations import FEATURES, LABELS


def fit_tree(x, y, depth=6, min_leaf=80):
    nodes = []

    def build(indices, level):
        counts = np.bincount(y[indices], minlength=4)
        p = counts/counts.sum()
        node = {"feature": -1, "threshold": 0., "left": -1, "right": -1,
                "class": int(np.argmax(p)), "score": float(p.max()), "samples": len(indices)}
        idx = len(nodes); nodes.append(node)
        if level >= depth or len(indices) < 2*min_leaf or np.count_nonzero(counts) == 1:
            return idx
        best, chosen = float(len(indices)*(1-np.dot(p, p))), None
        for feature in range(x.shape[1]):
            values = x[indices, feature]
            for threshold in np.unique(np.quantile(values, np.linspace(.05, .95, 13)).astype(np.float32)):
                left = values <= threshold
                nl = int(left.sum()); nr = len(left)-nl
                if min(nl, nr) < min_leaf:
                    continue
                lc = np.bincount(y[indices[left]], minlength=4)
                rc = counts-lc
                impurity = nl-float(np.dot(lc, lc))/nl+nr-float(np.dot(rc, rc))/nr
                if impurity < best-1e-8:
                    best, chosen = impurity, (feature, float(threshold), left)
        if chosen is not None:
            f, threshold, mask = chosen
            node.update(feature=f, threshold=threshold, left=build(indices[mask], level+1),
                        right=build(indices[~mask], level+1))
        return idx
    build(np.arange(len(y)), 0)
    return nodes


def tree_predict(nodes, x, valid, threshold=.8):
    result = np.full(len(x), 3, dtype=np.int8)
    score = np.zeros(len(x), dtype=np.float32)
    for i in np.flatnonzero(valid & np.isfinite(x).all(axis=1)):
        n = nodes[0]
        while n["feature"] >= 0:
            n = nodes[n["left"] if x[i, n["feature"]] <= n["threshold"] else n["right"]]
        score[i] = n["score"]
        # Blink in warm channel is reheat/ambiguous; never fresh cooking.
        if score[i] >= threshold and x[i, 3] < 2:
            result[i] = n["class"]
    return result, score


def rule_predict(x, valid):
    result = np.full(len(x), 3, dtype=np.int8)
    for i in np.flatnonzero(valid):
        lock, le, warm, we, white, re, timer, te, temp, slope, lid = x[i]
        if we >= 2 or te >= 2:
            continue
        if warm > .9:
            result[i] = 2
        elif white > .9 and re == 0 and slope > .3 and lock > .9:
            result[i] = 1
        elif warm < .1 and white < .1 and temp < 33 and abs(slope) < .3:
            result[i] = 0
    return result


def metrics(y, pred, valid):
    matrix = np.zeros((4, 4), dtype=int)
    for a, b in zip(y, pred):
        matrix[a, b] += 1
    accepted = pred != 3
    known = y != 3
    return {"windows": len(y), "invalid_windows": int((~valid).sum()),
            "coverage": float(accepted.mean()) if len(y) else None,
            "accepted_accuracy": float((pred[accepted] == y[accepted]).mean()) if accepted.any() else None,
            "known_recall_including_abstentions": float((pred[known] == y[known]).mean()) if known.any() else None,
            "unknown_false_acceptance": float((pred[~known] != 3).mean()) if (~known).any() else None,
            "invalid_false_acceptance": int(((~valid) & accepted).sum()),
            "confusion_true_rows_pred_columns": matrix.tolist(), "label_order": list(LABELS)}


def export_c(model, path):
    """Float32 features in fixed order; raw feature extraction stays separate."""
    lines = ["/* Generated synthetic candidate. Not qualified for live use. */", "#include <math.h>",
             "typedef struct { short f, l, r, cls; float cut, score; } A07Node;",
             f'#define A07_MODEL_VERSION "{model["version"]}"',
             f"#define A07_FEATURE_COUNT {len(FEATURES)}", "static const A07Node a07_nodes[] = {"]
    literal = lambda x: format(float(np.float32(x)), '.9e')+'f'
    for n in model["nodes"]:
        lines.append('{%d,%d,%d,%d,%s,%s},' % (n['feature'], n['left'], n['right'], n['class'], literal(n['threshold']), literal(n['score'])))
    lines += ["};", "/* Returns 0 inactive, 1 cooking, 2 warming, 3 unknown. */",
              "static int a07_predict(const float x[A07_FEATURE_COUNT], int valid, float *score) {",
              "  *score=0; if(!valid) return 3;",
              "  for(int j=0;j<A07_FEATURE_COUNT;j++) if(!isfinite(x[j])) return 3;",
              "  int i=0; while(a07_nodes[i].f>=0) {",
              "    const A07Node *n=&a07_nodes[i]; i=x[n->f]<=n->cut?n->l:n->r; }",
              "  *score=a07_nodes[i].score;",
              f"  if(*score < {literal(model['score_threshold'])} || x[3]>=2) return 3;",
              "  return a07_nodes[i].cls;", "}"]
    Path(path).write_text('\n'.join(lines)+'\n', encoding='utf-8')


def train(root):
    root = Path(root)
    provenance = json.loads((root/'manifest.json').read_text(encoding='utf-8'))['input_kind']
    if provenance not in ('synthetic', 'measured'):
        raise ValueError('unsupported dataset provenance')
    data = np.load(root/"features.npz")
    x, y, valid, split = (data[k] for k in ("x", "y", "valid", "split"))
    if not np.isfinite(x).all():
        raise ValueError("nonfinite features")
    for field in ("profile", "session"):
        groups = [set(data[field][split == name].tolist()) for name in ("train", "validation", "test", "stress")]
        if any(groups[i] & groups[j] for i in range(4) for j in range(i)):
            raise ValueError(f"{field} leakage across splits")
    train_mask = (split == "train") & valid
    nodes = fit_tree(x[train_mask], y[train_mask])
    val = split == "validation"
    candidates = []
    for cutoff in (.65, .75, .8, .85, .9, .95, .99):
        p, _ = tree_predict(nodes, x[val], valid[val], cutoff)
        m = metrics(y[val], p, valid[val]); candidates.append({"threshold": cutoff, **m})
    eligible = [c for c in candidates if (c["accepted_accuracy"] or 0) >= .95 and (c["unknown_false_acceptance"] or 0) <= .05]
    chosen = max(eligible, key=lambda c:c['coverage']) if eligible else candidates[-1]
    fingerprint = hashlib.sha256(json.dumps({'nodes':nodes,'threshold':chosen['threshold'],
        'features':list(FEATURES),'data':hashlib.sha256((root/'features.npz').read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()
    model = {"version": 'a07-tree-v1-'+fingerprint[:12], "features": list(FEATURES), "labels": list(LABELS),
             "score_threshold": chosen['threshold'], "nodes": nodes, "input_kind": provenance,
             "live_qualified": False, "score_meaning": "training leaf frequency, NOT real-world confidence"}
    report = {"version": VERSION, "synthetic_only": provenance == 'synthetic', "test_used_for_tuning": False,
              "validation_target_met": bool(eligible), "validation_threshold_search": candidates,
              "nodes": len(nodes), "max_depth_limit": 6, "feature_count": len(FEATURES),
              "splits": {}, "scenarios": {}, "limits": ["Unverified cooker/manual match and sensor curves",
              "Correlated windows are not independent trials", "3-minute excerpts, not full cycle evaluation",
              "No measured test set; no claim of real accuracy", "C export consumes features, not ADC driver"]}
    metadata = {r['session_id']:r for r in map(json.loads,(root/'sessions.jsonl').read_text().splitlines())}
    scenario = np.asarray([metadata[s]['scenario'] for s in data['session']])
    for name in ("train", "validation", "test", "stress"):
        m = split == name
        p, _ = tree_predict(nodes, x[m], valid[m], model['score_threshold'])
        r = rule_predict(x[m], valid[m])
        report['splits'][name] = {"model": metrics(y[m], p, valid[m]), "rules": metrics(y[m], r, valid[m]),
                                  "sessions": len(set(data['session'][m].tolist()))}
        if name in ("test", "stress"):
            for s in sorted(set(scenario[m])):
                k = scenario[m] == s
                report['scenarios'][name+':'+s] = metrics(y[m][k], p[k], valid[m][k])
    (root/'model.json').write_text(json.dumps(model, indent=2), encoding='utf-8')
    (root/'evaluation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    export_c(model, root/'a07_model.h')
    # Vectors include rejected/faulted inputs; usable by a future board harness.
    selected = np.flatnonzero((split == 'test') | (split == 'stress'))[::53][:1000]
    expected, score = tree_predict(nodes, x[selected], valid[selected], model['score_threshold'])
    np.savez_compressed(root/'parity_vectors.npz', x=x[selected], valid=valid[selected], expected=expected)
    return report


def phase_fields(label, score, method="edge_model", version=VERSION):
    """Only A05 inference fields; raw observations and their quality stay separate."""
    known = label in (0, 1, 2)
    if method not in ('edge_model','rule') or not version or len(version.encode()) > 64 or not np.isfinite(score):
        raise ValueError('invalid inference metadata')
    return {"CookerPhase": (1, 2, 3)[label] if known else 0,
            "CookerPhaseQuality": 1 if known else 0,
            "InferenceMethod": 1 if method == 'edge_model' else 2,
            "ModelVersion": version, "InferenceScore": int(round(max(0, min(1, score))*10000)) if known else None,
            "input_kind": "test"}
