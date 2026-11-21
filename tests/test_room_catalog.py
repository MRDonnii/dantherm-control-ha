import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

import pytest  # noqa: E402

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from hch_passivelink import room_catalog  # noqa: E402
from hch_passivelink.smart_coordinator import SmartPassiveLinkCoordinator  # noqa: E402

SETUP = [
    {"name": "Stue", "type": "normal", "priority": "high", "control": True, "enabled": True,
     "sensors": {"temperature": "sensor.stue_t", "humidity": "sensor.stue_rh", "co2": None, "pm25": None}},
    {"name": "Badeværelse", "type": "bathroom", "control": True,
     "sensors": {"humidity": "sensor.bad_rh"}},
    {"name": "Garage", "type": "excluded", "sensors": {"temperature": "sensor.garage_t"}},
    {"name": "Mads", "type": "normal", "enabled": False, "sensors": {"temperature": "sensor.mads_t"}},
    {"name": "Brændeovn", "type": "source", "control": True, "sensors": {"temperature": "sensor.ovn_t"}},
    {"name": "Tomt", "type": "normal", "sensors": {}},
]


def test_sources_follow_the_pi_setup_and_skip_excluded_rooms():
    sources = {source["name"]: source for source in room_catalog.sources_from_setup(SETUP)}
    assert set(sources) == {"Stue", "Badeværelse", "Brændeovn"}
    assert sources["Stue"]["temperature"] == "sensor.stue_t"
    assert sources["Stue"]["priority"] == "high"
    assert sources["Badeværelse"]["room_type"] == "bathroom"
    # Measuring rooms never control the ventilation.
    assert sources["Brændeovn"]["control"] is False


def test_legacy_payload_carries_entities_for_the_migration():
    payload = room_catalog.legacy_payload([
        {"name": "Køkken", "co2": "sensor.co2", "priority": "high", "room_type": "normal"},
        {"name": "Tom"},
    ])
    assert payload == {"Køkken": {"enabled": True, "control": True, "priority": "high",
                                  "room_type": "normal", "entities": {"co2": "sensor.co2"}}}


def test_room_sensor_is_ranked_before_appliance_and_smoothed_sensors():
    score = room_catalog.sensor_score
    room = score("sensor.temp_fugtighed_stue_temperature", "stue", "Stue", 2)
    stove = score("sensor.braendeovn_temp_fugtighed_temperature", "stue", "Stue", 2)
    smoothed = score("sensor.stue_stue_temperature_ema", "stue", "Stue", 1)
    feels = score("sensor.weathersense_feels_like_temperature_stuen", "stue", "Stue", 1)
    assert room > stove > smoothed
    assert room > feels


def _registry_hass(monkeypatch):
    from homeassistant.helpers import area_registry, device_registry, entity_registry

    def entry(entity_id, device_class, area_id=None, device_id=None, **extra):
        values = dict(entity_id=entity_id, domain=entity_id.split(".")[0], platform="zha",
                      disabled_by=None, hidden_by=None, entity_category=None, device_class=None,
                      original_device_class=device_class, area_id=area_id, device_id=device_id,
                      name=None, original_name=None)
        values.update(extra)
        return SimpleNamespace(**values)

    entities = [
        entry("sensor.stue_ema", "temperature", area_id="stue"),
        entry("sensor.temp_fugtighed_stue_temperature", "temperature", device_id="d1"),
        entry("sensor.temp_fugtighed_stue_humidity", "humidity", device_id="d1"),
        entry("sensor.sove_co2", "carbon_dioxide", area_id="sovevaerelse"),
        entry("sensor.sove_pm", "pm25", area_id="sovevaerelse"),
        entry("sensor.hidden", "temperature", area_id="stue", hidden_by="user"),
        entry("sensor.disabled", "temperature", area_id="stue", disabled_by="user"),
        entry("sensor.diag", "temperature", area_id="stue", entity_category="diagnostic"),
        entry("sensor.own", "temperature", area_id="stue", platform="hch_passivelink"),
        entry("sensor.power", "power", area_id="stue"),
        entry("binary_sensor.door", "temperature", area_id="stue"),
        entry("sensor.no_area", "temperature"),
    ]
    areas = [SimpleNamespace(id="stue", name="Stue"), SimpleNamespace(id="sovevaerelse", name="Soveværelse"),
             SimpleNamespace(id="garage", name="Garage")]
    devices = {"d1": SimpleNamespace(area_id="stue")}
    monkeypatch.setattr(area_registry, "async_get", lambda hass: SimpleNamespace(async_list_areas=lambda: areas))
    monkeypatch.setattr(device_registry, "async_get", lambda hass: SimpleNamespace(async_get=devices.get))
    monkeypatch.setattr(entity_registry, "async_get",
                        lambda hass: SimpleNamespace(entities={e.entity_id: e for e in entities}))
    states = {"sensor.temp_fugtighed_stue_temperature": SimpleNamespace(
        name="Stue temperatur", attributes={"unit_of_measurement": "°C"})}
    return SimpleNamespace(states=SimpleNamespace(get=states.get))


def test_catalog_lists_areas_with_their_climate_sensors(monkeypatch):
    catalog = room_catalog.build_catalog(_registry_hass(monkeypatch))
    by_area = {area["area_id"]: area for area in catalog}
    assert [area["area_name"] for area in catalog] == ["Garage", "Soveværelse", "Stue"]
    stue = [sensor["entity_id"] for sensor in by_area["stue"]["sensors"]]
    assert stue[0] == "sensor.temp_fugtighed_stue_temperature"  # via its device's area, ranked first
    assert set(stue) == {"sensor.temp_fugtighed_stue_temperature", "sensor.temp_fugtighed_stue_humidity",
                         "sensor.stue_ema"}
    assert by_area["stue"]["sensors"][0]["name"] == "Stue temperatur"
    assert by_area["stue"]["sensors"][0]["unit"] == "°C"
    assert {s["kind"] for s in by_area["sovevaerelse"]["sensors"]} == {"co2", "pm25"}
    assert by_area["garage"]["sensors"] == []


class FakeClient:
    def __init__(self, view, fail=False):
        self.view, self.fail, self.calls = view, fail, []

    async def async_send_catalog(self, areas, legacy=None):
        self.calls.append(("catalog", legacy))
        if self.fail:
            raise ConnectionError("old Pi")
        return self.view

    async def async_get_rooms(self):
        self.calls.append(("rooms",))
        if self.fail:
            raise ConnectionError("old Pi")
        return self.view

    async def async_send_rooms(self, rooms, valid_for):
        self.calls.append(("send", rooms))
        return {}


def _coordinator(client, legacy, monkeypatch):
    monkeypatch.setattr("hch_passivelink.smart_coordinator.build_catalog", lambda hass: [])
    states = {"sensor.stue_t": SimpleNamespace(state="21.5", attributes={}),
              "sensor.old_co2": SimpleNamespace(state="700", attributes={})}
    coordinator = SmartPassiveLinkCoordinator.__new__(SmartPassiveLinkCoordinator)
    coordinator.hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    coordinator.controller_client = client
    coordinator.room_sources = legacy
    coordinator.rooms_enabled = True
    coordinator.pi_rooms = None
    coordinator._room_entities = []
    coordinator._remove_room_listener = None
    coordinator._pi_migrated = False
    coordinator.smart_input_valid_for = 180
    monkeypatch.setattr(coordinator, "_update_room_listener", lambda: None)
    return coordinator


def test_room_values_follow_the_pi_setup_and_legacy_rooms_move_once(monkeypatch):
    legacy = [{"name": "Gammelt", "co2": "sensor.old_co2"}]
    client = FakeClient({"rooms": SETUP[:1], "migrated": True, "catalog_age_seconds": 5})
    coordinator = _coordinator(client, legacy, monkeypatch)
    # Before the Pi answers, the old rooms are still sent.
    assert list(coordinator._room_payload()) == ["Gammelt"]
    asyncio.run(coordinator._async_room_cycle(catalog=True))
    assert client.calls[0] == ("catalog", {"Gammelt": {"enabled": True, "control": True, "priority": "auto",
                                                       "room_type": "auto", "entities": {"co2": "sensor.old_co2"}}})
    sent = client.calls[-1][1]
    assert list(sent) == ["Stue"]
    assert sent["Stue"]["temperature"] == 21.5
    # Once taken over, the old rooms are never offered again.
    asyncio.run(coordinator.async_send_catalog())
    assert client.calls[-1] == ("catalog", None)


def test_old_pi_without_room_setup_keeps_the_old_rooms(monkeypatch):
    client = FakeClient(None, fail=True)
    coordinator = _coordinator(client, [{"name": "Gammelt", "co2": "sensor.old_co2"}], monkeypatch)
    asyncio.run(coordinator._async_room_cycle())
    assert client.calls[-1][0] == "send"
    assert list(client.calls[-1][1]) == ["Gammelt"]


def test_lost_catalogue_is_resent(monkeypatch):
    client = FakeClient({"rooms": [], "migrated": True, "catalog_age_seconds": None})
    coordinator = _coordinator(client, [], monkeypatch)
    asyncio.run(coordinator.async_refresh_room_setup())
    assert [call[0] for call in client.calls] == ["rooms", "catalog"]


def test_only_dmi_current_air_values_count_as_weather_sensors():
    from custom_components.hch_passivelink.room_catalog import _is_weather_sensor
    assert _is_weather_sensor("dmi_vejr", "sensor.dmi_vejr_temperatur", "temperature")
    assert _is_weather_sensor("dmi_vejr", "sensor.dmi_vejr_luftfugtighed", "humidity")
    assert not _is_weather_sensor("dmi_vejr", "sensor.dmi_vejr_graestemperatur", "temperature")
    assert not _is_weather_sensor("dmi_vejr", "sensor.dmi_vejr_hojeste_temperatur_i_dag", "temperature")
    assert not _is_weather_sensor("other", "sensor.dmi_vejr_temperatur", "temperature")



def test_presence_sensors_are_not_offered(monkeypatch):
    from homeassistant.helpers import area_registry, device_registry, entity_registry

    def entry(entity_id, device_class, area_id="stue"):
        return SimpleNamespace(entity_id=entity_id, domain=entity_id.split(".")[0], platform="esphome",
                               disabled_by=None, hidden_by=None, entity_category=None, device_class=None,
                               original_device_class=device_class, area_id=area_id, device_id=None,
                               name=None, original_name=None)

    entities = [entry("binary_sensor.hps_stuen_presence", "occupancy"), entry("sensor.stue_t", "temperature")]
    monkeypatch.setattr(area_registry, "async_get", lambda hass: SimpleNamespace(
        async_list_areas=lambda: [SimpleNamespace(id="stue", name="Stue")]))
    monkeypatch.setattr(device_registry, "async_get", lambda hass: SimpleNamespace(async_get=lambda _id: None))
    monkeypatch.setattr(entity_registry, "async_get",
                        lambda hass: SimpleNamespace(entities={e.entity_id: e for e in entities}))
    catalog = room_catalog.build_catalog(SimpleNamespace(states=SimpleNamespace(get=lambda _e: None)))
    assert [s["entity_id"] for s in catalog[0]["sensors"]] == ["sensor.stue_t"]


def test_presence_left_in_a_pi_setup_is_ignored():
    # A room setup from gateway 1.12.0/1.12.1 can still carry a presence sensor.
    sources = room_catalog.sources_from_setup([
        {"name": "Kontor", "type": "normal", "sensors": {"presence": "binary_sensor.hps_kontor_presence"}},
        {"name": "Stue", "type": "normal", "sensors": {"temperature": "sensor.t", "presence": "binary_sensor.p"}},
    ])
    assert [source["name"] for source in sources] == ["Stue"]
    assert "presence" not in sources[0]
    assert room_catalog.tracked_entities(sources) == ["sensor.t"]
