#pragma once
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/dt-bindings/adc/nrf-saadc.h>
#include "config.hpp"
namespace a08 {
// nRF54LM20 internal reference is 900 mV: gain 1/4 gives a 3.6 V
// conversion range. The analog pin must still remain within its supply rails.
// Keep acquisition and raw-to-voltage conversion on the same gain.
constexpr adc_gain SENSOR_ADC_GAIN = ADC_GAIN_1_4;
class Sensors {
    const device *adc=DEVICE_DT_GET(DT_NODELABEL(adc));
    const device *gpio=DEVICE_DT_GET(DT_NODELABEL(gpio1));
    bool ready[5]{}, contact_ready=false;
public:
    int16_t raw_codes[5]{};
    int read_status[5]{};
    int32_t millivolts[5]{}; // -1 when the sample cannot be converted
    int init() {
        int failures=0;
        const uint8_t inputs[]={NRF_SAADC_AIN7,NRF_SAADC_AIN6,
            NRF_SAADC_AIN5,NRF_SAADC_AIN4,NRF_SAADC_AIN3};
        for(int j=0;j<5;j++) {
            adc_channel_cfg cfg{};
            cfg.gain=SENSOR_ADC_GAIN; cfg.reference=ADC_REF_INTERNAL;
            cfg.acquisition_time=ADC_ACQ_TIME(ADC_ACQ_TIME_MICROSECONDS,40);
            cfg.channel_id=j; cfg.input_positive=inputs[j];
            ready[j]=device_is_ready(adc) && adc_channel_setup(adc,&cfg)==0;
            failures+=!ready[j];
        }
        // External pull-up is specified in A08 wiring; no output drive.
        contact_ready=device_is_ready(gpio) && gpio_pin_configure(gpio,10,GPIO_INPUT)==0;
        return failures+!contact_ready;
    }
    void read(a07::Sample &s) {
        int16_t raw[5]{};
        uint32_t mask=0;
        for(int j=0;j<5;j++) if(ready[j]) mask|=1u<<j;
        adc_sequence seq{}; seq.channels=mask; seq.buffer=raw;
        seq.buffer_size=sizeof(raw); seq.resolution=12;
        const int rc=mask ? adc_read(adc,&seq) : -ENODEV;
        unsigned index=0;
        for(int j=0;j<5;j++) {
            s.adc[j]=NAN;
            millivolts[j]=-1;
            raw_codes[j]=INT16_MIN; read_status[j]=-ENODEV;
            if(!ready[j]) continue;
            const int16_t code=raw[index++];
            read_status[j]=rc; raw_codes[j]=code;
            // Leave a small rail margin: this board clipped at 4092, not 4095.
            if(rc!=0 || code<=4 || code>=4091) continue;
            int32_t mv=code;
            if(adc_raw_to_millivolts(adc_ref_internal(adc),SENSOR_ADC_GAIN,12,&mv)!=0) continue;
            millivolts[j]=mv;
            if(SENSORS_CONFIRMED && std::isfinite(SUPPLY_MV) && SUPPLY_MV>0 && mv>0 && mv<SUPPLY_MV)
                s.adc[j]=mv/SUPPLY_MV*4095.0;
        }
        const int v=contact_ready ? gpio_pin_get_raw(gpio,10) : -1;
        s.lid=SENSORS_CONFIRMED && v>=0 ? static_cast<int8_t>((v!=0)==HIGH_IS_OPEN) : -1;
    }
};
}
