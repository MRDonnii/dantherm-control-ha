"""Device registry lookups that work across the supported HA versions."""

from __future__ import annotations

from typing import Any


def find_entry_device(device_registry: Any, identifier: tuple[str, str], entry_id: str) -> Any:
    """Return this config entry's device with ``identifier``, or None.

    HA 2026.8 scoped devices to one config entry and added
    ``async_get_device_by_identifier(identifier, config_entry_id)``; the global
    ``async_get_device(identifiers=...)`` is deprecated and goes away in 2027.8.
    Older versions (the integration supports 2025.8+) only have the global
    lookup, where identifiers are unique across the whole registry.
    """
    get_by_identifier = getattr(device_registry, "async_get_device_by_identifier", None)
    if get_by_identifier is not None:
        return get_by_identifier(identifier, entry_id)
    return device_registry.async_get_device(identifiers={identifier})
