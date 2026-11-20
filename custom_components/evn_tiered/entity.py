"""Lớp entity dùng chung."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, VERSION


class EvnEntity(Entity):
    """Entity gắn với thiết bị của một entry."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, key: str) -> None:
        self.runtime = entry.runtime_data
        self.tracker = entry.runtime_data.tracker
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="EVN (local calculation)",
            model="Tiered tariff",
            sw_version=VERSION,
            entry_type=DeviceEntryType.SERVICE,
        )
