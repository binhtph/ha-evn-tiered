"""Tiền điện bậc thang EVN: tính tiền điện theo bậc từ sensor energy bất kỳ."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_VERSION
from .tracker import EvnTracker, storage_key

PLATFORMS = [Platform.SENSOR]

type EvnConfigEntry = ConfigEntry[EvnTracker]


async def async_setup_entry(hass: HomeAssistant, entry: EvnConfigEntry) -> bool:
    """Thiết lập một entry."""
    tracker = EvnTracker(hass, entry)
    await tracker.async_setup()
    entry.runtime_data = tracker
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: EvnConfigEntry) -> None:
    """Đổi giá / VAT / sensor xong thì nạp lại."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: EvnConfigEntry) -> bool:
    """Gỡ entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: EvnConfigEntry) -> None:
    """Xóa entry: xóa luôn dữ liệu đã lưu."""
    await Store(hass, STORAGE_VERSION, storage_key(entry.entry_id)).async_remove()
