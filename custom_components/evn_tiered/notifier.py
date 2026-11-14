"""Gửi báo cáo / cảnh báo qua Telegram và app điện thoại."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import html
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .calc import fmt_kwh, fmt_vnd, round_half_up
from .const import (
    CONF_LOST_HOURS,
    CONF_MOBILE,
    CONF_REPORT_TIME,
    CONF_TELEGRAM,
    CONF_TPL_DAILY,
    CONF_TPL_LOST,
    CONF_TPL_PERIOD,
    CONF_TPL_TIER,
    CONF_TPL_YEAR,
    DEFAULT_LOST_HOURS,
    DEFAULT_REPORT_TIME,
    DEFAULT_TEMPLATES,
    EVENT_TIER_CHANGED,
)
from .tracker import EvnTracker, period_name

_LOGGER = logging.getLogger(__name__)


def channel_key(kind: str, target: str) -> str:
    """Khóa công tắc của một kênh gửi."""
    return f"{kind}:{target}"


def channels(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    """Danh sách (loại, đích) đã cấu hình."""
    return [("telegram", t) for t in cfg.get(CONF_TELEGRAM, [])] + [
        ("mobile", m) for m in cfg.get(CONF_MOBILE, [])
    ]


class EvnNotifier:
    """Lập lịch và gửi tin."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, tracker: EvnTracker) -> None:
        self.hass = hass
        self.entry = entry
        self.tracker = tracker
        self.cfg = {**entry.data, **entry.options}
        self._unsubs: list[Callable[[], None]] = []
        self._lost_timers: dict[str, Callable[[], None]] = {}

    @property
    def title(self) -> str:
        return self.entry.title.upper()

    # ---------------------------------------------------------------- vòng đời
    @callback
    def async_setup(self) -> None:
        h, m, s = (int(x) for x in str(self.cfg.get(CONF_REPORT_TIME, DEFAULT_REPORT_TIME)).split(":"))
        self._unsubs.append(
            async_track_time_change(self.hass, self._on_report_time, hour=h, minute=m, second=s)
        )
        self._unsubs.append(self.hass.bus.async_listen(EVENT_TIER_CHANGED, self._on_tier))
        self._unsubs.append(
            async_track_state_change_event(self.hass, self.tracker.sources, self._on_source)
        )
        for eid in self.tracker.sources:
            st = self.hass.states.get(eid)
            if st is None or st.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                self._start_lost_timer(eid)

    @callback
    def async_unload(self) -> None:
        for unsub in self._unsubs:
            unsub()
        for cancel in self._lost_timers.values():
            cancel()
        self._unsubs.clear()
        self._lost_timers.clear()

    # ------------------------------------------------------------- sự kiện
    async def _on_report_time(self, now: datetime) -> None:
        sw = self.tracker.switches
        today = now.date()
        if sw.get("report_daily", True):
            await self.async_send_report("daily")
        last = self.tracker.last_period
        if last and last["end"] == today.isoformat():
            if sw.get("report_period", True):
                await self.async_send_report("period")
            if today.month == 1 and today == self.tracker.period_start and sw.get("report_year", True):
                await self.async_send_report("year")

    async def _on_tier(self, event: Event) -> None:
        if event.data.get("entry_id") != self.entry.entry_id:
            return
        if not self.tracker.switches.get("alert_tier", True):
            return
        variables = self._base_vars()
        variables["don_gia_txt"] = fmt_vnd(event.data.get("price_vat", 0))
        await self._send("tier", f"⬆️ {self.title} › LÊN BẬC {event.data['tier']}", CONF_TPL_TIER, variables)

    @callback
    def _on_source(self, event: Event[EventStateChangedData]) -> None:
        eid = event.data["entity_id"]
        new = event.data["new_state"]
        if new is None or new.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            if eid not in self._lost_timers:
                self._start_lost_timer(eid)
        elif (cancel := self._lost_timers.pop(eid, None)) is not None:
            cancel()

    @callback
    def _start_lost_timer(self, eid: str) -> None:
        hours = float(self.cfg.get(CONF_LOST_HOURS, DEFAULT_LOST_HOURS))

        async def _fire(_now: datetime) -> None:
            self._lost_timers.pop(eid, None)
            if not self.tracker.switches.get("alert_lost", True):
                return
            st = self.hass.states.get(eid)
            name = st.name if st else eid
            await self._send("lost", f"⚠️ {self.title} › MẤT SỐ LIỆU", CONF_TPL_LOST,
                             {"nguon": name, "so_gio": f"{hours:g}"})

        self._lost_timers[eid] = async_call_later(self.hass, timedelta(hours=hours), _fire)

    # ------------------------------------------------------------- nội dung
    def _money(self, no_vat: float, vat_pct: float | None = None) -> dict[str, Any]:
        vat = self.tracker.vat_percent if vat_pct is None else vat_pct
        with_vat = round_half_up(no_vat * (1 + vat / 100))
        return {
            "tien_chua_vat": round_half_up(no_vat),
            "tien_chua_vat_txt": fmt_vnd(no_vat),
            "tien": with_vat,
            "tien_txt": fmt_vnd(with_vat),
        }

    def _base_vars(self) -> dict[str, Any]:
        t = self.tracker
        v: dict[str, Any] = {
            "ten": self.entry.title,
            "vat": f"{t.vat_percent:g}",
            "ky": {
                "ten": period_name(t.period_start),
                "kwh": round(t.period_kwh, 2),
                "kwh_txt": fmt_kwh(t.period_kwh),
                "bac": t.current_tier,
                **self._money(t.period_cost_before_vat),
            },
            "hom_qua": None,
            "du_tinh": None,
        }
        if (y := t.yesterday()) is not None:
            iso, r = y
            v["hom_qua"] = {
                "ngay": f"{iso[8:10]}/{iso[5:7]}",
                "kwh": r["kwh"],
                "kwh_txt": fmt_kwh(r["kwh"]),
                "bac": r["tier"],
                **self._money(r["cost"], r.get("vat")),
            }
        if (p := t.projection()) is not None:
            v["du_tinh"] = {
                "kwh": round(p["kwh"]),
                "kwh_txt": f"{round(p['kwh'])}",
                "tien": round(p["cost"] / 1000) * 1000,
                "tien_txt": fmt_vnd(round(p["cost"] / 1000) * 1000),
            }
        return v

    def _period_vars(self) -> dict[str, Any]:
        v = self._base_vars()
        periods = self.tracker.periods
        last = periods[-1] if periods else None
        if last:
            v["ky_truoc"] = {
                "ten": last["name"],
                "kwh": last["kwh"],
                "kwh_txt": fmt_kwh(last["kwh"]),
                "bac": last.get("tier", ""),
                "tien_chua_vat": last.get("cost", 0),
                "tien_chua_vat_txt": fmt_vnd(last.get("cost", 0)),
                "tien": last.get("cost_vat", 0),
                "tien_txt": fmt_vnd(last.get("cost_vat", 0)),
            }
        v["so_voi_ky_truoc"] = None
        if len(periods) >= 2 and periods[-2]["kwh"] > 0:
            pct = (periods[-1]["kwh"] - periods[-2]["kwh"]) / periods[-2]["kwh"] * 100
            v["so_voi_ky_truoc"] = f"{pct:+.1f}".replace(".", ",")
        return v

    def _year_vars(self, year: int) -> dict[str, Any]:
        v = self._base_vars()
        s = self.tracker.year_summary(year)
        v["nam"] = {
            "nam": year,
            "kwh": round(s["kwh"], 2),
            "kwh_txt": fmt_kwh(s["kwh"]),
            "so_ky": s["count"],
            "tien_chua_vat": s["cost"],
            "tien_chua_vat_txt": fmt_vnd(s["cost"]),
            "tien": s["cost_vat"],
            "tien_txt": fmt_vnd(s["cost_vat"]),
            "cao_nhat": None,
            "thap_nhat": None,
        }
        if "max" in s:
            v["nam"]["cao_nhat"] = {"ten": s["max"]["name"], "kwh_txt": fmt_kwh(s["max"]["kwh"])}
            v["nam"]["thap_nhat"] = {"ten": s["min"]["name"], "kwh_txt": fmt_kwh(s["min"]["kwh"])}
        return v

    async def async_send_report(self, kind: str) -> None:
        """Gửi một loại báo cáo (dùng cho lịch và dịch vụ send_report)."""
        if kind == "daily":
            await self._send(kind, f"⚡ {self.title} › HÔM QUA", CONF_TPL_DAILY, self._base_vars())
        elif kind == "period":
            v = self._period_vars()
            if "ky_truoc" not in v:
                return
            await self._send(kind, f"📊 {self.title} › KỲ {v['ky_truoc']['ten']}", CONF_TPL_PERIOD, v)
        elif kind == "year":
            year = dt_util.now().year - 1
            await self._send(kind, f"📅 {self.title} › NĂM {year}", CONF_TPL_YEAR, self._year_vars(year))

    # ------------------------------------------------------------- gửi
    def render(self, tpl_key: str, variables: dict[str, Any]) -> str:
        source = self.cfg.get(tpl_key) or DEFAULT_TEMPLATES[tpl_key]
        try:
            return Template(source, self.hass).async_render(variables, parse_result=False).strip()
        except TemplateError as err:
            _LOGGER.error("Lỗi mẫu tin %s: %s", tpl_key, err)
            return f"(Lỗi mẫu tin {tpl_key}: {err})"

    async def _send(self, kind: str, title: str, tpl_key: str, variables: dict[str, Any]) -> None:
        message = self.render(tpl_key, variables)
        if not message:
            return
        ts = dt_util.now()
        for ch_kind, target in channels(self.cfg):
            if not self.tracker.switches.get(channel_key(ch_kind, target), True):
                continue
            try:
                if ch_kind == "telegram":
                    await self.hass.services.async_call(
                        "telegram_bot", "send_message",
                        {
                            "entity_id": [target],
                            "title": f"<b>{html.escape(title)}</b>",
                            "message": f"{html.escape(message)}\n<i>({ts:%H:%M:%S %d/%m/%Y})</i>",
                            "parse_mode": "html",
                        },
                        blocking=True,
                    )
                else:
                    await self.hass.services.async_call(
                        "notify", target.removeprefix("notify."),
                        {"title": title, "message": f"{message}\n({ts:%H:%M})"},
                        blocking=True,
                    )
            except Exception as err:  # noqa: BLE001 - một kênh lỗi không chặn kênh khác
                _LOGGER.warning("Gửi %s tới %s lỗi: %s", kind, target, err)

    async def async_send_file(self, path: str, caption: str) -> int:
        """Gửi file qua các kênh Telegram đang bật. Trả về số kênh đã gửi."""
        sent = 0
        for ch_kind, target in channels(self.cfg):
            if ch_kind != "telegram" or not self.tracker.switches.get(channel_key(ch_kind, target), True):
                continue
            try:
                await self.hass.services.async_call(
                    "telegram_bot", "send_document",
                    {"entity_id": [target], "file": path, "caption": caption},
                    blocking=True,
                )
                sent += 1
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Gửi file tới %s lỗi: %s", target, err)
        return sent
