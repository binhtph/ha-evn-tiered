"""Hằng số cho integration Tiền điện bậc thang EVN."""

from __future__ import annotations

DOMAIN = "evn_tiered"

CONF_SOURCES = "energy_sensors"
CONF_BILLING_DAY = "billing_day"
CONF_VAT = "vat_percent"

MAX_TIERS = 6


def tier_limit_key(index: int) -> str:
    """Khóa cấu hình: hạn mức (kWh cộng dồn) của bậc thứ index (1-based)."""
    return f"tier_{index}_limit"


def tier_price_key(index: int) -> str:
    """Khóa cấu hình: đơn giá (đ/kWh, chưa VAT) của bậc thứ index (1-based)."""
    return f"tier_{index}_price"


# Biểu giá điện sinh hoạt 5 bậc, áp dụng từ 01/11/2026 (QĐ 2892/QĐ-BCT), chưa VAT.
# (hạn mức cộng dồn kWh/kỳ, đơn giá đ/kWh); None = không giới hạn (bậc cuối).
DEFAULT_TIERS: list[tuple[float | None, float]] = [
    (100, 1901),
    (200, 2289),
    (400, 2894),
    (700, 3456),
    (None, 3844),
]
DEFAULT_VAT = 8.0
DEFAULT_BILLING_DAY = 1
DEFAULT_NAME = "Tiền điện"

CURRENCY = "VND"
STORAGE_VERSION = 1
SAVE_DELAY = 30  # giây

SERVICE_SET_PERIOD_ENERGY = "set_period_energy"
SERVICE_RESET_PERIOD = "reset_period"
ATTR_ENERGY = "energy"
