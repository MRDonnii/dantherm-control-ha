"""Rooms for the Pi controller: HA's sensor catalogue and the room setup on the Pi.

Rooms are set up in the Dantherm WebUI (Opsætning → Rum), not in Home
Assistant. Home Assistant only

* sends a catalogue of its areas and their temperature, humidity, CO2 and
  PM2.5 sensors (``build_catalog``), so the WebUI can offer them, and
* reads the room setup back from the Pi and sends the values of the chosen
  sensors (``sources_from_setup``).

The rooms from the old options flow are sent once with the catalogue
(``legacy_payload``) so the Pi can take them over; until the Pi has a room
setup they are still used, so nothing stops working during an update.
"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from .const import DOMAIN, ROOM_SENSOR_KEYS

# HA device classes -> the Pi's measurement names.
DEVICE_CLASS_KINDS = {
    "temperature": "temperature",
    "humidity": "humidity",
    "carbon_dioxide": "co2",
    "pm25": "pm25",
}
KIND_ORDER = ("temperature", "humidity", "co2", "pm25")
MAX_AREAS = 64
MAX_SENSORS_PER_AREA = 40
# Sensors that are rarely the room's air: appliance, device and setpoint
# temperatures, smoothed copies, "feels like" values and car or heating
# system readings. They stay in the catalogue but are offered last.
_POOR_MARKERS = (
    "feels_like", "setpoint", "setpunkt", "device_temperature", "_ema", "thermostat",
    "local_temperature", "internal", "cpu", "chip", "processor", "oven", "vaskemaskine",
    "opvask", "torretumbler", "dishwasher", "washer", "dryer", "sirene", "siren", "_pir",
    "_rog_", "smoke", "cabin", "probe", "charger", "battery", "dew", "dugpunkt", "target",
    "fremlob", "retur", "brugsvand", "fjernvarme", "outside_temperature", "inside_temperature",
    "nozzle", "printer", "_bed_temp", "_ams_", "freezer", "fryser", "koeleskab", "fridge",
)
# Room types the Pi uses that are not ventilated rooms (see the Pi's room_setup.py).
NOT_INDOOR = {"source", "outdoor", "extract", "excluded"}


def _fold(text: object) -> str:
    value = str(text or "").casefold()
    for old, new in (("æ", "ae"), ("ø", "o"), ("å", "aa")):
        value = value.replace(old, new)
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def sensor_score(entity_id: str, area_id: str, area_name: str, device_kinds: int) -> int:
    """Higher is more likely the room's own climate sensor."""
    folded = entity_id.casefold()
    score = 2 * max(0, device_kinds - 1)
    if any(marker in folded for marker in _POOR_MARKERS):
        score -= 10
    for word in {_fold(area_id), _fold(area_name)}:
        if len(word) >= 3 and word in folded:
            score += 3
            break
    return score


def build_catalog(hass) -> list[dict[str, object]]:
    """Every HA area with its temperature, humidity, CO2 and PM2.5 sensors.

    Disabled, hidden, diagnostic/config entities and this integration's own
    entities are left out. Sensors are grouped by kind with the likely room
    sensor first, so "the first one" is a sensible automatic choice.
    """
    from homeassistant.helpers import area_registry as ar
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    areas = ar.async_get(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    found: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in list(entities.entities.values()):
        if entry.domain != "sensor" or entry.platform == DOMAIN:
            continue
        if entry.disabled_by is not None or entry.hidden_by is not None or entry.entity_category is not None:
            continue
        device_class = entry.device_class or entry.original_device_class
        kind = DEVICE_CLASS_KINDS.get(str(device_class or ""))
        if kind is None:
            continue
        area_id = entry.area_id
        if area_id is None and entry.device_id:
            device = devices.async_get(entry.device_id)
            area_id = device.area_id if device else None
        if not area_id:
            continue
        state = hass.states.get(entry.entity_id)
        name = (state.name if state is not None else None) or entry.name or entry.original_name or entry.entity_id
        unit = state.attributes.get("unit_of_measurement") if state is not None else None
        found[area_id].append({
            "entity_id": entry.entity_id, "name": str(name)[:80], "kind": kind,
            "unit": str(unit)[:16] if unit else None, "device_id": entry.device_id,
        })
    catalog: list[dict[str, object]] = []
    for area in sorted(areas.async_list_areas(), key=lambda item: item.name.casefold()):
        sensors = found.get(area.id) or []
        kinds_per_device: dict[str, set[str]] = defaultdict(set)
        for sensor in sensors:
            if sensor["device_id"]:
                kinds_per_device[str(sensor["device_id"])].add(str(sensor["kind"]))
        sensors.sort(key=lambda sensor: (
            KIND_ORDER.index(str(sensor["kind"])),
            -sensor_score(str(sensor["entity_id"]), area.id, area.name,
                          len(kinds_per_device.get(str(sensor["device_id"]), ()))),
            str(sensor["entity_id"]),
        ))
        catalog.append({
            "area_id": area.id,
            "area_name": area.name,
            "sensors": [
                {key: value for key, value in sensor.items() if key != "device_id" and value is not None}
                for sensor in sensors[:MAX_SENSORS_PER_AREA]
            ],
        })
    return catalog[:MAX_AREAS]


def sources_from_setup(rooms: Iterable[object]) -> list[dict[str, object]]:
    """The room setup from the Pi as the room sources the coordinator sends values for.

    Excluded and switched-off rooms are not sent; the Pi decides type,
    priority and control itself, so only the name and sensors matter here.
    """
    result: list[dict[str, object]] = []
    for room in rooms:
        if not isinstance(room, dict):
            continue
        name = str(room.get("name") or "").strip()[:64]
        if not name or room.get("type") == "excluded" or room.get("enabled") is False:
            continue
        sensors = room.get("sensors") if isinstance(room.get("sensors"), dict) else {}
        source: dict[str, object] = {
            "name": name,
            "enabled": True,
            "control": room.get("control", True) is not False and room.get("type") not in NOT_INDOOR,
            "priority": str(room.get("priority") or "auto"),
            "room_type": "bathroom" if room.get("type") == "bathroom" else "normal",
        }
        for kind in ROOM_SENSOR_KEYS:
            entity_id = sensors.get(kind)
            if isinstance(entity_id, str) and entity_id.startswith("sensor."):
                source[kind] = entity_id
        if any(source.get(kind) for kind in ROOM_SENSOR_KEYS):
            result.append(source)
    return result


def legacy_payload(sources: Iterable[dict[str, object]]) -> dict[str, dict[str, object]]:
    """The old options-flow rooms in the shape the Pi migrates from."""
    rooms: dict[str, dict[str, object]] = {}
    for source in sources:
        name = str(source.get("name") or "").strip()[:64]
        entities = {kind: str(source[kind]) for kind in ROOM_SENSOR_KEYS if source.get(kind)}
        if not name or not entities:
            continue
        rooms[name] = {
            "enabled": bool(source.get("enabled", True)),
            "control": bool(source.get("control", True)),
            "priority": str(source.get("priority") or "auto"),
            "room_type": str(source.get("room_type") or "auto"),
            "entities": entities,
        }
    return rooms


def tracked_entities(sources: Iterable[dict[str, object]]) -> list[str]:
    """The entities whose changes trigger a room push."""
    return sorted({
        str(entity_id)
        for source in sources
        if bool(source.get("enabled", True))
        for entity_id in (source.get(kind) for kind in ROOM_SENSOR_KEYS)
        if entity_id
    })
