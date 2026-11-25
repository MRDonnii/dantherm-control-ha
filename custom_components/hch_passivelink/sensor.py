"""Sensors for Dantherm Control."""

import math
from dataclasses import dataclass
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant

try:  # HA 2026.8+: CONCENTRATION_PARTS_PER_MILLION is deprecated (removed 2027.8).
    from homeassistant.const import UnitOfRatio
    CONCENTRATION_PARTS_PER_MILLION = UnitOfRatio.PARTS_PER_MILLION
except ImportError:
    from homeassistant.const import CONCENTRATION_PARTS_PER_MILLION
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .controller_entity import (
    AFTERHEAT_VALVE_KEY,
    AfterheatControlEntity,
    ControllerEntity,
    afterheat_valve_percent,
    has_afterheat_valve,
)
from .coordinator import PassiveLinkCoordinator
from .entity import PI_KEYS, PREHEATER_KEYS, PassiveLinkEntity, async_add_entities_when_ready
from .mk2_entities import Mk2AlarmSensor, Mk2OperatingHoursSensor, drop_mk1_only, is_mk2


@dataclass(frozen=True, kw_only=True)
class Description(SensorEntityDescription):
    pass


DESCRIPTIONS = (
    Description(key="outdoor_temperature", translation_key="outdoor_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="supply_temperature", translation_key="supply_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="extract_temperature", translation_key="extract_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="exhaust_temperature", translation_key="exhaust_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="co2", translation_key="co2", native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION, device_class=SensorDeviceClass.CO2, state_class=SensorStateClass.MEASUREMENT),
    Description(key="room_temperature", translation_key="room_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="relative_humidity", translation_key="relative_humidity", native_unit_of_measurement=PERCENTAGE, device_class=SensorDeviceClass.HUMIDITY, state_class=SensorStateClass.MEASUREMENT),
    Description(key="measured_relative_humidity", translation_key="measured_relative_humidity", native_unit_of_measurement=PERCENTAGE, device_class=SensorDeviceClass.HUMIDITY, state_class=SensorStateClass.MEASUREMENT),
    Description(key="afterheat_setpoint", translation_key="afterheat_setpoint", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="heating_coil_before_temperature", translation_key="heating_coil_before_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="heating_coil_after_temperature", translation_key="heating_coil_after_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="heating_coil_air_delta", translation_key="heating_coil_air_delta", native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT, icon="mdi:delta"),
    Description(key="afterheat_room_setpoint", translation_key="afterheat_room_setpoint", icon="mdi:home-thermometer-outline"),
    Description(key="afterheat_extract_setpoint", translation_key="afterheat_extract_setpoint", icon="mdi:thermometer-off"),
    Description(key="extract_fan_rpm", translation_key="extract_fan_rpm", native_unit_of_measurement="rpm", state_class=SensorStateClass.MEASUREMENT),
    Description(key="supply_fan_rpm", translation_key="supply_fan_rpm", native_unit_of_measurement="rpm", state_class=SensorStateClass.MEASUREMENT),
    Description(key="extract_fan_percent", translation_key="extract_fan_percent", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="supply_fan_percent", translation_key="supply_fan_percent", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="heat_recovery_efficiency", translation_key="heat_recovery_efficiency", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT, icon="mdi:heat-wave"),
    Description(key="supply_extract_delta", translation_key="supply_extract_delta", native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT, icon="mdi:swap-vertical"),
    Description(key="outdoor_temperature_source", translation_key="outdoor_temperature_source", device_class=SensorDeviceClass.ENUM, options=["unit_sensor"], entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:thermometer-check"),
    Description(key="air_quality_index", translation_key="air_quality_index", device_class=SensorDeviceClass.ENUM, options=["good", "moderate", "poor"], icon="mdi:leaf"),
    Description(key="bus_frame_rate", translation_key="bus_frame_rate", native_unit_of_measurement="frames/min", state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:pulse"),
    Description(key="heat_recovery_status", translation_key="heat_recovery_status", device_class=SensorDeviceClass.ENUM, options=["good", "acceptable", "low"], icon="mdi:check-decagram-outline"),
    Description(key="heat_recovery_trend", translation_key="heat_recovery_trend", device_class=SensorDeviceClass.ENUM, options=["normal", "watch", "degraded"], icon="mdi:chart-timeline-variant"),
    Description(key="heat_recovery_reference", translation_key="heat_recovery_reference", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    Description(key="heat_recovery_drop", translation_key="heat_recovery_drop", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    Description(key="fan_control_delta", translation_key="fan_control_delta", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT, icon="mdi:fan-plus"),
    Description(key="fan_rpm_delta", translation_key="fan_rpm_delta", native_unit_of_measurement="rpm", state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:fan-chevron-up"),
    Description(key="filter_last_change", translation_key="filter_last_change", entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:calendar-check"),
    Description(key="filter_change_count", translation_key="filter_change_count", entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:counter"),
    Description(key="preheater_flow_temperature", translation_key="preheater_flow_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="preheater_return_temperature", translation_key="preheater_return_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT),
    Description(key="preheater_water_delta", translation_key="preheater_water_delta", native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=SensorStateClass.MEASUREMENT, icon="mdi:delta"),
    Description(key="preheater_activity", translation_key="preheater_activity", device_class=SensorDeviceClass.ENUM, options=["inactive", "low", "normal", "high"], icon="mdi:radiator"),
    Description(key="operating_mode", translation_key="operating_mode", device_class=SensorDeviceClass.ENUM, options=["fireplace", "standby", "auto_or_boost", "manual_1", "manual_2", "manual_3", "auto_or_scheduled"]),
    Description(key="current_level", translation_key="current_level", device_class=SensorDeviceClass.ENUM, options=["off", "level_1", "level_2", "level_3", "boost"]),
    Description(key="filter_interval_days", translation_key="filter_interval_days", native_unit_of_measurement=UnitOfTime.DAYS, entity_category=EntityCategory.DIAGNOSTIC),
    Description(key="filter_days_remaining", translation_key="filter_days_remaining", native_unit_of_measurement=UnitOfTime.DAYS, entity_category=EntityCategory.DIAGNOSTIC),
    Description(key="filter_life_percent", translation_key="filter_life_percent", native_unit_of_measurement=PERCENTAGE, entity_category=EntityCategory.DIAGNOSTIC),
    Description(key="filter_status", translation_key="filter_status", device_class=SensorDeviceClass.ENUM, options=["ok", "change_soon", "overdue"], entity_category=EntityCategory.DIAGNOSTIC),
    Description(key="filter_source", translation_key="filter_source", device_class=SensorDeviceClass.ENUM, options=["hcp4_synchronized", "unit"], entity_category=EntityCategory.DIAGNOSTIC),
    Description(key="heat_recovery_raw", translation_key="heat_recovery_raw", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    Description(key="bypass_raw", translation_key="bypass_raw", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    Description(key="status_code", translation_key="status_code", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    Description(key="afterheat_raw", translation_key="afterheat_raw", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    Description(key="command_raw", translation_key="command_raw", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    Description(key="pi_cpu_temperature", translation_key="pi_cpu_temperature", native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    Description(key="pi_uptime_seconds", translation_key="pi_uptime_seconds", native_unit_of_measurement=UnitOfTime.SECONDS, device_class=SensorDeviceClass.DURATION, entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:timer-outline"),
    Description(key="pi_load_average_1m", translation_key="pi_load_average_1m", state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:speedometer"),
    Description(key="pi_memory_used_percent", translation_key="pi_memory_used_percent", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:memory"),
    Description(key="pi_disk_used_percent", translation_key="pi_disk_used_percent", native_unit_of_measurement=PERCENTAGE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, icon="mdi:harddisk"),
    Description(key="pi_core_voltage", translation_key="pi_core_voltage", native_unit_of_measurement=UnitOfElectricPotential.VOLT, device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC),
    Description(key="pi_model", translation_key="pi_model", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False, icon="mdi:raspberry-pi"),
    Description(key="pi_kernel_version", translation_key="pi_kernel_version", entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False, icon="mdi:linux"),
)


CONTROLLER_SENSOR_SPECS = (
    ("active_master", "Aktiv master", None, "mdi:server-network"),
    ("effective_source", "Effektiv kilde", None, "mdi:source-branch"),
    ("effective_level", "Effektivt ventilationsniveau", None, "mdi:fan-speed-3"),
    ("effective_reason", "Effektiv årsag", None, "mdi:text-box-search-outline"),
    ("smart_demand", "Smart Auto behov", None, "mdi:brain"),
    ("smart_requested_level", "Smart Auto ønsket niveau", None, "mdi:fan-chevron-up"),
    ("smart_target_level", "Smart Auto aktivt målniveau", None, "mdi:target"),
    ("smart_controlling_room", "Smart Auto styrende rum", None, "mdi:home-lightning-bolt-outline"),
    ("smart_controlling_metric", "Smart Auto styrende måling", None, "mdi:gauge"),
    ("smart_max_co2", "Smart Auto højeste CO2", CONCENTRATION_PARTS_PER_MILLION, "mdi:molecule-co2"),
    ("smart_max_co2_room", "Smart Auto højeste CO2 rum", None, "mdi:home-alert-outline"),
    ("smart_max_rh", "Smart Auto højeste luftfugtighed", PERCENTAGE, "mdi:water-percent"),
    ("smart_max_rh_room", "Smart Auto højeste RH rum", None, "mdi:home-alert-outline"),
    ("smart_inputs_age_seconds", "Smart Auto inputalder", UnitOfTime.SECONDS, "mdi:timer-sand"),
    ("hardware_control_state", "Hardwarekontrolstatus", None, "mdi:shield-lock-outline"),
    ("hcp4_last_foreign_write_age", "Seneste HCP4-write", UnitOfTime.SECONDS, "mdi:timer-outline"),
    ("actual_fan_extract_percent", "Faktisk udsugning", PERCENTAGE, "mdi:fan"),
    ("actual_fan_supply_percent", "Faktisk indblæsning", PERCENTAGE, "mdi:fan"),
    ("actual_unit_frost_supply_percent", "Frostsikring: indblæsning", PERCENTAGE, "mdi:fan-chevron-down"),
    ("actual_fan_extract_rpm", "Faktisk udsugning RPM", "rpm", "mdi:fan-speed-2"),
    ("actual_fan_supply_rpm", "Faktisk indblæsning RPM", "rpm", "mdi:fan-speed-2"),
    ("actual_afterheat_setpoint", "Faktisk eftervarme setpunkt", UnitOfTemperature.CELSIUS, "mdi:thermostat"),
    ("actual_afterheat_selection", "Faktisk eftervarmevalg", None, "mdi:radiator"),
    ("actual_supply_before_heater_temperature", "Luft før varmeflade", UnitOfTemperature.CELSIUS, "mdi:thermometer-low"),
    ("attic_temperature", "Loftrum temperatur", UnitOfTemperature.CELSIUS, "mdi:home-roof"),
    ("supply_recovery_percent", "Genvinding indblæsningsside", PERCENTAGE, "mdi:heat-wave"),
    ("recovered_heat_w", "Genvundet varme", UnitOfPower.WATT, "mdi:heat-wave"),
    ("afterheat_lift", "Eftervarme temperaturløft", UnitOfTemperature.CELSIUS, "mdi:delta"),
    ("afterheat_power_w", "Eftervarme effekt til luft", UnitOfPower.WATT, "mdi:radiator"),
    ("supply_airflow_estimate_m3h", "Indblæsning luftmængde anslået", "m³/h", "mdi:weather-windy"),
    ("diagnostics_status", "Diagnose status", None, "mdi:stethoscope"),
    ("diagnostics_alarm_text", "Diagnose advarsler", None, "mdi:alert-outline"),
    ("frost_state", "Frost i veksler", None, "mdi:snowflake-alert"),
    ("extract_recovery_percent", "Genvinding udsugningsside", PERCENTAGE, "mdi:heat-wave"),
    ("specific_fan_power", "Specifik ventilatoreffekt (SFP)", "W/(m³/s)", "mdi:fan-alert"),
    ("filter_power_ratio", "Filter strøm i forhold til rent filter", None, "mdi:air-filter"),
    ("filter_power_change_percent", "Filter strømændring mod rene filtre", PERCENTAGE, "mdi:air-filter"),
    ("filter_hours_since_change", "Filter timer siden skift", UnitOfTime.HOURS, "mdi:timer-sand"),
    ("extract_rooms_temperature", "Udsugningsrum temperatur", UnitOfTemperature.CELSIUS, "mdi:home-thermometer"),
    ("extract_duct_loss_k", "Udsugningskanal afkøling", "K", "mdi:thermometer-minus"),
    ("extract_duct_loss_percent", "Udsugningskanal andel tabt", PERCENTAGE, "mdi:air-filter"),
    ("recovery_factor", "Genvundet varme pr. kWh strøm i dag", None, "mdi:multiplication"),
    ("recovered_energy_today_kwh", "Genvundet varme i dag", UnitOfEnergy.KILO_WATT_HOUR, "mdi:heat-wave"),
    ("afterheat_energy_today_kwh", "Eftervarme til luft i dag", UnitOfEnergy.KILO_WATT_HOUR, "mdi:radiator"),
    ("unit_energy_today_kwh", "Anlæggets elforbrug i dag", UnitOfEnergy.KILO_WATT_HOUR, "mdi:lightning-bolt"),
    ("actual_supply_air_temperature", "Faktisk indblæsningstemperatur", UnitOfTemperature.CELSIUS, "mdi:thermometer-high"),
    ("actual_afterheat_frost_temperature", "Eftervarme frostføler", UnitOfTemperature.CELSIUS, "mdi:snowflake-thermometer"),
    ("actual_afterheat_valve_percent", "Eftervarme ventil", PERCENTAGE, "mdi:valve"),
    ("fireplace_remaining_seconds", "Pejsetid tilbage", UnitOfTime.SECONDS, "mdi:timer-outline"),
    ("actual_bypass_raw", "Bypass statuskode", None, "mdi:valve"),
    ("actual_bypass_travel_direction", "Bypass rejseretning", None, "mdi:swap-horizontal"),
    ("actual_bypass_travel_seconds", "Bypass rejsetid", UnitOfTime.SECONDS, "mdi:timer-sand"),
    ("bypass_travel_expected_seconds", "Bypass forventet rejsetid", UnitOfTime.SECONDS, "mdi:timer-outline"),
    ("actual_bypass_request", "Bypass aktuelt ønske", None, "mdi:valve"),
    ("actual_afterheat_outdoor_lockout", "Eftervarme sommerstop", None, "mdi:weather-sunny-alert"),
    ("cooling_state", "Frikølingstilstand", None, "mdi:snowflake"),
    # The room temperature free cooling and the afterheat steer by, and where it comes from.
    ("afterheat_room_temperature", "Styrende rumtemperatur", UnitOfTemperature.CELSIUS, "mdi:home-thermometer"),
    ("afterheat_room_source_used", "Kilde til styrende rumtemperatur", None, "mdi:home-search-outline"),
    ("quick_boost_remaining_seconds", "Hurtig boost tilbage", UnitOfTime.SECONDS, "mdi:fan-clock"),
    ("cool_boost_remaining_seconds", "Køl tid tilbage", UnitOfTime.SECONDS, "mdi:snowflake-alert"),
    ("bonfire_remaining_seconds", "Bål tid tilbage", UnitOfTime.SECONDS, "mdi:campfire"),
    ("standby_remaining_seconds", "Slukket tid tilbage", UnitOfTime.SECONDS, "mdi:power-sleep"),
    ("balance_running_excess_percent", "Luftbalance lige nu", PERCENTAGE, "mdi:scale-balance"),
)


class BalanceStatusSensor(ControllerEntity, SensorEntity):
    """Air balance details from the Pi: duct ratio in use and the heat-balance learning."""

    def __init__(self, coordinator, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator, key, name)
        self._attr_icon = icon
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        client = getattr(self.coordinator, "controller_client", None)
        return bool(client and client.connected and isinstance(self.coordinator.controller_state.get("balance"), dict))

    @property
    def native_value(self):
        state = self.coordinator.controller_state
        if self.key == "balance_duct_ratio_in_use":
            return (state.get("balance") or {}).get("duct_ratio")
        return (state.get("balance_live") or {}).get("reason")

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self.coordinator.controller_state
        balance = state.get("balance") or {}
        learned = balance.get("learned") or {}
        live = state.get("balance_live") or {}
        if self.key == "balance_duct_ratio_in_use":
            return {
                "kilde": balance.get("duct_ratio_source"),
                "laert_kanalforhold": learned.get("ratio"),
                "laert_i_brug": learned.get("ratio_in_use"),
                "maalinger": learned.get("count"),
                "naetter": learned.get("nights"),
                "spredning": learned.get("spread"),
                "sikkerhed": learned.get("confidence"),
                "seneste": (learned.get("last") or {}).get("reason"),
            }
        return {
            "tilstand": live.get("state"),
            "temperaturforskel_k": live.get("delta_t_now"),
            "udsugning_over_indblaesning_nu_pct": live.get("excess_now_percent"),
            "kanalforhold_nu": live.get("duct_ratio_now"),
            "fremdrift_pct": live.get("progress_percent"),
        }


def _number(value) -> float | None:
    # Python's JSON parser accepts NaN/Infinity; they must not crash int(round()).
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


# (key, name, unit, icon, decimals) for the afterheat level regulation (HCH5 Control 1.9.0+).
AFTERHEAT_LEVEL_SENSOR_SPECS = (
    ("afterheat_level", "Eftervarme trin", None, "mdi:stairs", 0),
    ("afterheat_level_wanted", "Eftervarme mål-trin", None, "mdi:stairs-up", 1),
    ("afterheat_target", "Eftervarme mål", UnitOfTemperature.CELSIUS, "mdi:thermometer-auto", 1),
)


class AfterheatLevelSensor(AfterheatControlEntity, SensorEntity):
    """HAC1 afterheat level 0..10, the wanted (continuous) level and the target in °C."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, key: str, name: str, unit, icon: str, decimals: int) -> None:
        super().__init__(coordinator, key, name)
        self._attr_translation_key = key
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._decimals = decimals
        if unit == UnitOfTemperature.CELSIUS:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_suggested_display_precision = decimals

    @property
    def native_value(self):
        value = _number(self.controller_value)
        if value is None:
            return None
        return int(round(value)) if self._decimals == 0 else round(value, self._decimals)

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        if self.key != "afterheat_level":
            return None
        state = self.coordinator.controller_state
        return {"level_max": state.get("afterheat_level_max"), "level_target": state.get("afterheat_level_target")}


class AfterheatControlSensor(AfterheatControlEntity, SensorEntity):
    """Who regulates the afterheat and why, as the Pi's own short Danish text."""

    _attr_icon = "mdi:radiator"
    # The countdown and the error change on every poll; keep them out of the recorder
    # (the error is T2AH minus the target, both recorded as their own sensors).
    _unrecorded_attributes = frozenset({"next_tick_seconds", "control_error"})

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "afterheat_control_reason", "Eftervarme styring")
        self._attr_translation_key = "afterheat_control_reason"

    @property
    def native_value(self):
        value = self.controller_value
        # A sensor state holds at most 255 characters.
        return str(value)[:255] if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        state = self.coordinator.controller_state
        return {
            "control_mode": state.get("afterheat_control_mode"),
            "control_active": state.get("afterheat_control_active"),
            "next_action": state.get("afterheat_next_action"),
            "next_tick_seconds": state.get("afterheat_next_tick_seconds"),
            "hac1_setpoint": state.get("afterheat_hac1_setpoint"),
            "level_target": state.get("afterheat_level_target"),
            "control_error": state.get("afterheat_control_error"),
        }


# The Pi's compact block `afterheat_ha` (Dantherm Control 1.13.0-beta.11+): the afterheat model, its
# predictions and its performance. (key, name, path in the block, unit, device class, state class, icon, decimals)
AFTERHEAT_MODEL_SPECS = (
    ("algorithm", "Eftervarme styringsmetode", ("label",), None, None, None, "mdi:brain", None),
    ("model_state", "Eftervarme modelstatus", ("model_state",), None, None, None, "mdi:school-outline", None),
    ("air_1min", "Eftervarme forudsagt luft om 1 min", ("air_1min_c",), UnitOfTemperature.CELSIUS,
     SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer-chevron-up", 1),
    ("air_10min", "Eftervarme forudsagt luft om 10 min", ("air_10min_c",), UnitOfTemperature.CELSIUS,
     SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer-chevron-up", 1),
    ("air_lowest", "Eftervarme laveste forudsagte luft", ("air_lowest_c",), UnitOfTemperature.CELSIUS,
     SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer-chevron-down", 1),
    ("prediction_error", "Eftervarme forudsigelsesfejl", ("prediction_error_k",), "K", None,
     SensorStateClass.MEASUREMENT, "mdi:chart-bell-curve", 2),
    ("step_k", "Eftervarme varme pr. trin", ("step_k",), "K", None, SensorStateClass.MEASUREMENT, "mdi:stairs-up", 1),
    ("tau_up", "Eftervarme reaktionstid op", ("tau_up_s",), UnitOfTime.SECONDS, SensorDeviceClass.DURATION,
     SensorStateClass.MEASUREMENT, "mdi:timer-sand", 0),
    ("tau_down", "Eftervarme reaktionstid ned", ("tau_down_s",), UnitOfTime.SECONDS, SensorDeviceClass.DURATION,
     SensorStateClass.MEASUREMENT, "mdi:timer-sand", 0),
    ("dead", "Eftervarme forsinkelse", ("dead_s",), UnitOfTime.SECONDS, SensorDeviceClass.DURATION,
     SensorStateClass.MEASUREMENT, "mdi:timer-outline", 0),
    ("offset", "Eftervarme rettelse", ("offset_k",), "K", None, SensorStateClass.MEASUREMENT, "mdi:delta", 2),
    ("power", "Eftervarme effekt", ("heat_kw",), UnitOfPower.KILO_WATT, SensorDeviceClass.POWER,
     SensorStateClass.MEASUREMENT, "mdi:radiator", 2),
    ("energy_today", "Eftervarme energi i dag", ("energy_today_kwh",), UnitOfEnergy.KILO_WATT_HOUR,
     SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING, "mdi:radiator", 2),
    ("under_floor_24h", "Eftervarme tid under bund 24 t", ("performance", "24h", "under_percent"), PERCENTAGE, None,
     SensorStateClass.MEASUREMENT, "mdi:thermometer-low", 1),
    ("mean_error_24h", "Eftervarme middelafvigelse 24 t", ("performance", "24h", "mean_error_k"), "K", None,
     SensorStateClass.MEASUREMENT, "mdi:plus-minus-variant", 2),
    ("changes_24h", "Eftervarme skift pr. time 24 t", ("performance", "24h", "changes_per_hour"), "skift/t", None,
     SensorStateClass.MEASUREMENT, "mdi:swap-vertical", 1),
)
MODEL_STATE_TEXT = {"learning": "Lærer", "ready": "Klar", "uncertain": "Usikker"}


class AfterheatModelSensor(ControllerEntity, SensorEntity):
    """One value of the Pi's afterheat model block; unavailable while the Pi has no value for it.

    An older Pi never sends the block: the sensors are then not created at all.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"hvorfor"})

    def __init__(self, coordinator, field: str, name: str, path: tuple, unit, device_class, state_class,
                 icon: str, decimals: int | None) -> None:
        super().__init__(coordinator, f"afterheat_ha_{field}", name)
        self._path = path
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_icon = icon
        if decimals is not None:
            self._attr_suggested_display_precision = decimals
        self._text = unit is None

    def _block(self) -> dict | None:
        block = self.coordinator.controller_state.get("afterheat_ha")
        return block if isinstance(block, dict) else None

    @property
    def controller_value(self):
        value = self._block()
        for step in self._path:
            value = value.get(step) if isinstance(value, dict) else None
        if value is None:
            return None
        if self._text:
            text = MODEL_STATE_TEXT.get(value, value) if self.key.endswith("model_state") else value
            return str(text)[:255]
        return round(float(value), 4) if _number(value) is not None else None

    @property
    def available(self) -> bool:
        client = getattr(self.coordinator, "controller_client", None)
        return bool(client and client.connected and self.controller_value is not None)

    @property
    def defer_until_data(self) -> bool:
        return True

    @property
    def has_data(self) -> bool:
        return self._block() is not None

    @property
    def native_value(self):
        return self.controller_value

    @property
    def extra_state_attributes(self) -> dict[str, object] | None:
        block = self._block()
        if block is None:
            return None
        if self.key == "afterheat_ha_algorithm":
            return {"hvorfor": block.get("why")}
        if self.key == "afterheat_ha_step_k":
            return {"naeste_trin": block.get("step_next_k")}
        return None


class PassiveLinkSensor(PassiveLinkEntity, SensorEntity, RestoreEntity):
    def __init__(self, coordinator: PassiveLinkCoordinator, description: Description) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._restored_value = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.key not in {
            "afterheat_setpoint",
            "afterheat_room_setpoint",
            "afterheat_extract_setpoint",
        } or self.key in self.coordinator.data:
            return
        state = await self.async_get_last_state()
        if state is None or state.state in {"unknown", "unavailable"}:
            return
        if self.key == "afterheat_setpoint":
            try:
                self._restored_value = float(state.state)
            except ValueError:
                return
        else:
            self._restored_value = state.state

    @property
    def available(self) -> bool:
        return super().available or self._restored_value is not None

    @property
    def native_value(self):
        value = self.coordinator.data.get(self.key, self._restored_value)
        if self.key in {"afterheat_room_setpoint", "afterheat_extract_setpoint"}:
            return "OFF" if value == "off" else f"{value} °C"
        return value


ENERGY_SENSOR_SPECS = (
    ("recovered_energy_kwh", "Genvundet varme energi", "mdi:heat-wave"),
    ("afterheat_energy_kwh", "Eftervarme energi til luft", "mdi:radiator"),
    ("unit_energy_kwh", "Anlæggets elforbrug (fra Pi)", "mdi:lightning-bolt"),
)


class ControllerEnergySensor(ControllerEntity, SensorEntity):
    """Cumulative kWh from the Pi; usable in the HA energy dashboard and utility meters."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator, key: str, name: str, icon: str) -> None:
        super().__init__(coordinator, key, name)
        self._attr_icon = icon

    @property
    def native_value(self):
        return self.controller_value


# Units whose values are live measurements; HA then keeps 5-minute statistics and
# shows the same smooth graph as the other sensors instead of every raw sample.
MEASUREMENT_UNITS = {
    UnitOfTemperature.CELSIUS: SensorDeviceClass.TEMPERATURE,
    UnitOfPower.WATT: SensorDeviceClass.POWER,
    CONCENTRATION_PARTS_PER_MILLION: SensorDeviceClass.CO2,
    PERCENTAGE: None,
    "rpm": None,
    "m³/h": None,
    "W/(m³/s)": None,
}
# Setpoints change in steps on purpose and are not filtered or averaged.
SETPOINT_KEYS = {"actual_afterheat_setpoint"}
# The controller's room temperature sources; a single HA room comes as "room:<name>".
ROOM_SOURCE_LABELS = {
    "ha_average": "Gennemsnit af HA-rum",
    "t3": "Udsugningsluft (T3)",
    "t5": "HRC2-rumføler (T5)",
}
# A single sample this far from its neighbours is a bus glitch, not air.
SPIKE_LIMIT_C = 1.0


class ControllerStatusSensor(ControllerEntity, SensorEntity):
    def __init__(self, coordinator, key: str, name: str, unit, icon: str) -> None:
        super().__init__(coordinator, key, name)
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._temperature = unit == UnitOfTemperature.CELSIUS and key not in SETPOINT_KEYS
        if unit in MEASUREMENT_UNITS and key not in SETPOINT_KEYS:
            self._attr_state_class = SensorStateClass.MEASUREMENT
            if MEASUREMENT_UNITS[unit] is not None:
                self._attr_device_class = MEASUREMENT_UNITS[unit]
        self._accepted: float | None = None
        self._pending: float | None = None
        self._seen_state: object = None
        self._shown: float | None = None

    @property
    def available(self) -> bool:
        if self.key != AFTERHEAT_VALVE_KEY:
            return super().available
        client = getattr(self.coordinator, "controller_client", None)
        return bool(client and client.connected and has_afterheat_valve(self.coordinator.controller_state))

    @property
    def controller_value(self):
        if self.key == AFTERHEAT_VALVE_KEY:
            # Valve opening = HAC1 level x 10 %, also from a gateway that only sends the level.
            return afterheat_valve_percent(self.coordinator.controller_state)
        return super().controller_value

    @property
    def native_value(self):
        value = self.controller_value
        if value is None and self.key == "standby_remaining_seconds":
            # The Pi has no end time while the unit is on; that is zero seconds left.
            state = self.coordinator.controller_state
            return 0 if "standby_active" in state and not state["standby_active"] else None
        if self.key == "afterheat_room_source_used" and isinstance(value, str):
            return ROOM_SOURCE_LABELS.get(value, value.removeprefix("room:"))
        if not self._temperature or not isinstance(value, (int, float)):
            return value
        # Each controller poll replaces controller_state with a new dict; filter a
        # sample once, however often HA reads the state in between.
        state = self.coordinator.controller_state
        if state is not self._seen_state:
            self._seen_state = state
            self._shown = self._filtered(float(value))
        return self._shown

    def _filtered(self, value: float) -> float:
        """Round to 0.1 °C and drop a lone sample that jumps and comes straight back."""
        if self._accepted is None or abs(value - self._accepted) <= SPIKE_LIMIT_C:
            self._accepted, self._pending = value, None
        elif self._pending is not None and abs(value - self._pending) <= SPIKE_LIMIT_C / 2:
            # Two samples agree on the new level: it is a real change.
            self._accepted, self._pending = value, None
        elif self._pending != value:
            self._pending = value
        return round(self._accepted, 1)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities = [
        PassiveLinkSensor(coordinator, description)
        for description in DESCRIPTIONS
        if description.key not in PREHEATER_KEYS | PI_KEYS
        or coordinator._auxiliary_client is not None
    ]
    entities = drop_mk1_only(coordinator, entities)
    if is_mk2(coordinator):
        entities.extend([Mk2AlarmSensor(coordinator), Mk2OperatingHoursSensor(coordinator)])
    if coordinator.controller_client is not None:
        entities.extend(
            ControllerStatusSensor(coordinator, key, name, unit, icon)
            for key, name, unit, icon in CONTROLLER_SENSOR_SPECS
        )
        entities.extend(
            ControllerEnergySensor(coordinator, key, name, icon)
            for key, name, icon in ENERGY_SENSOR_SPECS
        )
        entities.extend([
            BalanceStatusSensor(coordinator, "balance_duct_ratio_in_use", "Luftbalance kanalforhold i brug", "mdi:pipe"),
            BalanceStatusSensor(coordinator, "balance_heat_balance", "Luftbalance varmebalance", "mdi:heat-wave"),
        ])
        entities.extend(
            AfterheatLevelSensor(coordinator, *spec) for spec in AFTERHEAT_LEVEL_SENSOR_SPECS
        )
        entities.append(AfterheatControlSensor(coordinator))
        entities.extend(AfterheatModelSensor(coordinator, *spec) for spec in AFTERHEAT_MODEL_SPECS)
    async_add_entities_when_ready(entry, coordinator, async_add_entities, entities)
