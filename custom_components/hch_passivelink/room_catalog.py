"""Rooms for the Pi controller: HA's sensor catalogue and the room setup on the Pi.

Rooms are set up in the Dantherm WebUI (Opsætning → Rum), not in Home
Assistant. Home Assistant only

* sends a catalogue of its areas and their temperature, humidity, CO2 and
  PM2.5 sensors and presence sensors (``build_catalog``), so the WebUI can
  offer them, and
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
# Presence: binary sensors with these device classes (mmWave radars, PIRs).
PRESENCE_KIND = "presence"
PRESENCE_DEVICE_CLASSES = {"occupancy", "presence", "motion"}
KIND_ORDER = ("temperature", "humidity", "co2", "pm25", PRESENCE_KIND)
# Parts of a radar's sub-entities (targets, zones, output pins) that are not the room's presence.
_PRESENCE_POOR = ("radar_", "_zone_", "zone_", "out_pin", "moving", "still", "doorbell", "user_present",
                  "printer", "laser", "foto", "camera", "kamera")
MAX_AREAS = 64
MAX_SENSORS_PER_AREA = 40
# Presence sensors offered per area (a radar brings many sub-entities; the best come first).
MAX_PRESENCE_PER_AREA = 8
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


# The weather service's current air temperature and humidity (DMI Vejr) have no HA
# area: they go in a virtual area that the WebUI lists first for outdoor rooms.
WEATHER_AREA_ID = "_weather"
WEATHER_AREA_NAME = "Vejr (DMI)"
WEATHER_OBJECT_IDS = {"temperature": ("dmi_vejr_temperatur",), "humidity": ("dmi_vejr_luftfugtighed",)}


# Kept in the catalogue only while sorting.
_INTERNAL = ("device_id", "device_class")


def _is_weather_sensor(platform: object, entity_id: str, kind: str) -> bool:
    """The DMI weather service's current air temperature or humidity (not grass, soil, max/min or forecast)."""
    object_id = entity_id.split(".", 1)[-1]
    return "dmi" in str(platform or "") and object_id in WEATHER_OBJECT_IDS.get(kind, ())


def presence_score(entity_id: str, device_class: str, area_id: str, area_name: str) -> int:
    """Higher is more likely the room's own presence sensor (e.g. binary_sensor.hps_stuen_presence)."""
    folded = entity_id.casefold()
    score = sensor_score(entity_id, area_id, area_name, 1)
    if device_class in ("occupancy", "presence"):
        score += 2
    if folded.endswith(("_presence", "_occupancy", "_tilstede")) or "presence_gruppe" in folded:
        score += 4
    if any(marker in folded for marker in _PRESENCE_POOR):
        score -= 6
    return score


def build_catalog(hass) -> list[dict[str, object]]:
    """Every HA area with its temperature, humidity, CO2 and PM2.5 sensors and presence sensors.

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
        if entry.domain not in ("sensor", "binary_sensor") or entry.platform == DOMAIN:
            continue
        if entry.disabled_by is not None or entry.hidden_by is not None or entry.entity_category is not None:
            continue
        device_class = entry.device_class or entry.original_device_class
        if entry.domain == "binary_sensor":
            kind = PRESENCE_KIND if str(device_class or "") in PRESENCE_DEVICE_CLASSES else None
        else:
            kind = DEVICE_CLASS_KINDS.get(str(device_class or ""))
        if kind is None:
            continue
        area_id = entry.area_id
        if area_id is None and entry.device_id:
            device = devices.async_get(entry.device_id)
            area_id = device.area_id if device else None
        if not area_id:
            if kind == PRESENCE_KIND or not _is_weather_sensor(entry.platform, entry.entity_id, kind):
                continue
            area_id = WEATHER_AREA_ID
        state = hass.states.get(entry.entity_id)
        name = (state.name if state is not None else None) or entry.name or entry.original_name or entry.entity_id
        unit = state.attributes.get("unit_of_measurement") if state is not None else None
        found[area_id].append({
            "entity_id": entry.entity_id, "name": str(name)[:80], "kind": kind,
            "unit": str(unit)[:16] if unit else None, "device_id": entry.device_id,
            "device_class": str(device_class or ""),
        })
    catalog: list[dict[str, object]] = []
    weather = sorted(found.get(WEATHER_AREA_ID) or [], key=lambda sensor: (KIND_ORDER.index(str(sensor["kind"])), str(sensor["entity_id"])))
    if weather:
        catalog.append({
            "area_id": WEATHER_AREA_ID, "area_name": WEATHER_AREA_NAME,
            "sensors": [{key: value for key, value in sensor.items() if key not in _INTERNAL and value is not None}
                        for sensor in weather[:MAX_SENSORS_PER_AREA]],
        })
    for area in sorted(areas.async_list_areas(), key=lambda item: item.name.casefold()):
        sensors = found.get(area.id) or []
        kinds_per_device: dict[str, set[str]] = defaultdict(set)
        for sensor in sensors:
            if sensor["device_id"] and sensor["kind"] != PRESENCE_KIND:
                kinds_per_device[str(sensor["device_id"])].add(str(sensor["kind"]))
        sensors.sort(key=lambda sensor: (
            KIND_ORDER.index(str(sensor["kind"])),
            -(presence_score(str(sensor["entity_id"]), str(sensor.get("device_class") or ""), area.id, area.name)
              if sensor["kind"] == PRESENCE_KIND else
              sensor_score(str(sensor["entity_id"]), area.id, area.name,
                           len(kinds_per_device.get(str(sensor["device_id"]), ())))),
            str(sensor["entity_id"]),
        ))
        catalog.append({
            "area_id": area.id,
            "area_name": area.name,
            "sensors": [
                {key: value for key, value in sensor.items() if key not in _INTERNAL and value is not None}
                for sensor in _capped(sensors)
            ],
        })
    return catalog[:MAX_AREAS]


def _capped(sensors: list[dict[str, object]]) -> list[dict[str, object]]:
    """The measurements as before (best first, at most 40) plus the best few presence sensors."""
    measurements = [sensor for sensor in sensors if sensor["kind"] != PRESENCE_KIND]
    presence = [sensor for sensor in sensors if sensor["kind"] == PRESENCE_KIND]
    return measurements[:MAX_SENSORS_PER_AREA] + presence[:MAX_PRESENCE_PER_AREA]


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
        presence = sensors.get(PRESENCE_KIND)
        if isinstance(presence, str) and presence.startswith("binary_sensor."):
            source[PRESENCE_KIND] = presence
        if any(source.get(kind) for kind in (*ROOM_SENSOR_KEYS, PRESENCE_KIND)):
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
        for entity_id in (source.get(kind) for kind in (*ROOM_SENSOR_KEYS, PRESENCE_KIND))
        if entity_id
    })
