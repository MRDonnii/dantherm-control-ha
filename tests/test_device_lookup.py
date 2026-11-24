"""Device registry lookups: per-entry on HA 2026.8+, global fallback on older HA."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "custom_components" / "hch_passivelink"))

import const  # noqa: E402
from device_lookup import find_entry_device  # noqa: E402

DOMAIN = "hch_passivelink"
MAIN = (DOMAIN, "hch5_mk1_hac1")
CONTROLLER = (DOMAIN, "hch5_pi_controller")


class _Registry:
    """Minimal device registry; ``per_entry`` mimics HA 2026.8+ scoping."""

    def __init__(self, per_entry: bool) -> None:
        self.per_entry = per_entry
        self.devices: dict[str, SimpleNamespace] = {}
        self.calls: list[tuple] = []

    def _match(self, identifier, entry_id):
        for device in self.devices.values():
            if identifier in device.identifiers and (
                not self.per_entry or entry_id in device.config_entries
            ):
                return device
        return None

    def async_get_device(self, *, identifiers):
        self.calls.append(("async_get_device", identifiers))
        if self.per_entry:
            raise AssertionError("deprecated global lookup used on new HA")
        (identifier,) = identifiers
        return self._match(identifier, None)

    def async_get_or_create(self, *, config_entry_id, identifiers, name, **_kwargs):
        (identifier,) = identifiers
        device = self._match(identifier, config_entry_id)
        if device is None:
            device = SimpleNamespace(
                id=f"dev{len(self.devices) + 1}",
                identifiers=set(identifiers),
                config_entries={config_entry_id},
                name=name,
            )
            self.devices[device.id] = device
        else:
            device.config_entries.add(config_entry_id)
        return device


class _NewRegistry(_Registry):
    def __init__(self) -> None:
        super().__init__(per_entry=True)

    def async_get_device_by_identifier(self, identifier, config_entry_id):
        self.calls.append(("async_get_device_by_identifier", identifier, config_entry_id))
        return self._match(identifier, config_entry_id)


def _setup_devices(registry, entry_id: str, default_main: str):
    """Mirror the device part of async_setup_entry."""
    existing_main = find_entry_device(registry, MAIN, entry_id)
    existing_controller = find_entry_device(registry, CONTROLLER, entry_id)
    main_name = const.kept_device_name(existing_main.name if existing_main else None, default_main)
    controller_name = const.kept_device_name(
        existing_controller.name if existing_controller else None, const.CONTROLLER_DEVICE_NAME
    )
    main_id = registry.async_get_or_create(
        config_entry_id=entry_id, identifiers={MAIN}, name=main_name
    ).id
    registry.async_get_or_create(
        config_entry_id=entry_id, identifiers={CONTROLLER}, name=controller_name
    )
    return main_id, main_name, controller_name


def test_uses_per_entry_lookup_when_available():
    registry = _NewRegistry()
    assert find_entry_device(registry, MAIN, "entry1") is None
    device = registry.async_get_or_create(config_entry_id="entry1", identifiers={MAIN}, name="Main")
    assert find_entry_device(registry, MAIN, "entry1") is device
    assert find_entry_device(registry, MAIN, "entry2") is None
    assert registry.calls == [
        ("async_get_device_by_identifier", MAIN, "entry1"),
        ("async_get_device_by_identifier", MAIN, "entry1"),
        ("async_get_device_by_identifier", MAIN, "entry2"),
    ]


def test_falls_back_to_global_lookup_on_older_ha():
    registry = _Registry(per_entry=False)
    assert not hasattr(registry, "async_get_device_by_identifier")
    assert find_entry_device(registry, CONTROLLER, "entry1") is None
    device = registry.async_get_or_create(
        config_entry_id="entry1", identifiers={CONTROLLER}, name="Ctrl"
    )
    assert find_entry_device(registry, CONTROLLER, "entry1") is device
    assert registry.calls == [
        ("async_get_device", {CONTROLLER}),
        ("async_get_device", {CONTROLLER}),
    ]


def test_two_entries_keep_their_own_devices_and_names():
    registry = _NewRegistry()
    main1, name1, _ = _setup_devices(registry, "entry1", "Dantherm HCH5 Control")
    main2, name2, _ = _setup_devices(registry, "entry2", "Dantherm RCV320 P2")
    assert main1 != main2
    assert (name1, name2) == ("Dantherm HCH5 Control", "Dantherm RCV320 P2")
    # Renaming entry1's device does not leak into entry2's lookup.
    registry.devices[main1].name = "Loft"
    assert find_entry_device(registry, MAIN, "entry1").name == "Loft"
    assert find_entry_device(registry, MAIN, "entry2").name == "Dantherm RCV320 P2"
    assert all(call[0] == "async_get_device_by_identifier" for call in registry.calls)


@pytest.mark.parametrize("registry_cls", [_NewRegistry, lambda: _Registry(per_entry=False)])
def test_reload_reuses_devices_and_keeps_names(registry_cls):
    registry = registry_cls()
    first = _setup_devices(registry, "entry1", "Dantherm HCH5 Control")
    # A later release (or a model read over Modbus) proposes a new default name.
    second = _setup_devices(registry, "entry1", "Dantherm HCH 5 MKII")
    assert second == first
    assert second[1:] == ("Dantherm HCH5 Control", const.CONTROLLER_DEVICE_NAME)
    assert len(registry.devices) == 2


def test_setup_no_longer_uses_the_deprecated_lookup():
    source = (
        Path(__file__).parents[1] / "custom_components" / "hch_passivelink" / "__init__.py"
    ).read_text()
    assert ".async_get_device(" not in source
    assert 'find_entry_device(device_registry, (DOMAIN, "hch5_mk1_hac1"), entry.entry_id)' in source
    assert '(DOMAIN, "hch5_pi_controller"), entry.entry_id' in source


def test_with_the_real_device_registry(tmp_path):
    """Exercise the helper against the installed HA's real DeviceRegistry."""
    pytest.importorskip("homeassistant")
    from homeassistant.config_entries import ConfigEntries, ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers import device_registry as dr

    async def run():
        hass = HomeAssistant(str(tmp_path))
        hass.config_entries = ConfigEntries(hass, {})
        try:
            try:
                for entry_id in ("entry1", "entry2"):
                    hass.config_entries._entries[entry_id] = ConfigEntry(
                        domain=DOMAIN, title=entry_id, data={}, options={}, source="user",
                        version=1, minor_version=1, unique_id=entry_id, discovery_keys={},
                        subentries_data=None, entry_id=entry_id,
                    )
            except (AttributeError, TypeError) as err:  # internals differ in this HA version
                return str(err)
            registry = dr.DeviceRegistry(hass)
            await registry.async_load()
            native = hasattr(registry, "async_get_device_by_identifier")

            first = _setup_devices(registry, "entry1", "Dantherm HCH5 Control")
            again = _setup_devices(registry, "entry1", "Dantherm HCH 5 MKII")
            assert again == first
            assert len(registry.devices) == 2
            assert registry.async_get(first[0]).name == "Dantherm HCH5 Control"

            other = _setup_devices(registry, "entry2", "Dantherm RCV320 P2")
            if native:
                # HA 2026.8+: entry2 gets a device of its own, and a reload reuses it.
                assert "entry2" in registry.async_get(other[0]).config_entries
                assert find_entry_device(registry, MAIN, "entry1").id == first[0]
                count = len(registry.devices)
                assert _setup_devices(registry, "entry2", "Dantherm RCV320 P2") == other
                assert len(registry.devices) == count
            else:
                # Older HA: identifiers are global, so both entries share one device.
                assert other[0] == first[0]
                assert len(registry.devices) == 2

            if not native:
                # Same registry data through the new-style call (as on 2026.8+).
                def by_identifier(identifier, config_entry_id):
                    return next(
                        (
                            d for d in registry.devices.values()
                            if identifier in d.identifiers and config_entry_id in d.config_entries
                        ),
                        None,
                    )

                registry.async_get_device_by_identifier = by_identifier
                assert find_entry_device(registry, MAIN, "entry1").id == first[0]
                assert find_entry_device(registry, MAIN, "entry3") is None
        finally:
            await hass.async_stop(force=True)
        return None

    skipped = asyncio.run(run())
    if skipped:
        pytest.skip(f"cannot build config entries in this HA version: {skipped}")
