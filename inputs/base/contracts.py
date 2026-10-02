"""Boundary between raw input adapters and the future event normalizer.

This module specifies transport shape only. It does not infer behavior or risk.
"""

from typing import Literal, TypedDict

Source = Literal["simulator", "matter", "smartthings", "xiaomi"]
Availability = Literal["online", "offline", "unknown"]
Quality = Literal["simulated", "observed", "unknown"]


class RawInputEvent(TypedDict):
    device_id: str
    event_type: str  # Adapter-specific raw field; the normalizer maps it later.
    value: str
    observed_at: str  # ISO 8601 with timezone; source observation time
    received_at: str  # ISO 8601 with timezone; HA reception time
    source: Source
    availability: Availability
    quality: Quality

