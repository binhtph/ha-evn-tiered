"""Config flow và options flow (nhập giá, VAT, sensor trên UI)."""

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
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .calc import TierConfigError, parse_tiers
from .const import (
    CONF_BILLING_DAY,
    CONF_SOURCES,
    CONF_VAT,
    CURRENCY,
    DEFAULT_BILLING_DAY,
    DEFAULT_NAME,
    DEFAULT_TIERS,
    DEFAULT_VAT,
    DOMAIN,
    MAX_TIERS,
    tier_limit_key,
    tier_price_key,
)


def _default_values() -> dict[str, Any]:
    values: dict[str, Any] = {
        CONF_NAME: DEFAULT_NAME,
        CONF_SOURCES: [],
        CONF_BILLING_DAY: DEFAULT_BILLING_DAY,
        CONF_VAT: DEFAULT_VAT,
    }
    for i, (limit, price) in enumerate(DEFAULT_TIERS, start=1):
        if limit is not None:
            values[tier_limit_key(i)] = limit
        values[tier_price_key(i)] = price
    return values


def _schema(values: dict[str, Any], *, with_name: bool) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if with_name:
        fields[vol.Required(CONF_NAME, default=values.get(CONF_NAME, DEFAULT_NAME))] = (
            TextSelector()
        )
    fields[vol.Required(CONF_SOURCES, default=values.get(CONF_SOURCES, []))] = (
        EntitySelector(
            EntitySelectorConfig(domain="sensor", device_class="energy", multiple=True)
        )
    )
    fields[
        vol.Required(
            CONF_BILLING_DAY, default=values.get(CONF_BILLING_DAY, DEFAULT_BILLING_DAY)
        )
    ] = NumberSelector(
        NumberSelectorConfig(min=1, max=28, step=1, mode=NumberSelectorMode.BOX)
    )
    fields[vol.Required(CONF_VAT, default=values.get(CONF_VAT, DEFAULT_VAT))] = (
        NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=30,
                step=0.1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="%",
            )
        )
    )
    limit_selector = NumberSelector(
        NumberSelectorConfig(
            min=0, step="any", mode=NumberSelectorMode.BOX, unit_of_measurement="kWh"
        )
    )
    price_selector = NumberSelector(
        NumberSelectorConfig(
            min=0,
            step="any",
            mode=NumberSelectorMode.BOX,
            unit_of_measurement=f"{CURRENCY}/kWh",
        )
    )
    # Dùng suggested_value (không phải default) để người dùng xóa trống được.
    for i in range(1, MAX_TIERS + 1):
        fields[
            vol.Optional(
                tier_limit_key(i),
                description={"suggested_value": values.get(tier_limit_key(i))},
            )
        ] = limit_selector
        fields[
            vol.Optional(
                tier_price_key(i),
                description={"suggested_value": values.get(tier_price_key(i))},
            )
        ] = price_selector
    return vol.Schema(fields)


def _validate(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Chuẩn hóa và kiểm tra dữ liệu nhập. Trả về (dữ liệu sạch, lỗi)."""
    errors: dict[str, str] = {}
    clean: dict[str, Any] = {
        CONF_SOURCES: list(user_input.get(CONF_SOURCES) or []),
        CONF_BILLING_DAY: int(user_input[CONF_BILLING_DAY]),
        CONF_VAT: float(user_input[CONF_VAT]),
    }
    for i in range(1, MAX_TIERS + 1):
        for key in (tier_limit_key(i), tier_price_key(i)):
            if user_input.get(key) is not None:
                clean[key] = float(user_input[key])
    if not clean[CONF_SOURCES]:
        errors[CONF_SOURCES] = "no_sources"
    try:
        parse_tiers(clean)
    except TierConfigError as err:
        errors["base"] = err.args[0]
    return clean, errors


class EvnTieredConfigFlow(ConfigFlow, domain=DOMAIN):
    """Thêm một bộ tính tiền điện."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        values = _default_values()
        if user_input is not None:
            clean, errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME], data={}, options=clean
                )
            values = {**user_input}
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(values, with_name=True),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> EvnTieredOptionsFlow:
        return EvnTieredOptionsFlow()


class EvnTieredOptionsFlow(OptionsFlow):
    """Sửa sensor, ngày chốt, VAT, giá các bậc."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        values = dict(self.config_entry.options)
        if user_input is not None:
            clean, errors = _validate(user_input)
            if not errors:
                return self.async_create_entry(data=clean)
            values = {**user_input}
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(values, with_name=False),
            errors=errors,
        )
