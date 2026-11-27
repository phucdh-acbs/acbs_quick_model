# Hướng dẫn thư viện Python

Phía tích hợp chỉ dùng `quick_model.service`. Các module khác của `quick_model` là engine nội bộ và có thể đổi giữa các
phiên bản.

```python
from quick_model import service as S
```

## 1. `S.forecast(ticker, family="nonfinancial", *, frames=None, folder=None, years=None, overrides=None)`

| Tham số | Ý nghĩa |
|---|---|
| `ticker` | Mã cổ phiếu |
| `family` | `"nonfinancial"` hoặc `"bank"` |
| `frames` | Dữ liệu GBQ: `{tên bảng GBQ: DataFrame hoặc list[dict]}`, đúng tên cột GBQ; được chứa nhiều mã (thư viện lọc theo mã) |
| `folder` | Hoặc thư mục CSV `<mã viết thường>_<is\|bs\|cf\|note\|notes>.csv`. Truyền đúng một trong `frames` / `folder` |
| `years` | Số năm dự phóng tính từ năm gốc, 1–10, mặc định 5 |
| `overrides` | Giả định do analyst/agent đặt (mục 3) |

Cách mô hình chạy:
- **Năm gốc** là năm có báo cáo cả năm mới nhất.
- Engine chạy **theo quý** từ sau quý công bố mới nhất đến hết Q4 năm sau, rồi chạy theo năm.
- **Số cả năm luôn bằng tổng 4 quý.**
- Mọi khoản mục dùng phương pháp L1, giả định tự tính từ lịch sử, khoản bất thường ngoài hoạt động chính được tự loại.
- Mỗi mã mất khoảng 1 giây.

```python
frames = {"INCOME_STATEMENT": df_is, "BALANCE_SHEET": df_bs, "CASH_FLOW": df_cf,   # bắt buộc (ngân hàng: không có CF)
          "STX_FSC_NOTECOMPANY": df_note}                                            # tùy chọn (ngân hàng: STX_FSC_NOTEBANK)
out = S.forecast("HPG", "nonfinancial", frames=frames)
```

## 2. Hợp đồng dữ liệu

`S.data_contract(family)` trả về `{bảng GBQ: {"required", "id_column", "metadata_columns", "item_columns",
"item_labels"}}`, tức là danh sách cột cần truy vấn. Chi tiết xem `HOP_DONG_DU_LIEU.md`.

## 3. Đặt giả định (`overrides`)

```python
S.assumption_catalog("nonfinancial")   # ID, nhãn, đơn vị, miền hợp lệ, cách tính mặc định
out = S.forecast("HPG", "nonfinancial", frames=frames, years=3,
                 overrides={"gross_margin": {"2026": 0.17},            # chỉ năm 2026, năm khác giữ số tự tính
                            "revenue_yoy_growth": [0.10, 0.08, 0.06],   # đủ số năm
                            "effective_tax_rate": 0.18})                # một số cho mọi năm
```

- **Đơn vị engine.** Tỷ lệ viết dạng thập phân (0,17 = 17%); `ratio_annual` là tỷ lệ theo năm; `days` là số ngày;
  `VND` và `VND_per_year` là đồng.
- **Giá trị là mục tiêu cả năm.** Với năm đang chạy, phần chênh so với các quý đã công bố được dồn vào các quý còn lại.
- **Nguồn.** Giả định đã đặt có `source = "analyst"` trong kết quả; số tự tính có `source = "historical_default"`.
- **Lỗi.** Giả định không tồn tại hoặc sai số năm gây `ServiceError`. Số nằm ngoài miền hợp lệ bị báo lỗi trong `issues`.
- **Kịch bản ngành.** Đặt cùng một override cho mọi mã trong ngành, ví dụ kịch bản xấu biên gộp giảm.

## 4. Kết quả `ForecastOutput`

| Thuộc tính | Nội dung |
|---|---|
| `meta` | Mã, nhóm, `model_version`, năm gốc, quý công bố mới nhất, năm và quý dự phóng, giả định đã đặt, `disclaimer` |
| `status` | `ok`, `usable`, `flags`, `publishable`, `balanced`, `feasible`, `funding_gap_max`, số lỗi và cảnh báo, … |
| `annual` | DataFrame dòng × năm. Gồm cột `line_id`, `label`, `statement` (IS/BS/CF/SCH), `unit`, rồi `"2025 A"` (thực tế), `"6T2026 A"` (lũy kế năm đang chạy), `"2026 F"` (dự phóng) |
| `quarterly` | DataFrame dòng × quý: `"2026Q2 A"`, `"2026Q3 F"`, … |
| `ratios` | `{"annual": DataFrame, "quarterly": DataFrame}`: tỷ lệ chính, **đơn vị %** |
| `assumptions` | Giả định × năm dự phóng, kèm `source`, `unit`, `note` (cách tính) |
| `issues` | `severity` (error/warning/info), `code`, `block`, `period`, `message` |
| `one_offs` | Khoản bất thường đã tự loại khỏi lịch sử: dòng, quý, số tiền, quy tắc, mô tả |

- `out.to_dict()`: toàn bộ kết quả dạng JSON (NaN thành `null`), cùng cấu trúc với response của API.
- `out.explain(line_id, period)`: giải thích một ô, gồm `headline` (một câu tóm tắt), `formula`, `drivers`, `bridge`
  (đóng góp từng yếu tố), `notes`. `period` có dạng `"2026"` hoặc `"2026Q3"`.

Ý nghĩa từng trường: xem `KET_QUA.md`.

## 5. Lỗi

- **Đầu vào sai** (nhóm, thiếu bảng hay cột, override, mã không có dữ liệu): `S.ServiceError` (kế thừa `ValueError`),
  thông báo tiếng Việt nêu rõ lỗi.
- **Lỗi mô hình:** không gây exception. Khi đó `status.ok = false` và chi tiết nằm trong `issues` (`severity = "error"`).

## 6. Vận hành

- Thư viện không ghi file, không gọi mạng, không giữ trạng thái: chạy nhiều mã song song bằng nhiều tiến trình.
- `meta` và các số liệu không phụ thuộc thời điểm chạy.
