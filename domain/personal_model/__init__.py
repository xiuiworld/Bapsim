"""Local, per-cooker usage-pattern learning."""

from .engine import PersonalUsageModel

OPERATING_WINDOW_SETTINGS = {
    "window_history_days": 10,
    "historical_slot_lock": True,
    "drift_gate_minutes": 75,
}


def operating_usage_model() -> PersonalUsageModel:
    return PersonalUsageModel(**OPERATING_WINDOW_SETTINGS)


__all__ = ["PersonalUsageModel", "OPERATING_WINDOW_SETTINGS", "operating_usage_model"]
