"""Stable handoff after cooker detection for a future optional evidence provider.

No household-device adapter or fusion is installed in the current service.
"""

from __future__ import annotations

from typing import Mapping, Protocol


class OptionalEvidenceProvider(Protocol):
    def augment(self, cooker_detection: Mapping[str, object]) -> Mapping[str, object]:
        """Return the cooker decision plus optional evidence when enabled later."""


def cooker_only(detection: Mapping[str, object]) -> dict[str, object]:
    """Current implementation: pass the cooker result to workflow unchanged."""
    return dict(detection)
