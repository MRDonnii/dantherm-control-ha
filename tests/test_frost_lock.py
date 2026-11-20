"""Afterheat frost lock: HA mirrors the Pi's lock and passes its refusals on to the user."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from homeassistant.components.climate.const import HVACMode  # noqa: E402
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError  # noqa: E402
from hch_passivelink import binary_sensor, button  # noqa: E402
from hch_passivelink.climate import AfterheatClimate, format_floor  # noqa: E402
from hch_passivelink.controller_api import ControllerApiClient, ControllerRejectedError  # noqa: E402

LOCKED = {
    "afterheat_frost_lock": True,
    "afterheat_frost_lock_reason": "Frost: ude -1,6 °C (grænse 3 °C)",
    "afterheat_frost_lock_floor": 18.0,
    "afterheat_frost_lock_enforced": True,
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


def climate(state, **kwargs):
    return AfterheatClimate(Coordinator({"afterheat_setpoint": 16, "afterheat_enabled": True, **state}, **kwargs))


def test_older_firmware_keeps_heat_and_off():
    entity = climate({})
    assert entity.hvac_modes == [HVACMode.HEAT, HVACMode.OFF]
    assert entity.min_temp == 10
    assert entity.extra_state_attributes is None
    asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))
    asyncio.run(entity.async_set_temperature(temperature=12))
    assert entity.coordinator.commands == [{"afterheat_enabled": False}, {"afterheat_setpoint": 12}]


def test_unlocked_pi_behaves_like_before():
    entity = climate({**LOCKED, "afterheat_frost_lock": False, "afterheat_frost_lock_reason": None})
    assert entity.hvac_modes == [HVACMode.HEAT, HVACMode.OFF]
    assert entity.min_temp == 10
    assert entity.extra_state_attributes["frost_lock"] is False
    asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))
    assert entity.coordinator.commands == [{"afterheat_enabled": False}]


def test_locked_offers_only_heat_from_the_floor():
    entity = climate({**LOCKED, "afterheat_enabled": False})
    assert entity.hvac_modes == [HVACMode.HEAT]
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.min_temp == 18.0
    # The stored 16 °C is shown as the 18 °C the Pi actually runs.
    target = entity.native_target_temperature if hasattr(entity, "native_target_temperature") else entity.target_temperature
    assert target == 18.0
    assert entity.extra_state_attributes["frost_lock_reason"].startswith("Frost:")


def test_locked_refuses_off_and_low_setpoints():
    entity = climate(LOCKED)
    with pytest.raises(ServiceValidationError) as off:
        asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))
    assert off.value.translation_key == "afterheat_frost_locked"
    with pytest.raises(ServiceValidationError) as low:
        asyncio.run(entity.async_set_temperature(temperature=15))
    assert low.value.translation_key == "afterheat_frost_floor"
    assert low.value.translation_placeholders == {"floor": "18"}
    assert entity.coordinator.commands == []
    asyncio.run(entity.async_set_temperature(temperature=18))
    asyncio.run(entity.async_set_hvac_mode(HVACMode.HEAT))
    assert entity.coordinator.commands == [{"afterheat_setpoint": 18}, {"afterheat_enabled": True}]


def test_fractional_floor_is_not_rounded_below():
    entity = climate({**LOCKED, "afterheat_frost_lock_floor": 18.5})
    assert entity.min_temp == 18.5
    asyncio.run(entity.async_set_temperature(temperature=18.5))
    assert entity.coordinator.commands == [{"afterheat_setpoint": 19}]
    with pytest.raises(ServiceValidationError) as low:
        asyncio.run(entity.async_set_temperature(temperature=18.2))
    assert low.value.translation_placeholders == {"floor": "18,5"}


def test_floor_uses_a_danish_decimal_comma():
    assert format_floor(18.0) == "18"
    assert format_floor(18.5) == "18,5"


def test_pi_refusal_text_reaches_the_user():
    text = "Frostsikring: eftervarmen kan ikke slukkes, mens det fryser."
    entity = climate({}, reject=ControllerRejectedError(text, 400))
    with pytest.raises(ServiceValidationError) as error:
        asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))
    assert str(error.value) == text

    entity = climate({}, reject=ConnectionError("timeout"))
    with pytest.raises(HomeAssistantError) as error:
        asyncio.run(entity.async_set_hvac_mode(HVACMode.OFF))
    assert not isinstance(error.value, ServiceValidationError)


def cool_buttons(coordinator):
    entities = []
    asyncio.run(button.async_setup_entry(None, SimpleNamespace(runtime_data=coordinator), entities.extend))
    return {entity.minutes: entity for entity in entities if isinstance(entity, button.CoolBoostButton)}


def test_cool_buttons_are_blocked_while_locked_but_stop_stays():
    coordinator = Coordinator({"cool_boost_remaining_seconds": 0})
    buttons = cool_buttons(coordinator)
    assert all(entity.available for entity in buttons.values())

    coordinator.publish_controller(**LOCKED)
    assert [minutes for minutes, entity in buttons.items() if entity.available] == [0]
    with pytest.raises(ServiceValidationError):
        asyncio.run(buttons[30].async_press())
    asyncio.run(buttons[0].async_press())
    assert coordinator.commands == [{"cool_boost_minutes": 0}]


def test_frost_lock_sensor_waits_for_a_pi_that_knows_it():
    coordinator = Coordinator()
    batches = []
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=lambda unload: None)
    asyncio.run(binary_sensor.async_setup_entry(None, entry, lambda entities: batches.append(list(entities))))
    created = [entity for batch in batches for entity in batch]
    assert "afterheat_frost_lock" not in {entity.key for entity in created}

    coordinator.publish_controller(**LOCKED)
    created = [entity for batch in batches for entity in batch]
    sensor = next(entity for entity in created if entity.key == "afterheat_frost_lock")
    assert sensor.is_on is True
    assert sensor.icon == "mdi:snowflake-lock"
    assert sensor.extra_state_attributes == {
        "reason": LOCKED["afterheat_frost_lock_reason"], "floor": 18.0, "enforced": True,
    }


class Response:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def client_for(status, body):
    session = SimpleNamespace(request=lambda *args, **kwargs: Response(status, body))
    return ControllerApiClient(session, "127.0.0.1", 8080, "token", lambda state: None)


def test_client_marks_pi_refusals():
    with pytest.raises(ControllerRejectedError) as error:
        asyncio.run(client_for(400, {"error": "Frostsikring: Køl kan ikke startes, mens det fryser."}).async_command({"cool_boost_minutes": 30}))
    assert str(error.value) == "Frostsikring: Køl kan ikke startes, mens det fryser."
    assert error.value.status == 400

    with pytest.raises(ConnectionError) as error:
        asyncio.run(client_for(500, {"error": "boom"}).async_command({}))
    assert not isinstance(error.value, ControllerRejectedError)

    with pytest.raises(ConnectionError) as error:
        asyncio.run(client_for(502, ValueError("not json")).async_command({}))
    assert str(error.value) == "Controller HTTP 502"
