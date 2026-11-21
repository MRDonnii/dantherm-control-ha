import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

import pytest  # noqa: E402

pytest.importorskip("homeassistant")
from hch_passivelink.forecast_source import forecast_signals, number, resolve_entity  # noqa: E402


def state(value):
    return SimpleNamespace(state=value)


def test_entity_resolution_default_cleared_and_chosen():
    exists = {"sensor.dmi"}.__contains__
    assert resolve_entity({}, "k", "sensor.dmi", exists) == "sensor.dmi"
    assert resolve_entity({}, "k", "sensor.other", exists) is None
    assert resolve_entity({"k": ""}, "k", "sensor.dmi", exists) is None
    assert resolve_entity({"k": "sensor.mine"}, "k", "sensor.dmi", exists) == "sensor.mine"


def test_numbers_outside_the_range_are_unknown():
    assert number(state("26.3")) == 26.3
    assert number(state("unknown")) is None
    assert number(state("99")) is None
    assert number(None) is None


def test_signals_for_the_pi_carry_only_the_forecast():
    states = {"sensor.f": state("26.3"), "sensor.t": state("unknown"), "zone.home": state("0"),
              "alarm_control_panel.a": state("armed_away")}
    assert forecast_signals(states.get, "sensor.f", "sensor.t") == {"forecast_high_c": 26.3, "today_high_c": None}
    assert forecast_signals(states.get, None, None) == {}
