// Replay raw synthetic samples through the streaming port and compare to NumPy.
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include "preprocess.hpp"
#include "a07_model.h"
#include "raw_vectors.h"
LOG_MODULE_REGISTER(a07_raw, LOG_LEVEL_INF);
static a07::Preprocessor processor;
#include "blink_selftest.hpp"
// Independent assertions supplement the PC oracle: neither side may silently
// produce temperatures after a rejected configuration or reuse stale LED state.
static unsigned safety_checks() {
    const a07::Calibration base{{500,500,500,500},{3000,3000,3000,3000},4095,10000,10000,3950,0,true};
    unsigned failures=0, rejected=0, accepted=0, recovery=0;
    const double bad[]={NAN,INFINITY,-INFINITY,0,-1};
    for(unsigned field=0;field<13;field++) for(double value:bad) {
        // A zero/negative offset and zero LED endpoints can be valid.
        auto c=base;
        double *p=field==0?&c.adc_max:field==1?&c.series:field==2?&c.r25:
            field==3?&c.beta:field==4?&c.offset:field<9?&c.off[field-5]:&c.on[field-9];
        *p=value;
        const bool expected=std::isfinite(value) &&
            (field==4 || (field>=5 && value==0));
        bool ok=processor.reset(c);
        failures+=ok!=expected;
        if(expected) ++accepted;
        else {
            ++rejected;
            for(unsigned i=0;i<1240;i++) {
                a07::Sample s{};s.ms=i*50;s.sequence=i;s.boot=1;s.lid=0;
                for(double &v:s.adc)v=2048;
                a07::Result out{};
                failures+=processor.push(s,out) || out.surface_good || out.valid;
            }
        }
    }
    for(unsigned initial=0;initial<2;initial++) for(unsigned fault=0;fault<7;fault++)
        for(unsigned target=0;target<2;target++) {
            processor.reset(base);
            unsigned unknown=0, restored=0;
            for(unsigned i=0;i<1720;i++) {
                a07::Sample s{};s.ms=i*50;s.sequence=i;s.boot=1;s.lid=0;
                s.adc[0]=i<1200?(initial?3000:500):i<1280?1750:(target?3000:500);
                s.adc[1]=3000;s.adc[2]=s.adc[3]=500;s.adc[4]=2048;
                if(fault==0 && i>=1200)s.ms+=200;
                if(fault==1 && i>=1200)s.sequence++;
                if(fault==2 && i>=1200)s.boot=2;
                if(fault==3 && i==1200)s.adc[0]=NAN;
                if(fault==4 && i==1200)s.adc[0]=4095;
                if(fault==5 && i<1280)s.adc[0]=1750; // unknown at startup
                // fault 6 is uninterrupted hysteresis, which must stay known.
                a07::Result out{};
                if(processor.push(s,out)) {
                    if(i>=1200 && i<1280) {
                        ++unknown;
                        failures+=out.led[0]!=(fault==6?(initial?2:1):0);
                        failures+=out.led_quality[0]!=(fault==6?1:0);
                    }
                    if(i>=1680) {
                        ++restored;
                        failures+=out.led[0]!=(target?2:1) || !out.led_quality[0];
                    }
                    // Samples 1200..1599 are 400 clean observations; the
                    // link preceding their first sample is outside the window.
                    const bool gap_window=fault<3 && i>=1200 && i<1599;
                    failures+=out.led[1]!=(gap_window?0:2) || out.led_quality[1]!=(gap_window?0:1);
                }
            }
            failures+=unknown!=2 || restored!=1;
            ++recovery;
        }
    LOG_INF("A07 SAFETY END rejected=%u accepted=%u recovery=%u failures=%u",
        rejected,accepted,recovery,failures);
    return failures;
}
static void run_raw(void *, void *, void *) {
    unsigned failures=0, windows=0;
    uint64_t total=0, worst=0;
    LOG_INF("A07 RAW BEGIN version=%s state_bytes=%u model=%s", a07::PREPROCESS_VERSION,
        (unsigned)sizeof(processor), A07_MODEL_VERSION);
    failures+=safety_checks();
    failures+=blink_checks();
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
