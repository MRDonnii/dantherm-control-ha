"""Base entities for the Raspberry Pi controller (Dantherm Control)."""
from __future__ import annotations

import asyncio

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
