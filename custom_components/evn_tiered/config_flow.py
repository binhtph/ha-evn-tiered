"""Config flow và options flow (menu: nguồn & kỳ, giá, thông báo, nội dung tin)."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import TemplateError
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TimeSelector,
)
from homeassistant.helpers.template import Template

from .calc import TierConfigError, parse_tiers
from .const import (
    CONF_BILLING_DAY,
    CONF_LOST_HOURS,
    CONF_MOBILE,
    CONF_REPORT_TIME,
    CONF_SOURCES,
    CONF_TELEGRAM,
    CONF_VAT,
    CURRENCY,
    DEFAULT_BILLING_DAY,
    DEFAULT_LOST_HOURS,
    DEFAULT_NAME,
    DEFAULT_REPORT_TIME,
    DEFAULT_TEMPLATES,
    DEFAULT_TIERS,
    DEFAULT_VAT,
    DOMAIN,
    MAX_TIERS,
    TEMPLATE_KEYS,
    tier_limit_key,
    tier_price_key,
)

TIER_KEYS = [k for i in range(1, MAX_TIERS + 1) for k in (tier_limit_key(i), tier_price_key(i))]


def default_options() -> dict[str, Any]:
    values: dict[str, Any] = {
        CONF_SOURCES: [],
        CONF_BILLING_DAY: DEFAULT_BILLING_DAY,
        CONF_VAT: DEFAULT_VAT,
        CONF_TELEGRAM: [],
        CONF_MOBILE: [],
        CONF_REPORT_TIME: DEFAULT_REPORT_TIME,
        CONF_LOST_HOURS: DEFAULT_LOST_HOURS,
    }
    for i, (limit, price) in enumerate(DEFAULT_TIERS, start=1):
        if limit is not None:
            values[tier_limit_key(i)] = limit
        values[tier_price_key(i)] = price
    return values


# ------------------------------------------------------------------ schema
def _box(**kw: Any) -> NumberSelector:
    return NumberSelector(NumberSelectorConfig(mode=NumberSelectorMode.BOX, **kw))


def _source_schema(v: dict[str, Any], with_name: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=v.get(CONF_NAME, DEFAULT_NAME))] = TextSelector()
    fields[vol.Required(CONF_SOURCES, default=v.get(CONF_SOURCES, []))] = EntitySelector(
        EntitySelectorConfig(domain="sensor", device_class="energy", multiple=True)
    )
    fields[vol.Required(CONF_BILLING_DAY, default=v.get(CONF_BILLING_DAY, DEFAULT_BILLING_DAY))] = _box(
        min=1, max=28, step=1
    )
    return vol.Schema(fields)


def _price_schema(v: dict[str, Any]) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_VAT, default=v.get(CONF_VAT, DEFAULT_VAT)): _box(
            min=0, max=30, step=0.1, unit_of_measurement="%"
        )
    }
    for i in range(1, MAX_TIERS + 1):
        # suggested_value (không phải default) để xóa trống được.
        fields[vol.Optional(tier_limit_key(i), description={"suggested_value": v.get(tier_limit_key(i))})] = (
            _box(min=0, step="any", unit_of_measurement="kWh")
        )
        fields[vol.Optional(tier_price_key(i), description={"suggested_value": v.get(tier_price_key(i))})] = (
            _box(min=0, step="any", unit_of_measurement=f"{CURRENCY}/kWh")
        )
    return vol.Schema(fields)


def _notify_schema(hass: HomeAssistant, v: dict[str, Any]) -> vol.Schema:
    mobile = sorted(
        s for s in hass.services.async_services_for_domain("notify") if s.startswith("mobile_app_")
    )
    mobile = sorted(set(mobile) | set(v.get(CONF_MOBILE, [])))
    return vol.Schema({
        vol.Optional(CONF_TELEGRAM, default=v.get(CONF_TELEGRAM, [])): EntitySelector(
            EntitySelectorConfig(domain="notify", multiple=True)
        ),
        vol.Optional(CONF_MOBILE, default=v.get(CONF_MOBILE, [])): SelectSelector(
            SelectSelectorConfig(options=mobile, multiple=True, custom_value=True)
        ),
        vol.Required(CONF_REPORT_TIME, default=v.get(CONF_REPORT_TIME, DEFAULT_REPORT_TIME)): TimeSelector(),
        vol.Required(CONF_LOST_HOURS, default=v.get(CONF_LOST_HOURS, DEFAULT_LOST_HOURS)): _box(
            min=0.25, max=48, step=0.25, unit_of_measurement="h"
        ),
    })


def _message_schema(v: dict[str, Any]) -> vol.Schema:
    multiline = TextSelector(TextSelectorConfig(multiline=True))
    return vol.Schema({
        vol.Optional(key, description={"suggested_value": v.get(key) or DEFAULT_TEMPLATES[key]}): multiline
        for key in TEMPLATE_KEYS
    })


# ------------------------------------------------------------------ kiểm tra
def _clean_source(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    clean = {
        CONF_SOURCES: list(user_input.get(CONF_SOURCES) or []),
        CONF_BILLING_DAY: int(user_input[CONF_BILLING_DAY]),
    }
    return clean, ({CONF_SOURCES: "no_sources"} if not clean[CONF_SOURCES] else {})


def _clean_price(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    clean: dict[str, Any] = {CONF_VAT: float(user_input[CONF_VAT])}
    for key in TIER_KEYS:
        if user_input.get(key) is not None:
            clean[key] = float(user_input[key])
    try:
        parse_tiers(clean)
    except TierConfigError as err:
        return clean, {"base": err.args[0]}
    return clean, {}


def _clean_notify(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_TELEGRAM: list(user_input.get(CONF_TELEGRAM) or []),
        CONF_MOBILE: [m.removeprefix("notify.") for m in user_input.get(CONF_MOBILE) or []],
        CONF_REPORT_TIME: user_input[CONF_REPORT_TIME],
        CONF_LOST_HOURS: float(user_input[CONF_LOST_HOURS]),
    }


def _clean_messages(hass: HomeAssistant, user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for key in TEMPLATE_KEYS:
        text = (user_input.get(key) or "").strip()
        if not text or text == DEFAULT_TEMPLATES[key].strip():
            continue  # trống = dùng mẫu mặc định
        try:
            Template(text, hass).ensure_valid()
        except TemplateError:
            errors[key] = "template_invalid"
        clean[key] = text
    return clean, errors


# ------------------------------------------------------------------ flow
class EvnTieredConfigFlow(ConfigFlow, domain=DOMAIN):
    """Thêm một bộ tính tiền điện."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        values: dict[str, Any] = {}
        if user_input is not None:
            clean, errors = _clean_source(user_input)
            if not errors:
                options = default_options() | clean
                return self.async_create_entry(title=user_input[CONF_NAME], data={}, options=options)
            values = user_input
        return self.async_show_form(
            step_id="user", data_schema=_source_schema(values, with_name=True), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EvnTieredOptionsFlow:
        return EvnTieredOptionsFlow()


class EvnTieredOptionsFlow(OptionsFlow):
    """Menu cấu hình."""

    def __init__(self) -> None:
        self._options: dict[str, Any] | None = None

    @property
    def options(self) -> dict[str, Any]:
        if self._options is None:
            self._options = dict(self.config_entry.options)
        return self._options

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["source", "prices", "notify", "messages", "save"]
        )

    async def async_step_save(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_create_entry(data=self.options)

    async def async_step_source(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            clean, errors = _clean_source(user_input)
            if not errors:
                self.options.update(clean)
                return await self.async_step_init()
        return self.async_show_form(
            step_id="source", data_schema=_source_schema(user_input or self.options, False), errors=errors
        )

    async def async_step_prices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            clean, errors = _clean_price(user_input)
            if not errors:
                for key in TIER_KEYS:
                    self.options.pop(key, None)
                self.options.update(clean)
                return await self.async_step_init()
        return self.async_show_form(
            step_id="prices", data_schema=_price_schema(user_input or self.options), errors=errors
        )

    async def async_step_notify(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self.options.update(_clean_notify(user_input))
            return await self.async_step_init()
        return self.async_show_form(step_id="notify", data_schema=_notify_schema(self.hass, self.options))

    async def async_step_messages(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            clean, errors = _clean_messages(self.hass, user_input)
            if not errors:
                for key in TEMPLATE_KEYS:
                    self.options.pop(key, None)
                self.options.update(clean)
                return await self.async_step_init()
        return self.async_show_form(
            step_id="messages", data_schema=_message_schema(user_input or self.options), errors=errors
        )
