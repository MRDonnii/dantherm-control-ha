"""Phase 4: connection hardening, reauth, age memory, orphan cleanup, dew point."""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

import pytest  # noqa: E402

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from hch_passivelink import client as client_mod  # noqa: E402
from hch_passivelink import entity as entity_mod  # noqa: E402
from hch_passivelink import runtime_memory as memory_mod  # noqa: E402
from hch_passivelink import smart_coordinator as sc  # noqa: E402
from hch_passivelink.const import entry_unique_id  # noqa: E402
from hch_passivelink.controller_api import ControllerApiClient, ControllerRejectedError  # noqa: E402
from hch_passivelink.weather_source import dew_point_c, weather_payload  # noqa: E402


def run(coro):
    return asyncio.run(coro)


# ha-int-5: a reset connection must not end the stream task
def test_tcp_stream_survives_reset_in_wait_closed(monkeypatch):
    attempts = []

    class Reader:
        async def read(self, _n):
            raise ConnectionResetError("reset")

    class Writer:
        def close(self):
            pass

        async def wait_closed(self):
            raise ConnectionResetError("reset again")

    async def fake_open(host, port):
        attempts.append(1)
        if len(attempts) >= 2:
            client._stopped = True
        return Reader(), Writer()

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(client_mod.asyncio, "open_connection", fake_open)
    monkeypatch.setattr(client_mod.asyncio, "sleep", no_sleep)
    client = client_mod.PassiveLinkClient("h", 1, lambda _: None)
    run(client.run())  # must return normally, after reconnecting once
    assert len(attempts) == 2


def test_tcp_stream_survives_unexpected_error(monkeypatch):
    attempts = []

    async def fake_open(host, port):
        attempts.append(1)
        if len(attempts) >= 2:
            client._stopped = True
        raise ValueError("boom")

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(client_mod.asyncio, "open_connection", fake_open)
    monkeypatch.setattr(client_mod.asyncio, "sleep", no_sleep)
    client = client_mod.PassiveLinkClient("h", 1, lambda _: None)
    run(client.run())
    assert len(attempts) == 2


# ha-int-6 / -7 / -10: the controller poll loop
def _api(update=lambda _: None):
    return ControllerApiClient(SimpleNamespace(), "h", 1, "t", update, poll_seconds=2)


def test_controller_loop_survives_a_bad_update_and_goes_unavailable(monkeypatch):
    calls = []

    def bad_update(_state):
        calls.append(1)
        if len(calls) >= 2:
            api._stopped = True
        raise ValueError("bad value")

    api = _api(bad_update)

    async def fake_json(self, method, path, payload=None):
        return {"ok": 1}

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(ControllerApiClient, "_json", fake_json)
    monkeypatch.setattr("hch_passivelink.controller_api.asyncio.sleep", no_sleep)
    run(api.run())
    assert len(calls) == 2 and api.connected is False


def test_controller_freshness_and_reconnect_callback():
    api = _api()
    seen = []
    api.on_connected = lambda: seen.append("up")
    assert not api.is_fresh
    api._mark_ok()
    api._mark_ok()
    assert api.is_fresh and seen == ["up"]  # only on the change from away to connected
    api.last_ok -= 1000
    assert not api.is_fresh
    api.connected = False
    api._mark_ok()
    assert seen == ["up", "up"]


def test_rejected_token_starts_reauth_once():
    class Response:
        status = 401

        async def json(self, content_type=None):
            return {"error": "bad token"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    api = ControllerApiClient(
        SimpleNamespace(request=lambda *a, **k: Response()), "h", 1, "t", lambda _: None
    )
    started = []
    api.on_auth_failed = lambda: started.append(1)
    for _ in range(2):
        with pytest.raises(ControllerRejectedError):
            run(api.async_get_state())
    assert started == [1] and api.auth_failed
    api.auth_failed = False
    api._mark_ok()
    assert not api.auth_failed


def test_repair_issue_after_15_minutes_and_stuck_loop_goes_unavailable(monkeypatch):
    created, deleted = [], []
    monkeypatch.setattr(sc.ir, "async_create_issue", lambda hass, domain, issue_id, **k: created.append(issue_id))
    monkeypatch.setattr(sc.ir, "async_delete_issue", lambda hass, domain, issue_id: deleted.append(issue_id))
    api = _api()
    coordinator = SimpleNamespace(
        controller_client=api, _controller_lost_since=None, hass=None,
        async_update_listeners=lambda: created.append("listeners"),
    )
    check = lambda: sc.SmartPassiveLinkCoordinator._update_controller_health(coordinator)  # noqa: E731
    api._mark_ok()
    api.last_ok -= 1000  # the loop is stuck although connected is still True
    check()
    assert api.connected is False and "listeners" in created
    assert "controller_unreachable" not in created
    coordinator._controller_lost_since -= sc.HEALTH_ISSUE_DELAY + 1
    check()
    assert "controller_unreachable" in created
    api.auth_failed = True
    check()
    assert "controller_token_rejected" in created
    api._mark_ok()
    check()
    assert "controller_unreachable" in deleted and coordinator._controller_lost_since is None


# ha-int-3: real report times survive a restart
class FakeStore:
    saved = None

    def __init__(self, *args, **kwargs):
        pass

    async def async_load(self):
        return FakeStore.saved

    async def async_save(self, data):
        FakeStore.saved = data

    def async_delay_save(self, func, delay):
        FakeStore.saved = func()


def _state(value, reported):
    return SimpleNamespace(state=value, last_reported=reported, last_updated=reported)


def test_restart_keeps_the_real_report_time_of_an_unchanged_value(monkeypatch):
    monkeypatch.setattr(memory_mod, "Store", FakeStore)
    FakeStore.saved = None
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    first = memory_mod.RuntimeMemory(None, "e")
    run(first.async_load())
    assert first.age_seconds("sensor.pm", _state("4", now - timedelta(hours=5)), now) == 5 * 3600
    run(first.async_save())

    # Home Assistant restarts: the same value now has a last_reported of "now".
    second = memory_mod.RuntimeMemory(None, "e")
    run(second.async_load())
    assert second.age_seconds("sensor.pm", _state("4", now), now) == 5 * 3600
    # A new report (same value) counts as fresh at once; so does a new value.
    later = now + timedelta(minutes=10)
    assert second.age_seconds("sensor.pm", _state("4", later), later) == 0
    third = memory_mod.RuntimeMemory(None, "e")
    run(third.async_load())
    assert third.age_seconds("sensor.pm", _state("9", now), now) == 0


# ha-int-4: a sensor is alive when its device is
def test_device_seen_age_uses_the_freshest_entity_of_the_device(monkeypatch):
    now = datetime.now(timezone.utc)
    states = {
        "sensor.pm25": SimpleNamespace(state="3", attributes={}, last_reported=now - timedelta(hours=4),
                                       last_updated=now - timedelta(hours=4)),
        "sensor.co2": SimpleNamespace(state="600", attributes={}, last_reported=now - timedelta(seconds=20),
                                      last_updated=now - timedelta(seconds=20)),
        "sensor.dead": SimpleNamespace(state="unavailable", attributes={}),
    }
    entries = {
        "sensor.pm25": SimpleNamespace(entity_id="sensor.pm25", device_id="dev"),
        "sensor.co2": SimpleNamespace(entity_id="sensor.co2", device_id="dev"),
        "sensor.dead": SimpleNamespace(entity_id="sensor.dead", device_id="dev"),
    }
    monkeypatch.setattr(sc.er, "async_get", lambda hass: SimpleNamespace(async_get=entries.get))
    monkeypatch.setattr(sc.er, "async_entries_for_device", lambda registry, device_id: list(entries.values()))
    hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    source = {"name": "Køkken", "pm25": "sensor.pm25"}
    plain, _, _ = sc.room_readings(hass, source)
    assert plain["pm25_age_s"] >= 4 * 3600 and "device_seen_age_s" not in plain  # off by default
    seen, _, _ = sc.room_readings(hass, source, device_seen=True)
    assert 19 <= seen["pm25_device_seen_age_s"] <= 25 and seen["device_seen_age_s"] == seen["pm25_device_seen_age_s"]
    assert seen["pm25_age_s"] >= 4 * 3600


# ha-int-8
def test_entry_unique_id_follows_the_address():
    assert entry_unique_id({"connection_type": "tcp", "host": "10.0.0.12", "port": 4196}) == "10.0.0.12:4196"
    assert entry_unique_id({"connection_type": "serial", "serial_port": "/dev/x"}) == "serial:/dev/x"
    assert entry_unique_id({"connection_type": "mk2_tcp", "host": "h", "port": 502, "unit_id": 2}) == "mk2:h:502:2"
    assert entry_unique_id({"connection_type": "tcp", "host": "", "port": 1}) is None


# ha-int-9
def test_orphaned_duct_entities_are_removed_after_two_weeks(monkeypatch):
    monkeypatch.setattr(memory_mod, "Store", FakeStore)
    removed = []
    registry = SimpleNamespace(async_remove=removed.append)
    items = [SimpleNamespace(unique_id="u_loss", entity_id="sensor.loss"),
             SimpleNamespace(unique_id="u_cool", entity_id="sensor.cool")]
    monkeypatch.setattr(entity_mod.er, "async_get", lambda hass: registry)
    monkeypatch.setattr(entity_mod.er, "async_entries_for_config_entry", lambda reg, entry_id: items)
    states = {"sensor.loss": SimpleNamespace(attributes={"restored": True}),
              "sensor.cool": SimpleNamespace(attributes={"restored": True})}
    hass = SimpleNamespace(states=SimpleNamespace(get=states.get), is_running=False)
    memory = memory_mod.RuntimeMemory(hass, "e")
    memory._loaded = True
    coordinator = SimpleNamespace(
        hass=hass, age_memory=memory, controller_state={"a": 1},
        controller_client=SimpleNamespace(connected=True),
    )
    pending = [SimpleNamespace(key="extract_duct_loss_k", unique_id="u_loss"),
               SimpleNamespace(key="cool_boost_remaining_seconds", unique_id="u_cool")]
    entry = SimpleNamespace(entry_id="e")
    entity_mod._clean_up_orphans(entry, coordinator, pending)
    assert removed == []  # the two weeks start counting now
    memory._missing_since["u_loss"] -= memory_mod.ORPHAN_AFTER_S + 1
    memory._missing_since["u_cool"] = memory._missing_since.get("u_cool", 0) - memory_mod.ORPHAN_AFTER_S
    memory.last_orphan_check = 0
    entity_mod._clean_up_orphans(entry, coordinator, pending)
    assert removed == ["sensor.loss"]  # the Køl countdown is empty most of the time and stays


# ha-int-12
def test_dew_point_is_sent_for_rooms_and_weather():
    assert dew_point_c(-0.5, 78) == pytest.approx(-3.87, abs=0.05)
    now = datetime.now(timezone.utc)
    states = {
        "sensor.t": _state("-0.5", now), "sensor.rh": _state("78", now),
    }
    hass = SimpleNamespace(states=SimpleNamespace(get=states.get))
    values, _, _ = sc.room_readings(hass, {"name": "Udeluft", "temperature": "sensor.t", "humidity": "sensor.rh"})
    assert values["dew_point_c"] == pytest.approx(-3.87, abs=0.05)
    weather = SimpleNamespace(state="cloudy", last_updated=now, attributes={"temperature": 1.0, "humidity": 80})
    assert "dew_point_c" in weather_payload("weather.dmi", weather)
