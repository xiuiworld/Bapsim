#pragma once
#include "a07/preprocess.hpp"
namespace a08 {
// Enable only after voltage, wiring, polarity and each calibration are measured.
constexpr bool SENSORS_CONFIRMED = false;
// Only the NTC channel has been commissioned for nominal temperature readings.
// Accuracy has not been checked against a reference thermometer.
constexpr bool TEMPERATURE_ENABLED = true;
// Zero selects the measured internal VDD for the supplied VDD:IO wiring.
constexpr double SUPPLY_MV = 0;
constexpr bool HIGH_IS_OPEN = true;
constexpr a07::Calibration CAL = {{500,500,500,500},{3000,3000,3000},4095,10000,10000,3950,0,true};
constexpr const char *CAL_VERSION = "ntc-10k-b3950-nominal-v1";
}
