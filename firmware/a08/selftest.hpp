#pragma once
#include "pipeline.hpp"
namespace a08 {
inline unsigned selftest(Pipeline &p) {
    unsigned failures=0;
    // Existing Edge AI metadata must survive live transport, without a rule substitution.
    a07::Result projected{}; projected.valid=projected.lid_good=projected.surface_good=true;
    projected.surface_c=35; projected.lid=0;
    a07::SnapshotMeta em{7,1,1,60000,2,.99f,"edge-candidate-test"};
    uint8_t ef[110]{}; size_t en=0;
    failures+=!a07::encode_live_edge_snapshot(projected,em,ef,sizeof(ef),en) ||
        ef[6]!=0 || ef[34]!=3 || ef[41]!=1 || ef[42]!=1 || ef[45]==0;
    em.phase=3;
    failures+=!a07::encode_live_edge_snapshot(projected,em,ef,sizeof(ef),en) ||
        ef[34]!=0 || ef[41]!=0 || ef[43]!=255 || ef[44]!=255;
    const a07::Calibration cal={{500,500,500,500},{3000,3000,3000},4095,10000,10000,3950,0,true};
    // Serialization boundary test only; these frames are never published.
    p.reset(cal,7,true);
    a07::Sample live_sample{}; live_sample.boot=99; live_sample.lid=0;
    for(int j=0;j<5;j++) live_sample.adc[j]=2048;
    for(unsigned i=0;i<5;i++) {
        live_sample.ms=i*50; live_sample.sequence=i; live_sample.lid=i<3?0:1;
        const bool emitted=p.push(live_sample);
        if(emitted) failures+=p.frame[6]!=0 || p.frame[34]!=0 || p.frame[41]!=0 ||
            p.frame[42]!=0 || p.frame[43]!=255 || p.frame[44]!=255 || p.frame[45]!=0;
        if(i==1) failures+=!emitted || p.frame[27]!=0 || p.frame[35]!=1;
        if(i==4) failures+=!emitted || p.frame[27]!=1 || p.frame[35]!=1;
    }
    live_sample.ms+=500; live_sample.sequence++;
    failures+=!p.push(live_sample) || p.frame[27]!=255 || p.frame[35]!=0;
    p.reset(cal); live_sample.ms=0; live_sample.sequence=0;
    failures+=!p.push(live_sample) || p.frame[6]!=1;
    // Temperature must publish without light/contact or a 60-second AI window.
    failures+=std::fabs(temperature_c(2047.5,cal)-25.0)>0.001;
    failures+=std::isfinite(temperature_c(0,cal)) || std::isfinite(temperature_c(4095,cal));
    failures+=std::isfinite(temperature_c(NAN,cal));
    failures+=temperature_c(1500,cal)<=temperature_c(2047.5,cal);
    p.reset(cal);
    a07::Sample only_temp{}; only_temp.boot=1; only_temp.lid=-1;
    for(int j=0;j<4;j++) only_temp.adc[j]=NAN;
    only_temp.adc[4]=2047.5;
    failures+=!p.push(only_temp) || p.frame[28]!=0xc4 || p.frame[29]!=0x09 || p.frame[36]!=1;
    failures+=p.frame[27]!=255 || p.frame[34]!=0;
    for(int j=30;j<34;j++) failures+=p.frame[j]!=0;
    only_temp.ms=2000; only_temp.sequence=40; only_temp.adc[4]=NAN;
    failures+=!p.push(only_temp) || p.frame[28]!=0 || p.frame[29]!=128 || p.frame[36]!=2;
    // A confirmed contact must publish with uncalibrated light channels.
    p.reset(cal); only_temp.adc[4]=2047.5;
    for(unsigned i=0;i<5;i++) {
        only_temp.ms=i*50; only_temp.sequence=i; only_temp.lid=i<3 ? 0 : 1;
        const bool emitted=p.push(only_temp);
        if(i==1) failures+=!emitted || p.frame[27]!=0;
        if(i==4) failures+=!emitted || p.frame[27]!=1;
    }
    only_temp.ms=250; only_temp.sequence=5; only_temp.lid=-1;
    failures+=!p.push(only_temp) || p.frame[27]!=255;
    p.reset(cal);
    a07::Sample s{}; s.boot=42; s.lid=0;
    for(int j=0;j<4;j++) s.adc[j]=500;
    s.adc[4]=2048;
    for(unsigned i=0;i<1300;i++) {
        s.ms=i*50; s.sequence=i;
        // A short open/close must be captured before the first 60 s result.
        s.lid=i>=100 && i<120 ? 1 : 0;
        const bool emitted=p.push(s);
        if(i==101) failures+=!emitted || p.frame[27]!=1;
        if(i==121) failures+=!emitted || p.frame[27]!=0;
        if(i==121) {
            uint8_t h[a05::History::MAX_BYTES]; p.history.encode(h,sizeof(h));
            unsigned pos=4, opens=0, closes=0;
            for(unsigned k=0;k<h[3];k++) {
                const unsigned n=h[pos++]; opens+=h[pos+27]==1; closes+=h[pos+27]==0; pos+=n;
            }
            failures+=opens==0 || closes<2;
        }
    }
    uint8_t journal[a05::History::MAX_BYTES];
    failures+=p.history.encode(journal,sizeof(journal))==0 || journal[3]!=8;
    // Gap + ambiguous light cannot reuse the old inference/LED state.
    s.ms+=500; ++s.sequence; s.adc[0]=1750;
    failures+=!p.push(s) || p.frame[30]!=0 || p.frame[34]!=0;
    // Reboot/sequence discontinuity must not retain a debounced closed contact.
    ++s.boot; s.ms+=50; ++s.sequence;
    p.push(s); failures+=p.frame[27]!=255;
    auto bad=cal; bad.beta=NAN;
    failures+=p.reset(bad);
    for(unsigned i=0;i<1240;i++) { s.ms=i*50; s.sequence=i; p.push(s); }
    failures+=p.frame[34]!=0 || p.frame[28]!=0 || p.frame[29]!=128;
    // Slow warming LED blink must remain unknown even on steady-looking 2 s slices.
    p.reset(cal); s.lid=0;
    for(unsigned i=0;i<3600;i++) {
        s.ms=i*50; s.sequence=i; s.adc[0]=500;
        s.adc[1]=i%134<67 ? 3000 : 500;
        if(p.push(s) && i>=1200) failures+=p.frame[31]!=3 || p.frame[34]!=0;
    }
    // An excluded timer remains UNKNOWN, never a fabricated OFF value. The
    // model may accept only a result independent of both timer features.
    p.reset(cal,7); s.boot=84; s.lid=0;
    for(unsigned i=0;i<1300;i++) {
        s.ms=i*50; s.sequence=i;
        s.adc[0]=s.adc[2]=500; s.adc[1]=3000; s.adc[4]=2048;
        s.adc[3]=i%3==0 ? NAN : i%3==1 ? 0 : 4095;
        if(p.push(s) && i>=1240) failures+=p.frame[33]!=0 || p.frame[40]!=0 || p.frame[34]!=3;
    }
    // Excluding one LED must not relax the other channels or time continuity.
    s.ms+=500; ++s.sequence;
    failures+=!p.push(s) || p.frame[34]!=0;
    // White light leakage must not create warm blinking; real warm blinking
    // must still abstain. Use synthetic ADC, never emit these samples to Matter.
    auto leak=cal;
    leak.off[1]=820; leak.on[1]=1320;
    leak.warm_white_gain=.215; leak.white_reference=500;
    for(unsigned mode=0;mode<2;mode++) {
        p.reset(leak,7); s.boot=100+mode;
        for(unsigned i=0;i<2400;i++) {
            s.ms=i*50; s.sequence=i; s.lid=0;
            s.adc[0]=500; s.adc[2]=i%20<10 ? 2600 : 500;
            s.adc[1]=(mode ? (i%100<50 ? 1300 : 900) : 1000)+
                leak.warm_white_gain*(s.adc[2]-500);
            s.adc[3]=NAN; s.adc[4]=2048;
            if(p.push(s) && i>=1240) {
                failures+=p.frame[31]!=(mode ? 3 : 1);
                if(mode) failures+=p.frame[34]!=0;
            }
        }
    }
    // A failed reference clears the cached warm LED immediately as well.
    s.adc[2]=NAN; s.ms+=50; ++s.sequence;
    p.push(s); // The first confirmed contact offsets the 2 s reporting schedule by 50 ms.
    s.ms+=50; ++s.sequence;
    failures+=!p.push(s) || p.frame[31]!=0 || p.frame[34]!=0;
    return failures;
}
}
