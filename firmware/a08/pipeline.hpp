#pragma once
#include "a07/preprocess.hpp"
#include "a07/a07_snapshot.hpp"
#include "a07/a07_model.h"
#include "a05_history.hpp"
#include "temperature.hpp"
namespace a08 {
// Single producer. Caller serializes access with the Matter consumer.
class Pipeline {
    a07::Preprocessor processor;
    a07::Result latest{};
    int8_t stable=-1, candidate=-1;
    uint64_t since=0, last_ms=0, last_seq=0, last_report=0;
    uint32_t boot=0, counter=0;
    bool seen=false, calibrated=false;
    double adc_max=4095;
    a07::Calibration temperature_cal{};
public:
    a05::History history;
    uint8_t frame[110]{};
    size_t length=0;
    uint32_t reports() const { return counter; }
    bool reset(const a07::Calibration &cal) {
        calibrated=processor.reset(cal); temperature_cal=cal; adc_max=cal.adc_max; latest={}; latest.lid=-1;
        stable=candidate=-1; seen=false; counter=0; length=0;
        history=a05::History{}; return calibrated;
    }
    bool push(const a07::Sample &s) {
        const bool gap=seen && (s.boot!=boot || s.ms<=last_ms || s.ms-last_ms<40 ||
            s.ms-last_ms>60 || last_seq==UINT64_MAX || s.sequence!=last_seq+1);
        const int8_t old=stable;
        bool had_observation=latest.valid || latest.surface_good || latest.lid_good;
        for(int j=0;j<4;j++) had_observation=had_observation || latest.led_quality[j]==1;
        if(gap) { stable=candidate=-1; latest={}; latest.lid=-1; }
        if(s.lid!=0 && s.lid!=1) stable=candidate=-1;
        else {
            if(candidate!=s.lid) { candidate=s.lid; since=s.ms; }
            if(s.ms>=since && s.ms-since>=50) stable=candidate;
        }
        a07::Result result{};
        if(calibrated && processor.push(s,result)) latest=result;
        const double temp=calibrated ? temperature_c(s.adc[4],temperature_cal) : static_cast<double>(NAN);
        latest.surface_good=std::isfinite(temp);
        latest.surface_c=latest.surface_good ? static_cast<float>(temp) : NAN;
        // Immediate faults invalidate cached inference before the next 2 s result.
        bool faulty=false;
        for(int j=0;j<5;j++) {
            const bool bad=!std::isfinite(s.adc[j]) || s.adc[j]<=1 || s.adc[j]>=adc_max-1;
            faulty=faulty || bad;
            if(bad && j<4) { latest.led[j]=0; latest.led_quality[j]=0; }
            if(bad && j==4) { latest.surface_good=false; latest.surface_c=NAN; }
        }
        if(faulty || stable<0 || gap) { latest.valid=false; }
        latest.lid=stable; latest.lid_good=stable>=0;
        const bool emit=!seen || old!=stable || (gap && had_observation) || s.ms-last_report>=2000;
        seen=true; boot=s.boot; last_ms=s.ms; last_seq=s.sequence;
        if(!emit) return false;
        float score=0;
        const int phase=a07_predict(latest.x,latest.valid,&score);
        a07::SnapshotMeta meta{s.boot,counter+1,counter+1,s.ms,phase,score,A07_MODEL_VERSION};
        // Physical commissioning remains test-origin until live producer approval.
        if(!a07::encode_test_snapshot(latest,meta,frame,sizeof(frame),length)) return false;
        ++counter; last_report=s.ms;
        return history.append(frame,length);
    }
};
}
