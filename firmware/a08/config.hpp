#pragma once
#include "a07/preprocess.hpp"
#include "site_profile.hpp"
namespace a08 {
// Opt-in during generation only; synthetic self-tests keep Pipeline's test default.
constexpr bool LIVE_OBSERVATIONS = false;
// Explicit transport opt-in; model qualification remains separate.
constexpr bool LIVE_EDGE_OUTPUT = false;
// Disable verbose raw UART for the final demo; the offline archive is independent.
// Diagnostic builds can enable this without changing sensor or inference policy.
constexpr bool RAW_UART_ENABLED = false;
// Demo operation does not start another raw-data collection campaign.
// Retain existing flash archives for recovery; do not append new raw records.
constexpr bool RAW_ARCHIVE_ENABLED = false;
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
// Installation calibration and channel trust come from site_profile.hpp.
// The default excludes the unreliable timer from observations and decisions.
}
