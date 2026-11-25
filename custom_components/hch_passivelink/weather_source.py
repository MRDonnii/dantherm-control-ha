"""Turn one selected Home Assistant weather entity into leased Pi observations."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


MAX_AGE = timedelta(hours=2)
CONDITIONS = {
    "clear-night", "cloudy", "fog", "hail", "lightning", "lightning-rainy",
    "partlycloudy", "pouring", "rainy", "snowy", "snowy-rainy", "sunny",
    "windy", "windy-variant", "exceptional",
}


def _number(value: object, minimum: float, maximum: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if math.isfinite(number) and minimum <= number <= maximum else None


def dew_point_c(temperature_c: float, humidity_pct: float) -> float | None:
    """Dew point (Magnus formula, over water). The Pi uses it to move a humidity
    reading to another temperature, e.g. outdoor humidity to the unit's T1."""
    if not 0 < humidity_pct <= 100 or not -60 <= temperature_c <= 60:
        return None
    a, b = 17.62, 243.12
    gamma = math.log(humidity_pct / 100) + a * temperature_c / (b + temperature_c)
    return round(b * gamma / (a - gamma), 2)


def weather_payload(entity_id: str, state: object, *, now: datetime | None = None) -> dict[str, object] | None:
    """Return current weather, omitting stale/unavailable observations."""
    if not entity_id.startswith("weather.") or state is None:
        return None
    condition = str(getattr(state, "state", ""))
    if condition not in CONDITIONS:
        return None
    updated = getattr(state, "last_updated", None)
    now = now or datetime.now(timezone.utc)
    if not isinstance(updated, datetime) or updated.tzinfo is None or now - updated > MAX_AGE:
        return None
    attrs = getattr(state, "attributes", {})
    unit = str(attrs.get("temperature_unit") or "°C").upper()
    if unit not in {"°C", "C", "°F", "F"}:
        return None
    def celsius(value: object) -> float | None:
        number = _number(value, -80, 140)
        if number is None:
            return None
        return round((number - 32) * 5 / 9, 2) if "F" in unit else number
    result: dict[str, object] = {"source": entity_id, "condition": condition}
    for key, value in (
        ("temperature_c", celsius(attrs.get("temperature"))),
        ("dew_point_c", celsius(attrs.get("dew_point"))),
        ("humidity_pct", _number(attrs.get("humidity"), 0, 100)),
    ):
        if value is not None:
            result[key] = value
    if "dew_point_c" not in result and "temperature_c" in result and "humidity_pct" in result:
        # Outdoor humidity must be moved to the unit's T1 via the dew point,
        # so a weather entity without one gets it worked out here.
        dew = dew_point_c(result["temperature_c"], result["humidity_pct"])
        if dew is not None:
            result["dew_point_c"] = dew
    return result
