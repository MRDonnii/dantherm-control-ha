"""Forecast highs for the Pi controller's night cooling.

Home Assistant only reads the states; the Pi decides what to do with them.
Kept free of Home Assistant objects (states are passed in) so it is easy to test.
"""
from __future__ import annotations

from collections.abc import Callable


def number(state: object) -> float | None:
    """A numeric state between -40 and 60 °C, or None."""
    value = getattr(state, "state", None)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if -40 <= result <= 60 else None


def resolve_entity(configured: dict, key: str, default: str | None, exists: Callable[[str], bool]) -> str | None:
    """The entity to read: the chosen one, nothing if the field was cleared ("") or the default if it exists."""
    if key in configured:
        value = configured.get(key)
        return str(value) if value else None
    return default if default and exists(default) else None


def forecast_signals(get_state: Callable[[str], object], forecast_entity: str | None,
                     today_entity: str | None) -> dict[str, object]:
    """The leased signals the Pi takes: forecast_high_c and today_high_c (None clears)."""
    signals: dict[str, object] = {}
    if forecast_entity:
        signals["forecast_high_c"] = number(get_state(forecast_entity))
    if today_entity:
        signals["today_high_c"] = number(get_state(today_entity))
    return signals
