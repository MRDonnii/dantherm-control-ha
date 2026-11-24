"""PassiveLink coordinator extensions for Pi control and HA room inputs."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.util import dt as dt_util

from homeassistant.components import persistent_notification
from homeassistant.core import Event, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import (
    CONF_FORECAST_HIGH_ENTITY,
    CONF_TODAY_HIGH_ENTITY,
    DEFAULT_FORECAST_HIGH_ENTITY,
    DEFAULT_TODAY_HIGH_ENTITY,
    ROOM_SENSOR_KEYS,
    ROOM_SENSOR_RANGES,
)
from .forecast_source import forecast_signals, resolve_entity
from .coordinator import PassiveLinkCoordinator
from .room_catalog import build_catalog, legacy_payload, sources_from_setup, tracked_entities
from .weather_source import weather_payload
from .sensor_age import reported_age_seconds

_LOGGER = logging.getLogger(__name__)
SIGNAL_VALID_FOR = 300
# How often HA's area/sensor catalogue is sent to the Pi for the WebUI page "Rum".
CATALOG_INTERVAL = timedelta(minutes=10)
# The Pi keeps the catalogue in memory; resend it when it is older than this (or gone after a restart).
CATALOG_MAX_AGE_S = 900


class SmartPassiveLinkCoordinator(PassiveLinkCoordinator):
    """Keep PassiveLink receive-only while adding a separate Pi controller API."""

    def __init__(self, *args, controller_client=None, room_sources=None,
                 smart_input_valid_for: int = 180, unit_power_entity: str | None = None,
                 energy_entities: dict[str, str | None] | None = None,
                 weather_entity: str | None = None,
                 climate_entities: dict[str, str] | None = None,
                 rooms_enabled: bool | None = None,
                 **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.controller_client = controller_client
        self.controller_state: dict[str, object] = {}
        self.controller_task = None
        # Rooms from the old options flow: sent once to the Pi to be taken over,
        # and used only while the Pi has no room setup of its own.
        self.room_sources = list(room_sources or [])
        self.rooms_enabled = bool(self.room_sources) if rooms_enabled is None else bool(rooms_enabled)
        # The room setup made in the Dantherm WebUI (Opsætning → Rum), read from the Pi.
        self.pi_rooms: list[dict[str, object]] | None = None
        self._room_entities: list[str] = []
        self._remove_catalog_timer = None
        self._room_cycle_task: asyncio.Task | None = None
        # Set once the Pi says it has taken the old rooms over (or has its own).
        self._pi_migrated = False
        self.smart_input_valid_for = max(30, min(900, int(smart_input_valid_for)))
        self._remove_room_listener = None
        self._remove_room_timer = None
        self._room_send_task: asyncio.Task | None = None
        # Automatic fireplace signal. The Pi leases it for SIGNAL_VALID_FOR
        # seconds and HA renews it every minute while it is on, so a stopped
        # HA lets it expire on the Pi instead of holding fireplace mode.
        self.fireplace_signal = False
        self._remove_signal_timer = None
        # Optional power meter on the unit, leased to the Pi like the fireplace signal.
        self.unit_power_entity = unit_power_entity
        self._remove_power_listener = None
        self._power_sent_at = 0.0
        # Optional daily energy meter and prices for the Pi's energy tiles; renewed each minute.
        self.energy_entities = {
            field: entity_id for field, entity_id in (energy_entities or {}).items() if entity_id
        }
        self.weather_entity = weather_entity if weather_entity and weather_entity.startswith("weather.") else None
        self._remove_weather_listener = None
        self._weather_sent_at = 0.0
        # Forecast highs for the Pi's night cooling
        # (options: a chosen entity, "" for none, or absent for the default).
        self.climate_entities = dict(climate_entities or {})

    def co2_offset(self) -> int:
        """The Pi's CO2 calibration, so HA shows the same corrected CO2 as the WebUI."""
        offset = self.controller_state.get("co2_offset")
        if isinstance(offset, (int, float)) and not isinstance(offset, bool) and -1000 <= offset <= 1000:
            return int(offset)
        return 0

    @callback
    def async_handle_controller_update(self, state: dict[str, object]) -> None:
        self.controller_state = dict(state)
        self._sync_alarm_notifications(state.get("diagnostics_alarms"))
        # Controller entities use the same coordinator listener mechanism as
        # PassiveLink entities, but controller state remains separately namespaced.
        self.async_update_listeners()

    @callback
    def _sync_alarm_notifications(self, alarms: object) -> None:
        """One HA notification per active Pi diagnostics alarm, dismissed when it clears."""
        if not isinstance(alarms, list):
            return
        active = {
            str(alarm["code"]): str(alarm.get("text") or alarm["code"])
            for alarm in alarms
            if isinstance(alarm, dict) and alarm.get("code")
        }
        known = getattr(self, "_alarm_codes", set())
        for code in active.keys() - known:
            persistent_notification.async_create(
                self.hass, active[code], title="Ventilation",
                notification_id=f"hch5_diagnostics_{code}",
            )
        for code in known - active.keys():
            persistent_notification.async_dismiss(self.hass, f"hch5_diagnostics_{code}")
        self._alarm_codes = set(active)

    async def async_controller_command(self, patch: dict[str, object]) -> dict[str, object]:
        if self.controller_client is None:
            raise ConnectionError("Controller API is not configured")
        return await self.controller_client.async_command(patch)

    def _room_payload(self) -> dict[str, dict[str, object]]:
        """Build leased room observations plus controller metadata.

        HA remains only a sensor catalogue/frontend. Priority and control flags
        are hints to the Pi; all demand calculation stays on the controller.
        """
        rooms: dict[str, dict[str, object]] = {}
        for source in SmartPassiveLinkCoordinator.active_room_sources(self):
            name = str(source.get("name") or "").strip()
            if not name:
                continue
            values: dict[str, object] = {
                "source": "home_assistant",
                "measurement_age_version": 1,
                "enabled": bool(source.get("enabled", True)),
                "control": bool(source.get("control", True)),
                "priority": str(source.get("priority") or "auto"),
                "room_type": str(source.get("room_type") or "auto"),
            }
            # Lets the Pi show which HA sensor each value came from.
            entities: dict[str, str] = {}
            for kind, (minimum, maximum) in ROOM_SENSOR_RANGES.items():
                entity_id = source.get(kind)
                if not entity_id:
                    continue
                state = self.hass.states.get(entity_id)
                if state is None or state.state in {"unknown", "unavailable", "none", ""}:
                    continue
                try:
                    value = float(state.state)
                except (TypeError, ValueError):
                    continue
                # The Pi rejects the whole message on one out-of-range value.
                if not minimum <= value <= maximum:
                    continue
                values[kind] = value
                entities[kind] = str(entity_id)
                age = reported_age_seconds(state, dt_util.utcnow())
                if age is not None:
                    values[f"{kind}_age_s"] = age
            if entities:
                values["entities"] = entities
                rooms[name] = values
        return rooms

    def active_room_sources(self) -> list[dict[str, object]]:
        """The rooms set up on the Pi, or the old options-flow rooms until the Pi has some."""
        pi_rooms = getattr(self, "pi_rooms", None)
        if pi_rooms:
            return sources_from_setup(pi_rooms)
        return list(self.room_sources)

    @callback
    def _apply_room_setup(self, view: object) -> None:
        if not isinstance(view, dict) or not isinstance(view.get("rooms"), list):
            return
        self.pi_rooms = [room for room in view["rooms"] if isinstance(room, dict)]
        self._pi_migrated = view.get("migrated") is True or bool(self.pi_rooms)
        self._update_room_listener()

    @callback
    def _update_room_listener(self) -> None:
        """Follow the sensors of the current rooms; they change when the WebUI setup changes."""
        entity_ids = tracked_entities(self.active_room_sources())
        if entity_ids == self._room_entities and (self._remove_room_listener is not None or not entity_ids):
            return
        if self._remove_room_listener is not None:
            self._remove_room_listener()
            self._remove_room_listener = None
        self._room_entities = entity_ids
        if entity_ids:
            self._remove_room_listener = async_track_state_change_event(
                self.hass, entity_ids, self._schedule_room_push
            )

    async def async_send_catalog(self) -> None:
        """Send HA's areas and sensors to the Pi and take its room setup back."""
        if self.controller_client is None or not self.rooms_enabled:
            return
        try:
            view = await self.controller_client.async_send_catalog(
                build_catalog(self.hass),
                None if self._pi_migrated else (legacy_payload(self.room_sources) or None),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - an older Pi has no room setup; keep the old rooms
            _LOGGER.debug("Unable to send the room catalogue to the Pi controller: %s", error)
            return
        self._apply_room_setup(view)

    async def async_refresh_room_setup(self) -> None:
        """Read the room setup from the Pi; resend the catalogue if the Pi has lost it."""
        if self.controller_client is None or not self.rooms_enabled:
            return
        try:
            view = await self.controller_client.async_get_rooms()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - keep the last known setup
            _LOGGER.debug("Unable to read the room setup from the Pi controller: %s", error)
            return
        age = view.get("catalog_age_seconds") if isinstance(view, dict) else None
        if not isinstance(age, (int, float)) or age > CATALOG_MAX_AGE_S:
            await self.async_send_catalog()
            return
        self._apply_room_setup(view)

    async def _async_room_cycle(self, *, catalog: bool = False) -> None:
        if catalog:
            await self.async_send_catalog()
        else:
            await self.async_refresh_room_setup()
        await self.async_send_room_inputs()

    async def async_send_room_inputs(self) -> None:
        if self.controller_client is None or not self.rooms_enabled:
            return
        if not self.active_room_sources():
            return
        try:
            await self.controller_client.async_send_rooms(
                self._room_payload(), self.smart_input_valid_for
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - fire-and-forget push, never fatal
            # Covers OSError/ConnectionError/TimeoutError plus aiohttp's own
            # exception hierarchy (e.g. ServerDisconnectedError), which does
            # not subclass the builtins above and previously escaped this
            # background task and failed config entry unload/reload.
            _LOGGER.debug("Unable to push room data to Pi controller: %s", error)

    async def async_set_fireplace_signal(self, value: bool) -> None:
        if self.controller_client is None:
            raise ConnectionError("Controller API is not configured")
        self.fireplace_signal = bool(value)
        await self.controller_client.async_send_signals(
            {"fireplace": self.fireplace_signal}, SIGNAL_VALID_FOR
        )
        self.async_update_listeners()

    def _unit_power(self) -> float | None:
        state = self.hass.states.get(self.unit_power_entity) if self.unit_power_entity else None
        if state is None:
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        if str(state.attributes.get("unit_of_measurement", "W")).lower() == "kw":
            value *= 1000
        return value if 0 <= value <= 5000 else None

    async def _async_send_unit_power(self) -> None:
        power = self._unit_power()
        if self.controller_client is None or power is None:
            return
        self._power_sent_at = self.hass.loop.time()
        try:
            await self.controller_client.async_send_signals(
                {"unit_power_w": round(power, 1)}, SIGNAL_VALID_FOR
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - retried on the next change or minute
            _LOGGER.debug("Unable to send unit power to Pi controller: %s", error)

    @callback
    def _schedule_unit_power(self, _event: Event | None = None) -> None:
        # A Shelly reports every few seconds; ten seconds is plenty for a display.
        if _event is not None and self.hass.loop.time() - self._power_sent_at < 10:
            return
        self.hass.async_create_background_task(
            self._async_send_unit_power(), "Dantherm HCH unit power push"
        )

    def _energy_signal(self, field: str, entity_id: str) -> float | None:
        state = self.hass.states.get(entity_id)
        if state is None:
            return None
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return None
        unit = str(state.attributes.get("unit_of_measurement") or "").lower()
        if field == "unit_energy_measured_today_kwh":
            if unit == "wh":
                value /= 1000
            elif unit == "mwh":
                value *= 1000
            return value if 0 <= value <= 10000 else None
        # Prices must reach the Pi in kr/kWh; Danish price sensors often use øre/kWh.
        if "øre" in unit or "ore/" in unit:
            value /= 100
        if unit.endswith("/mwh"):
            value /= 1000
        return value if 0 <= value <= 100 else None

    async def _async_send_energy_signals(self) -> None:
        if self.controller_client is None or not self.energy_entities:
            return
        signals = {}
        for field, entity_id in self.energy_entities.items():
            value = self._energy_signal(field, entity_id)
            if value is not None:
                signals[field] = round(value, 4)
        if not signals:
            return
        try:
            await self.controller_client.async_send_signals(signals, SIGNAL_VALID_FOR)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - retried on the next minute
            _LOGGER.debug("Unable to send energy data to Pi controller: %s", error)

    async def _async_send_weather(self) -> None:
        if self.controller_client is None or not self.weather_entity:
            return
        state = self.hass.states.get(self.weather_entity)
        weather = weather_payload(self.weather_entity, state)
        try:
            await self.controller_client.async_send_signals({"weather": weather}, SIGNAL_VALID_FOR)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - weather is optional; retry next minute
            _LOGGER.debug("Unable to send weather data to Pi controller: %s", error)

    @callback
    def _schedule_weather_push(self, _event: Event | None = None) -> None:
        if _event is not None and self.hass.loop.time() - self._weather_sent_at < 10:
            return
        self._weather_sent_at = self.hass.loop.time()
        self.hass.async_create_background_task(
            self._async_send_weather(), "Dantherm HCH weather push"
        )

    def _climate_payload(self) -> dict[str, object]:
        exists = lambda entity_id: self.hass.states.get(entity_id) is not None  # noqa: E731
        forecast = resolve_entity(self.climate_entities, CONF_FORECAST_HIGH_ENTITY, DEFAULT_FORECAST_HIGH_ENTITY, exists)
        today = resolve_entity(self.climate_entities, CONF_TODAY_HIGH_ENTITY, DEFAULT_TODAY_HIGH_ENTITY, exists)
        return forecast_signals(self.hass.states.get, forecast, today)

    async def _async_send_climate_signals(self) -> None:
        """Forecast highs (night cooling) for the Pi, renewed each minute."""
        if self.controller_client is None:
            return
        payload = self._climate_payload()
        if not payload:
            return
        try:
            await self.controller_client.async_send_signals(payload, SIGNAL_VALID_FOR)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - optional; retried next minute
            _LOGGER.debug("Unable to send the forecast highs to Pi controller: %s", error)

    async def _async_renew_signals(self) -> None:
        await self._async_send_unit_power()
        await self._async_send_energy_signals()
        await self._async_send_weather()
        await self._async_send_climate_signals()
        if self.controller_client is None or not self.fireplace_signal:
            return
        try:
            await self.controller_client.async_send_signals(
                {"fireplace": True}, SIGNAL_VALID_FOR
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - renewal retries next minute
            _LOGGER.debug("Unable to renew fireplace signal: %s", error)

    @callback
    def _schedule_room_push(self, _event: Event | None = None) -> None:
        if self._room_send_task is not None and not self._room_send_task.done():
            return

        async def delayed_push() -> None:
            await asyncio.sleep(1)
            await self.async_send_room_inputs()

        self._room_send_task = self.hass.async_create_background_task(
            delayed_push(), "Dantherm HCH Smart Auto room push"
        )

    async def async_start_controller(self) -> None:
        if self.controller_client is None:
            return
        self.controller_client._update = self.async_handle_controller_update
        self.controller_task = self.hass.async_create_background_task(
            self.controller_client.run(), "Dantherm HCH Pi controller API"
        )
        self._remove_signal_timer = async_track_time_interval(
            self.hass,
            callback(lambda _now: self.hass.async_create_background_task(
                self._async_renew_signals(), "Dantherm HCH fireplace signal renewal"
            )),
            timedelta(seconds=60),
        )
        if self.unit_power_entity:
            self._remove_power_listener = async_track_state_change_event(
                self.hass, [self.unit_power_entity], self._schedule_unit_power
            )
            self._schedule_unit_power()
        if self.energy_entities:
            self.hass.async_create_background_task(
                self._async_send_energy_signals(), "Dantherm HCH energy data push"
            )
        if self.weather_entity:
            self._remove_weather_listener = async_track_state_change_event(
                self.hass, [self.weather_entity], self._schedule_weather_push
            )
            self._schedule_weather_push()
        self.hass.async_create_background_task(
            self._async_send_climate_signals(), "Dantherm HCH forecast push"
        )
        if self.rooms_enabled:
            self._update_room_listener()
            self._remove_room_timer = async_track_time_interval(
                self.hass,
                callback(lambda _now: self.hass.async_create_background_task(
                    self._async_room_cycle(), "Dantherm HCH Smart Auto room cycle"
                )),
                timedelta(seconds=60),
            )
            self._remove_catalog_timer = async_track_time_interval(
                self.hass,
                callback(lambda _now: self.hass.async_create_background_task(
                    self.async_send_catalog(), "Dantherm HCH room catalogue"
                )),
                CATALOG_INTERVAL,
            )
            self._room_cycle_task = self.hass.async_create_background_task(
                self._async_room_cycle(catalog=True), "Dantherm HCH room catalogue and push"
            )

    async def async_shutdown(self) -> None:
        if self._remove_weather_listener is not None:
            self._remove_weather_listener()
            self._remove_weather_listener = None
        if self._remove_power_listener is not None:
            self._remove_power_listener()
            self._remove_power_listener = None
        if self._remove_signal_timer is not None:
            self._remove_signal_timer()
            self._remove_signal_timer = None
        if self._remove_room_listener is not None:
            self._remove_room_listener()
            self._remove_room_listener = None
        if self._remove_room_timer is not None:
            self._remove_room_timer()
            self._remove_room_timer = None
        if self._remove_catalog_timer is not None:
            self._remove_catalog_timer()
            self._remove_catalog_timer = None
        if self._room_cycle_task is not None and not self._room_cycle_task.done():
            self._room_cycle_task.cancel()
            try:
                await self._room_cycle_task
            except asyncio.CancelledError:
                pass
            except Exception as error:  # noqa: BLE001 - never block shutdown
                _LOGGER.debug("Room catalogue task ended with error during shutdown: %s", error)
        self._room_cycle_task = None
        if self._room_send_task is not None:
            self._room_send_task.cancel()
            try:
                await self._room_send_task
            except asyncio.CancelledError:
                pass
            except Exception as error:  # noqa: BLE001 - never block shutdown
                _LOGGER.debug("Room push task ended with error during shutdown: %s", error)
            self._room_send_task = None
        if self.controller_client is not None:
            await self.controller_client.stop()
        if self.controller_task is not None:
            self.controller_task.cancel()
            try:
                await self.controller_task
            except asyncio.CancelledError:
                pass
            self.controller_task = None
        await super().async_shutdown()
