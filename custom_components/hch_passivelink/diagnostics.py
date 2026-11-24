"""Diagnostics for Dantherm Control."""

from homeassistant.components.diagnostics import async_redact_data

from .smart_coordinator import describe_missing_sensors

TO_REDACT = {"host"}


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    result = {
        "config": async_redact_data(dict(entry.data), TO_REDACT),
        "connected": coordinator.client.connected,
        "decoded_values": dict(coordinator.data or {}),
    }
    if getattr(coordinator, "rooms_enabled", False) and hasattr(coordinator, "missing_room_sensors"):
        # Selected room sensors without a valid value.
        missing = coordinator.missing_room_sensors()
        result["missing_room_sensors"] = missing
        result["manglende_rumsensorer"] = describe_missing_sensors(missing)
    return result

