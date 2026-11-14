# EVN Tiered Electricity Cost for Home Assistant (`evn_tiered`)

Integration này tính tiền điện theo biểu giá bậc thang EVN từ bất kỳ sensor energy (kWh) nào. Mọi cài đặt đều làm trên giao diện, không cần YAML, Jinja hay shell script.

- Mặc định dùng biểu giá sinh hoạt **5 bậc từ 01/11/2026** (QĐ 2892/QĐ-BCT) và VAT 8%. Số bậc chỉnh được tới 6, giá và VAT sửa được bất cứ lúc nào.
- Có số liệu hôm nay, hôm qua, kỳ này, kỳ trước, năm nay, năm trước, dự tính cả kỳ, bậc hiện tại và đơn giá.
- Kỳ hóa đơn bắt đầu theo ngày tùy chọn (1–28).
- Khi chốt mỗi ngày và mỗi kỳ, integration lưu lại cả bảng giá và VAT lúc đó. Sau này đổi giá thì số tiền các kỳ cũ vẫn giữ nguyên.
- Gửi báo cáo qua Telegram và app điện thoại: tổng kết ngày, kỳ, năm; báo khi lên bậc; báo khi mất số liệu. Bật tắt từng loại tin và từng kênh bằng công tắc. Nội dung tin sửa được.
- Xuất CSV theo hai cách: link tải trên web, hoặc gửi file qua Telegram.
- Nhập được dữ liệu CSV của bộ "Energy Monitor" cũ.

## Cài đặt

**HACS:** vào HACS → ⋮ → Custom repositories → dán link repo này, chọn loại *Integration* → Download → khởi động lại Home Assistant.

**Thủ công:** chép thư mục `custom_components/evn_tiered` vào `/config/custom_components/` → khởi động lại Home Assistant.

Sau khi khởi động lại: **Settings → Devices & services → Add integration → "EVN Tiered Electricity Cost"**. Đặt tên (ví dụ "Điện nhà"), chọn sensor energy và ngày bắt đầu kỳ.

## Cấu hình (nút Configure)

| Mục | Nội dung |
|---|---|
| Sensor nguồn và ngày chốt kỳ | Một hoặc nhiều sensor (cộng lại); ngày bắt đầu kỳ (1 = theo tháng dương lịch) |
| Giá điện và VAT | Tối đa 6 bậc. "Đến kWh" là hạn mức cộng dồn; bậc cuối để trống hạn mức; xóa đơn giá của một bậc để bỏ bậc đó |
| Thông báo | Kênh Telegram (notify entity của bot), app điện thoại (`mobile_app_*`), giờ gửi báo cáo, số giờ mất số liệu thì báo |
| Nội dung tin nhắn | Mẫu Jinja cho từng loại tin. Để trống thì dùng mẫu mặc định |

Sửa xong một mục thì bấm **Lưu và đóng**. Kỳ đang chạy được tính lại theo giá mới; các kỳ đã chốt giữ nguyên số cũ.

### Lịch gửi tin
- Hằng ngày, đúng giờ đã chọn: tổng kết hôm qua, lũy kế kỳ, bậc hiện tại, dự tính cả kỳ.
- Ngày đầu kỳ mới: gửi thêm tổng kết kỳ vừa xong, so sánh với kỳ trước đó.
- Kỳ đầu tiên của năm: gửi thêm tổng kết năm trước, kèm kỳ dùng nhiều nhất và ít nhất.
- Gửi ngay khi kỳ hiện tại sang bậc mới.
- Gửi khi sensor nguồn mất kết nối lâu hơn số giờ đã cài.

### Biến dùng trong mẫu tin
`ten`, `vat`, `ky` (gồm `ten`, `kwh_txt`, `bac`, `tien_txt`, `tien_chua_vat_txt`), `hom_qua` (gồm `ngay`, `kwh_txt`, `tien_txt`, `bac`), `du_tinh` (gồm `kwh_txt`, `tien_txt`), `ky_truoc`, `so_voi_ky_truoc`, `nam` (gồm `nam`, `kwh_txt`, `so_ky`, `tien_txt`, `cao_nhat`, `thap_nhat`), `don_gia_txt` (tin lên bậc), `nguon` và `so_gio` (tin mất số liệu). Các biến `*_txt` đã định dạng sẵn kiểu Việt Nam (`1.234.567`, `12,50`).

## Entity tạo ra (ví dụ tên "Điện nhà")

| Entity | Ý nghĩa |
|---|---|
| `sensor.dien_nha_energy_today`, `sensor.dien_nha_cost_today` | Điện và tiền hôm nay (tiền có VAT; thuộc tính có số chưa VAT) |
| `sensor.dien_nha_energy_yesterday`, `sensor.dien_nha_cost_yesterday` | Hôm qua |
| `sensor.dien_nha_period_energy`, `sensor.dien_nha_period_cost` | Kỳ này; thuộc tính có chi tiết từng bậc và tiền VAT |
| `sensor.dien_nha_projected_period_cost` | Dự tính tiền cả kỳ |
| `sensor.dien_nha_last_period_energy`, `sensor.dien_nha_last_period_cost` | Kỳ trước, kèm bảng giá lúc chốt |
| `sensor.dien_nha_year_energy`, `sensor.dien_nha_year_cost` | Năm nay |
| `sensor.dien_nha_last_year_energy`, `sensor.dien_nha_last_year_cost` | Năm trước, kèm kỳ cao nhất và thấp nhất |
| `sensor.dien_nha_current_tier`, `sensor.dien_nha_current_price` | Bậc và đơn giá có VAT đang áp dụng |
| `switch.dien_nha_*` | Bật tắt từng loại báo cáo và từng kênh gửi |
| `button.dien_nha_export_csv_download_link` | Tạo thông báo trên HA có link tải CSV (link dùng được 24 giờ) |
| `button.dien_nha_export_csv_to_telegram` | Gửi file CSV qua Telegram |
| `button.dien_nha_send_daily_report_now` | Gửi thử báo cáo ngày |

Tên hiển thị là tiếng Việt, còn entity_id sinh theo tiếng Anh. Kiểm tra entity_id thực tế ở Settings → Entities.

Dùng với **Energy dashboard**: trong mục Grid consumption, chọn "Use an entity with current price" rồi chọn `sensor.…_current_price`.

### Card Lovelace mẫu

```yaml
type: entities
title: Điện nhà
entities:
  - type: section
    label: Hôm nay
  - entity: sensor.dien_nha_energy_today
    name: Điện hôm nay
  - entity: sensor.dien_nha_cost_today
    name: Tiền hôm nay
  - type: section
    label: Hôm qua
  - entity: sensor.dien_nha_energy_yesterday
    name: Điện hôm qua
  - entity: sensor.dien_nha_cost_yesterday
    name: Tiền hôm qua
  - type: section
    label: Kỳ này
  - entity: sensor.dien_nha_period_energy
    name: Điện kỳ này
  - entity: sensor.dien_nha_period_cost
    name: Tiền kỳ này
  - entity: sensor.dien_nha_current_tier
    name: Bậc hiện tại
  - entity: sensor.dien_nha_current_price
    name: Đơn giá (có VAT)
  - entity: sensor.dien_nha_projected_period_cost
    name: Dự tính cả kỳ
  - type: section
    label: Năm
  - entity: sensor.dien_nha_year_energy
    name: Điện năm nay
  - entity: sensor.dien_nha_year_cost
    name: Tiền năm nay
  - entity: sensor.dien_nha_last_year_energy
    name: Điện năm trước
  - entity: sensor.dien_nha_last_year_cost
    name: Tiền năm trước
```

Biểu đồ theo ngày, tháng, năm: dùng card `statistics-graph`, chọn `stat_types: [change]`, `period: day`, `month` hoặc `year`, với entity `sensor.…_period_energy` hoặc `sensor.…_period_cost`.

## Dịch vụ (gọi trên bất kỳ sensor nào của integration)

| Dịch vụ | Dùng để |
|---|---|
| `evn_tiered.set_period_energy` | Đặt số kWh đã dùng trong kỳ, ví dụ cho khớp với công tơ EVN |
| `evn_tiered.reset_period` | Chốt kỳ ngay và đếm lại từ 0 |
| `evn_tiered.import_legacy_csv` | Nhập CSV Energy Monitor cũ (mặc định đọc từ `/config/energy_monitor/history`) |
| `evn_tiered.export_csv` | Tạo link tải CSV; tham số `telegram: true` để gửi file qua Telegram |
| `evn_tiered.send_report` | Gửi ngay một loại báo cáo: `daily`, `period` hoặc `year` |

## Chuyển từ Energy Monitor cũ (`packages/energy.yaml`)

1. Cài integration và cấu hình kênh gửi. Chọn cùng sensor nguồn với bộ cũ, ví dụ `sensor.smart_energy_monitor_energy`.
2. Vào Developer tools → Actions, chạy:
   ```yaml
   action: evn_tiered.import_legacy_csv
   target:
     entity_id: sensor.dien_nha_period_cost
   data:
     folder: /config/energy_monitor/history
     today_entity: sensor.dien_ngay
   ```
   Các tháng cũ giữ đúng số tiền đã chốt. Kỳ hiện tại được tính lại theo biểu giá mới. Số điện hôm nay lấy từ `sensor.dien_ngay`. Chạy lại lệnh này không bị nhân đôi số liệu.
3. Kiểm tra số liệu. Nếu đúng thì xóa `packages/energy.yaml`, `custom_templates/evn.jinja` và `energy_monitor/`, rồi khởi động lại HA.

## Lưu trữ

- Số đếm và lịch sử từng ngày, từng kỳ nằm ở `/config/.storage/evn_tiered.<entry_id>`. File này có trong bản sao lưu HA và không bị xóa khi recorder dọn dữ liệu cũ.
- Biểu đồ dùng long-term statistics của HA.
- File CSV gửi qua Telegram được ghi vào thư mục media, ví dụ `/media/evn_tiered/`.

## Giới hạn

Integration không tự chia hạn mức bậc theo số ngày khi kỳ hóa đơn lệch tháng, và không chia giá theo ngày khi EVN đổi giá giữa kỳ. Số tiền chỉ để tham khảo; số thực trả theo hóa đơn EVN.
