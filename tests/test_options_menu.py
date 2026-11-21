import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components"))

import pytest  # noqa: E402

pytest.importorskip("homeassistant")
pytest.importorskip("serial")
from hch_passivelink.config_flow import PassiveLinkOptionsFlow  # noqa: E402

TRANSLATIONS = Path(__file__).parents[1] / "custom_components/hch_passivelink/translations"


def _flow(options):
    flow = PassiveLinkOptionsFlow(SimpleNamespace(data={}, options=options))
    shown = {}
    flow.async_show_menu = lambda **kwargs: shown.update(kwargs) or kwargs
    flow.async_show_form = lambda **kwargs: shown.update(kwargs) or kwargs
    return flow, shown


def test_rooms_and_settings_are_only_pointed_to_the_webui():
    flow, shown = _flow({"controller_host": "10.0.0.5", "controller_port": 8080,
                         "smart_rooms": [{"name": "Bad", "humidity": "sensor.bad"}]})
    asyncio.run(flow.async_step_controller_menu())
    assert shown["menu_options"] == ["controller_sensors", "rooms_info", "webui_info", "finish_controller"]
    asyncio.run(flow.async_step_rooms_info())
    assert shown["step_id"] == "rooms_info"
    assert shown["description_placeholders"] == {"url": "http://10.0.0.5:8080/#/rooms", "count": "1"}
    asyncio.run(flow.async_step_webui_info())
    assert shown["description_placeholders"]["url"] == "http://10.0.0.5:8080/#/settings"
    # No room editing steps are left in the integration.
    for step in ("rooms_menu", "add_room", "edit_room", "remove_room", "controller_settings", "fan_profiles"):
        assert not hasattr(flow, f"async_step_{step}")


def test_old_rooms_are_kept_unchanged_when_options_are_saved():
    flow, _shown = _flow({"smart_rooms": [{"name": "Bad", "humidity": "sensor.bad"}]})
    flow.async_create_entry = lambda **kwargs: kwargs
    result = asyncio.run(flow.async_step_finish_controller())
    assert result["data"]["smart_rooms"][0]["name"] == "Bad"


@pytest.mark.parametrize("lang", ["da", "en"])
def test_translations_cover_the_menu(lang):
    steps = json.loads((TRANSLATIONS / f"{lang}.json").read_text(encoding="utf-8"))["options"]["step"]
    assert set(steps["controller_menu"]["menu_options"]) == {"controller_sensors", "rooms_info", "webui_info", "finish_controller"}
    assert "{url}" in steps["rooms_info"]["description"]
    assert "rooms_menu" not in steps and "controller_settings" not in steps
