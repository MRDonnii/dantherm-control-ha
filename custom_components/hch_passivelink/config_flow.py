"""Config flow for Dantherm Control."""

from __future__ import annotations

import asyncio
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import Mk2Client, PassiveLinkClient, PassiveSerialClient
from .controller_api import ControllerApiClient
from .sensor_support import (
    KIND_CO2,
    KIND_ENERGY,
    KIND_HUMIDITY,
    KIND_PM25,
    KIND_POWER,
    KIND_PRICE,
    KIND_TEMPERATURE,
    supported_entity_ids,
)
from .presence_source import HOME_DOMAINS
from .const import (
    CONF_CONNECTION_TYPE,
    CONF_SERIAL_PORT,
    CONNECTION_MK2,
    CONNECTION_SERIAL,
    CONF_UNIT_ID,
    DEFAULT_MK2_PORT,
    CONNECTION_TCP,
    CONF_FILTER_NOTIFY_DAYS,
    CONF_FILTER_NOTIFY_ENABLED,
    CONF_FILTER_NOTIFY_SERVICE,
    CONF_PREHEATER_SENSORS_ENABLED,
    CONF_PREHEATER_SENSOR_HOST,
    CONF_PREHEATER_SENSOR_PORT,
    CONF_PREHEATER_SWAP_SENSORS,
    CONF_CONTROLLER_API_ENABLED,
    CONF_CONTROLLER_HOST,
    CONF_CONTROLLER_PORT,
    CONF_CONTROLLER_TOKEN,
    CONF_SMART_ROOMS_ENABLED,
    CONF_SMART_INPUT_VALID_FOR,
    CONF_UNIT_POWER_ENTITY,
    CONF_UNIT_ENERGY_TODAY_ENTITY,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_HEAT_PRICE_ENTITY,
    CONF_WEATHER_ENTITY,
    CLIMATE_SIGNAL_ENTITIES,
    CONF_FORECAST_HIGH_ENTITY,
    CONF_HOME_AWAY_ENTITY,
    CONF_TODAY_HIGH_ENTITY,
    DEFAULT_FORECAST_HIGH_ENTITY,
    DEFAULT_TODAY_HIGH_ENTITY,
    ENERGY_SIGNAL_ENTITIES,
    CONF_SMART_ROOMS,
    DEFAULT_CONTROLLER_PORT,
    DEFAULT_FILTER_NOTIFY_DAYS,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PREHEATER_SENSOR_PORT,
    DEFAULT_SMART_INPUT_VALID_FOR,
    MAX_SMART_ROOMS,
    SMART_ROOM_PRIORITIES,
    SMART_ROOM_TYPES,
    ROOM_SLOT_COUNT,
    room_name_key,
    room_temperature_key,
    room_humidity_key,
    room_co2_key,
    DOMAIN,
)

ROOM_SENSOR_KINDS = {
    "temperature": KIND_TEMPERATURE,
    "humidity": KIND_HUMIDITY,
    "co2": KIND_CO2,
    "pm25": KIND_PM25,
}
ENERGY_SIGNAL_KINDS = {
    CONF_UNIT_POWER_ENTITY: KIND_POWER,
    CONF_UNIT_ENERGY_TODAY_ENTITY: KIND_ENERGY,
    CONF_ELECTRICITY_PRICE_ENTITY: KIND_PRICE,
    CONF_HEAT_PRICE_ENTITY: KIND_PRICE,
}


# Passive RS485 is for an HCH5 MK1 + HAC1; units with the Dantherm Residential
# Modbus map are read and controlled over Modbus TCP.
CONNECTION_LABELS = {
    CONNECTION_TCP: "HCH5 MK1 + HAC1 – RS485 over TCP",
    CONNECTION_SERIAL: "HCH5 MK1 + HAC1 – USB-RS485",
    CONNECTION_MK2: "Dantherm Modbus TCP (HCH5 MKII, RCV, HCV, HCC)",
}


class PassiveLinkConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            if user_input[CONF_CONNECTION_TYPE] == CONNECTION_SERIAL:
                return await self.async_step_serial()
            if user_input[CONF_CONNECTION_TYPE] == CONNECTION_MK2:
                return await self.async_step_mk2()
            return await self.async_step_tcp()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_CONNECTION_TYPE, default=CONNECTION_TCP): vol.In(CONNECTION_LABELS)
            }),
        )

    async def async_step_mk2(self, user_input: dict | None = None) -> FlowResult:
        """A Dantherm unit (HCH5 MKII, RCV, HCV, HCC) over Modbus TCP – readings and controls without the Pi."""
        errors = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST].strip(), int(user_input[CONF_PORT])
            unit_id = int(user_input.get(CONF_UNIT_ID, 1))
            client = Mk2Client(host, port, unit_id, lambda _: None)
            try:
                await client.probe()
            except (OSError, ConnectionError, ValueError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"mk2:{host}:{port}:{unit_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{DEFAULT_NAME} ({client.unit.info.model})",
                    data={CONF_CONNECTION_TYPE: CONNECTION_MK2, CONF_HOST: host, CONF_PORT: port, CONF_UNIT_ID: unit_id},
                )
            finally:
                await client.stop()
        return self.async_show_form(
            step_id="mk2",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_MK2_PORT): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Required(CONF_UNIT_ID, default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=247)),
            }),
            errors=errors,
        )

    async def async_step_tcp(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST], user_input[CONF_PORT]
            try:
                await PassiveLinkClient(host, port, lambda _: None).probe()
            except (OSError, ConnectionError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={CONF_CONNECTION_TYPE: CONNECTION_TCP, **user_input},
                )
        return self.async_show_form(
            step_id="tcp",
            data_schema=vol.Schema({
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }),
            errors=errors,
        )

    async def async_step_serial(self, user_input: dict | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            port = user_input[CONF_SERIAL_PORT]
            try:
                await PassiveSerialClient(port, lambda _: None).probe()
            except (OSError, ConnectionError, asyncio.TimeoutError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"serial:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=DEFAULT_NAME,
                    data={CONF_CONNECTION_TYPE: CONNECTION_SERIAL, **user_input},
                )
        return self.async_show_form(
            step_id="serial",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL_PORT): str}),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return PassiveLinkOptionsFlow(config_entry)


class PassiveLinkOptionsFlow(config_entries.OptionsFlow):
    """Configure the connection, the Pi controller API and the HA sensors sent to the Pi.

    Rooms and every ventilation setting live in the Dantherm WebUI; the old
    options-flow rooms are only kept (unchanged) so the Pi can take them over.
    """

    def __init__(self, config_entry) -> None:
        self._config_entry = config_entry
        self._pending: dict = {}
        self._rooms = self._load_rooms({**config_entry.data, **config_entry.options})
        self._controller_client: ControllerApiClient | None = None
        self._controller_state: dict[str, object] = {}

    @property
    def _current(self) -> dict:
        return {**self._config_entry.data, **self._config_entry.options}

    @staticmethod
    def _load_rooms(config: dict) -> list[dict[str, object]]:
        """Load new dynamic rooms, falling back to legacy fixed slots."""
        rooms: list[dict[str, object]] = []
        raw_rooms = config.get(CONF_SMART_ROOMS)
        if isinstance(raw_rooms, list):
            for raw in raw_rooms[:MAX_SMART_ROOMS]:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name", "")).strip()
                if not name:
                    continue
                priority = str(raw.get("priority", "auto"))
                if priority not in SMART_ROOM_PRIORITIES:
                    priority = "auto"
                room_type = str(raw.get("room_type", "auto"))
                if room_type not in SMART_ROOM_TYPES:
                    room_type = "auto"
                room = {
                    "name": name[:64],
                    "enabled": bool(raw.get("enabled", True)),
                    "control": bool(raw.get("control", True)),
                    "priority": priority,
                    "room_type": room_type,
                }
                for key in ROOM_SENSOR_KINDS:
                    value = str(raw.get(key, "")).strip()
                    if value:
                        room[key] = value
                rooms.append(room)
            return rooms

        # Transparent migration from the original 8 fixed room slots.
        for slot in range(1, ROOM_SLOT_COUNT + 1):
            name = str(config.get(room_name_key(slot), "")).strip()
            if not name:
                continue
            room: dict[str, object] = {
                "name": name[:64],
                "enabled": True,
                "control": True,
                "priority": "auto",
            }
            for kind, key_func in (
                ("temperature", room_temperature_key),
                ("humidity", room_humidity_key),
                ("co2", room_co2_key),
            ):
                value = str(config.get(key_func(slot), "")).strip()
                if value:
                    room[kind] = value
            if len(room) > 4:
                rooms.append(room)
        return rooms

    def _entity_selector(self, kind: str, selected: object = None):
        """Offer only sensors whose unit/device class the controller understands."""
        return selector.EntitySelector(selector.EntitySelectorConfig(
            domain="sensor",
            multiple=False,
            include_entities=supported_entity_ids(
                self.hass.states.async_all("sensor"), kind, keep=selected
            ),
        ))

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        current = self._current
        errors = {}
        if user_input is not None:
            connection_type = user_input[CONF_CONNECTION_TYPE]
            options = dict(user_input)
            if connection_type == CONNECTION_SERIAL:
                serial_port = str(user_input.get(CONF_SERIAL_PORT, "")).strip()
                if not serial_port:
                    errors[CONF_SERIAL_PORT] = "required"
                else:
                    try:
                        await PassiveSerialClient(serial_port, lambda _: None).probe()
                    except (OSError, ConnectionError, asyncio.TimeoutError):
                        errors["base"] = "cannot_connect"
                    options[CONF_SERIAL_PORT] = serial_port
            else:
                host = str(user_input.get(CONF_HOST, "")).strip()
                port = int(user_input[CONF_PORT])
                if not host:
                    errors[CONF_HOST] = "required"
                else:
                    try:
                        if connection_type == CONNECTION_MK2:
                            probe_client = Mk2Client(host, port, int(current.get(CONF_UNIT_ID, 1)), lambda _: None)
                            try:
                                await probe_client.probe()
                            finally:
                                await probe_client.stop()
                        else:
                            await PassiveLinkClient(host, port, lambda _: None).probe()
                    except (OSError, ConnectionError, ValueError, asyncio.TimeoutError):
                        errors["base"] = "cannot_connect"
                    options[CONF_HOST] = host
                    options[CONF_PORT] = port
                    if connection_type == CONNECTION_MK2:
                        options[CONF_UNIT_ID] = int(current.get(CONF_UNIT_ID, 1))
            if not errors:
                self._pending = options
                # Chosen on the separate sensor page; keep them unless changed there.
                for key in ENERGY_SIGNAL_ENTITIES:
                    if current.get(key):
                        self._pending[key] = current[key]
                if current.get(CONF_WEATHER_ENTITY):
                    self._pending[CONF_WEATHER_ENTITY] = current[CONF_WEATHER_ENTITY]
                for key in CLIMATE_SIGNAL_ENTITIES:
                    if key in current:  # "" (cleared) is kept too: it means "send nothing"
                        self._pending[key] = current[key]
                if user_input.get(CONF_CONTROLLER_API_ENABLED, False):
                    return await self.async_step_controller()
                self._pending[CONF_SMART_ROOMS] = self._rooms
                return self.async_create_entry(title="", data=self._pending)

        schema = vol.Schema({
            vol.Required(
                CONF_CONNECTION_TYPE,
                default=current.get(CONF_CONNECTION_TYPE, CONNECTION_TCP),
            ): vol.In(CONNECTION_LABELS),
            vol.Optional(CONF_HOST, default=current.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional(CONF_SERIAL_PORT, default=current.get(CONF_SERIAL_PORT, "")): str,
            vol.Required(
                CONF_FILTER_NOTIFY_ENABLED,
                default=current.get(CONF_FILTER_NOTIFY_ENABLED, True),
            ): bool,
            vol.Required(
                CONF_FILTER_NOTIFY_DAYS,
                default=current.get(CONF_FILTER_NOTIFY_DAYS, DEFAULT_FILTER_NOTIFY_DAYS),
            ): selector.NumberSelector(selector.NumberSelectorConfig(
                min=1, max=180, step=1, mode=selector.NumberSelectorMode.BOX
            )),
            vol.Optional(
                CONF_FILTER_NOTIFY_SERVICE,
                default=current.get(CONF_FILTER_NOTIFY_SERVICE, ""),
            ): str,
            vol.Required(
                CONF_PREHEATER_SENSORS_ENABLED,
                default=current.get(CONF_PREHEATER_SENSORS_ENABLED, False),
            ): bool,
            vol.Optional(
                CONF_PREHEATER_SENSOR_HOST,
                default=current.get(CONF_PREHEATER_SENSOR_HOST, current.get(CONF_HOST, "")),
            ): str,
            vol.Required(
                CONF_PREHEATER_SENSOR_PORT,
                default=current.get(CONF_PREHEATER_SENSOR_PORT, DEFAULT_PREHEATER_SENSOR_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_PREHEATER_SWAP_SENSORS,
                default=current.get(CONF_PREHEATER_SWAP_SENSORS, False),
            ): bool,
            vol.Required(
                CONF_CONTROLLER_API_ENABLED,
                default=current.get(CONF_CONTROLLER_API_ENABLED, True),
            ): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_controller(self, user_input: dict | None = None) -> FlowResult:
        current = self._current
        errors = {}
        default_host = current.get(CONF_CONTROLLER_HOST) or self._pending.get(CONF_HOST) or current.get(CONF_HOST, "")
        if user_input is not None:
            host = str(user_input[CONF_CONTROLLER_HOST]).strip()
            token = str(user_input[CONF_CONTROLLER_TOKEN]).strip()
            port = int(user_input[CONF_CONTROLLER_PORT])
            if not host:
                errors[CONF_CONTROLLER_HOST] = "required"
            if not token:
                errors[CONF_CONTROLLER_TOKEN] = "required"
            if not errors:
                client = ControllerApiClient(
                    async_get_clientsession(self.hass), host, port, token, lambda _: None
                )
                try:
                    self._controller_state = await client.async_probe()
                except (OSError, ConnectionError, asyncio.TimeoutError):
                    errors["base"] = "controller_cannot_connect"
            if not errors:
                self._controller_client = client
                self._pending.update(user_input)
                return await self.async_step_controller_menu()

        schema = vol.Schema({
            vol.Required(CONF_CONTROLLER_HOST, default=default_host): str,
            vol.Required(
                CONF_CONTROLLER_PORT,
                default=current.get(CONF_CONTROLLER_PORT, DEFAULT_CONTROLLER_PORT),
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_CONTROLLER_TOKEN,
                default=current.get(CONF_CONTROLLER_TOKEN, ""),
            ): selector.TextSelector(selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD
            )),
            vol.Required(
                CONF_SMART_ROOMS_ENABLED,
                default=current.get(CONF_SMART_ROOMS_ENABLED, True),
            ): bool,
            vol.Required(
                CONF_SMART_INPUT_VALID_FOR,
                default=current.get(CONF_SMART_INPUT_VALID_FOR, DEFAULT_SMART_INPUT_VALID_FOR),
            ): selector.NumberSelector(selector.NumberSelectorConfig(
                min=30, max=900, step=30, mode=selector.NumberSelectorMode.BOX
            )),
        })
        return self.async_show_form(step_id="controller", data_schema=schema, errors=errors)

    async def async_step_controller_sensors(self, user_input: dict | None = None) -> FlowResult:
        """Choose which HA sensors are leased to the controller at the chosen IP."""
        if user_input is not None:
            # An emptied optional entity selector is absent from user_input.
            for key in (*ENERGY_SIGNAL_ENTITIES, CONF_WEATHER_ENTITY):
                self._pending.pop(key, None)
            self._pending.update(
                {key: user_input[key] for key in (*ENERGY_SIGNAL_ENTITIES, CONF_WEATHER_ENTITY) if user_input.get(key)}
            )
            # Cleared: "" so the default is not picked again (home/away "" = automatic).
            for key in CLIMATE_SIGNAL_ENTITIES:
                self._pending[key] = user_input.get(key) or ""
            return await self.async_step_controller_menu()

        fields = {}
        for key, kind in ENERGY_SIGNAL_KINDS.items():
            selected = self._pending.get(key)
            fields[vol.Optional(key, description={"suggested_value": selected})] = (
                self._entity_selector(kind, selected)
            )
        fields[vol.Optional(CONF_WEATHER_ENTITY, description={"suggested_value": self._pending.get(CONF_WEATHER_ENTITY)})] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather", multiple=False)
        )
        for key, default in ((CONF_FORECAST_HIGH_ENTITY, DEFAULT_FORECAST_HIGH_ENTITY),
                             (CONF_TODAY_HIGH_ENTITY, DEFAULT_TODAY_HIGH_ENTITY)):
            if key in self._pending:
                suggested = self._pending.get(key) or None
            else:
                suggested = default if self.hass.states.get(default) is not None else None
            fields[vol.Optional(key, description={"suggested_value": suggested})] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", multiple=False)
            )
        fields[vol.Optional(CONF_HOME_AWAY_ENTITY, description={"suggested_value": self._pending.get(CONF_HOME_AWAY_ENTITY) or None})] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=list(HOME_DOMAINS), multiple=False)
        )
        return self.async_show_form(
            step_id="controller_sensors",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "controller": f"{self._pending.get(CONF_CONTROLLER_HOST, '')}:"
                f"{self._pending.get(CONF_CONTROLLER_PORT, DEFAULT_CONTROLLER_PORT)}",
            },
        )

    def _webui_url(self, page: str = "") -> str:
        """The Dantherm WebUI on the Pi, served on the controller port."""
        host = str(self._pending.get(CONF_CONTROLLER_HOST) or self._current.get(CONF_CONTROLLER_HOST) or "").strip()
        port = self._pending.get(CONF_CONTROLLER_PORT) or self._current.get(CONF_CONTROLLER_PORT) or DEFAULT_CONTROLLER_PORT
        if not host:
            return "Dantherm-WebUI"
        return f"http://{host}:{int(port)}/#/{page}" if page else f"http://{host}:{int(port)}/"

    async def async_step_controller_menu(self, user_input: dict | None = None) -> FlowResult:
        """What Home Assistant sets up. Everything about the ventilation itself is set up in the WebUI."""
        del user_input
        return self.async_show_menu(
            step_id="controller_menu",
            menu_options=["controller_sensors", "rooms_info", "webui_info", "finish_controller"],
            description_placeholders={"url": self._webui_url()},
        )

    async def async_step_rooms_info(self, user_input: dict | None = None) -> FlowResult:
        """Rooms are set up in one place only: the Dantherm WebUI (Opsætning → Rum)."""
        if user_input is not None:
            return await self.async_step_controller_menu()
        return self.async_show_form(
            step_id="rooms_info",
            data_schema=vol.Schema({}),
            description_placeholders={"url": self._webui_url("rooms"), "count": str(len(self._rooms))},
        )

    async def async_step_webui_info(self, user_input: dict | None = None) -> FlowResult:
        """Operation, steps, air quality and afterheat are set up in the Dantherm WebUI."""
        if user_input is not None:
            return await self.async_step_controller_menu()
        return self.async_show_form(
            step_id="webui_info",
            data_schema=vol.Schema({}),
            description_placeholders={"url": self._webui_url("settings")},
        )

    async def async_step_finish_controller(self, user_input: dict | None = None) -> FlowResult:
        del user_input
        self._pending[CONF_SMART_ROOMS] = self._rooms
        # Do not persist the old fixed-slot representation when options are saved.
        for slot in range(1, ROOM_SLOT_COUNT + 1):
            for key_func in (room_name_key, room_temperature_key, room_humidity_key, room_co2_key):
                self._pending.pop(key_func(slot), None)
        return self.async_create_entry(title="", data=self._pending)
