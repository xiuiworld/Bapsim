#pragma once
#include "pipeline.hpp"
namespace a08 {
inline unsigned selftest(Pipeline &p) {
    unsigned failures=0;
    const a07::Calibration cal={{500,500,500,500},{3000,3000,3000},4095,10000,10000,3950,0,true};
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
    return failures;
}
}
