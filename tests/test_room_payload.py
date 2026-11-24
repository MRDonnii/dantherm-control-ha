import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

import pytest  # noqa: E402

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from hch_passivelink.smart_coordinator import SmartPassiveLinkCoordinator  # noqa: E402


def _payload(sources, values):
    states = {
        entity_id: SimpleNamespace(state=value, attributes={})
        for entity_id, value in values.items()
    }
    fake = SimpleNamespace(
        room_sources=sources,
        hass=SimpleNamespace(states=SimpleNamespace(get=states.get)),
    )
    return SmartPassiveLinkCoordinator._room_payload(fake)


def test_room_payload_matches_pi_contract():
    rooms = _payload(
        [
            {"name": "Køkken", "co2": "sensor.co2", "pm25": "sensor.pm25",
             "humidity": "sensor.rh", "priority": "high", "room_type": "normal"},
            {"name": "Bad", "humidity": "sensor.bath", "room_type": "bathroom"},
        ],
        {"sensor.co2": "668", "sensor.pm25": "42", "sensor.rh": "unavailable",
         "sensor.bath": "71"},
    )
    assert rooms["Køkken"]["co2"] == 668
    assert rooms["Køkken"]["pm25"] == 42
    assert "humidity" not in rooms["Køkken"]
    assert rooms["Køkken"]["room_type"] == "normal"
    assert rooms["Køkken"]["entities"] == {"co2": "sensor.co2", "pm25": "sensor.pm25"}
    assert rooms["Bad"]["room_type"] == "bathroom"


def test_out_of_range_values_are_skipped_not_sent():
    # One bad value would make the Pi reject the whole message with HTTP 400.
    rooms = _payload(
        [{"name": "Stue", "co2": "sensor.co2", "pm25": "sensor.pm25"}],
        {"sensor.co2": "5", "sensor.pm25": "nan"},
    )
    assert rooms == {}



def test_temperature_age_is_sent_and_presence_is_not():
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    states = {
        "sensor.stue_t": SimpleNamespace(state="21.4", attributes={},
                                         last_reported=dt_util.utcnow() - timedelta(minutes=20)),
        "binary_sensor.stue_p": SimpleNamespace(state="on", attributes={}),
    }
    fake = SimpleNamespace(
        room_sources=[
            {"name": "Stue", "temperature": "sensor.stue_t", "presence": "binary_sensor.stue_p"},
            {"name": "Kontor", "presence": "binary_sensor.stue_p"},
        ],
        hass=SimpleNamespace(states=SimpleNamespace(get=states.get)),
    )
    rooms = SmartPassiveLinkCoordinator._room_payload(fake)
    assert 1195 <= rooms["Stue"]["temperature_age_s"] <= 1210
    assert "presence" not in rooms["Stue"]
    assert "Kontor" not in rooms


def test_all_sensor_ages_are_sent_even_when_forwarded_values_are_unchanged():
    from datetime import timedelta
    from homeassistant.util import dt as dt_util
    kinds=('temperature','humidity','co2','pm25')
    values=('22','55','1200','35')
    states={f'sensor.{kind}':SimpleNamespace(state=value,attributes={},last_reported=dt_util.utcnow()-timedelta(hours=4))
            for kind,value in zip(kinds,values)}
    fake=SimpleNamespace(room_sources=[{'name':'Stue',**{kind:f'sensor.{kind}' for kind in kinds}}],
                         hass=SimpleNamespace(states=SimpleNamespace(get=states.get)))
    payload=SmartPassiveLinkCoordinator._room_payload(fake)['Stue']
    assert payload['measurement_age_version']==1
    for kind in kinds:
        assert 14399 <= payload[f'{kind}_age_s'] <= 14410
    # A second forwarding operation does not replace the original report time.
    again=SmartPassiveLinkCoordinator._room_payload(fake)['Stue']
    assert again['co2_age_s']>=payload['co2_age_s']
