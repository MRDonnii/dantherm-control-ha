"""Afterheat level regulation (HCH5 Control 1.9.0+): sensors, mode select and climate attributes."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from homeassistant.exceptions import ServiceValidationError  # noqa: E402
from hch_passivelink import select, sensor  # noqa: E402
from hch_passivelink.climate import AfterheatClimate  # noqa: E402
from hch_passivelink.controller_api import ControllerRejectedError  # noqa: E402

TRANSLATIONS = Path(__file__).parents[1] / "custom_components" / "hch_passivelink" / "translations"

PI_STATE = {
    "afterheat_setpoint": 21,
    "afterheat_enabled": True,
    "afterheat_control_mode": "pi",
    "afterheat_control_active": "pi",
    "afterheat_level": 3,
    "afterheat_level_wanted": 3.4,
    "afterheat_level_target": 4,
    "afterheat_level_max": 10,
    "afterheat_next_action": "up",
    "afterheat_next_tick_seconds": 12,
    "afterheat_target": 21.5,
    "afterheat_hac1_setpoint": 22,
    "afterheat_control_error": -0.62,
    "afterheat_control_reason": "Går et trin op: 20,9 °C under mål",
}

AFTERHEAT_KEYS = {
    "afterheat_level", "afterheat_level_wanted", "afterheat_target",
    "afterheat_control_reason", "afterheat_control_mode",
}


class Coordinator:
    def __init__(self, state=None, *, reject=None):
        self.data = {}
        self.controller_state = dict(state or {})
        self.controller_client = SimpleNamespace(connected=True)
        self._auxiliary_client = None
        self.client = None
        self.available = True
        self.listeners = []
        self.commands = []
        self.reject = reject

    def async_add_listener(self, update_callback, context=None):
        self.listeners.append(update_callback)
        return lambda: self.listeners.remove(update_callback)

    def publish_controller(self, **values):
        self.controller_state = {**self.controller_state, **values}
        for update_callback in list(self.listeners):
            update_callback()

    async def async_controller_command(self, patch):
        if self.reject is not None:
            raise self.reject
        self.commands.append(patch)
        return self.controller_state


def setup_platform(module, coordinator):
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=lambda unload: None)
    batches = []
    asyncio.run(module.async_setup_entry(None, entry, lambda entities: batches.append(list(entities))))
    return {entity.key: entity for batch in batches for entity in batch}


def created(coordinator):
    return {**setup_platform(sensor, coordinator), **setup_platform(select, coordinator)}


def test_older_firmware_creates_no_regulation_entities_and_keeps_climate_attributes():
    coordinator = Coordinator({"afterheat_setpoint": 21, "afterheat_enabled": True})
    entities = created(coordinator)
    assert not AFTERHEAT_KEYS & set(entities)
    assert "mode" in entities  # the other selects still come at once
    climate = AfterheatClimate(coordinator)
    assert climate.extra_state_attributes is None

    frost = Coordinator({"afterheat_setpoint": 21, "afterheat_frost_lock": False, "afterheat_frost_lock_floor": 18})
    attributes = AfterheatClimate(frost).extra_state_attributes
    assert attributes["frost_lock"] is False
    assert "hac1_level" not in attributes


def test_entities_appear_when_the_pi_first_reports_regulation():
    coordinator = Coordinator({"afterheat_setpoint": 21})
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=lambda unload: None)
    batches = []
    for module in (sensor, select):
        asyncio.run(module.async_setup_entry(None, entry, lambda entities: batches.append(list(entities))))
    keys = lambda: [entity.key for batch in batches for entity in batch]  # noqa: E731
    assert not AFTERHEAT_KEYS & set(keys())

    # Native mode right after start: level not read yet, still the entities come.
    coordinator.publish_controller(afterheat_control_mode="native", afterheat_level=None)
    assert AFTERHEAT_KEYS <= set(keys())
    assert len(keys()) == len(set(keys()))


def test_level_sensors_follow_the_pi():
    entities = created(Coordinator(PI_STATE))
    level = entities["afterheat_level"]
    assert level.available
    assert level.native_value == 3
    assert level.extra_state_attributes == {"level_max": 10, "level_target": 4}
    assert entities["afterheat_level_wanted"].native_value == 3.4
    target = entities["afterheat_target"]
    assert target.native_value == 21.5
    assert target.native_unit_of_measurement == "°C"
    assert target.device_class == "temperature"
    assert entities["afterheat_level"].name == "Eftervarme trin"
    assert entities["afterheat_level_wanted"].name == "Eftervarme mål-trin"
    assert target.name == "Eftervarme mål"


def test_stale_level_is_unknown_not_unavailable():
    entities = created(Coordinator({**PI_STATE, "afterheat_level": None, "afterheat_level_wanted": True}))
    level = entities["afterheat_level"]
    assert level.available
    assert level.native_value is None
    assert entities["afterheat_level_wanted"].native_value is None  # a bool is no number


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "3"])
def test_non_finite_or_text_level_is_unknown(bad):
    entities = created(Coordinator({**PI_STATE, "afterheat_level": bad, "afterheat_target": bad}))
    assert entities["afterheat_level"].native_value is None
    assert entities["afterheat_target"].native_value is None


def test_control_sensor_carries_reason_and_details():
    control = created(Coordinator(PI_STATE))["afterheat_control_reason"]
    assert control.name == "Eftervarme styring"
    assert control.native_value == "Går et trin op: 20,9 °C under mål"
    assert control.extra_state_attributes == {
        "control_mode": "pi",
        "control_active": "pi",
        "next_action": "up",
        "next_tick_seconds": 12,
        "hac1_setpoint": 22,
        "level_target": 4,
        "control_error": -0.62,
    }
    assert {"next_tick_seconds", "control_error"} <= control._unrecorded_attributes


def test_control_sensor_unavailable_when_pi_disconnects():
    coordinator = Coordinator(PI_STATE)
    control = created(coordinator)["afterheat_control_reason"]
    coordinator.controller_client.connected = False
    assert not control.available


def test_mode_select_maps_pi_and_hac1():
    coordinator = Coordinator(PI_STATE)
    mode = created(coordinator)["afterheat_control_mode"]
    assert mode.name == "Eftervarme regulering"
    assert mode.options == ["pi", "native"]
    assert mode.current_option == "pi"
    asyncio.run(mode.async_select_option("native"))
    assert coordinator.commands == [{"afterheat_control_mode": "native"}]

    coordinator.publish_controller(afterheat_control_mode="weird")
    assert mode.current_option is None
    with pytest.raises(ValueError):
        asyncio.run(mode.async_select_option("auto"))


def test_mode_select_passes_the_pi_refusal_on():
    coordinator = Coordinator(PI_STATE, reject=ControllerRejectedError("Kun administrator kan ændre reguleringen", 403))
    mode = created(coordinator)["afterheat_control_mode"]
    with pytest.raises(ServiceValidationError, match="Kun administrator"):
        asyncio.run(mode.async_select_option("pi"))


def test_climate_exposes_level_for_the_card():
    attributes = AfterheatClimate(Coordinator({**PI_STATE, "afterheat_frost_lock": True, "afterheat_frost_lock_floor": 18})).extra_state_attributes
    assert attributes["frost_lock"] is True
    assert attributes["hac1_level"] == 3
    assert attributes["level_wanted"] == 3.4
    assert attributes["level_target"] == 4
    assert attributes["level_max"] == 10
    assert attributes["control_active"] == "pi"
    assert attributes["control_reason"] == "Går et trin op: 20,9 °C under mål"
    assert attributes["control_target"] == 21.5
    # The live card attributes are recorded by their own sensors, not on every poll here.
    assert {"hac1_level", "level_wanted", "control_reason"} <= AfterheatClimate._unrecorded_attributes


def test_translations_name_the_new_entities_and_options():
    for language, pi_label, hac1_label in (("da", "Pi", "HAC1"), ("en", "Pi", "HAC1")):
        entity = json.loads((TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8"))["entity"]
        assert entity["select"]["afterheat_control_mode"]["state"] == {"pi": pi_label, "native": hac1_label}
        for key in ("afterheat_level", "afterheat_level_wanted", "afterheat_target", "afterheat_control_reason"):
            assert entity["sensor"][key]["name"]
