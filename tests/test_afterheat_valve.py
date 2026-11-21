"""Afterheat valve opening: HAC1 level 0..10 times 10 % (0-10 V to the valve actuator)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from homeassistant.const import PERCENTAGE  # noqa: E402
from hch_passivelink import sensor  # noqa: E402
from hch_passivelink.climate import AfterheatClimate  # noqa: E402
from hch_passivelink.controller_entity import afterheat_valve_percent  # noqa: E402
from hch_passivelink.sensor import ControllerStatusSensor  # noqa: E402

KEY = "actual_afterheat_valve_percent"


class Coordinator:
    def __init__(self, state=None):
        self.data = {}
        self.controller_state = dict(state or {})
        self.controller_client = SimpleNamespace(connected=True)
        self._auxiliary_client = None
        self.client = None
        self.available = True
        self.listeners = []

    def async_add_listener(self, update_callback, context=None):
        self.listeners.append(update_callback)
        return lambda: self.listeners.remove(update_callback)

    def publish(self, state):
        # Each controller poll replaces the state with a new dict.
        self.controller_state = dict(state)
        for update_callback in list(self.listeners):
            update_callback()


def _valve(state):
    coordinator = Coordinator(state)
    return ControllerStatusSensor(coordinator, KEY, "Eftervarme ventil", PERCENTAGE, "mdi:valve"), coordinator


@pytest.mark.parametrize(("state", "expected"), [
    ({"actual_afterheat_level": 0}, 0),
    ({"actual_afterheat_level": 3}, 30),
    ({"actual_afterheat_level": 10}, 100),
    ({"actual_afterheat_level": 14}, 100),
    ({"actual_afterheat_level": -1}, 0),
    # Only the Pi controller's own level (older gateway without the snapshot key).
    ({"afterheat_level": 4}, 40),
    # The gateway's own value wins, also when it says stale (None).
    ({KEY: 70, "actual_afterheat_level": 3}, 70),
    ({KEY: None, "actual_afterheat_level": 3}, None),
    ({"actual_afterheat_level": None, "afterheat_level": None}, None),
    ({"actual_afterheat_level": True}, None),
    ({"actual_afterheat_level": float("nan")}, None),
    ({}, None),
])
def test_valve_percent_is_level_times_ten(state, expected):
    assert afterheat_valve_percent(state) == expected


def test_register_209_on_off_flag_is_not_the_opening():
    # afterheat_active (register 209) is 16 at any level >= 1; it must not become 100 %.
    assert afterheat_valve_percent({"afterheat_active": True, "actual_afterheat_level": 2}) == 20


@pytest.mark.parametrize("mode", ["native", "pi"])
def test_sensor_follows_the_level_in_both_modes(mode):
    entity, coordinator = _valve({"afterheat_control_mode": mode, "actual_afterheat_level": 2})
    assert entity.available and entity.has_data
    assert entity.native_value == 20
    assert entity.native_unit_of_measurement == PERCENTAGE
    coordinator.publish({"afterheat_control_mode": mode, "actual_afterheat_level": 5})
    assert entity.native_value == 50
    # A stale level is unknown, not unavailable.
    coordinator.publish({"afterheat_control_mode": mode, "actual_afterheat_level": None})
    assert entity.available and entity.native_value is None


def test_sensor_unavailable_without_level_or_connection():
    entity, coordinator = _valve({"afterheat_setpoint": 21})
    assert not entity.available and not entity.has_data
    coordinator.controller_client = SimpleNamespace(connected=False)
    coordinator.publish({"actual_afterheat_level": 3})
    assert not entity.available


def test_sensor_is_created_from_the_level_of_an_older_gateway():
    coordinator = Coordinator({"afterheat_setpoint": 21})
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=lambda unload: None)
    batches = []
    asyncio.run(sensor.async_setup_entry(None, entry, lambda entities: batches.append(list(entities))))
    keys = lambda: [entity.key for batch in batches for entity in batch]  # noqa: E731
    assert KEY not in keys()
    coordinator.publish({"afterheat_setpoint": 21, "actual_afterheat_level": 3})
    assert keys().count(KEY) == 1
    valve = next(entity for batch in batches for entity in batch if entity.key == KEY)
    assert valve.native_value == 30


def test_climate_carries_valve_and_hac1_setpoint_for_the_card():
    state = {
        "afterheat_setpoint": 21, "afterheat_control_mode": "pi", "afterheat_control_active": "pi",
        "afterheat_level": 2, "afterheat_target": 21.0, "afterheat_hac1_setpoint": 26,
        "actual_afterheat_level": 2, KEY: 20,
    }
    attributes = AfterheatClimate(Coordinator(state)).extra_state_attributes
    assert attributes["valve_percent"] == 20
    assert attributes["hac1_setpoint"] == 26
    assert attributes["control_target"] == 21.0
    assert {"valve_percent", "hac1_setpoint"} <= AfterheatClimate._unrecorded_attributes
