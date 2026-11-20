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
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .calc import price_table, round_half_up
from .const import (
    ATTR_ENERGY,
    CURRENCY,
    HISTORY_KINDS,
    LEGACY_DIR,
    SERVICE_EXPORT_CSV,
    SERVICE_GET_HISTORY,
    SERVICE_IMPORT_LEGACY,
    SERVICE_RESET_PERIOD,
    SERVICE_SEND_REPORT,
    SERVICE_SET_PERIOD_ENERGY,
)
from .entity import EvnEntity
from .export import async_export
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


def _year_reset(t: EvnTracker) -> datetime:
    return dt_util.start_of_local_day(t.day.replace(month=1, day=1))


def _period_attrs(t: EvnTracker) -> dict[str, Any]:
    return {
        "ky_bat_dau": t.period_start.isoformat(),
        "ky_ket_thuc": t.period_end.isoformat(),
        "san_luong_kwh": round(t.period_kwh, 2),
        "bac": t.current_tier,
        "tien_chua_vat": round_half_up(t.period_cost_before_vat),
        "vat_phan_tram": t.vat_percent,
        "tien_vat": round_half_up(t.period_cost) - round_half_up(t.period_cost_before_vat),
        "chi_tiet_bac": t.breakdown(),
    }


def _today_attrs(t: EvnTracker) -> dict[str, Any]:
    return {"tien_chua_vat": round_half_up(t.today_cost_before_vat)}


def _yesterday_attrs(t: EvnTracker) -> dict[str, Any]:
    if (y := t.yesterday()) is None:
        return {}
    iso, r = y
    return {"ngay": iso, "bac": r["tier"], "tien_chua_vat": r["cost"], "ghi_chu": r.get("note", "")}


def _yesterday_cost(t: EvnTracker) -> float | None:
    if (y := t.yesterday()) is None:
        return None
    r = y[1]
    return round_half_up(r["cost"] * (1 + r.get("vat", t.vat_percent) / 100))


def _projection_attrs(t: EvnTracker) -> dict[str, Any]:
    if (p := t.projection()) is None:
        return {}
    return {
        "san_luong_du_tinh_kwh": round(p["kwh"], 1),
        "so_ngay_da_qua": round(p["elapsed_days"], 1),
        "so_ngay_ca_ky": round(p["total_days"]),
    }


def _last_attrs(t: EvnTracker) -> dict[str, Any]:
    if not (p := t.last_period):
        return {}
    return {"ky": p.get("name"), "ky_bat_dau": p["start"], "ky_ket_thuc": p["end"],
            "bac": p.get("tier"), "tien_chua_vat": p.get("cost"), "bang_gia": p.get("prices")}


def _year_attrs(year_offset: int) -> Callable[[EvnTracker], dict[str, Any]]:
    def fn(t: EvnTracker) -> dict[str, Any]:
        year = t.day.year + year_offset
        s = t.year_summary(year)
        attrs: dict[str, Any] = {"nam": year, "so_ky": s["count"], "tien_chua_vat": s["cost"],
                                 "san_luong_kwh": round(s["kwh"], 2)}
        if "max" in s:
            attrs["ky_cao_nhat"] = f"{s['max']['name']} ({s['max']['kwh']:.2f} kWh)"
            attrs["ky_thap_nhat"] = f"{s['min']['name']} ({s['min']['kwh']:.2f} kWh)"
        return attrs

    return fn


def _year_value(year_offset: int, field: str) -> Callable[[EvnTracker], float | None]:
    def fn(t: EvnTracker) -> float | None:
        s = t.year_summary(t.day.year + year_offset)
        if year_offset and s["count"] == 0:
            return None
        return s[field]

    return fn


def _money(key: str, **kw: Any) -> EvnSensorDescription:
    return EvnSensorDescription(
        key=key, translation_key=key, device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement=CURRENCY, suggested_display_precision=0, **kw,
    )


def _energy(key: str, **kw: Any) -> EvnSensorDescription:
    return EvnSensorDescription(
        key=key, translation_key=key, device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, suggested_display_precision=2, **kw,
    )


SENSORS: tuple[EvnSensorDescription, ...] = (
    _money("today_cost", state_class=SensorStateClass.TOTAL, value_fn=lambda t: t.today_cost,
           attrs_fn=_today_attrs, last_reset_fn=_day_reset),
    _energy("today_energy", state_class=SensorStateClass.TOTAL, value_fn=lambda t: t.day_kwh,
            last_reset_fn=_day_reset),
    _money("yesterday_cost", value_fn=_yesterday_cost, attrs_fn=_yesterday_attrs),
    _energy("yesterday_energy", value_fn=lambda t: y[1]["kwh"] if (y := t.yesterday()) else None,
            attrs_fn=_yesterday_attrs),
    _money("period_cost", state_class=SensorStateClass.TOTAL, value_fn=lambda t: t.period_cost,
           attrs_fn=_period_attrs, last_reset_fn=_period_reset),
    _energy("period_energy", state_class=SensorStateClass.TOTAL, value_fn=lambda t: t.period_kwh,
            last_reset_fn=_period_reset),
    _money("projected_cost", value_fn=lambda t: p["cost"] if (p := t.projection()) else None,
           attrs_fn=_projection_attrs),
    _money("year_cost", state_class=SensorStateClass.TOTAL, value_fn=_year_value(0, "cost_vat"),
           attrs_fn=_year_attrs(0), last_reset_fn=_year_reset),
    _energy("year_energy", state_class=SensorStateClass.TOTAL, value_fn=_year_value(0, "kwh"),
            attrs_fn=_year_attrs(0), last_reset_fn=_year_reset),
    _money("last_year_cost", value_fn=_year_value(-1, "cost_vat"), attrs_fn=_year_attrs(-1)),
    _energy("last_year_energy", value_fn=_year_value(-1, "kwh"), attrs_fn=_year_attrs(-1)),
    _money("last_period_cost", value_fn=lambda t: t.last_period.get("cost_vat") if t.last_period else None,
           attrs_fn=_last_attrs),
    _energy("last_period_energy", value_fn=lambda t: t.last_period["kwh"] if t.last_period else None,
            attrs_fn=_last_attrs),
    EvnSensorDescription(key="current_tier", translation_key="current_tier",
                         value_fn=lambda t: t.current_tier),
    EvnSensorDescription(
        key="current_price", translation_key="current_price",
        native_unit_of_measurement=f"{CURRENCY}/{UnitOfEnergy.KILO_WATT_HOUR}",
        suggested_display_precision=0, value_fn=lambda t: t.current_price,
        attrs_fn=lambda t: {"bang_gia": _price_table(t)},
    ),
)


def _price_table(t: EvnTracker) -> str:
    return f"{price_table(t.tiers)} (chưa VAT), VAT {t.vat_percent:g}%"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    """Tạo các sensor cho một config entry."""
    async_add_entities(EvnSensor(entry, desc) for desc in SENSORS)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_PERIOD_ENERGY,
        {vol.Required(ATTR_ENERGY): vol.All(vol.Coerce(float), vol.Range(min=0))},
        "async_set_period_energy",
    )
    platform.async_register_entity_service(SERVICE_RESET_PERIOD, None, "async_reset_period")
    platform.async_register_entity_service(
        SERVICE_IMPORT_LEGACY,
        {vol.Optional("folder", default=LEGACY_DIR): cv.string,
         vol.Optional("today_entity"): cv.entity_id},
        "async_import_legacy",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_EXPORT_CSV,
        {vol.Optional("year"): vol.Coerce(int), vol.Optional("telegram", default=False): cv.boolean},
        "async_export_csv",
        supports_response=SupportsResponse.OPTIONAL,
    )
    platform.async_register_entity_service(
        SERVICE_SEND_REPORT,
        {vol.Required("kind"): vol.In(["daily", "period", "year"])},
        "async_send_report",
    )
    platform.async_register_entity_service(
        SERVICE_GET_HISTORY,
        {
            vol.Optional("kind", default="summary"): vol.In(HISTORY_KINDS),
            vol.Optional("year"): vol.All(vol.Coerce(int), vol.Range(min=2000, max=2100)),
            vol.Optional("month"): vol.All(vol.Coerce(int), vol.Range(min=1, max=12)),
        },
        "async_get_history",
        supports_response=SupportsResponse.ONLY,
    )


class EvnSensor(EvnEntity, SensorEntity):
    """Một sensor đọc số liệu từ EvnTracker."""

    entity_description: EvnSensorDescription

    def __init__(self, entry: ConfigEntry, desc: EvnSensorDescription) -> None:
        super().__init__(entry, desc.key)
        self.entity_description = desc

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.tracker.add_listener(self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | int | None:
        return self.entity_description.value_fn(self.tracker)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.tracker) if fn else None

    @property
    def last_reset(self) -> datetime | None:
        fn = self.entity_description.last_reset_fn
        return fn(self.tracker) if fn else None

    # ---- dịch vụ (gọi trên bất kỳ sensor nào của integration)
    async def async_set_period_energy(self, energy: float) -> None:
        self.tracker.set_period_energy(energy)

    async def async_reset_period(self) -> None:
        self.tracker.reset_period()

    async def async_import_legacy(self, folder: str = LEGACY_DIR,
                                  today_entity: str | None = None) -> ServiceResponse:
        return await self.tracker.async_import_legacy(folder, today_entity)

    async def async_export_csv(self, year: int | None = None, telegram: bool = False) -> ServiceResponse:
        return await async_export(self.hass, self.runtime, year=year, telegram=telegram)

    async def async_send_report(self, kind: str) -> None:
        await self.runtime.notifier.async_send_report(kind)

    async def async_get_history(self, kind: str = "summary", year: int | None = None,
                                month: int | None = None) -> ServiceResponse:
        return self.tracker.history(kind, year, month)
