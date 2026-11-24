"""Age of the last real HA state report, not of our forwarding message."""
from __future__ import annotations
from datetime import datetime
import math


def reported_age_seconds(state, now: datetime) -> int | None:
    reported = getattr(state, 'last_reported', None) or getattr(state, 'last_updated', None)
    try:
        age = (now - reported).total_seconds()
    except (TypeError, AttributeError, OverflowError):
        return None
    # A future or invalid timestamp must not be presented as a new measurement.
    return math.ceil(age) if math.isfinite(age) and age >= 0 else None
