#pragma once
#include "a07/preprocess.hpp"
namespace a08 {
// Enable only after voltage, wiring, polarity and each calibration are measured.
constexpr bool SENSORS_CONFIRMED = false;
constexpr double SUPPLY_MV = 0;
constexpr bool HIGH_IS_OPEN = true;
constexpr a07::Calibration CAL = {{500,500,500,500},{3000,3000,3000},4095,10000,10000,3950,0,true};
constexpr const char *CAL_VERSION = "uncommissioned";
}
