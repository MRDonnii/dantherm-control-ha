"""Climate entity for the HCH5/HAC1 after-heater.

Home Assistant exposes a thermostat UI, but the Raspberry Pi controller remains
source of truth. The Pi only writes the verified HAC1 supply-air setpoint; the
HCH5/HAC1 continues to regulate the water valve and safety functions itself.

While the Pi reports the afterheat frost lock (HCH5 Control with
``afterheat_frost_lock``), the water coil in the attic must stay on: the entity
then offers only HEAT and no setpoint below the Pi's frost floor. Older Pi
firmware without these keys keeps the plain HEAT/OFF thermostat.

HCH5 Control 1.9.0+ also reports the HAC1 afterheat level (0..10) and who
regulates it; the attributes ``hac1_level``, ``level_wanted`` and
``control_reason`` carry it to the HCH5 card and are absent on older firmware.
"""
from __future__ import annotations

import math

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import parent_link, sub_model
from .controller_entity import ControllerEntity, has_afterheat_control

# HA 2026.11 renamed the climate temperature API to native_*; the old names
# warn and stop working in 2027.11. Older HA (>= 2025.8) only knows the old ones.
_NATIVE_API = hasattr(ClimateEntity, "native_current_temperature")

DEFAULT_MIN_TEMP = 10


def format_floor(value: float) -> str:
    """18.0 -> "18", 18.5 -> "18,5" (Danish decimal comma like the Pi's texts)."""
    return f"{value:g}".replace(".", ",")


class AfterheatClimate(ControllerEntity, ClimateEntity):
    """Supply-air afterheat thermostat backed by the Pi controller API."""

    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_max_temp = 35
    _attr_target_temperature_step = 1
    _attr_icon = "mdi:radiator"
    # The HAC1 level attributes are for the live card; the level, wanted level,
    # target and reason are recorded by their own sensors. The wanted level and
    # the reason text change on almost every poll, so keep them out of the
    # recorder here instead of writing a new attributes row every 5 s.
    _unrecorded_attributes = frozenset({
        "hac1_level", "level_wanted", "level_target", "level_max",
        "control_active", "control_reason", "control_target",
    })

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "afterheat_setpoint", "Eftervarme")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "hch5_mk1_hac1_afterheat")},
            name="Eftervarme",
            manufacturer="Dantherm",
            model=sub_model(coordinator, "HAC1 eftervarme", "Eftervarme"),
            **parent_link(coordinator),
        )

    def _target_temperature(self) -> float | None:
        value = self.coordinator.controller_state.get("afterheat_setpoint")
        if not isinstance(value, (int, float)):
            return None
        floor = self.frost_lock_floor if self.frost_lock else None
        # While frozen the Pi runs at least the floor, whatever is stored.
        return max(float(value), floor) if floor is not None else float(value)

    def _min_temp(self) -> float:
        floor = self.frost_lock_floor if self.frost_lock else None
        return floor if floor is not None else DEFAULT_MIN_TEMP

    min_temp = property(_min_temp)
    if hasattr(ClimateEntity, "native_min_temp"):
        native_min_temp = property(_min_temp)

    @property
    def hvac_modes(self) -> list[HVACMode]:
        return [HVACMode.HEAT] if self.frost_lock else [HVACMode.HEAT, HVACMode.OFF]

    @property
    def hvac_mode(self) -> HVACMode:
        if self.frost_lock:
            # The Pi keeps the afterheat on while it freezes, even if OFF is stored.
            return HVACMode.HEAT
        return HVACMode.HEAT if self.coordinator.controller_state.get("afterheat_enabled") is not False else HVACMode.OFF

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        state = self.coordinator.controller_state
        attributes: dict[str, object] = {}
        if "afterheat_frost_lock" in state:
            attributes.update({
                "frost_lock": state.get("afterheat_frost_lock") is True,
                "frost_lock_reason": state.get("afterheat_frost_lock_reason"),
                "frost_lock_floor": self.frost_lock_floor,
                "frost_lock_enforced": state.get("afterheat_frost_lock_enforced"),
            })
        if has_afterheat_control(state):
            # HCH5 Control 1.9.0+: the HAC1 level for the HCH5 card ("Trin N/10").
            attributes.update({
                "hac1_level": state.get("afterheat_level"),
                "level_wanted": state.get("afterheat_level_wanted"),
                "level_target": state.get("afterheat_level_target"),
                "level_max": state.get("afterheat_level_max"),
                "control_active": state.get("afterheat_control_active"),
                "control_reason": state.get("afterheat_control_reason"),
                "control_target": state.get("afterheat_target"),
            })
        return attributes or None

    def _current_temperature(self) -> float | None:
        state = self.coordinator.controller_state
        value = state.get("actual_supply_air_temperature")
        if not isinstance(value, (int, float)):
            value = self.coordinator.data.get("heating_coil_after_temperature")
        return float(value) if isinstance(value, (int, float)) else None

    if _NATIVE_API:
        _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
        native_target_temperature = property(_target_temperature)
        native_current_temperature = property(_current_temperature)
    else:
        _attr_temperature_unit = UnitOfTemperature.CELSIUS
        target_temperature = property(_target_temperature)
        current_temperature = property(_current_temperature)

    @property
    def hvac_action(self) -> HVACAction:
        state = self.coordinator.controller_state
        active = state.get("actual_afterheat")
        if active is None:
            active = self.coordinator.data.get("afterheat_active")
        return HVACAction.HEATING if active is True else HVACAction.IDLE

    async def async_set_temperature(self, **kwargs) -> None:
        value = kwargs.get("temperature")
        if value is None:
            return
        setpoint = int(round(float(value)))
        floor = self.frost_lock_floor if self.frost_lock else None
        if floor is not None and setpoint < floor <= float(value):
            # A fractional floor (e.g. 18.5) chosen as is must not round below it.
            setpoint = math.ceil(floor)
        if floor is not None and setpoint < floor:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="afterheat_frost_floor",
                translation_placeholders={"floor": format_floor(floor)},
            )
        await self.async_command({"afterheat_setpoint": setpoint})

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in (HVACMode.HEAT, HVACMode.OFF):
            raise ValueError("Unsupported afterheat mode")
        if hvac_mode == HVACMode.OFF and self.frost_lock:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="afterheat_frost_locked",
            )
        await self.async_command({"afterheat_enabled": hvac_mode == HVACMode.HEAT})


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    if coordinator.controller_client is not None:
        async_add_entities([AfterheatClimate(coordinator)])
