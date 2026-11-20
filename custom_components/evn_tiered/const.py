"""Hằng số cho integration Tiền điện bậc thang EVN."""

from __future__ import annotations

DOMAIN = "evn_tiered"
VERSION = "1.2.0"

# --- nguồn & kỳ
CONF_SOURCES = "energy_sensors"
CONF_BILLING_DAY = "billing_day"
# --- giá
CONF_VAT = "vat_percent"
MAX_TIERS = 6
# --- thông báo
CONF_TELEGRAM = "telegram_targets"  # notify entity của telegram_bot
CONF_MOBILE = "mobile_targets"  # tên dịch vụ notify.mobile_app_*
CONF_REPORT_TIME = "report_time"
CONF_LOST_HOURS = "source_lost_hours"
# --- nội dung tin
CONF_TPL_DAILY = "tpl_daily"
CONF_TPL_PERIOD = "tpl_period"
CONF_TPL_YEAR = "tpl_year"
CONF_TPL_TIER = "tpl_tier"
CONF_TPL_LOST = "tpl_lost"
TEMPLATE_KEYS = (CONF_TPL_DAILY, CONF_TPL_PERIOD, CONF_TPL_YEAR, CONF_TPL_TIER, CONF_TPL_LOST)


def tier_limit_key(index: int) -> str:
    """Khóa cấu hình: hạn mức (kWh cộng dồn) của bậc thứ index (1-based)."""
    return f"tier_{index}_limit"


def tier_price_key(index: int) -> str:
    """Khóa cấu hình: đơn giá (đ/kWh, chưa VAT) của bậc thứ index (1-based)."""
    return f"tier_{index}_price"


# Biểu giá điện sinh hoạt 5 bậc, áp dụng từ 01/11/2026 (QĐ 2892/QĐ-BCT), chưa VAT.
DEFAULT_TIERS: list[tuple[float | None, float]] = [
    (100, 1901),
    (200, 2289),
    (400, 2894),
    (700, 3456),
    (None, 3844),
]
DEFAULT_VAT = 8.0
DEFAULT_BILLING_DAY = 1
DEFAULT_NAME = "Điện nhà"
DEFAULT_REPORT_TIME = "07:00:00"
DEFAULT_LOST_HOURS = 1.0

DEFAULT_TEMPLATES = {
    CONF_TPL_DAILY: (
        "{% if hom_qua %}Hôm qua {{ hom_qua.ngay }}: {{ hom_qua.kwh_txt }} kWh, "
        "{{ hom_qua.tien_txt }} đ.{% else %}Chưa có số của hôm qua.{% endif %}\n"
        "Kỳ {{ ky.ten }} đến nay: {{ ky.kwh_txt }} kWh, bậc {{ ky.bac }}.\n"
        "Chưa VAT {{ ky.tien_chua_vat_txt }} đ, có VAT {{ vat }}% {{ ky.tien_txt }} đ."
        "{% if du_tinh %}\nDự tính cả kỳ: khoảng {{ du_tinh.kwh_txt }} kWh, "
        "{{ du_tinh.tien_txt }} đ.{% endif %}"
    ),
    CONF_TPL_PERIOD: (
        "Tổng {{ ky_truoc.kwh_txt }} kWh, bậc {{ ky_truoc.bac }}.\n"
        "Chưa VAT {{ ky_truoc.tien_chua_vat_txt }} đ, có VAT {{ ky_truoc.tien_txt }} đ."
        "{% if so_voi_ky_truoc is not none %}\nSo với kỳ trước nữa: "
        "{{ so_voi_ky_truoc }}% kWh.{% endif %}"
    ),
    CONF_TPL_YEAR: (
        "Tổng {{ nam.kwh_txt }} kWh trong {{ nam.so_ky }} kỳ.\n"
        "Chưa VAT {{ nam.tien_chua_vat_txt }} đ, có VAT {{ nam.tien_txt }} đ."
        "{% if nam.cao_nhat %}\nCao nhất: {{ nam.cao_nhat.ten }} ({{ nam.cao_nhat.kwh_txt }} kWh). "
        "Thấp nhất: {{ nam.thap_nhat.ten }} ({{ nam.thap_nhat.kwh_txt }} kWh).{% endif %}"
    ),
    CONF_TPL_TIER: (
        "Kỳ {{ ky.ten }} đã sang bậc {{ ky.bac }} ({{ don_gia_txt }} đ/kWh có VAT) "
        "ở mức {{ ky.kwh_txt }} kWh."
    ),
    CONF_TPL_LOST: (
        "Sensor {{ nguon }} mất kết nối hơn {{ so_gio }} giờ. "
        "Phần điện lúc mất kết nối sẽ được cộng khi sensor có lại số."
    ),
}

# Công tắc (switch) do integration tạo: báo cáo và kênh gửi.
REPORT_SWITCHES = ("report_daily", "report_period", "report_year", "alert_tier", "alert_lost")

CURRENCY = "VND"
STORAGE_VERSION = 1
SAVE_DELAY = 30  # giây

SERVICE_SET_PERIOD_ENERGY = "set_period_energy"
SERVICE_RESET_PERIOD = "reset_period"
SERVICE_IMPORT_LEGACY = "import_legacy_csv"
SERVICE_EXPORT_CSV = "export_csv"
SERVICE_SEND_REPORT = "send_report"
SERVICE_GET_HISTORY = "get_history"
HISTORY_KINDS = ("summary", "days", "periods")
ATTR_ENERGY = "energy"

EVENT_TIER_CHANGED = f"{DOMAIN}_tier_changed"
EVENT_PERIOD_CLOSED = f"{DOMAIN}_period_closed"
EVENT_DAY_CLOSED = f"{DOMAIN}_day_closed"

LEGACY_DIR = "/config/energy_monitor/history"
