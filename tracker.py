"""Bộ đếm sản lượng theo kỳ / theo ngày, lưu bền qua khởi động lại."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
import logging
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
    tier_cost,
)
from .const import (
    CONF_BILLING_DAY,
    CONF_SOURCES,
    CONF_VAT,
    DOMAIN,
    SAVE_DELAY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)

_UNIT_FACTOR = {
    UnitOfEnergy.WATT_HOUR: 0.001,
    UnitOfEnergy.KILO_WATT_HOUR: 1.0,
    UnitOfEnergy.MEGA_WATT_HOUR: 1000.0,
}
# Giảm nhỏ hơn mức này coi là nhiễu số liệu, không phải công tơ bị reset.
_NOISE_KWH = 0.01


def storage_key(entry_id: str) -> str:
    """Khóa file lưu trong .storage."""
    return f"{DOMAIN}.{entry_id}"


class EvnTracker:
    """Theo dõi các sensor energy nguồn và cộng dồn sản lượng."""

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
        self.last_period: dict[str, Any] | None = None

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
            eid: float(value)
            for eid, value in data.get("readings", {}).items()
            if eid in self.sources
        }
        self.last_period = data.get("last_period")

        self._rollover(dt_util.now().date())
        # Bù phần điện tiêu thụ trong lúc HA tắt (nếu sensor nguồn vẫn tăng).
        for entity_id in self.sources:
            self._process(entity_id, self.hass.states.get(entity_id))
        self._schedule_save()

        self._unsubs.append(
            async_track_state_change_event(self.hass, self.sources, self._on_state)
        )
        self._unsubs.append(
            async_track_time_change(
                self.hass, self._on_midnight, hour=0, minute=0, second=1
            )
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
        self._changed()

    @callback
    def _on_midnight(self, now: datetime) -> None:
        self._rollover(now.date())
        self._changed()

    @callback
    def _rollover(self, today: date) -> None:
        """Sang kỳ mới / ngày mới nếu cần."""
        expected = period_start_for(today, self.billing_day)
        if expected > self.period_start:
            self._close_period(expected)
        elif expected < self.period_start:
            # Ngày chốt bị đổi sớm hơn: chỉ dời mốc, giữ sản lượng.
            self.period_start = expected
        if today != self.day:
            self.day = today
            self.day_kwh = 0.0

    @callback
    def _close_period(self, new_start: date) -> None:
        self.last_period = {
            "start": self.period_start.isoformat(),
            "end": new_start.isoformat(),
            "kwh": round(self.period_kwh, 3),
            "cost": round(self._with_vat(tier_cost(self.period_kwh, self.tiers))),
        }
        _LOGGER.info(
            "%s: kết thúc kỳ %s, %.2f kWh, %s đ",
            self.entry.title,
            self.period_start,
            self.period_kwh,
            self.last_period["cost"],
        )
        self.period_start = new_start
        self.period_kwh = 0.0
        self.day_kwh = 0.0

    @callback
    def _process(self, entity_id: str, state: State | None) -> None:
        """Cộng phần chênh lệch của một sensor nguồn."""
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE, ""):
            return
        try:
            value = float(state.state)
        except ValueError:
            return
        unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        value *= _UNIT_FACTOR.get(unit, 1.0)

        previous = self.readings.get(entity_id)
        if previous is None:
            self.readings[entity_id] = value
            return
        if value == 0 and previous > 0:
            # Nhiều ổ cắm báo 0 thoáng qua khi mất kết nối rồi trở lại số cũ;
            # bỏ qua để không cộng trùng. Nếu thực sự reset, số kế tiếp (>0, nhỏ)
            # sẽ được xử lý ở nhánh reset bên dưới.
            return
        delta = value - previous
        if delta < 0:
            if -delta < _NOISE_KWH:
                return  # nhiễu: giữ số đọc cũ
            # Công tơ / sensor bị reset về 0: toàn bộ giá trị mới là phần tăng.
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
            "last_period": self.last_period,
        }

    # ------------------------------------------------------------------ dịch vụ
    @callback
    def set_period_energy(self, kwh: float) -> None:
        """Hiệu chỉnh sản lượng kỳ hiện tại (vd. theo chỉ số công tơ EVN)."""
        self.period_kwh = max(kwh, 0.0)
        self.day_kwh = min(self.day_kwh, self.period_kwh)
        self._changed()

    @callback
    def reset_period(self) -> None:
        """Đóng kỳ hiện tại ngay và bắt đầu kỳ mới từ 0."""
        self._close_period(dt_util.now().date())
        self._changed()

    # ------------------------------------------------------------ số liệu xuất ra
    def _with_vat(self, amount: float) -> float:
        return amount * (1 + self.vat_percent / 100)

    @property
    def period_cost(self) -> float:
        return self._with_vat(tier_cost(self.period_kwh, self.tiers))

    @property
    def period_cost_before_vat(self) -> float:
        return tier_cost(self.period_kwh, self.tiers)

    @property
    def today_cost(self) -> float:
        before = max(self.period_kwh - self.day_kwh, 0.0)
        return self._with_vat(
            tier_cost(self.period_kwh, self.tiers) - tier_cost(before, self.tiers)
        )

    @property
    def current_tier(self) -> int:
        return current_tier(self.period_kwh, self.tiers)[0]

    @property
    def current_price(self) -> float:
        """Đơn giá đã gồm VAT của kWh tiếp theo."""
        return self._with_vat(current_tier(self.period_kwh, self.tiers)[1])

    @property
    def period_end(self) -> date:
        return next_period_start(self.period_start)

    def breakdown(self) -> list[dict[str, Any]]:
        return [
            {
                "bac": part["tier"],
                "kwh": round(part["kwh"], 2),
                "don_gia": part["price"],
                "thanh_tien": round(part["amount"]),
            }
            for part in breakdown(self.period_kwh, self.tiers)
        ]

    def projection(self) -> dict[str, float] | None:
        """Dự tính sản lượng/tiền cả kỳ theo tốc độ dùng hiện tại."""
        start = dt_util.start_of_local_day(self.period_start)
        end = dt_util.start_of_local_day(self.period_end)
        total_days = (end - start).total_seconds() / 86400
        elapsed_days = (dt_util.now() - start).total_seconds() / 86400
        if elapsed_days < 0.5:
            return None
        kwh = self.period_kwh * total_days / min(elapsed_days, total_days)
        return {
            "kwh": kwh,
            "cost": self._with_vat(tier_cost(kwh, self.tiers)),
            "elapsed_days": elapsed_days,
            "total_days": total_days,
        }
