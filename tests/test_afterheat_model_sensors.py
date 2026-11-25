"""The afterheat model sensors (Pi block `afterheat_ha`): created from 1.13.0-beta.11, silent before."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass  # noqa: E402
from test_afterheat_control import Coordinator, created, setup_platform  # noqa: E402

from hch_passivelink import sensor  # noqa: E402

BLOCK = {
    "algorithm": "mpc", "label": "Forudseende (MPC)", "why": "Ser 12 min frem", "model_state": "ready",
    "samples": 900, "step_k": 4.2, "step_next_k": 1.1, "tau_up_s": 110, "tau_down_s": 240, "dead_s": 60,
    "offset_k": -0.3, "refit": None, "air_1min_c": 22.1, "air_10min_c": 22.4, "air_lowest_c": 21.8,
    "prediction_error_k": 0.12, "heat_kw": 1.35, "energy_today_kwh": 3.21, "pid": None,
    "performance": {"1h": {}, "24h": {"under_percent": 2.5, "mean_error_k": 0.15, "changes_per_hour": 4.0}},
}
@pytest.fixture(autouse=True)
def event_loop_for_entities():
    """Entities read the current loop when built; asyncio.run in setup_platform clears it afterwards."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    loop.close()


PREFIX = "afterheat_ha_"
MODEL = {f"{PREFIX}{spec[0]}" for spec in sensor.AFTERHEAT_MODEL_SPECS}


def entities_for(state):
    return {key: entity for key, entity in setup_platform(sensor, Coordinator(state)).items() if key in MODEL}


def test_older_gateway_without_the_block_creates_nothing():
    assert entities_for({"afterheat_control_mode": "pi"}) == {}


def test_all_sixteen_sensors_with_units_and_classes():
    found = entities_for({"afterheat_control_mode": "pi", "afterheat_ha": BLOCK})
    assert set(found) == MODEL and len(found) == 16
    assert found[PREFIX + "algorithm"].native_value == "Forudseende (MPC)"
    assert found[PREFIX + "algorithm"].extra_state_attributes == {"hvorfor": "Ser 12 min frem"}
    assert found[PREFIX + "model_state"].native_value == "Klar"
    power, energy = found[PREFIX + "power"], found[PREFIX + "energy_today"]
    assert (power.native_value, power.native_unit_of_measurement, power.device_class) == (1.35, "kW", SensorDeviceClass.POWER)
    assert energy.native_value == 3.21 and energy.device_class == SensorDeviceClass.ENERGY
    assert energy.state_class == SensorStateClass.TOTAL_INCREASING and energy.native_unit_of_measurement == "kWh"
    assert found[PREFIX + "air_10min"].native_value == 22.4 and found[PREFIX + "air_10min"].native_unit_of_measurement == "°C"
    assert found[PREFIX + "under_floor_24h"].native_value == 2.5
    assert found[PREFIX + "changes_24h"].native_value == 4.0
    assert all(entity.entity_category == "diagnostic" for entity in found.values())
    assert all(entity.unique_id.startswith("hch5_pi_controller_afterheat_ha_") for entity in found.values())
    assert found[PREFIX + "step_k"].name == "Eftervarme varme pr. trin"


def test_none_values_are_unavailable_without_errors():
    empty = {**BLOCK, "heat_kw": None, "air_1min_c": None, "label": None, "performance": {"24h": {}}}
    found = entities_for({"afterheat_control_mode": "pi", "afterheat_ha": empty})
    assert not found[PREFIX + "power"].available and found[PREFIX + "power"].native_value is None
    assert not found[PREFIX + "air_1min"].available
    assert not found[PREFIX + "algorithm"].available
    assert not found[PREFIX + "under_floor_24h"].available
    assert found[PREFIX + "energy_today"].available
    # The block vanishing again (gateway downgraded): unavailable, no exception.
    coordinator = Coordinator({"afterheat_ha": BLOCK})
    entity = {k: e for k, e in setup_platform(sensor, coordinator).items() if k in MODEL}[PREFIX + "power"]
    coordinator.controller_state = {}
    assert not entity.available and entity.native_value is None
