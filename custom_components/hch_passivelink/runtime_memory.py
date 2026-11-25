"""What the integration remembers across Home Assistant restarts.

* When each room sensor really last reported. A restart resets Home Assistant's own
  ``last_reported`` for every entity to "now", which would make a frozen sensor look
  fresh again and defeat the Pi's 3 hour stale guard.
* Since when a deferred entity has had no data, so a leftover (orphaned) registry
  entry can be cleaned up after a long time.
"""
from __future__ import annotations

import math
import time
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

STORE_VERSION = 1
SAVE_DELAY_S = 300
# A deferred entity without data for this long is an orphan (see async_remove_orphans).
ORPHAN_AFTER_S = 14 * 86400


def _report_epoch(state) -> float | None:
    reported = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
    try:
        value = reported.timestamp()
    except (AttributeError, OverflowError, OSError, ValueError):
        return None
    return value if value == value else None


class RuntimeMemory:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(hass, STORE_VERSION, f"hch_passivelink.{entry_id}.memory")
        # entity_id -> {"state": str, "eff": float (real last report), "ha": float | None, "seen": bool}
        self._ages: dict[str, dict[str, object]] = {}
        self._missing_since: dict[str, float] = {}
        self._dirty = False
        self._loaded = False
        self.created_at = time.time()
        self.last_orphan_check = 0.0

    async def async_load(self) -> None:
        self._loaded = True
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        ages = stored.get("ages")
        if isinstance(ages, dict):
            for entity_id, record in ages.items():
                if (
                    isinstance(record, dict)
                    and isinstance(record.get("state"), str)
                    and isinstance(record.get("eff"), (int, float))
                    and not isinstance(record.get("eff"), bool)
                ):
                    # Loaded from disk: not yet compared with a live state this run.
                    self._ages[str(entity_id)] = {
                        "state": record["state"], "eff": float(record["eff"]), "ha": None, "seen": False,
                    }
        missing = stored.get("missing_since")
        if isinstance(missing, dict):
            self._missing_since = {
                str(key): float(value) for key, value in missing.items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            }

    def _snapshot(self) -> dict[str, object]:
        return {
            "ages": {
                entity_id: {"state": record["state"], "eff": record["eff"]}
                for entity_id, record in self._ages.items()
            },
            "missing_since": self._missing_since,
        }

    def _mark_dirty(self) -> None:
        if self._loaded and not self._dirty:
            self._dirty = True
            # Written every few minutes, and by Home Assistant itself when it stops.
            self._store.async_delay_save(self._flush_data, SAVE_DELAY_S)

    def _flush_data(self) -> dict[str, object]:
        self._dirty = False
        return self._snapshot()

    async def async_save(self) -> None:
        if not self._loaded:
            return  # never read: saving now would overwrite what is stored
        self._dirty = False
        await self._store.async_save(self._snapshot())

    # --- reading ages -------------------------------------------------------------

    def report_epoch(self, entity_id: str, state) -> float | None:
        """The real time of the sensor's last report, as a Unix time.

        Home Assistant's own value is used as it is, except right after a restart:
        when the value is unchanged since the last run, the remembered time is kept
        instead of the restart time. Any new report after that counts at once.
        """
        ha_ts = _report_epoch(state)
        if ha_ts is None:
            return None
        value = str(getattr(state, "state", ""))
        record = self._ages.get(entity_id)
        if record is None:
            self._ages[entity_id] = {"state": value, "eff": ha_ts, "ha": ha_ts, "seen": True}
            self._mark_dirty()
            return ha_ts
        eff = float(record["eff"])
        if not record["seen"]:
            record["seen"] = True
            record["ha"] = ha_ts
            if value == record["state"] and eff <= ha_ts:
                return eff
            record.update(state=value, eff=ha_ts)
            self._mark_dirty()
            return ha_ts
        previous_ha = record["ha"]
        if value != record["state"] or previous_ha is None or ha_ts > float(previous_ha):
            record.update(state=value, eff=ha_ts, ha=ha_ts)
            # Saved in one batch every few minutes, so a restart keeps the real time.
            self._mark_dirty()
            return ha_ts
        return min(eff, ha_ts)

    def age_seconds(self, entity_id: str, state, now: datetime) -> int | None:
        reported = self.report_epoch(entity_id, state)
        if reported is None:
            return None
        age = now.timestamp() - reported
        # A future timestamp must not be presented as a new measurement.
        return math.ceil(age) if math.isfinite(age) and age >= 0 else None

    def prune_missing(self, hass: HomeAssistant) -> None:
        """Forget entities that no longer exist (only call once Home Assistant has settled)."""
        stale = [entity_id for entity_id in self._ages if hass.states.get(entity_id) is None]
        for entity_id in stale:
            del self._ages[entity_id]
        if stale:
            self._mark_dirty()

    # --- orphaned entities --------------------------------------------------------

    def note_missing(self, unique_id: str, now: float | None = None) -> float:
        """Since when the entity has had no data (a Unix time); starts counting now."""
        now = time.time() if now is None else now
        if unique_id not in self._missing_since:
            self._missing_since[unique_id] = now
            self._mark_dirty()
        return self._missing_since[unique_id]

    def clear_missing(self, unique_id: str) -> None:
        if self._missing_since.pop(unique_id, None) is not None:
            self._mark_dirty()
