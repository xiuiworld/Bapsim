#pragma once
#include "a07/preprocess.hpp"
namespace a08 {
// Nominal beta equation, independent of light/contact validity or AI history.
inline double temperature_c(double adc, const a07::Calibration &cal) {
    if(!std::isfinite(adc) || adc<=1 || adc>=cal.adc_max-1 ||
       !std::isfinite(cal.adc_max) || cal.adc_max<=2 ||
       !std::isfinite(cal.series) || cal.series<=0 ||
       !std::isfinite(cal.r25) || cal.r25<=0 ||
       !std::isfinite(cal.beta) || cal.beta<=0 || !std::isfinite(cal.offset)) return NAN;
    double r=cal.series*adc/(cal.adc_max-adc);
    if(!cal.ntc_to_ground) r=cal.series*cal.series/r;
    const double inv=1.0/298.15+std::log(r/cal.r25)/cal.beta;
    if(inv<=0) return NAN;
    const double t=1.0/inv-273.15+cal.offset;
    // Intersection of the existing wire contract and this probe's rated range.
    return t>=-20 && t<=120 ? t : static_cast<double>(NAN);
}
}
