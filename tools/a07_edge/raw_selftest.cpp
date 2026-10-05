// Replay raw synthetic samples through the streaming port and compare to NumPy.
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include "preprocess.hpp"
#include "a07_model.h"
#include "raw_vectors.h"
LOG_MODULE_REGISTER(a07_raw, LOG_LEVEL_INF);
static a07::Preprocessor processor;
static void run_raw(void *, void *, void *) {
    unsigned failures=0, windows=0;
    uint64_t total=0, worst=0;
    LOG_INF("A07 RAW BEGIN version=%s state_bytes=%u model=%s", a07::PREPROCESS_VERSION,
        (unsigned)sizeof(processor), A07_MODEL_VERSION);
    for(const auto &c: cases) {
        unsigned w=0, feature_errors=0, valid_errors=0, led_errors=0, model_errors=0;
        if(!processor.reset(c.cal)) { ++failures; continue; }
        for(unsigned i=0;i<c.samples;i++) {
            const auto &r=c.raw[i];
            a07::Sample s{}; s.ms=r.ms; s.sequence=r.sequence; s.boot=r.boot; s.lid=r.lid;
            for(int j=0;j<5;j++) s.adc[j]=r.adc[j]==65535 ? NAN : r.adc[j];
            a07::Result out;
            uint32_t start=k_cycle_get_32();
            bool ready=processor.push(s,out);
            uint64_t elapsed=k_cyc_to_ns_floor64(k_cycle_get_32()-start);
            if(ready) {
                total+=elapsed; if(elapsed>worst) worst=elapsed;
                if(w>=c.windows) { ++failures; break; }
                const auto &e=c.expected[w++];
                for(int j=0;j<11;j++) if(!std::isfinite(out.x[j]) ||
                    std::fabs(out.x[j]-e.x[j])>0.00002f+0.000002f*std::fabs(e.x[j])) ++feature_errors;
                valid_errors+=out.valid!=e.valid;
                for(int j=0;j<4;j++) led_errors+=out.led[j]!=e.led[j];
                float score=0;
                int cls=a07_predict(out.x,out.valid,&score);
                model_errors+=cls!=e.cls || std::fabs(score-e.score)>0.000001f;
            }
            if(i%20==0) k_sleep(K_MSEC(1));
        }
        bool failed=w!=c.windows || feature_errors || valid_errors || led_errors || model_errors;
        failures+=failed; windows+=w;
        LOG_INF("A07 RAW CASE %s windows=%u/%u features=%u valid=%u led=%u model=%u", c.name,
            w,c.windows,feature_errors,valid_errors,led_errors,model_errors);
    }
    size_t unused=0;
    int rc=k_thread_stack_space_get(k_current_get(), &unused);
    LOG_INF("A07 RAW END cases=%u windows=%u failures=%u window_mean_ns=%llu window_max_ns=%llu stack_rc=%d unused=%u",
        (unsigned)ARRAY_SIZE(cases),windows,failures,(unsigned long long)(total/(windows?windows:1)),
        (unsigned long long)worst,rc,(unsigned)unused);
}
K_THREAD_DEFINE(a07_raw_thread, 4096, run_raw, nullptr, nullptr, nullptr, 10, 0, 30000);
