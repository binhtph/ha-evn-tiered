"""Kiểm thử integration evn_tiered v1.1."""

from __future__ import annotations

from datetime import date
from http import HTTPStatus
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import async_fire_time_changed, async_mock_service

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.evn_tiered.calc import (
    TierConfigError,
    breakdown,
    current_tier,
    fmt_kwh,
    fmt_vnd,
    parse_tiers,
    period_start_for,
    price_table,
    tier_cost,
)
from custom_components.evn_tiered.const import DEFAULT_TIERS, DOMAIN

TIERS = DEFAULT_TIERS
SRC = "sensor.plug_energy"
SRC2 = "sensor.ac_energy"
TG = "notify.tele_bot"


@pytest.fixture(autouse=True)
def _auto(enable_custom_integrations, hass):
    hass.config.language = "vi"
    yield


@pytest.fixture
async def tz(hass):
    await hass.config.async_set_time_zone("Asia/Ho_Chi_Minh")


# ------------------------------------------------------------------ calc
def test_cost_matches_evn_examples():
    assert tier_cost(100, TIERS) == pytest.approx(190_100)
    assert tier_cost(300, TIERS) == pytest.approx(708_400)
    assert tier_cost(900, TIERS) == pytest.approx(190_100 + 228_900 + 578_800 + 1_036_800 + 768_800)
    assert [p["tier"] for p in breakdown(250, TIERS)] == [1, 2, 3]
    assert current_tier(100, TIERS) == (2, 2289)
    assert fmt_vnd(765_072.4) == "765.072"
    assert fmt_kwh(1234.5) == "1.234,50"
    assert price_table(TIERS) == "0-100:1901 101-200:2289 201-400:2894 401-700:3456 >700:3844"


def test_parse_tiers_errors():
    with pytest.raises(TierConfigError, match="tier_gap"):
        parse_tiers({"tier_1_limit": 50, "tier_1_price": 1, "tier_3_price": 2})
    with pytest.raises(TierConfigError, match="tier_limit_missing"):
        parse_tiers({"tier_1_price": 1, "tier_2_price": 2})


def test_period_start():
    assert period_start_for(date(2026, 11, 14), 2) == date(2026, 11, 2)
    assert period_start_for(date(2026, 11, 1), 2) == date(2026, 10, 2)


# ---------------------------------------------------------- helpers
async def _create(hass: HomeAssistant, billing_day: int = 1):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Nhà", "energy_sensors": [SRC, SRC2], "billing_day": billing_day}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return result["result"]


async def _options(hass, entry, step, data):
    r = await hass.config_entries.options.async_init(entry.entry_id)
    assert r["type"] is FlowResultType.MENU
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": step})
    r = await hass.config_entries.options.async_configure(r["flow_id"], data)
    if r["type"] is FlowResultType.FORM:
        return r
    assert r["type"] is FlowResultType.MENU
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"next_step_id": "save"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return r


def _set(hass, eid, value, unit="kWh"):
    hass.states.async_set(eid, value, {"unit_of_measurement": unit, "device_class": "energy"})


def _f(hass, eid) -> float:
    return float(hass.states.get(eid).state)


async def _midnight(hass, freezer, when):
    freezer.move_to(when)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


# ---------------------------------------------------------- integration
async def test_counting_and_defaults(hass, tz, freezer: FrozenDateTimeFactory):
    freezer.move_to("2026-11-14 10:00:00+07:00")
    _set(hass, SRC, 1000)
    _set(hass, SRC2, 5000, "Wh")
    await _create(hass)
    _set(hass, SRC, 1080)
    _set(hass, SRC2, 25000, "Wh")
    await hass.async_block_till_done()
    assert _f(hass, "sensor.nha_period_energy") == pytest.approx(100)
    assert _f(hass, "sensor.nha_period_cost") == pytest.approx(190_100 * 1.08)
    assert hass.states.get("sensor.nha_current_tier").state == "2"
    assert _f(hass, "sensor.nha_year_energy") == pytest.approx(100)
    assert hass.states.get("sensor.nha_period_cost").attributes["friendly_name"] == "Nhà Tiền điện kỳ này"
    # 0 thoáng qua -> không cộng trùng; reset thật -> cộng phần mới
    _set(hass, SRC, 0)
    _set(hass, SRC, 1080)
    _set(hass, SRC, 0)
    _set(hass, SRC, 2)
    await hass.async_block_till_done()
    assert _f(hass, "sensor.nha_period_energy") == pytest.approx(102)


async def test_rollover_history_and_reports(hass, tz, freezer: FrozenDateTimeFactory):
    tg_calls = async_mock_service(hass, "telegram_bot", "send_message")
    app_calls = async_mock_service(hass, "notify", "mobile_app_brians_iphone")
    freezer.move_to("2026-11-29 22:00:00+07:00")
    _set(hass, SRC, 0.5)
    _set(hass, SRC2, 0.5)
    entry = await _create(hass)
    await _options(hass, entry, "notify", {
        "telegram_targets": [TG], "mobile_targets": ["mobile_app_brians_iphone"],
        "report_time": "07:00:00", "source_lost_hours": 1,
    })
    assert hass.states.get("switch.nha_gui_telegram_tele_bot") or any(
        s.entity_id.startswith("switch.nha_telegram") for s in hass.states.async_all("switch")
    )

    _set(hass, SRC, 150.5)  # +150 -> lên bậc 2 => tin lên bậc
    await hass.async_block_till_done()
    assert any("bậc 2" in c.data["message"] for c in tg_calls)

    await _midnight(hass, freezer, "2026-11-30 00:00:02+07:00")
    assert _f(hass, "sensor.nha_energy_yesterday") == pytest.approx(150)
    assert _f(hass, "sensor.nha_energy_today") == 0
    _set(hass, SRC, 160.5)
    await hass.async_block_till_done()
    assert _f(hass, "sensor.nha_cost_today") == pytest.approx(10 * 2289 * 1.08)

    # báo cáo 07:00 hằng ngày, tắt kênh app thì chỉ gửi telegram
    app_switch = next(s.entity_id for s in hass.states.async_all("switch") if "brians" in s.entity_id)
    await hass.services.async_call("switch", "turn_off", {"entity_id": app_switch}, blocking=True)
    n_tg, n_app = len(tg_calls), len(app_calls)
    await _midnight(hass, freezer, "2026-11-30 07:00:00+07:00")
    assert len(tg_calls) == n_tg + 1 and len(app_calls) == n_app
    assert "Hôm qua 29/11: 150,00 kWh" in tg_calls[-1].data["message"]

    # sang kỳ mới: 07:00 ngày 1 gửi hằng ngày + tổng kết kỳ
    await _midnight(hass, freezer, "2026-12-01 00:00:02+07:00")
    assert _f(hass, "sensor.nha_last_period_energy") == pytest.approx(160)
    assert _f(hass, "sensor.nha_last_period_cost") == round((190_100 + 60 * 2289) * 1.08)
    n_tg = len(tg_calls)
    await _midnight(hass, freezer, "2026-12-01 07:00:00+07:00")
    assert len(tg_calls) == n_tg + 2
    assert "KỲ 11/2026" in tg_calls[-1].data["title"]

    # năm mới: tổng kết năm 2026
    for when in ("2027-01-01 00:00:02+07:00", "2027-01-01 07:00:00+07:00"):
        await _midnight(hass, freezer, when)
    assert "NĂM 2026" in tg_calls[-1].data["title"]
    assert _f(hass, "sensor.nha_last_year_energy") == pytest.approx(160)


async def test_source_lost_alert(hass, tz, freezer):
    tg_calls = async_mock_service(hass, "telegram_bot", "send_message")
    freezer.move_to("2026-11-14 10:00:00+07:00")
    _set(hass, SRC, 1)
    _set(hass, SRC2, 1)
    entry = await _create(hass)
    await _options(hass, entry, "notify", {"telegram_targets": [TG], "mobile_targets": [],
                                           "report_time": "21:00:00", "source_lost_hours": 1})
    hass.states.async_set(SRC, "unavailable")
    await hass.async_block_till_done()
    await _midnight(hass, freezer, "2026-11-14 11:00:05+07:00")
    assert any("MẤT SỐ LIỆU" in c.data["title"] for c in tg_calls)


async def test_prices_options_and_custom_message(hass, tz, freezer):
    tg_calls = async_mock_service(hass, "telegram_bot", "send_message")
    freezer.move_to("2026-11-14 10:00:00+07:00")
    _set(hass, SRC, 10)
    _set(hass, SRC2, 0.1)
    entry = await _create(hass, billing_day=2)
    _set(hass, SRC, 60)
    await hass.async_block_till_done()
    r = await _options(hass, entry, "prices", {"vat_percent": 10, "tier_1_price": 2000, "tier_2_price": 1})
    assert r["type"] is FlowResultType.FORM and r["errors"] == {"base": "tier_limit_missing"}
    await _options(hass, entry, "prices", {"vat_percent": 10, "tier_1_price": 2000})
    assert _f(hass, "sensor.nha_period_energy") == pytest.approx(50)
    assert _f(hass, "sensor.nha_period_cost") == pytest.approx(50 * 2000 * 1.1)
    assert hass.states.get("sensor.nha_period_cost").attributes["ky_bat_dau"] == "2026-11-02"

    await _options(hass, entry, "notify", {"telegram_targets": [TG], "mobile_targets": [],
                                           "report_time": "21:00:00", "source_lost_hours": 1})
    await _options(hass, entry, "messages", {"tpl_daily": "Kỳ {{ ky.ten }}: {{ ky.tien_txt }} đ"})
    await hass.services.async_call(DOMAIN, "send_report", {"kind": "daily"},
                                   target={"entity_id": "sensor.nha_period_cost"}, blocking=True)
    assert tg_calls[-1].data["message"].startswith("Kỳ 11/2026: 110.000 đ")
    r = await _options(hass, entry, "messages", {"tpl_daily": "{{ ky.ten "})
    assert r["errors"] == {"tpl_daily": "template_invalid"}


async def test_import_legacy(hass, tz, freezer, tmp_path):
    freezer.move_to("2026-11-14 10:00:00+07:00")
    d = tmp_path / "history"
    d.mkdir()
    (d / "energy_monthly.csv").write_text(
        "﻿Thang,kWh,Bac,Chua_VAT,Co_VAT,VAT_pct,So_ngay,Bang_gia\n"
        "09/2026,300.00,5,739500,798660,8,30,0-50:1984\n"
        "10/2026,250.00,4,600000,648000,8,31,0-50:1984\n"
        "12/2025,200.00,3,500000,540000,8,31,0-50:1984\n", encoding="utf-8")
    daily = "﻿Ngay,kWh,Luy_ke_thang_kWh,Tien_ngay_chua_VAT,Luy_ke_chua_VAT,Luy_ke_co_VAT,Bac,VAT_pct,Ghi_chu\n"
    for day in range(1, 14):  # 13 ngày x 10 kWh, tính theo giá cũ
        daily += f"{day:02d}/11/2026,10.00,{day*10}.00,20000,{day*20000},{day*21600},3,8,\n"
    (d / "energy_daily_2026.csv").write_text(daily, encoding="utf-8")
    hass.states.async_set("sensor.dien_ngay", 4.0, {"unit_of_measurement": "kWh"})
    _set(hass, SRC, 1)
    _set(hass, SRC2, 1)
    await _create(hass)
    resp = await hass.services.async_call(
        DOMAIN, "import_legacy_csv", {"folder": str(d), "today_entity": "sensor.dien_ngay"},
        target={"entity_id": "sensor.nha_period_cost"}, blocking=True, return_response=True,
    )
    assert resp["sensor.nha_period_cost"] == {"thang": 3, "ngay": 13}
    assert _f(hass, "sensor.nha_period_energy") == pytest.approx(134)
    assert _f(hass, "sensor.nha_period_cost") == pytest.approx(tier_cost(134, TIERS) * 1.08)
    assert _f(hass, "sensor.nha_last_period_cost") == 648000
    assert _f(hass, "sensor.nha_year_energy") == pytest.approx(300 + 250 + 134)
    assert _f(hass, "sensor.nha_last_year_energy") == pytest.approx(200)
    assert _f(hass, "sensor.nha_energy_yesterday") == pytest.approx(10)
    # nhập lại không nhân đôi
    await hass.services.async_call(DOMAIN, "import_legacy_csv", {"folder": str(d)},
                                   target={"entity_id": "sensor.nha_period_cost"}, blocking=True)
    assert _f(hass, "sensor.nha_period_energy") == pytest.approx(134)


async def test_export_csv_web(hass, tz, freezer, hass_client_no_auth):
    await async_setup_component(hass, "http", {})
    freezer.move_to("2026-11-14 10:00:00+07:00")
    _set(hass, SRC, 1)
    _set(hass, SRC2, 1)
    entry = await _create(hass)
    _set(hass, SRC, 21)
    await hass.async_block_till_done()
    await _midnight(hass, freezer, "2026-11-15 00:00:02+07:00")
    resp = await hass.services.async_call(DOMAIN, "export_csv", {},
                                          target={"entity_id": "sensor.nha_period_cost"},
                                          blocking=True, return_response=True)
    links = resp["sensor.nha_period_cost"]["links"]
    assert set(links) == {"daily_2026.csv", "monthly.csv"}
    client = await hass_client_no_auth()
    r = await client.get(f"/api/evn_tiered/{entry.entry_id}/daily_2026.csv")
    assert r.status == HTTPStatus.UNAUTHORIZED
    r = await client.get(links["daily_2026.csv"])
    assert r.status == HTTPStatus.OK
    text = await r.text()
    assert "14/11/2026,20.00,20.00" in text
    assert r.headers["Content-Disposition"] == 'attachment; filename="nha_daily_2026.csv"'
    # nút bấm tạo thông báo
    btn = next(s.entity_id for s in hass.states.async_all("button") if "link" in s.entity_id or "csv" in s.entity_id)
    await hass.services.async_call("button", "press", {"entity_id": btn}, blocking=True)
