// Procedural fixtures avoid filling the board flash with repeated raw samples.
// Expected states are specified independently of the PC oracle.
#pragma once
static unsigned blink_checks() {
    const a07::Calibration cal{{500,500,500,500},{3000,3000,3000,3000},4095,10000,10000,3950,0,true};
    const unsigned periods[]={200,134,80,40,20,10}; // 0.1..2 Hz, 50 ms ticks
    const unsigned duties[]={10,50,90}, phases[]={0,23,71};
    unsigned failures=0, cases=0, windows=0;
    for(unsigned channel=0;channel<4;channel++) for(unsigned period:periods)
        for(unsigned duty:duties) for(unsigned phase:phases) {
            processor.reset(cal);
            for(unsigned i=0;i<2400;i++) {
                a07::Sample s{};s.ms=i*50;s.sequence=i;s.boot=1;s.lid=0;
                s.adc[0]=500;s.adc[1]=3000;s.adc[2]=s.adc[3]=500;s.adc[4]=2048;
                const bool on=((i+period*phase/100)%period)*100<period*duty;
                s.adc[channel]=on?3000:500;
                a07::Result out{};
                if(processor.push(s,out)) {
                    ++windows;
                    failures+=out.led[channel]!=3 || !out.led_quality[channel];
                    if(channel==1) {
                        float score=0;
                        failures+=out.valid || a07_predict(out.x,out.valid,&score)!=3;
                    }
                }
                if(i%100==0)k_sleep(K_MSEC(1));
            }
            ++cases;
        }
    // A single change/pulse is not repeated blinking; after a full clean
    // history it must settle. Also check blinking->steady and a missing sample.
    for(unsigned mode=0;mode<4;mode++) {
        processor.reset(cal);
        for(unsigned i=0;i<2800;i++) {
            a07::Sample s{};s.ms=i*50;s.sequence=i;s.boot=1;s.lid=0;
            s.adc[0]=s.adc[2]=s.adc[3]=500;s.adc[4]=2048;
            bool on=mode==2?(i<1800?i%134<67:true):(i<1600 || (mode!=0 && i>=1620));
            s.adc[1]=mode==3&&i==1600?NAN:on?3000:500;
            a07::Result out{};
            if(processor.push(s,out)) {
                ++windows;
                if(mode!=2) {
                    failures+=out.led[1]==3;
                    if(i>=1600 && i<1999)failures+=out.led[1]!=0 || out.valid;
                } else if(i<1800)failures+=out.led[1]!=3 || out.valid;
                if(i>=2200)failures+=out.led[1]!=(mode==0?1:2) || !out.valid;
            }
            if(i%100==0)k_sleep(K_MSEC(1));
        }
        ++cases;
    }
    LOG_INF("A07 BLINK END cases=%u windows=%u failures=%u",cases,windows,failures);
    return failures;
}
