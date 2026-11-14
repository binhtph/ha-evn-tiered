"""Nút xuất CSV và gửi thử báo cáo."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import EvnEntity
from .export import async_export

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([
        EvnButton(entry, "export_csv"),
        EvnButton(entry, "export_csv_telegram"),
        EvnButton(entry, "send_daily_report"),
    ])


class EvnButton(EvnEntity, ButtonEntity):
    """Nút bấm."""

    def __init__(self, entry: ConfigEntry, key: str) -> None:
        super().__init__(entry, key)
        self._key = key
        self._attr_translation_key = key

    async def async_press(self) -> None:
        if self._key == "send_daily_report":
            await self.runtime.notifier.async_send_report("daily")
        else:
            await async_export(self.hass, self.runtime, year=None,
                               telegram=self._key == "export_csv_telegram")
