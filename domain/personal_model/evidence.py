"""Stable identifiers for accepted cooker-use groups.

The ID describes a group start already present in the personal model. It does
not turn an unobserved interval into evidence of non-use.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256


def usage_group_id(source: str, device_id: str, started_at: str) -> str:
    moment = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("usage group time requires timezone")
    canonical = moment.astimezone(timezone.utc).isoformat()
    digest = sha256(f"{source}\x00{device_id}\x00{canonical}".encode("utf-8")).hexdigest()
    return f"usage_group:{digest[:24]}"
