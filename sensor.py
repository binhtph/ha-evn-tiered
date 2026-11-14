"""Các sensor của Tiền điện bậc thang EVN."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_ENERGY,
    CURRENCY,
    DOMAIN,
    SERVICE_RESET_PERIOD,
    SERVICE_SET_PERIOD_ENERGY,
)
from .tracker import EvnTracker

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class EvnSensorDescription(SensorEntityDescription):
    """Mô tả sensor kèm hàm lấy giá trị."""

    value_fn: Callable[[EvnTracker], float | int | None]
    attrs_fn: Callable[[EvnTracker], dict[str, Any]] | None = None
    last_reset_fn: Callable[[EvnTracker], datetime | None] | None = None


def _period_reset(t: EvnTracker) -> datetime:
    return dt_util.start_of_local_day(t.period_start)


def _day_reset(t: EvnTracker) -> datetime:
    return dt_util.start_of_local_day(t.day)


def _period_attrs(t: EvnTracker) -> dict[str, Any]:
    return {
        "ky_bat_dau": t.period_start.isoformat(),
        "ky_ket_thuc": t.period_end.isoformat(),
        "san_luong_kwh": round(t.period_kwh, 2),
        "tien_chua_vat": round(t.period_cost_before_vat),
        "vat_phan_tram": t.vat_percent,
        "tien_vat": round(t.period_cost - t.period_cost_before_vat),
        "chi_tiet_bac": t.breakdown(),
    }


def _projection_attrs(t: EvnTracker) -> dict[str, Any]:
    p = t.projection()
    if p is None:
        return {}
    return {
        "san_luong_du_tinh_kwh": round(p["kwh"], 1),
        "so_ngay_da_qua": round(p["elapsed_days"], 1),
        "so_ngay_ca_ky": round(p["total_days"]),
    }


def _last_attrs(t: EvnTracker) -> dict[str, Any]:
    if not t.last_period:
        return {}
    return {"ky_bat_dau": t.last_period["start"], "ky_ket_thuc": t.last_period["end"]}


def _projection_cost(t: EvnTracker) -> float | None:
    p = t.projection()
    return None if p is None else p["cost"]


SENSORS: tuple[EvnSensorDescription, ...] = (
    EvnSensorDescription(
        key="period_cost",
        translation_key="period_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=0,
        value_fn=lambda t: t.period_cost,
        attrs_fn=_period_attrs,
        last_reset_fn=_period_reset,
    ),
    EvnSensorDescription(
        key="period_energy",
        translation_key="period_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda t: t.period_kwh,
        last_reset_fn=_period_reset,
    ),
    EvnSensorDescription(
        key="today_cost",
        translation_key="today_cost",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=0,
        value_fn=lambda t: t.today_cost,
        last_reset_fn=_day_reset,
    ),
    EvnSensorDescription(
        key="today_energy",
        translation_key="today_energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=lambda t: t.day_kwh,
        last_reset_fn=_day_reset,
    ),
    EvnSensorDescription(
        key="current_tier",
        translation_key="current_tier",
        value_fn=lambda t: t.current_tier,
    ),
    EvnSensorDescription(
        key="current_price",
        translation_key="current_price",
        native_unit_of_measurement=f"{CURRENCY}/{UnitOfEnergy.KILO_WATT_HOUR}",
        suggested_display_precision=0,
        value_fn=lambda t: t.current_price,
    ),
    EvnSensorDescription(
        key="projected_cost",
        translation_key="projected_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=0,
        value_fn=_projection_cost,
        attrs_fn=_projection_attrs,
    ),
    EvnSensorDescription(
        key="last_period_cost",
        translation_key="last_period_cost",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda t: t.last_period["cost"] if t.last_period else None,
        attrs_fn=_last_attrs,
    ),
    EvnSensorDescription(
        key="last_period_energy",
        translation_key="last_period_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda t: t.last_period["kwh"] if t.last_period else None,
        attrs_fn=_last_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Tạo các sensor cho một config entry."""
    tracker: EvnTracker = entry.runtime_data
    async_add_entities(EvnSensor(entry, tracker, desc) for desc in SENSORS)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_PERIOD_ENERGY,
        {vol.Required(ATTR_ENERGY): vol.All(vol.Coerce(float), vol.Range(min=0))},
        "async_set_period_energy",
    )
    platform.async_register_entity_service(
        SERVICE_RESET_PERIOD, {}, "async_reset_period"
    )


class EvnSensor(SensorEntity):
    """Một sensor đọc số liệu từ EvnTracker."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: EvnSensorDescription

    def __init__(
        self, entry: ConfigEntry, tracker: EvnTracker, desc: EvnSensorDescription
    ) -> None:
        self.entity_description = desc
        self._tracker = tracker
        self._attr_unique_id = f"{entry.entry_id}_{desc.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="EVN (tính cục bộ)",
            model="Biểu giá bậc thang",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._tracker.add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | int | None:
        return self.entity_description.value_fn(self._tracker)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self._tracker)

    @property
    def last_reset(self) -> datetime | None:
        if self.entity_description.last_reset_fn is None:
            return None
        return self.entity_description.last_reset_fn(self._tracker)

    async def async_set_period_energy(self, energy: float) -> None:
        """Dịch vụ: đặt sản lượng kỳ hiện tại."""
        self._tracker.set_period_energy(energy)

    async def async_reset_period(self) -> None:
        """Dịch vụ: bắt đầu kỳ mới ngay."""
        self._tracker.reset_period()
