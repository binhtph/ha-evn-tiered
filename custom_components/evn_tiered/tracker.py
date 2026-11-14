"""Bộ đếm sản lượng theo kỳ / ngày, lịch sử chốt ngày và chốt kỳ, lưu bền qua khởi động lại."""

from __future__ import annotations

from collections.abc import Callable
import csv
from datetime import date, datetime, timedelta
import io
import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfEnergy,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .calc import (
    Tier,
    breakdown,
    current_tier,
    next_period_start,
    parse_tiers,
    period_start_for,
    price_table,
    round_half_up,
    tier_cost,
)
from .const import (
    CONF_BILLING_DAY,
    CONF_SOURCES,
    CONF_VAT,
    DOMAIN,
    EVENT_DAY_CLOSED,
    EVENT_PERIOD_CLOSED,
    EVENT_TIER_CHANGED,
    REPORT_SWITCHES,
    SAVE_DELAY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

_UNIT_FACTOR = {
    UnitOfEnergy.WATT_HOUR: 0.001,
    UnitOfEnergy.KILO_WATT_HOUR: 1.0,
    UnitOfEnergy.MEGA_WATT_HOUR: 1000.0,
}
_NOISE_KWH = 0.01

DAILY_HEADER = [
    "Ngay", "kWh", "Luy_ke_ky_kWh", "Tien_ngay_chua_VAT", "Luy_ke_chua_VAT",
    "Luy_ke_co_VAT", "Bac", "VAT_pct", "Ghi_chu",
]
MONTHLY_HEADER = ["Thang", "kWh", "Bac", "Chua_VAT", "Co_VAT", "VAT_pct", "So_ngay", "Bang_gia"]


def storage_key(entry_id: str) -> str:
    """Khóa file lưu trong .storage."""
    return f"{DOMAIN}.{entry_id}"


def period_name(start: date) -> str:
    """Tên kỳ hiển thị: MM/YYYY theo tháng bắt đầu kỳ."""
    return start.strftime("%m/%Y")


def _num(value: Any) -> float:
    try:
        return float(str(value).strip().replace(" ", ""))
    except (TypeError, ValueError):
        return 0.0


class EvnTracker:
    """Theo dõi sensor energy nguồn, cộng dồn sản lượng, giữ lịch sử."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}
        self.sources: list[str] = list(cfg[CONF_SOURCES])
        self.billing_day = int(cfg[CONF_BILLING_DAY])
        self.vat_percent = float(cfg[CONF_VAT])
        self.tiers: list[Tier] = parse_tiers(cfg)

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []

        today = dt_util.now().date()
        self.period_start: date = period_start_for(today, self.billing_day)
        self.period_kwh = 0.0
        self.day: date = today
        self.day_kwh = 0.0
        self.readings: dict[str, float] = {}
        # Lịch sử: ngày đã chốt (iso -> bản ghi) và kỳ đã chốt (theo thứ tự thời gian).
        self.days: dict[str, dict[str, Any]] = {}
        self.periods: list[dict[str, Any]] = []
        self.switches: dict[str, bool] = {}
        self._last_tier: int | None = None

    # ---------------------------------------------------------------- vòng đời
    async def async_setup(self) -> None:
        """Nạp dữ liệu đã lưu và bắt đầu lắng nghe."""
        data = await self._store.async_load() or {}
        if "period_start" in data:
            self.period_start = date.fromisoformat(data["period_start"])
            self.period_kwh = float(data.get("period_kwh", 0.0))
        if "day" in data:
            self.day = date.fromisoformat(data["day"])
            self.day_kwh = float(data.get("day_kwh", 0.0))
        self.readings = {
            eid: float(v) for eid, v in data.get("readings", {}).items() if eid in self.sources
        }
        self.days = dict(data.get("days", {}))
        self.periods = list(data.get("periods", []))
        if not self.periods and data.get("last_period"):  # dữ liệu v1.0
            lp = data["last_period"]
            self.periods.append(
                {"start": lp["start"], "end": lp["end"], "kwh": lp["kwh"], "cost_vat": lp["cost"]}
            )
        self.switches = {key: True for key in REPORT_SWITCHES}
        self.switches.update(data.get("switches", {}))
        self._last_tier = data.get("last_tier")

        self._rollover(dt_util.now().date())
        for entity_id in self.sources:
            self._process(entity_id, self.hass.states.get(entity_id))
        if self._last_tier is None:
            self._last_tier = self.current_tier
        self._schedule_save()

        self._unsubs.append(
            async_track_state_change_event(self.hass, self.sources, self._on_state)
        )
        self._unsubs.append(
            async_track_time_change(self.hass, self._on_midnight, hour=0, minute=0, second=1)
        )

    async def async_unload(self) -> None:
        """Ngừng lắng nghe và ghi dữ liệu."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self._data())

    @callback
    def add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Đăng ký callback khi số liệu thay đổi."""
        self._listeners.append(update)

        @callback
        def remove() -> None:
            self._listeners.remove(update)

        return remove

    # ------------------------------------------------------------- xử lý sự kiện
    @callback
    def _on_state(self, event: Event[EventStateChangedData]) -> None:
        self._rollover(dt_util.now().date())
        self._process(event.data["entity_id"], event.data["new_state"])
        self._check_tier()
        self._changed()

    @callback
    def _on_midnight(self, now: datetime) -> None:
        self._rollover(now.date())
        self._changed()

    @callback
    def _rollover(self, today: date) -> None:
        """Chốt ngày, sang kỳ mới nếu cần."""
        if today != self.day:
            self._close_day(note="" if today - self.day == timedelta(days=1) else "Ghi bù: HA tắt qua nửa đêm")
        expected = period_start_for(today, self.billing_day)
        if expected > self.period_start:
            self._close_period(expected)
        elif expected < self.period_start:
            self.period_start = expected  # ngày chốt được đổi sớm hơn: dời mốc, giữ số
        if today != self.day:
            self.day = today
            self.day_kwh = 0.0

    @callback
    def _close_day(self, note: str = "") -> None:
        record = self._day_record(self.day_kwh, self.period_kwh, note)
        self.days[self.day.isoformat()] = record
        self.hass.bus.async_fire(
            EVENT_DAY_CLOSED,
            {"entry_id": self.entry.entry_id, "date": self.day.isoformat(), **record},
        )

    def _day_record(self, kwh: float, cum: float, note: str = "") -> dict[str, Any]:
        before = max(cum - kwh, 0.0)
        cum_cost = tier_cost(cum, self.tiers)
        return {
            "kwh": round(kwh, 3),
            "cum": round(cum, 3),
            "cost": round_half_up(cum_cost) - round_half_up(tier_cost(before, self.tiers)),
            "cum_cost": round_half_up(cum_cost),
            "cum_cost_vat": round_half_up(self._with_vat(cum_cost)),
            "tier": current_tier(cum, self.tiers)[0] if cum > 0 else 1,
            "vat": self.vat_percent,
            "note": note,
        }

    @callback
    def _close_period(self, new_start: date) -> None:
        record = self._period_record(self.period_start, new_start, self.period_kwh)
        self.periods.append(record)
        _LOGGER.info("%s: chốt kỳ %s, %.2f kWh, %s đ", self.entry.title, record["name"],
                     record["kwh"], record["cost_vat"])
        self.hass.bus.async_fire(
            EVENT_PERIOD_CLOSED, {"entry_id": self.entry.entry_id, **record}
        )
        self.period_start = new_start
        self.period_kwh = 0.0
        self.day_kwh = 0.0
        self._last_tier = 1

    def _period_record(self, start: date, end: date, kwh: float) -> dict[str, Any]:
        cost = tier_cost(kwh, self.tiers)
        return {
            "name": period_name(start),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "kwh": round(kwh, 3),
            "tier": current_tier(kwh, self.tiers)[0] if kwh > 0 else 1,
            "cost": round_half_up(cost),
            "cost_vat": round_half_up(self._with_vat(cost)),
            "vat": self.vat_percent,
            "days": (end - start).days,
            "prices": price_table(self.tiers),
        }

    @callback
    def _check_tier(self) -> None:
        tier = self.current_tier
        if self._last_tier is not None and tier > self._last_tier:
            self.hass.bus.async_fire(
                EVENT_TIER_CHANGED,
                {
                    "entry_id": self.entry.entry_id,
                    "tier": tier,
                    "previous": self._last_tier,
                    "kwh": round(self.period_kwh, 2),
                    "price_vat": round_half_up(self.current_price),
                },
            )
        self._last_tier = tier

    @callback
    def _process(self, entity_id: str, state: State | None) -> None:
        """Cộng phần chênh lệch của một sensor nguồn."""
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return
        try:
            value = float(state.state)
        except ValueError:
            return
        value *= _UNIT_FACTOR.get(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT), 1.0)

        previous = self.readings.get(entity_id)
        if previous is None:
            self.readings[entity_id] = value
            return
        if value == 0 and previous > 0:
            return  # về 0 thoáng qua (ổ cắm mất kết nối); reset thật sẽ xử lý ở số kế tiếp
        delta = value - previous
        if delta < 0:
            if -delta < _NOISE_KWH:
                return
            _LOGGER.debug("%s giảm %s -> %s, coi như reset", entity_id, previous, value)
            delta = value
        self.readings[entity_id] = value
        if delta > 0:
            self.period_kwh += delta
            self.day_kwh += delta

    @callback
    def _changed(self) -> None:
        self._schedule_save()
        for update in list(self._listeners):
            update()

    @callback
    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    def _data(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start.isoformat(),
            "period_kwh": self.period_kwh,
            "day": self.day.isoformat(),
            "day_kwh": self.day_kwh,
            "readings": self.readings,
            "days": self.days,
            "periods": self.periods,
            "switches": self.switches,
            "last_tier": self._last_tier,
        }

    # ------------------------------------------------------------------ dịch vụ
    @callback
    def set_switch(self, key: str, value: bool) -> None:
        self.switches[key] = value
        self._changed()

    @callback
    def set_period_energy(self, kwh: float) -> None:
        """Hiệu chỉnh sản lượng kỳ hiện tại (vd. theo chỉ số công tơ EVN)."""
        self.period_kwh = max(kwh, 0.0)
        self.day_kwh = min(self.day_kwh, self.period_kwh)
        self._last_tier = self.current_tier
        self._changed()

    @callback
    def reset_period(self) -> None:
        """Chốt kỳ hiện tại ngay và bắt đầu kỳ mới từ 0."""
        self._close_period(dt_util.now().date())
        self._changed()

    async def async_import_legacy(self, folder: str, today_entity: str | None) -> dict[str, int]:
        """Nhập CSV của Energy Monitor cũ (energy_daily_YYYY.csv, energy_monthly.csv)."""
        rows = await self.hass.async_add_executor_job(_read_legacy, Path(folder))
        result = {"thang": 0, "ngay": 0}

        known = {p["start"] for p in self.periods}
        for row in rows["monthly"]:
            try:
                month, year = (int(x) for x in row["Thang"].split("/"))
            except (KeyError, ValueError):
                continue
            start = date(year, month, 1)
            if start >= self.period_start or start.isoformat() in known:
                continue
            end = next_period_start(start)
            self.periods.append({
                "name": period_name(start),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "kwh": round(_num(row.get("kWh")), 3),
                "tier": int(_num(row.get("Bac"))),
                "cost": round_half_up(_num(row.get("Chua_VAT"))),
                "cost_vat": round_half_up(_num(row.get("Co_VAT"))),
                "vat": _num(row.get("VAT_pct")),
                "days": int(_num(row.get("So_ngay"))) or (end - start).days,
                "prices": row.get("Bang_gia", ""),
                "imported": True,
            })
            result["thang"] += 1
        self.periods.sort(key=lambda p: p["start"])

        today = dt_util.now().date()
        for row in rows["daily"]:
            try:
                d = datetime.strptime(row["Ngay"].strip(), "%d/%m/%Y").date()
            except (KeyError, ValueError):
                continue
            if d >= today:
                continue
            iso = d.isoformat()
            kwh = _num(row.get("kWh"))
            existing = self.days.get(iso)
            if existing and existing["kwh"] >= kwh:
                continue
            self.days[iso] = {
                "kwh": round(kwh, 3),
                "cum": round(_num(row.get("Luy_ke_thang_kWh")), 3),
                "cost": round_half_up(_num(row.get("Tien_ngay_chua_VAT"))),
                "cum_cost": round_half_up(_num(row.get("Luy_ke_chua_VAT"))),
                "cum_cost_vat": round_half_up(_num(row.get("Luy_ke_co_VAT"))),
                "tier": int(_num(row.get("Bac"))),
                "vat": _num(row.get("VAT_pct")),
                "note": (row.get("Ghi_chu") or "").strip(),
            }
            result["ngay"] += 1

        # Phần điện hôm nay trước khi cài integration (vd. utility meter Điện ngày cũ).
        if today_entity and (st := self.hass.states.get(today_entity)) is not None:
            value = _num(st.state) * _UNIT_FACTOR.get(
                st.attributes.get(ATTR_UNIT_OF_MEASUREMENT), 1.0
            )
            if value > self.day_kwh:
                self.day_kwh = value

        self._recompute_current_period(today)
        self._last_tier = self.current_tier
        self._changed()
        return result

    @callback
    def _recompute_current_period(self, today: date) -> None:
        """Tính lại các ngày của kỳ hiện tại theo biểu giá đang cài, và tổng kWh kỳ."""
        cum = 0.0
        d = self.period_start
        while d < today:
            iso = d.isoformat()
            if iso in self.days:
                kwh = self.days[iso]["kwh"]
                cum += kwh
                self.days[iso] = self._day_record(kwh, cum, self.days[iso].get("note", ""))
            d += timedelta(days=1)
        self.period_kwh = cum + self.day_kwh

    # ------------------------------------------------------------ số liệu xuất ra
    def _with_vat(self, amount: float) -> float:
        return amount * (1 + self.vat_percent / 100)

    @property
    def period_cost_before_vat(self) -> float:
        return tier_cost(self.period_kwh, self.tiers)

    @property
    def period_cost(self) -> float:
        return self._with_vat(self.period_cost_before_vat)

    @property
    def today_cost_before_vat(self) -> float:
        before = max(self.period_kwh - self.day_kwh, 0.0)
        return tier_cost(self.period_kwh, self.tiers) - tier_cost(before, self.tiers)

    @property
    def today_cost(self) -> float:
        return self._with_vat(self.today_cost_before_vat)

    @property
    def current_tier(self) -> int:
        return current_tier(self.period_kwh, self.tiers)[0]

    @property
    def current_price(self) -> float:
        return self._with_vat(current_tier(self.period_kwh, self.tiers)[1])

    @property
    def period_end(self) -> date:
        return next_period_start(self.period_start)

    @property
    def last_period(self) -> dict[str, Any] | None:
        return self.periods[-1] if self.periods else None

    def yesterday(self) -> tuple[str, dict[str, Any]] | None:
        iso = (self.day - timedelta(days=1)).isoformat()
        return (iso, self.days[iso]) if iso in self.days else None

    def breakdown(self) -> list[dict[str, Any]]:
        return [
            {"bac": p["tier"], "kwh": round(p["kwh"], 2), "don_gia": p["price"],
             "thanh_tien": round_half_up(p["amount"])}
            for p in breakdown(self.period_kwh, self.tiers)
        ]

    def projection(self) -> dict[str, float] | None:
        """Dự tính sản lượng/tiền cả kỳ theo tốc độ dùng hiện tại."""
        start = dt_util.start_of_local_day(self.period_start)
        end = dt_util.start_of_local_day(self.period_end)
        total = (end - start).total_seconds() / 86400
        elapsed = (dt_util.now() - start).total_seconds() / 86400
        if elapsed < 1:
            return None
        kwh = self.period_kwh * total / min(elapsed, total)
        return {"kwh": kwh, "cost": self._with_vat(tier_cost(kwh, self.tiers)),
                "elapsed_days": elapsed, "total_days": total}

    def year_summary(self, year: int) -> dict[str, Any]:
        """Tổng của các kỳ bắt đầu trong năm (gồm kỳ đang chạy nếu thuộc năm đó)."""
        items = [p for p in self.periods if p["start"][:4] == str(year)]
        kwh = sum(p["kwh"] for p in items)
        cost = sum(p.get("cost", 0) for p in items)
        cost_vat = sum(p.get("cost_vat", 0) for p in items)
        count = len(items)
        if self.period_start.year == year:
            kwh += self.period_kwh
            cost += round_half_up(self.period_cost_before_vat)
            cost_vat += round_half_up(self.period_cost)
            count += 1
        result: dict[str, Any] = {"kwh": kwh, "cost": cost, "cost_vat": cost_vat, "count": count}
        if items:
            hi = max(items, key=lambda p: p["kwh"])
            lo = min(items, key=lambda p: p["kwh"])
            result["max"] = {"name": hi["name"], "kwh": hi["kwh"]}
            result["min"] = {"name": lo["name"], "kwh": lo["kwh"]}
        return result

    # ------------------------------------------------------------------ CSV
    def csv_daily(self, year: int) -> str:
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(DAILY_HEADER)
        rows = sorted((k, v) for k, v in self.days.items() if k[:4] == str(year))
        for iso, r in rows:
            d = date.fromisoformat(iso)
            w.writerow([d.strftime("%d/%m/%Y"), f"{r['kwh']:.2f}", f"{r['cum']:.2f}", r["cost"],
                        r["cum_cost"], r["cum_cost_vat"], r["tier"], f"{r['vat']:g}",
                        r.get("note", "")])
        if self.day.year == year and self.day_kwh > 0:
            r = self._day_record(self.day_kwh, self.period_kwh, "Đang đếm (hôm nay)")
            w.writerow([self.day.strftime("%d/%m/%Y"), f"{r['kwh']:.2f}", f"{r['cum']:.2f}",
                        r["cost"], r["cum_cost"], r["cum_cost_vat"], r["tier"],
                        f"{r['vat']:g}", r["note"]])
        return "﻿" + out.getvalue()

    def csv_monthly(self) -> str:
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(MONTHLY_HEADER)
        for p in self.periods:
            w.writerow([p["name"], f"{p['kwh']:.2f}", p.get("tier", ""), p.get("cost", ""),
                        p.get("cost_vat", ""), f"{p.get('vat', 0):g}", p.get("days", ""),
                        p.get("prices", "")])
        return "﻿" + out.getvalue()

    def years(self) -> list[int]:
        ys = {int(k[:4]) for k in self.days} | {self.day.year}
        return sorted(ys)


def _read_legacy(folder: Path) -> dict[str, list[dict[str, str]]]:
    """Đọc CSV cũ (chạy trong executor)."""
    result: dict[str, list[dict[str, str]]] = {"daily": [], "monthly": []}
    if not folder.is_dir():
        raise FileNotFoundError(f"Không thấy thư mục {folder}")
    for path in sorted(folder.glob("energy_daily_*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as f:
            result["daily"].extend(csv.DictReader(f))
    monthly = folder / "energy_monthly.csv"
    if monthly.is_file():
        with monthly.open(encoding="utf-8-sig", newline="") as f:
            result["monthly"].extend(csv.DictReader(f))
    return result
