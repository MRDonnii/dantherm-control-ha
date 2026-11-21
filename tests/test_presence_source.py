import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

import pytest  # noqa: E402

pytest.importorskip("homeassistant")
from hch_passivelink.presence_source import (  # noqa: E402
    climate_signals,
    home_auto,
    home_from_entity,
    resolve_entity,
)


def state(value):
    return SimpleNamespace(state=value)


def test_auto_home_away_from_alarm_then_zone():
    assert home_auto([("alarm_control_panel.a", state("disarmed"))], state("3")) == (True, "3 personer hjemme")
    assert home_auto([("alarm_control_panel.a", state("armed_away"))], state("3"))[0] is False
    assert home_auto([("alarm_control_panel.a", state("armed_vacation"))], None)[0] is False
    assert home_auto([("alarm_control_panel.a", state("armed_home"))], state("0")) == (False, "0 personer hjemme")
    assert home_auto([], None)[0] is None


def test_chosen_entity_by_domain():
    assert home_from_entity("alarm_control_panel.x", state("armed_away"))[0] is False
    assert home_from_entity("alarm_control_panel.x", state("armed_night"))[0] is True
    assert home_from_entity("zone.home", state("0"))[0] is False
    assert home_from_entity("person.john", state("not_home"))[0] is False
    assert home_from_entity("group.family", state("home"))[0] is True
    assert home_from_entity("input_boolean.hjemme", state("off"))[0] is False
    assert home_from_entity("binary_sensor.someone", state("unavailable"))[0] is None
    assert home_from_entity("binary_sensor.someone", None)[0] is None


def test_entity_resolution_default_cleared_and_chosen():
    exists = {"sensor.dmi"}.__contains__
    assert resolve_entity({}, "k", "sensor.dmi", exists) == "sensor.dmi"
    assert resolve_entity({}, "k", "sensor.other", exists) is None
    assert resolve_entity({"k": ""}, "k", "sensor.dmi", exists) is None
    assert resolve_entity({"k": "sensor.mine"}, "k", "sensor.dmi", exists) == "sensor.mine"


def test_signals_for_the_pi():
    states = {"sensor.f": state("26.3"), "sensor.t": state("unknown"), "zone.home": state("0"),
              "alarm_control_panel.a": state("disarmed")}
    signals = climate_signals(states.get, "sensor.f", "sensor.t", None, ["alarm_control_panel.a"])
    assert signals == {"forecast_high_c": 26.3, "today_high_c": None, "home": False, "home_reason": "0 personer hjemme"}
    assert "forecast_high_c" not in climate_signals(states.get, None, None, "zone.home", [])
