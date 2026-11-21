"""Base entities for the Raspberry Pi controller (Dantherm Control)."""
from __future__ import annotations

import asyncio
import math

from aiohttp import ClientError
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONTROLLER_DEVICE_NAME, DOMAIN
from .controller_api import ControllerRejectedError
from .entity import OPTIONAL_CONTROLLER_KEYS, parent_link


class ControllerEntity(CoordinatorEntity):
    """Entity backed by controller state while the Pi remains source of truth."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"hch5_pi_controller_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "hch5_pi_controller")},
            name=getattr(coordinator, "controller_device_name", None) or CONTROLLER_DEVICE_NAME,
            manufacturer="Dantherm / Dantherm Control",
            model="Raspberry Pi controller",
            **parent_link(coordinator),
        )

    @property
    def available(self) -> bool:
        client = getattr(self.coordinator, "controller_client", None)
        return bool(client and client.connected and self.key in self.coordinator.controller_state)

    @property
    def max_level(self) -> int:
        """Highest fan step: 4 with Dantherm steps, 6 otherwise (and before 1.4.0)."""
        try:
            return int(self.coordinator.controller_state.get("max_level") or 6)
        except (TypeError, ValueError):
            return 6

    @property
    def controller_value(self):
        return self.coordinator.controller_state.get(self.key)

    @property
    def defer_until_data(self) -> bool:
        """Whether the entity is created only when the Pi first reports a value."""
        return self.key in OPTIONAL_CONTROLLER_KEYS

    @property
    def has_data(self) -> bool:
        return self.controller_value is not None

    @property
    def frost_lock(self) -> bool:
        """Afterheat frost lock reported by the Pi (False on firmware without it)."""
        return self.coordinator.controller_state.get("afterheat_frost_lock") is True

    @property
    def frost_lock_floor(self) -> float | None:
        value = self.coordinator.controller_state.get("afterheat_frost_lock_floor")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    async def async_command(self, patch: dict[str, object]) -> None:
        """Send a patch to the Pi; its own rejection text reaches the user as is."""
        try:
            await self.coordinator.async_controller_command(patch)
        except ControllerRejectedError as error:
            raise ServiceValidationError(str(error)) from error
        except (OSError, ClientError, ConnectionError, asyncio.TimeoutError) as error:
            raise HomeAssistantError(f"Dantherm Control svarer ikke: {error}") from error


# HCH5 Control 1.9.0+: the Pi can regulate the HAC1 afterheat level itself.
# Older firmware never reports this key; its entities are then never created.
AFTERHEAT_CONTROL_KEY = "afterheat_control_mode"


def has_afterheat_control(state: dict) -> bool:
    return AFTERHEAT_CONTROL_KEY in state


AFTERHEAT_VALVE_KEY = "actual_afterheat_valve_percent"
# HAC1 register 101 (0..10 = 0-10 V to the valve actuator), as the gateway reports it.
AFTERHEAT_LEVEL_KEYS = ("actual_afterheat_level", "afterheat_level")


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def has_afterheat_valve(state: dict) -> bool:
    """Whether the Pi reports the valve opening, or a HAC1 level it can be derived from."""
    return AFTERHEAT_VALVE_KEY in state or any(key in state for key in AFTERHEAT_LEVEL_KEYS)


def afterheat_valve_percent(state: dict) -> int | None:
    """The afterheat valve opening in % (0..100): the HAC1 level times 10, one level per volt.

    The gateway sends it itself (and None while register 101 is stale); an older
    gateway only sends the level, so it is derived from that. Register 209 is just
    an on/off flag and never used here.
    """
    if AFTERHEAT_VALVE_KEY in state:
        value = _finite(state.get(AFTERHEAT_VALVE_KEY))
        return None if value is None else int(round(min(100.0, max(0.0, value))))
    for key in AFTERHEAT_LEVEL_KEYS:
        level = _finite(state.get(key))
        if level is not None:
            return int(round(min(10.0, max(0.0, level)) * 10))
    return None


class AfterheatControlEntity(ControllerEntity):
    """Afterheat level regulation (Pi or HAC1); created once the Pi reports it.

    Its values may be null for a while (stale level, native mode), which shows
    as unknown rather than unavailable.
    """

    @property
    def available(self) -> bool:
        client = getattr(self.coordinator, "controller_client", None)
        return bool(client and client.connected and has_afterheat_control(self.coordinator.controller_state))

    @property
    def defer_until_data(self) -> bool:
        return True

    @property
    def has_data(self) -> bool:
        return has_afterheat_control(self.coordinator.controller_state)
