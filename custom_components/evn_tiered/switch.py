"""Công tắc bật/tắt từng loại báo cáo và từng kênh gửi."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import slugify

from .const import REPORT_SWITCHES
from .entity import EvnEntity
from .notifier import channel_key, channels

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    cfg = {**entry.data, **entry.options}
    entities: list[EvnSwitch] = [
        EvnSwitch(entry, key, translation_key=key) for key in REPORT_SWITCHES
    ]
    for kind, target in channels(cfg):
        st = hass.states.get(target) if kind == "telegram" else None
        label = st.name if st else target.removeprefix("notify.").removeprefix("mobile_app_")
        entities.append(
            EvnSwitch(entry, channel_key(kind, target), translation_key=f"channel_{kind}",
                      placeholders={"target": label}, uid=f"ch_{kind}_{slugify(target)}")
        )
    async_add_entities(entities)


class EvnSwitch(EvnEntity, SwitchEntity):
    """Một công tắc lưu trong storage của integration."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, key: str, *, translation_key: str,
                 placeholders: dict[str, str] | None = None, uid: str | None = None) -> None:
        super().__init__(entry, uid or key)
        self._key = key
        self._attr_translation_key = translation_key
        self._attr_translation_placeholders = placeholders or {}

    @property
    def is_on(self) -> bool:
        return self.tracker.switches.get(self._key, True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.tracker.set_switch(self._key, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.tracker.set_switch(self._key, False)
        self.async_write_ha_state()
