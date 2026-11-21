"""Home/away and forecast highs for the Pi controller (night cooling and occupancy rules).

Home Assistant only reads the states; the Pi decides what to do with them.
Kept free of Home Assistant objects (states are passed in) so it is easy to test.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

AWAY_ALARM_STATES = {"armed_away", "armed_vacation"}
UNKNOWN_STATES = {"unknown", "unavailable", "none", ""}
HOME_DOMAINS = ("alarm_control_panel", "zone", "person", "device_tracker", "group",
                "input_boolean", "binary_sensor", "switch")


def number(state: object) -> float | None:
    """A numeric state between -40 and 60 °C, or None."""
    value = getattr(state, "state", None)
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if -40 <= result <= 60 else None


def home_from_entity(entity_id: str, state: object) -> tuple[bool | None, str]:
    """Home (True), away (False) or unknown (None) from one chosen entity, with a short reason."""
    value = str(getattr(state, "state", "") or "").lower()
    if state is None or value in UNKNOWN_STATES:
        return None, f"{entity_id} er ukendt"
    domain = entity_id.split(".", 1)[0]
    if domain == "alarm_control_panel":
        return value not in AWAY_ALARM_STATES, f"Alarm: {value}"
    if domain == "zone":
        try:
            count = int(float(value))
        except ValueError:
            return None, f"{entity_id} er ukendt"
        return count > 0, f"{count} hjemme"
    if domain in ("person", "device_tracker"):
        return value == "home", f"{entity_id.split('.', 1)[1]}: {value}"
    if domain == "group":
        return value in ("home", "on"), f"Gruppe: {value}"
    return value == "on", f"{entity_id}: {value}"


def home_auto(alarms: Iterable[tuple[str, object]], zone_home: object) -> tuple[bool | None, str]:
    """No entity chosen: an alarm armed away/vacation means away, else zone.home's person count."""
    for entity_id, state in alarms:
        value = str(getattr(state, "state", "") or "").lower()
        if value in AWAY_ALARM_STATES:
            return False, f"Alarm {entity_id.split('.', 1)[1]}: {value}"
    if zone_home is not None:
        try:
            count = int(float(getattr(zone_home, "state", "")))
        except (TypeError, ValueError):
            return None, "zone.home er ukendt"
        return count > 0, f"{count} personer hjemme" if count != 1 else "1 person hjemme"
    return None, "Ingen alarm eller zone.home"


def resolve_entity(configured: dict, key: str, default: str | None, exists: Callable[[str], bool]) -> str | None:
    """The entity to read: the chosen one, nothing if the field was cleared ("") or the default if it exists."""
    if key in configured:
        value = configured.get(key)
        return str(value) if value else None
    return default if default and exists(default) else None


def climate_signals(get_state: Callable[[str], object], forecast_entity: str | None, today_entity: str | None,
                    home_entity: str | None, alarm_entities: Iterable[str]) -> dict[str, object]:
    """The leased signals the Pi takes: forecast_high_c, today_high_c, home and home_reason (None clears)."""
    signals: dict[str, object] = {}
    if forecast_entity:
        signals["forecast_high_c"] = number(get_state(forecast_entity))
    if today_entity:
        signals["today_high_c"] = number(get_state(today_entity))
    if home_entity:
        home, reason = home_from_entity(home_entity, get_state(home_entity))
    else:
        home, reason = home_auto(((entity_id, get_state(entity_id)) for entity_id in alarm_entities),
                                 get_state("zone.home"))
    signals["home"] = home
    signals["home_reason"] = reason
    return signals
