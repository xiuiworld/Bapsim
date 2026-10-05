// A07 raw.v1 reference preprocessing, 20 Hz input / 2 s output.
// Portable C++17, no heap or sensor/transport dependencies. Not live-qualified.
#pragma once
#include <cmath>
#include <cstdint>
#include <cstring>

namespace a07 {
constexpr const char *PREPROCESS_VERSION = "a07-preprocess-v1";
struct Calibration {
    double off[4], on[4];
    double adc_max, series, r25, beta, offset;
    bool ntc_to_ground;
};
struct Sample {
    uint64_t ms, sequence;
    uint32_t boot;
    double adc[5]; // Missing ADC is NaN; physical driver remains separate.
    int8_t lid; // -1 missing, 0 closed, 1 open
};
struct Result {
    float x[11];
    uint8_t led[4]; // unknown=0, off=1, on=2, blinking=3
    uint8_t led_quality[4]; // channel observations remain independent of model validity
    float surface_c; // latest 1 Hz candidate, not the model's 60 s slope
    int8_t lid; // debounced; -1 is unknown
    bool surface_good, lid_good;
    bool valid;
};
class Preprocessor {
    struct Slot {
        uint64_t ms;
        double temp;
        uint8_t on, good;
        int8_t contact;
        bool link;
    };
    Slot ring[1200];
    Calibration cal{};
    uint64_t count=0, previous_ms=0, previous_seq=0, since=0;
    uint32_t previous_boot=0;
    uint8_t optical=0;
    int8_t stable=-1, candidate=-1;
    bool calibrated=false;

    double temperature(double adc) const {
        if(!std::isfinite(adc) || adc<=1 || adc>=cal.adc_max-1) return NAN;
        double r=cal.series*adc/(cal.adc_max-adc);
        if(!cal.ntc_to_ground) r=cal.series*cal.series/r;
        double inv=1.0/298.15+std::log(r/cal.r25)/cal.beta;
        if(inv<=0) return NAN;
        double t=1.0/inv-273.15+cal.offset;
        return t>=-20 && t<=130 ? t : NAN;
    }
    const Slot &at(uint64_t i) const { return ring[i%1200]; }
public:
    bool reset(const Calibration &c) {
        cal=c; count=0; optical=0; stable=candidate=-1;
        previous_ms=previous_seq=since=0; previous_boot=0;
        calibrated=std::isfinite(c.adc_max) && c.adc_max>2 &&
            std::isfinite(c.series) && c.series>0 && std::isfinite(c.r25) && c.r25>0 &&
            std::isfinite(c.beta) && c.beta>0 && std::isfinite(c.offset);
        for(int j=0;j<4;j++) calibrated=calibrated && std::isfinite(c.off[j]) &&
            std::isfinite(c.on[j]) && std::fabs(c.on[j]-c.off[j])>=100;
        return calibrated;
    }
    // Returns true only at a complete scheduled window. Invalid windows still
    // return observations with valid=false; never impute missing samples.
    bool push(const Sample &s, Result &out) {
        out={};
        if(!calibrated) return false;
        Slot &v=ring[count%1200];
        v.ms=s.ms;
        v.link=count>0 && s.ms>previous_ms && s.ms-previous_ms>=40 &&
            s.ms-previous_ms<=60 && s.boot==previous_boot &&
            previous_seq!=UINT64_MAX && s.sequence==previous_seq+1;
        v.good=0;
        for(int j=0;j<4;j++) {
            double q=(s.adc[j]-cal.off[j])/(cal.on[j]-cal.off[j]);
            if(q>0.6) optical|=1u<<j;
            else if(q<0.4) optical&=~(1u<<j);
            if(std::isfinite(s.adc[j]) && s.adc[j]>1 && s.adc[j]<cal.adc_max-1)
                v.good|=1u<<j;
        }
        v.on=optical;
        if(count && (s.ms<=previous_ms || s.ms-previous_ms>100)) stable=candidate=-1;
        if(s.lid!=0 && s.lid!=1) stable=candidate=-1;
        else {
            if(s.lid!=candidate) { candidate=s.lid; since=s.ms; }
            if(s.ms-since>=50) stable=candidate;
        }
        v.contact=stable;
        v.temp=count%20==0 ? temperature(s.adc[4]) : 0;
        previous_ms=s.ms; previous_seq=s.sequence; previous_boot=s.boot;
        ++count;
        if(count<1200 || (count-1200)%40) return false;
        const uint64_t first=count-1200, a=count-40;
        unsigned on_count[4]={}, edges[4]={}, lid_count=0;
        uint8_t good=15; bool contact_ok=true, continuous=true;
        for(uint64_t i=first+1;i<count;i++) continuous=continuous && at(i).link;
        for(uint64_t i=a;i<count;i++) {
            const Slot &q=at(i); good&=q.good;
            contact_ok=contact_ok && q.contact>=0;
            lid_count+=q.contact>0;
            for(int j=0;j<4;j++) {
                on_count[j]+=(q.on>>j)&1;
                if(i>a) edges[j]+=((q.on^at(i-1).on)>>j)&1;
            }
        }
        for(int j=0;j<4;j++) {
            double duty=on_count[j]/40.0;
            out.x[2*j]=static_cast<float>(duty); out.x[2*j+1]=edges[j];
            out.led[j]=!(good&(1u<<j)) ? 0 : edges[j]>=2 ? 3 : duty>0.9 ? 2 : duty<0.1 ? 1 : 0;
            out.led_quality[j]=(good&(1u<<j)) ? 1 : 0;
        }
        double xs[60], ts[60], xm=0, tm=0;
        bool temp_ok=true;
        for(int i=0;i<60;i++) {
            const Slot &q=at(first+20*i);
            // Signed difference preserves backwards uptime in an invalid window.
            xs[i]=(static_cast<double>(q.ms)-static_cast<double>(at(first).ms))/60000.0;
            ts[i]=q.temp; temp_ok=temp_ok && std::isfinite(ts[i]);
            xm+=xs[i]; tm+=ts[i];
        }
        if(temp_ok) {
            xm/=60; tm/=60; double num=0, den=0;
            for(int i=0;i<60;i++) { num+=(xs[i]-xm)*(ts[i]-tm); den+=(xs[i]-xm)*(xs[i]-xm); }
            out.x[8]=static_cast<float>(ts[59]);
            out.x[9]=den>0 ? static_cast<float>(num/den) : NAN;
        }
        out.x[10]=lid_count/40.0f;
        out.surface_good=std::isfinite(ts[59]);
        out.surface_c=out.surface_good ? static_cast<float>(ts[59]) : NAN;
        out.lid=at(count-1).contact;
        out.lid_good=out.lid>=0;
        out.valid=good==15 && temp_ok && continuous && contact_ok;
        return true;
    }
};
} // namespace a07
