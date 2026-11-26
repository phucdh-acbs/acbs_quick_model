# Hướng dẫn REST API

API là lớp mỏng bọc `quick_model.service` (xem `HUONG_DAN_THU_VIEN.md`): cùng tham số và cùng kết quả. API không giữ
trạng thái, không gọi BigQuery, không ghi file.

## 1. Chạy

```bash
pip install -e ".[api]"
```

```bash
uvicorn quick_model_api.app:app --host 0.0.0.0 --port 8000 --workers 4
```

- Tài liệu tương tác: `http://<host>:8000/docs`. Schema OpenAPI: `/openapi.json`.
- Mỗi request dự phóng tốn khoảng 1–2 giây trên một lõi CPU. Tăng `--workers` theo số lõi.
- Gói chưa có xác thực, giới hạn truy cập hay TLS. Đặt API sau gateway nội bộ và không mở ra internet.

## 2. Endpoint

| Method | Đường dẫn | Mô tả |
|---|---|---|
| GET | `/health` | `{"status": "ok", "model_version": "L1 v2.6.7"}` |
| GET | `/v1/families/{family}/data-contract` | Bảng và cột GBQ cần lấy (cache lại theo phiên bản) |
| GET | `/v1/families/{family}/assumptions` | Giả định có thể đặt qua `overrides` |
| POST | `/v1/forecast` | Dự phóng một mã |
| POST | `/v1/explain` | Dự phóng rồi giải thích một ô |

`family` nhận một trong hai giá trị: `nonfinancial` hoặc `bank`.

## 3. `POST /v1/forecast`

| Trường | Bắt buộc | Mô tả |
|---|---|---|
| `ticker` | Có | Mã cổ phiếu |
| `family` | Không | `"nonfinancial"` (mặc định) hoặc `"bank"` |
| `statements` | Có | `{tên bảng GBQ: [dòng, …]}`, mỗi dòng là object có đúng tên cột GBQ |
| `years` | Không | 1–10, mặc định 5 |
| `overrides` | Không | `{assumption_id: số \| [số mỗi năm] \| {"2026": số}}`, đơn vị engine |

Request mẫu chạy được ngay: `examples/request_tcb_forecast.json`.

```bash
curl -X POST http://localhost:8000/v1/forecast -H "Content-Type: application/json" -d @examples/request_tcb_forecast.json
```

Dựng `statements` từ kết quả truy vấn GBQ:

```python
statements = {t: json.loads(df.to_json(orient="records", date_format="iso"))
              for t, df in {"INCOME_STATEMENT": df_is, "BALANCE_SHEET": df_bs}.items()}
requests.post(f"{host}/v1/forecast", json={"ticker": "TCB", "family": "bank", "statements": statements,
                                           "overrides": {"credit_cost": {"2026": 0.012}}})
```

Response có cùng cấu trúc với `ForecastOutput.to_dict()` (rút gọn):

```json
{
  "meta": {"ticker": "TCB", "family": "bank", "model_version": "L1 v2.6.7", "origin_year": "2025",
           "last_reported_quarter": "2026Q3", "forecast_years": ["2026", "2027", "2028"],
           "forecast_quarters": ["2026Q4", "2027Q1", "..."], "overrides": [], "disclaimer": "..."},
  "status": {"ok": true, "publishable": true, "balanced": true, "feasible": true, "errors": 0, "warnings": 27},
  "annual": {"columns": ["2021 A", "...", "9T2026 A", "2026 F", "2027 F", "2028 F"],
             "rows": [{"line_id": "npat_parent", "label": "LNST cổ đông công ty mẹ", "statement": "IS",
                       "unit": "VND", "values": {"2025 A": 1.9e13, "2026 F": 2.2e13}}]},
  "quarterly": {"columns": ["..."], "rows": ["..."]},
  "ratios": {"annual": {"columns": ["..."], "rows": [{"ratio": "NIM – ... (%)", "values": {"2026 F": 3.6}}]},
             "quarterly": {"...": "..."}},
  "assumptions": [{"assumption_id": "credit_cost", "label": "...", "unit": "ratio_annual", "source": "analyst",
                   "note": "", "2026": 0.012, "2027": 0.009}],
  "one_offs": [{"line": "...", "quarter": "2026Q1", "amount": 1.2e11, "rule": "...", "description": "..."}],
  "issues": [{"severity": "warning", "code": "ONE-01", "block": "...", "period": "2026Q1", "message": "..."}]
}
```

Khi dự phóng có lỗi chặn (`status.ok = false`), response chỉ có `meta`, `status` và `issues`.

## 4. `POST /v1/explain`

Body giống `/v1/forecast` và thêm hai trường: `line` (vd `"npat_parent"`) và `period` (`"2026"` hoặc `"2026Q3"`). Mẫu:
`examples/request_tcb_explain.json`.

Response: `{"meta": …, "explanation": {"headline", "formula", "value", "prior_value", "change", "drivers", "bridge",
"notes"}}`. `headline` là một câu tiếng Việt tóm tắt thay đổi và yếu tố chính.

## 5. Mã lỗi

| HTTP | Khi nào |
|---|---|
| 200 | Chạy xong; kiểm tra thêm `status.ok` và `status.publishable` |
| 422 | Đầu vào sai: thiếu bảng hay cột, sai `family`, override không tồn tại, không có dữ liệu của mã. `detail` nêu lý do |
| 500 | Lỗi ngoài dự kiến: gửi request và log cho Phòng Phân tích |

## 6. Gợi ý cho luồng sub-agent

1. Phân loại mã: ngân hàng dùng `family = "bank"`, còn lại dùng `"nonfinancial"`. Loại bất động sản, chứng khoán, bảo
   hiểm.
2. Gọi `/data-contract` một lần cho mỗi phiên bản mô hình để dựng truy vấn GBQ.
3. Chỉ công bố khi `status.publishable = true`. Nếu không, chuyển analyst xem `issues`.
4. Agent viết nhận định chỉ trích số từ response; dùng `explain` cho các ô chính.
5. Áp kịch bản ngành bằng cách gửi cùng `overrides` cho mọi mã trong ngành.
6. Luôn kèm `meta.model_version` và `meta.disclaimer` trong đầu ra.
