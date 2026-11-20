# Hợp đồng dữ liệu: lấy gì từ GBQ

Mô hình đọc BCTC thô **đúng tên bảng và tên cột của GBQ** (nguồn FiinX). Gói không truy vấn GBQ; phía IT tự truy vấn rồi
truyền kết quả vào thư viện (DataFrame) hoặc API (JSON). Hai phía dùng chung một database nên dữ liệu giống nhau.

Danh sách cột chính xác của từng nhóm luôn lấy từ nguồn có sẵn, không gõ tay:
- thư viện: `quick_model.service.data_contract("nonfinancial" | "bank")`;
- API: `GET /v1/families/{family}/data-contract`;
- file: `quick_model/config/manifests/<family>.csv` (bảng, mã khoản mục, tên tiếng Việt, dòng mapping).

## 1. Bảng cần lấy

| Nhóm | Bảng GBQ | Bắt buộc | Số mã khoản mục | Cột ID |
|---|---|---|---|---|
| `nonfinancial` | `INCOME_STATEMENT` | Có | 25 (ISA…) | `INCOME_STATEMENT_ID` |
| `nonfinancial` | `BALANCE_SHEET` | Có | 141 (BSA…) | `BALANCE_SHEET_ID` |
| `nonfinancial` | `CASH_FLOW` | Có | 48 (CFA…) | `CASH_FLOW_ID` |
| `nonfinancial` | `STX_FSC_NOTECOMPANY` | Không (nên có) | 60 | `NoteCompanyId` |
| `bank` | `INCOME_STATEMENT` | Có | 25 (ISB…, ISA…) | `INCOME_STATEMENT_ID` |
| `bank` | `BALANCE_SHEET` | Có | 100 (BSA…, BSB…) | `BALANCE_SHEET_ID` |
| `bank` | `STX_FSC_NOTEBANK` | Không (nên có) | 219 | `NoteBankId` |

Ngân hàng không cần lưu chuyển tiền tệ (mô hình ngân hàng cân bảng cân đối bằng kết quả kinh doanh).

## 2. Cột metadata (mỗi bảng)

Bảng IS, BS, CF:

`ORGAN_CODE, TICKER, YEAR_REPORT, LENGTH_REPORT, LENGTH_SERIES, START_DATE, END_DATE, PUBLIC_DATE, IS_AUDIT,
REPORT_FORM_TYPE_CODE, SOURCE_NAME, STATUS, CREATE_DATE, UPDATE_DATE`

Bảng thuyết minh dùng tên dạng CamelCase:

`OrganCode, Ticker, YearReport, LengthReport, LengthSeries, StartDate, EndDate, PublicDate, IsAudit,
ReportFormTypeCode, SourceName, Status, CreateDate, UpdateDate`

| Cột | Ý nghĩa và giá trị hợp lệ |
|---|---|
| `LENGTH_REPORT` | 1, 2, 3, 4 = quý 1–4; 5 = cả năm; 6 = lũy kế 6 tháng; 9 = lũy kế 9 tháng |
| `LENGTH_SERIES` | Số tháng của kỳ: 3 (quý), 12 (năm), 6, 9; phải khớp `LENGTH_REPORT` |
| `STATUS` | Chỉ dùng bản ghi `STATUS = 1` |
| `UPDATE_DATE` | Nhiều bản ghi cùng mã × năm × kỳ: mô hình giữ bản `UPDATE_DATE` mới nhất (hòa thì ID lớn hơn) |
| Ngày | Chuỗi ngày giờ GBQ (vd `2025-03-31 00:00:00.000000 UTC`) hoặc ISO 8601 |
| Khoản mục | Số, đơn vị VND như trong GBQ; ô trống là trống (không thay bằng 0) |

## 3. Phạm vi và số kỳ

- **BCTC hợp nhất**, cùng nguồn mà phòng phân tích dùng để kiểm chứng mô hình.
- **Lấy từ năm 2012** (tối thiểu 5 năm và 8 quý gần nhất; thiếu thì mô hình vẫn chạy nhưng gắn cờ mẫu yếu).
- **Lấy đủ các kỳ**: quý (1–4), cả năm (5), lũy kế 6 và 9 tháng. Mô hình cần số cả năm để có năm gốc và số quý để
  chạy phần dự phóng theo quý.
- Dữ liệu chứa nhiều mã cũng được: thư viện lọc theo `TICKER` của request.

## 4. Mẫu truy vấn (tham khảo)

Mẫu áp quy tắc chọn bản ghi ở phía GBQ. Mô hình vẫn áp lại quy tắc này nên gửi chưa lọc cũng được. Thay `<project>`,
`<dataset>` theo môi trường của IT, và lấy danh sách cột từ hợp đồng dữ liệu.

```sql
SELECT INCOME_STATEMENT_ID, ORGAN_CODE, TICKER, YEAR_REPORT, LENGTH_REPORT, LENGTH_SERIES, START_DATE, END_DATE,
       PUBLIC_DATE, IS_AUDIT, REPORT_FORM_TYPE_CODE, SOURCE_NAME, STATUS, CREATE_DATE, UPDATE_DATE,
       ISA1, ISA2, ISA3 /* ... đủ cột theo data-contract ... */
FROM `<project>.<dataset>.INCOME_STATEMENT`
WHERE TICKER = @ticker AND YEAR_REPORT >= 2012 AND STATUS = 1
QUALIFY ROW_NUMBER() OVER (PARTITION BY TICKER, YEAR_REPORT, LENGTH_REPORT
                           ORDER BY UPDATE_DATE DESC NULLS LAST, INCOME_STATEMENT_ID DESC) = 1
```

Dùng tham số (`@ticker`), không ghép chuỗi. Tài khoản truy vấn chỉ cần quyền đọc.

## 5. Dạng file CSV (khi chạy từ thư mục)

- Mỗi mã một thư mục, mỗi bảng một file `<mã viết thường>_<hậu tố>.csv`. Hậu tố: `is`, `bs`, `cf`, `note`
  (thuyết minh DN), `notes` (thuyết minh ngân hàng).
- File có dòng tiêu đề đúng tên cột GBQ. Ví dụ: `examples/data/hpg/`, `examples/data/tcb/`.

## 6. Lỗi dữ liệu thường gặp

| Thông báo | Nguyên nhân |
|---|---|
| `thiếu cột [...]` | Truy vấn thiếu cột so với hợp đồng dữ liệu |
| `thiếu dữ liệu bảng bắt buộc` | Không gửi bảng bắt buộc, hoặc bảng không có dòng nào của mã |
| `LENGTH_SERIES không khớp LENGTH_REPORT` | Dữ liệu kỳ không nhất quán; dòng lỗi bị bỏ, kiểm tra nguồn |
| `cột ... có giá trị không phải số` | Ô khoản mục chứa chữ |
| `chưa có báo cáo cả năm nào để làm năm gốc` | Thiếu kỳ `LENGTH_REPORT = 5` |
