"""Phần tính toán thuần (không phụ thuộc Home Assistant) để dễ kiểm thử."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from .const import MAX_TIERS, tier_limit_key, tier_price_key

# (hạn mức cộng dồn kWh hoặc None = không giới hạn, đơn giá đ/kWh chưa VAT)
Tier = tuple[float | None, float]


class TierConfigError(ValueError):
    """Cấu hình bậc không hợp lệ; args[0] là mã lỗi dùng cho translations."""


def parse_tiers(cfg: Mapping[str, Any]) -> list[Tier]:
    """Đọc danh sách bậc từ cấu hình phẳng tier_N_limit / tier_N_price.

    - Bậc được dùng khi có đơn giá.
    - Các bậc phải liên tiếp từ bậc 1.
    - Mọi bậc trừ bậc cuối phải có hạn mức, tăng dần.
    - Hạn mức của bậc cuối bị bỏ qua (luôn không giới hạn).
    """
    present: list[tuple[int, float | None, float]] = []
    for i in range(1, MAX_TIERS + 1):
        price = cfg.get(tier_price_key(i))
        if price is None or price == "":
            continue
        limit = cfg.get(tier_limit_key(i))
        limit = None if limit in (None, "") else float(limit)
        present.append((i, limit, float(price)))

    if not present:
        raise TierConfigError("no_tiers")
    if [p[0] for p in present] != list(range(1, len(present) + 1)):
        raise TierConfigError("tier_gap")

    tiers: list[Tier] = []
    previous = 0.0
    for pos, (_, limit, price) in enumerate(present):
        if price < 0:
            raise TierConfigError("tier_price_invalid")
        is_last = pos == len(present) - 1
        if is_last:
            tiers.append((None, price))
            break
        if limit is None:
            raise TierConfigError("tier_limit_missing")
        if limit <= previous:
            raise TierConfigError("tier_limit_order")
        tiers.append((limit, price))
        previous = limit
    return tiers


def breakdown(kwh: float, tiers: list[Tier]) -> list[dict[str, float]]:
    """Chia sản lượng kwh vào từng bậc. Trả về danh sách bậc đã dùng."""
    result: list[dict[str, float]] = []
    lower = 0.0
    remaining = max(kwh, 0.0)
    for index, (limit, price) in enumerate(tiers, start=1):
        if remaining <= 0:
            break
        size = remaining if limit is None else min(remaining, limit - lower)
        if size > 0:
            result.append(
                {"tier": index, "kwh": size, "price": price, "amount": size * price}
            )
            remaining -= size
        if limit is not None:
            lower = limit
    return result


def tier_cost(kwh: float, tiers: list[Tier]) -> float:
    """Tiền điện (chưa VAT) cho sản lượng kwh tính từ đầu kỳ."""
    return sum(part["amount"] for part in breakdown(kwh, tiers))


def current_tier(kwh: float, tiers: list[Tier]) -> tuple[int, float]:
    """Bậc và đơn giá (chưa VAT) áp dụng cho kWh tiếp theo."""
    for index, (limit, price) in enumerate(tiers, start=1):
        if limit is None or kwh < limit:
            return index, price
    return len(tiers), tiers[-1][1]


def period_start_for(day: date, billing_day: int) -> date:
    """Ngày bắt đầu kỳ hóa đơn chứa ngày `day` (billing_day: 1..28)."""
    if day.day >= billing_day:
        return date(day.year, day.month, billing_day)
    if day.month == 1:
        return date(day.year - 1, 12, billing_day)
    return date(day.year, day.month - 1, billing_day)


def next_period_start(start: date) -> date:
    """Ngày bắt đầu kỳ kế tiếp (cùng ngày chốt ở tháng sau)."""
    if start.month == 12:
        return date(start.year + 1, 1, start.day)
    return date(start.year, start.month + 1, start.day)
