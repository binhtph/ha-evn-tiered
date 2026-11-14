"""Xuất CSV: tải trên web (link có chữ ký) và gửi file qua Telegram."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
import logging
from pathlib import Path
import re

from aiohttp import web

from homeassistant.components import persistent_notification
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util, slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_FILE_RE = re.compile(r"^(daily_(\d{4})|monthly)\.csv$")
LINK_TTL = timedelta(hours=24)


def _tracker(hass: HomeAssistant, entry_id: str):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        return None
    return getattr(entry.runtime_data, "tracker", None)


class EvnCsvView(HomeAssistantView):
    """GET /api/evn_tiered/<entry_id>/<file>.csv (cần đăng nhập hoặc link có chữ ký)."""

    url = "/api/evn_tiered/{entry_id}/{filename}"
    name = "api:evn_tiered:csv"
    requires_auth = True

    async def get(self, request: web.Request, entry_id: str, filename: str) -> web.Response:
        hass: HomeAssistant = request.app["hass"]
        tracker = _tracker(hass, entry_id)
        match = _FILE_RE.match(filename)
        if tracker is None or match is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        body = tracker.csv_daily(int(match.group(2))) if match.group(2) else tracker.csv_monthly()
        return web.Response(
            body=body.encode("utf-8"),
            content_type="text/csv",
            charset="utf-8",
            headers={"Content-Disposition": f'attachment; filename="{_download_name(tracker, filename)}"'},
        )


def _download_name(tracker, filename: str) -> str:
    slug = slugify(tracker.entry.title) or "evn"
    return f"{slug}_{filename}"


def signed_links(hass: HomeAssistant, entry_id: str, years: list[int]) -> dict[str, str]:
    """Link tải có chữ ký, hết hạn sau LINK_TTL."""
    files = [f"daily_{y}.csv" for y in years] + ["monthly.csv"]
    return {
        f: async_sign_path(hass, f"/api/evn_tiered/{entry_id}/{f}", LINK_TTL) for f in files
    }


async def async_export(hass: HomeAssistant, runtime, *, year: int | None, telegram: bool) -> dict:
    """Tạo thông báo có link tải CSV; tùy chọn gửi file qua Telegram."""
    tracker = runtime.tracker
    entry_id = tracker.entry.entry_id
    years = [year] if year else tracker.years()
    links = signed_links(hass, entry_id, years)
    lines = [f"- [{name}]({url})" for name, url in links.items()]
    persistent_notification.async_create(
        hass,
        "Bấm để tải (link dùng được 24 giờ):\n\n" + "\n".join(lines),
        title=f"{tracker.entry.title}: xuất CSV",
        notification_id=f"{DOMAIN}_{entry_id}_export",
    )
    sent = 0
    if telegram:
        folder = _export_dir(hass)
        if folder is None:
            _LOGGER.warning(
                "Không có thư mục được phép (allowlist_external_dirs / media) để gửi file qua Telegram"
            )
        else:
            y = year or dt_util.now().year
            daily = folder / _download_name(tracker, f"daily_{y}.csv")
            monthly = folder / _download_name(tracker, "monthly.csv")
            daily_body, monthly_body = tracker.csv_daily(y), tracker.csv_monthly()

            def _write() -> None:
                folder.mkdir(parents=True, exist_ok=True)
                daily.write_text(daily_body, encoding="utf-8")
                monthly.write_text(monthly_body, encoding="utf-8")

            await hass.async_add_executor_job(_write)
            notifier = runtime.notifier
            sent += await notifier.async_send_file(str(daily), f"{tracker.entry.title}: từng ngày năm {y}")
            sent += await notifier.async_send_file(str(monthly), f"{tracker.entry.title}: tổng kết từng kỳ")
    return {"links": links, "telegram_files": sent}


def _export_dir(hass: HomeAssistant) -> Path | None:
    """Thư mục ghi file tạm mà telegram_bot được phép đọc."""
    candidates = [Path(p) / "evn_tiered" for p in hass.config.media_dirs.values()]
    candidates += [Path(p) / "evn_tiered" for p in sorted(hass.config.allowlist_external_dirs)]
    for path in candidates:
        if hass.config.is_allowed_path(str(path)):
            return path
    return None
