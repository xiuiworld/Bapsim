"""Attach a synthetic model parity harness to an A-owned disposable A05 app."""
import argparse
import json
from pathlib import Path
import numpy as np
from .model import export_c, tree_predict


def prepare(model_path, vectors_path, app):
    app = Path(app).resolve()
    if 'firmware' in app.parts or 'local-data' not in app.parts:
        raise ValueError('Use the disposable local-data A05 app, never firmware or SDK')
    model = json.loads(Path(model_path).read_text())
    data = np.load(vectors_path)
    x = np.asarray(data['x'], dtype=np.float32)
    valid = np.asarray(data['valid'], dtype=bool)
    expected, scores = tree_predict(model['nodes'], x, valid, model['score_threshold'])
    out = app / 'src' / 'a07'
    out.mkdir(exist_ok=True)
    export_c(model, out / 'a07_model.h')
    literal = lambda v: format(float(v), '.9e') + 'f'
    rows = ['{' + '{' + ','.join(map(literal, row)) + '},' +
            f'{int(v)},{int(e)},{literal(s)}' + '}'
            for row, v, e, s in zip(x, valid, expected, scores)]
    (out / 'vectors.h').write_text(
        'struct Vector { float x[11]; int valid, expected; float score; };\n'
        'static const Vector vectors[] = {\n' + ',\n'.join(rows) + '\n};\n')
    (out / 'selftest.cpp').write_text('''// Synthetic feature replay; never feeds live Matter attributes.
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include "a07_model.h"
#include "vectors.h"
LOG_MODULE_REGISTER(a07_model, LOG_LEVEL_INF);
static void run(void *, void *, void *) {
    unsigned mismatch=0, score_mismatch=0;
    uint64_t total=0, worst=0;
    LOG_INF("A07 BEGIN model=%s synthetic_features_only=1", A07_MODEL_VERSION);
    for (unsigned i=0; i<ARRAY_SIZE(vectors); ++i) {
        const auto &v=vectors[i]; float score;
        uint32_t start=k_cycle_get_32();
        int result=a07_predict(v.x, v.valid, &score);
        uint32_t elapsed=k_cycle_get_32()-start;
        uint64_t ns=k_cyc_to_ns_floor64(elapsed);
        total+=ns; if(ns>worst) worst=ns;
        if(result!=v.expected) ++mismatch;
        if(fabsf(score-v.score)>0.000001f) ++score_mismatch;
        if(i%32==0) k_sleep(K_MSEC(1));
    }
    size_t unused=0;
    int stack_rc=k_thread_stack_space_get(k_current_get(), &unused);
    LOG_INF("A07 END vectors=%u mismatch=%u score_mismatch=%u mean_ns=%llu max_ns=%llu stack_rc=%d stack_unused=%u",
        (unsigned)ARRAY_SIZE(vectors), mismatch, score_mismatch,
        (unsigned long long)(total/ARRAY_SIZE(vectors)), (unsigned long long)worst,
        stack_rc, (unsigned)unused);
}
// Dedicated low-priority thread, delayed until Matter startup has progressed.
K_THREAD_DEFINE(a07_test_thread, 3072, run, nullptr, nullptr, nullptr, 10, 0, 20000);
''')
    cmake = app / 'CMakeLists.txt'
    marker = 'target_sources(app PRIVATE src/a07/selftest.cpp)'
    text = cmake.read_text()
    if marker not in text:
        cmake.write_text(text + '\n' + marker + '\n')
    config = app / 'prj.conf'
    text = config.read_text()
    marker = '# A07 model parity instrumentation'
    if marker not in text:
        config.write_text(text + '\n' + marker + '\nCONFIG_THREAD_STACK_INFO=y\nCONFIG_INIT_STACKS=y\n')
    return {'model': model['version'], 'vectors': len(x), 'source': str(out),
            'scope': 'synthetic features only; no ADC preprocessing or live inference'}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--model', required=True)
    p.add_argument('--vectors', required=True)
    p.add_argument('--app', required=True)
    a = p.parse_args()
    print(json.dumps(prepare(a.model, a.vectors, a.app), indent=2))
