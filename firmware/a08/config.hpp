#pragma once
#include "a07/preprocess.hpp"
namespace a08 {
// Enable only after voltage, wiring, polarity and each calibration are measured.
constexpr bool SENSORS_CONFIRMED = true;
// The NTC channel is enabled for nominal temperature readings.
// Accuracy has not been checked against a reference thermometer.
constexpr bool TEMPERATURE_ENABLED = true;
// P1.10 verified LOW with magnet attached, HIGH with magnet removed.
constexpr bool CONTACT_ENABLED = true;
// Zero selects the measured internal VDD for the supplied VDD:IO wiring.
constexpr double SUPPLY_MV = 0;
constexpr bool HIGH_IS_OPEN = true;
// Installed CRP-G1020MP, development cooking run 2026-10-07.
// Values use raw/VDD_raw*4095, not the ADC's 3.6 V full-scale code.
// OFF anchors for warm/white include the measured adjacent-LED light bleed.
// This is installation-specific calibration, not independent model validation.
constexpr a07::Calibration CAL = {{52,952,1012,202},{2740,1490,2600,810},4095,10000,10000,3950,0,true};
constexpr const char *CAL_VERSION = "g1020-mounted-20261007-v1";
}
