# Giải thích kết quả

## 1. Quy ước chung

| Nội dung | Quy ước |
|---|---|
| Tiền | VND (không chia tỷ), dấu theo BCTC: chi phí âm, thu nhập dương |
| Tỷ lệ trong `annual`, `quarterly`, `assumptions` | Số thập phân (0,1569 = 15,69%) |
| Tỷ lệ trong `ratios` | **Phần trăm** (15,69 = 15,69%); số ngày là ngày |
| Kỳ | Năm `"2026"`, quý `"2026Q3"` |
| Hậu tố cột | ` A` = thực tế đã công bố; ` F` = dự phóng; `6T2026 A`, `9T2026 A` = lũy kế các quý đã công bố của năm đang chạy |
| Ô trống | `null` = không có số; không phải 0 |

Năm dự phóng đầu tiên = các quý đã công bố + các quý dự phóng, nên `"2026 F"` gồm cả phần thực tế của năm 2026.

## 2. `status`

| Trường | Ý nghĩa |
|---|---|
| `ok` | Tính xong, không có lỗi chặn |
| `usable` | **Dùng được kết quả** (v2.6.8): có dự phóng và không có lỗi chặn. Vẫn `true` khi thiếu vốn hay LNST âm |
| `flags` | Lưu ý đi kèm: `funding_gap`, `not_balanced`, `negative_earnings` |
| `publishable` | **Được phép công bố**: không lỗi, bảng cân đối cân, tiền khớp (phi tài chính) và không thiếu vốn |
| `balanced`, `balance_max_residual` | Bảng cân đối cân; chênh lệch lớn nhất (VND, phải ≈ 0) |
| `cash_reconciled` | Tiền cuối kỳ trên lưu chuyển tiền tệ khớp bảng cân đối (phi tài chính) |
| `feasible`, `funding_gap_max`, `first_gap_period` | Có thiếu vốn sau khi vay tự động hết hạn mức không; số thiếu lớn nhất; kỳ đầu tiên thiếu |
| `auto_funding_max` | Dư nợ vay tự động lớn nhất (phi tài chính) / liên ngân hàng tự động (ngân hàng) |
| `levels`, `mixed_levels` | Phương pháp từng khối (L1 trong gói này) |
| `latest_published` | Quý công bố mới nhất trong dữ liệu |
| `errors`, `warnings`, `hard_errors` | Số lỗi / cảnh báo |
| `history_point_in_time` | `false`: lịch sử là bản cập nhật cuối, không đúng như lúc công bố |

Bảng cân đối cân về toán học vẫn có thể không khả thi về kinh tế (thiếu vốn). Khi đó `feasible = false` và
`publishable = false`.

## 3. `issues`

`severity`: `error` (chặn, không dùng được), `warning` (cần xem), `info` (công bố). Mã thường gặp:

| Mã | Ý nghĩa |
|---|---|
| `unbalanced`, `negative_balance`, `unowned_line`, `unmapped_balance`, `CAL-03` | Lỗi chặn: bảng cân đối lệch, số dư âm, dòng thiếu phương pháp, doanh thu không tính được |
| `funding_gap` | Thiếu vốn sau khi vay tự động: kịch bản không khả thi |
| `negative_fcf`, `negative_gross_profit`, `negative_npat` | Tín hiệu cần xem; không phải lỗi |
| `default_out_of_range` | Giả định tự tính nằm ngoài miền hợp lệ (chỉ cảnh báo) |
| `volatile_history`, `blank_balance` | Lịch sử biến động mạnh; số dư trống |
| `INT-01` | Chưa có nợ vay trong lịch sử nhưng dự phóng phát sinh nợ (lãi suất mặc định 0) |
| `ONE-01` | Khoản bất thường cần analyst xác nhận (chưa tự áp) |
| `ONE-02`, `ONE-03` | Khoản bất thường đã phát hiện / đã tự loại khỏi lịch sử |
| `AUTO-01`, `AUTO-02` | Điều chỉnh cân nguồn tự động; còn vi phạm sau điều chỉnh |
| `BANK-LIQ-01` | Ngân hàng: tiền gửi được nâng lên để tỷ lệ cho vay / huy động không vượt trần |
| `annual_ytd` | Năm đang chạy = quý đã công bố + quý dự phóng |

## 4. `assumptions`

Mỗi dòng là một giả định đang dùng, mỗi năm dự phóng một cột.

| `source` | Ý nghĩa |
|---|---|
| `historical_default` | Tự tính từ lịch sử. `note` mô tả cách tính, vd "Từ thực tế 2025 (15.69%) hội tụ về EMA 5 năm …" |
| `explicit_policy` | Chính sách cố định, vd cổ tức = 0 đến khi analyst nhập |
| `analyst` | Do phía gọi đặt qua `overrides` |
| `auto_balance` | Do bước cân nguồn tự động chỉnh |
| `needs_input` | Không có số tự tính vững; cần nhập |

Giả định theo quý (dùng trong các quý dự phóng) được tính từ giả định năm cộng chuỗi quý gần nhất, theo spec mục 5.3.

## 5. `one_offs`

Các khoản ngoài hoạt động chính bị loại khỏi lịch sử trước khi tính giả định, gồm thu nhập tài chính, chi phí tài chính
khác, lãi/lỗ liên doanh liên kết và lợi nhuận khác. Quý đã công bố vẫn giữ số thật trong bảng.
- `rule`: `isolated_spike` (tăng vọt rồi về lại), `latest_cap` (quý mới nhất bị chặn trần), `trend_sigma` (lệch xu hướng).
- `description`: câu giải thích bằng tiếng Việt.

## 6. Dòng báo cáo chính (`line_id`)

| Phi tài chính | Ngân hàng |
|---|---|
| `net_revenue` doanh thu thuần | `nii` thu nhập lãi thuần |
| `gross_profit` lợi nhuận gộp | `net_fee_income` lãi thuần dịch vụ |
| `selling_expense`, `admin_expense` | `toi` tổng thu nhập hoạt động |
| `financial_income`, `interest_expense` | `opex` chi phí hoạt động |
| `pbt` lợi nhuận trước thuế | `ppop` lợi nhuận trước dự phòng |
| `npat` lợi nhuận sau thuế | `provision_expense` chi phí dự phòng |
| `npat_parent` LNST cổ đông công ty mẹ | `pbt`, `npat`, `npat_parent` |

Danh mục đầy đủ: cột `line_id` của bảng `annual`. Phương pháp tính từng dòng:
`phuong_phap/L1_phi_tai_chinh.md` (mục 3–8) và `phuong_phap/thiet_ke_bank_L1.md` (mục 0).
