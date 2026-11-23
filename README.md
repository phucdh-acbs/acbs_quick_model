# ACBS Quick Model L1: thư viện và API dự phóng nhanh BCTC

Phiên bản mô hình: **L1 v2.6.4** (chốt 23/11/2026). Gói này để team IT test và tích hợp vào luồng dự phóng tự động
(sub-agent). Phòng Phân tích cung cấp thư viện và API. Luồng gọi, lịch chạy và truy vấn GBQ do IT quản lý.

> Kết quả là dự phóng tự động từ lịch sử BCTC, **không phải khuyến nghị đầu tư**. Mọi đầu ra dùng đại trà phải kèm
> ghi chú này (`meta.disclaimer`).

## Thay đổi so với v2.6.3

- **Ngân hàng:** default `inv_securities_growth` = `min(EMA 5 năm YoY CK đầu tư, deposit_growth cùng năm)`; theo quý không
  vượt `deposit_growth` cùng quý (cấu hình `quarterly_engine.caps`, bỏ qua khi analyst nhập số). Sửa ánh xạ dòng CK đầu tư
  trong `bs_lines.csv` (`inv_securities_growth`). Phi tài chính (L1 v2.6.3) không đổi.
- **Sửa lỗi:** chia cho 0 / `inf` trong `hybrid.py` (chi phí biến đổi khi doanh thu quý ≤ 0, tăng trưởng năm ≤ −100%, nền
  doanh thu ≤ 0 → quý đó để trống); `PerformanceWarning` ở `raw_loader.py` khi bảng có hàng trăm mã BCTC.

## 1. Gói này làm gì

Nhận BCTC thô của **một mã**, đúng bảng và cột GBQ, rồi trả về:

- Ba báo cáo dự phóng (kết quả kinh doanh, cân đối, lưu chuyển tiền tệ với phi tài chính) cùng các lịch phụ trợ.
  - Theo năm: 5 năm lịch sử và 1–10 năm dự phóng (mặc định 5).
  - Theo quý: 8 quý lịch sử và các quý dự phóng đến hết năm sau.
- Tỷ lệ chính, giả định đã dùng (kèm nguồn), cảnh báo, các khoản bất thường đã tự loại, trạng thái "có thể công bố".
- Giải thích từng ô: công thức, yếu tố, đóng góp vào thay đổi.

| Nhóm BCTC | `family` | Trạng thái |
|---|---|---|
| Phi tài chính | `nonfinancial` | Dùng được. **Không dùng cho bất động sản** |
| Ngân hàng | `bank` | Dùng được |
| Bất động sản, chứng khoán, bảo hiểm | — | Chưa hỗ trợ |

Mô hình tất định: cùng dữ liệu và cùng tham số luôn ra cùng kết quả. Gói không gọi BigQuery, không ghi file và không
giữ trạng thái giữa các lần gọi.

## 2. Cấu trúc thư mục

```
acbs_quick_model_v2.6.4/
├── README.md
├── pyproject.toml
├── quick_model/               engine + cấu hình mô hình (quick_model/config)
│   └── service.py             điểm vào duy nhất cho phía tích hợp
├── quick_model_api/app.py     REST API (FastAPI)
├── docs/
│   ├── HOP_DONG_DU_LIEU.md    lấy gì từ GBQ
│   ├── HUONG_DAN_THU_VIEN.md  dùng trong Python
│   ├── HUONG_DAN_API.md       endpoint, request, response, triển khai
│   ├── KET_QUA.md             ý nghĩa từng phần kết quả, trạng thái, mã cảnh báo
│   └── phuong_phap/           spec mô hình (công thức, giả định, lịch sử phiên bản)
├── examples/
│   ├── data/hpg, data/tcb     dữ liệu mẫu (BCTC công khai, đúng định dạng GBQ)
│   └── request_tcb_*.json     request API chạy được ngay
└── tests/                     kiểm tra gói (gồm khóa bản chốt L1)
```

Không có trong gói: dashboard, công cụ truy vấn GBQ / sinh SQL, backtest, report.

## 3. Cài đặt

Cần **Python 3.10–3.13** (pandas 2.x chưa có bản cài sẵn cho Python mới hơn; trên Windows dùng `py -3.12` nếu máy có
nhiều bản).

```bash
python -m venv .venv
```

Kích hoạt môi trường: Windows `.venv\Scripts\activate`, Linux/macOS `source .venv/bin/activate`. Sau đó cài gói:

```bash
pip install -e ".[api,test]"
```

Chỉ cần thư viện thì `pip install -e .` là đủ (phụ thuộc duy nhất là pandas).

Kiểm tra cài đặt:

```bash
python -m pytest -q
```

Mọi test phải qua (khoảng 15 giây).

## 4. Dùng nhanh

**Thư viện** (chi tiết ở `docs/HUONG_DAN_THU_VIEN.md`):

```python
from quick_model import service as S

out = S.forecast("HPG", "nonfinancial", folder="examples/data/hpg", years=3)
print(out.status["ok"], out.status["publishable"])
print(out.annual[["line_id", "label", "2025 A", "2026 F"]].head())
print(out.explain("npat_parent", "2026")["headline"])
payload = out.to_dict()   # JSON
```

**API** (chi tiết ở `docs/HUONG_DAN_API.md`):

```bash
uvicorn quick_model_api.app:app --port 8000
```

```bash
curl -X POST http://localhost:8000/v1/forecast -H "Content-Type: application/json" -d @examples/request_tcb_forecast.json
```

Tài liệu tương tác ở `http://localhost:8000/docs`.

## 5. Lưu ý khi dùng đại trà

- **Mức sai số kỳ vọng.** Khi chạy thử trên quá khứ (209 DN phi tài chính, 18 ngân hàng, 2022–2026), ở năm bình thường:
  - doanh thu cả năm của một DN điển hình lệch khoảng 11%, lợi nhuận cả năm khoảng 30%;
  - tổng lợi nhuận cả nhóm lệch khoảng 20%;
  - lợi nhuận ngân hàng lệch khoảng 16%.

  Năm ngành đảo chiều lệch nhiều hơn. Xem `docs/phuong_phap/`.
- **Chỉ đưa ra ngoài khi `status.publishable = true`.** Ngược lại thì đọc `issues`.
- **Không dùng cho bất động sản.** Thận trọng với DN có sự kiện riêng lớn (M&A, tái cấu trúc, ngừng hoạt động).
- **Năm sau kém chính xác hơn năm đang chạy.**
- **Dữ liệu GBQ là bản cập nhật cuối**, không phải số tại thời điểm công bố.

## 6. Phiên bản và thay đổi

- `meta.model_version` và `/health` đều trả `"L1 v2.6.4"`.
- L1 phi tài chính đã khóa. Nếu `quick_model/config` bị sửa, `tests/test_l1_frozen.py` sẽ báo lỗi.
- Chỉ Phòng Phân tích được đổi phương pháp, và mỗi lần đổi đi kèm phiên bản mới. Team IT không sửa cấu hình mô hình.

## 7. Báo lỗi khi test

Gửi kèm:
- mã, `family`, tham số request;
- `meta` và `status` của kết quả, hoặc thông báo lỗi 422;
- nếu nghi lỗi do dữ liệu: các dòng GBQ đã gửi.

Không gửi thông tin khách hàng hay thông tin đăng nhập.
