# Công cụ AI cho mô hình L1: dự phóng và định giá

Hai công cụ để agent AI dùng mô hình L1 trong repo này. Chỉ gọi `quick_model.service`; **không sửa tệp nào của mô hình**. Không gọi mạng, không gọi mô hình AI,
cùng đầu vào cho cùng kết quả.

Nguyên tắc: **máy đo, kiểm và tính; AI chọn.** Công cụ không đặt sẵn phương pháp, tỷ trọng hay bội số nào.

| Công cụ | Làm gì | Không làm gì |
|---|---|---|
| `quick_model_forecast` | dự phóng một mã, lưu lượt chạy theo mã nội dung, cổng kiểm, câu trả lời khoảng 1 KB | không ra giá |
| `quick_model_valuation` | bảng đo cho AI chọn; AI chọn xong thì kiểm và tính giá trị mỗi cổ phiếu | không tự chọn |

**Luật định giá là luật tạm thời**, chưa phải công thức cuối cùng Phòng Phân tích chốt (`rule_status` trong kết quả nói lại điều này).

## Dùng nhanh

```bash
python -m quick_model_tools schemas
```

```bash
python -m quick_model_tools forecast --in input.json
```

Đầu vào của dự phóng (thư mục dữ liệu đúng tệp và cột như `docs/HOP_DONG_DU_LIEU.md`):

```json
{"op": "run", "ticker": "TCB", "com_type_code": "NH", "icb_code": "8355", "dataset_dir": "examples/data/tcb", "store": "runs"}
```

Kết quả `brief`: `run_id`, `usable`, `flags`, `reasons`, `publishable`, số chính (tỷ đồng), quý tới. Theo hướng dẫn của mô hình (`docs/HUONG_DAN_NHANH_AGENT.md`):
**dùng số khi `usable` là true**; thiếu vốn, LNST âm, nghi đổi phạm vi hợp nhất là cờ trong `flags`, không chặn; `publishable` chỉ quyết định đăng tự động.
`usable` false: `reasons` nói lý do; `NEEDS_INPUT` kê các giả định mô hình cần nhập (truyền `overrides` kèm lý do). `model_ok` giữ cho phía gọi cũ, bằng `usable`.
`op`: `run`, `view` (`brief`, `thesis`, `table`, `assumptions`, `issues`, `one_offs`, `ratios`), `explain` (một ô), `assumptions`.

## Chốt ngành

`com_type_code` và `icb_code` bắt buộc ở `op: run`, lấy từ dòng ORGANIZATION có `STATUS = 1` của mã. Nhóm báo cáo tài chính suy từ hai trường này:

| Mã loại | Kết quả |
|---|---|
| `CT`, ICB không bắt đầu bằng 86 | phi tài chính |
| `NH` | ngân hàng |
| `CK` chứng khoán, `BH` bảo hiểm, ICB 86xx bất động sản | từ chối `FAMILY_UNSUPPORTED`, chờ bản cập nhật mô hình; tạm dùng luồng sẵn có |

Vì sao bắt buộc: một mã chứng khoán chạy như phi tài chính vẫn ra số và không có cảnh báo nào. Khi mô hình có thêm nhóm, chỉ cần sửa `ROUTING` trong `settings.py`.

## Cổng kiểm (cờ, không chặn)

| Mã | Khi nào |
|---|---|
| `FUNDING_GAP` | thiếu vốn sau khi vay tự động: kịch bản "không có vốn bổ sung", vẫn dùng số |
| `LATEST_QUARTER_NOT_USED` | quý mới nhất có trong dữ liệu nhưng mô hình bỏ |
| `SCOPE_SUSPECT` | nghi đổi phạm vi hợp nhất: có thể chạy lại với `rebase: true`, chuyên viên nên xem |
| `NEGATIVE_EARNINGS` | LNST mẹ âm ở quý mới nhất hoặc kỳ dự phóng: định giá tự bỏ P/E khi EPS 12 tháng tới ≤ 0, P/B và lợi nhuận thặng dư vẫn dùng được |
| `NOT_PUBLISHABLE` | mô hình chưa cho đăng tự động; vẫn dùng số |
| `NOT_BALANCED` | bảng cân đối dự phóng lệch |
| `OVERRIDE_OUTSIDE_HISTORY` | giả định nhập tay ngoài khoảng lịch sử |

## Định giá: hai bước

**Bước 1. Bảng đo** (chưa có `choice`):

```json
{"run_id": "qmt:…", "store": "runs", "valuation_date": "2026-11-23", "multiples_csv": "multiples.csv", "shares_million": 7000, "shares_source": "…", "market": {"rf": 0.04, "erp": 0.08}}
```

Trả `status: need_choice`: hồ sơ dự phóng (LNST, ROE, EPS tới so EPS 4 quý, độ biến động quá khứ, tỷ trọng công ty liên kết, cảnh báo), từng phương pháp
(`pe`, `pb`, `pb_roe`, `residual_income`) với các mốc đo được (P10, P25, trung vị, P75, P90, hôm nay) và giá trị của mỗi mốc, hôm nay ở phân vị nào trong 5 năm,
trần tái định giá; phương pháp không dùng được kèm lý do. `pb_roe` và `residual_income` cho lưới thử theo beta và g.

**Bước 2. AI gửi `choice`:** 1 đến 3 phương pháp, tổng tỷ trọng 1, mỗi phương pháp có lý do (ít nhất 30 ký tự):

```json
{"decided_by": "valuation", "methods": {"pe": {"weight": 0.6, "multiple": 7.5, "reason": "…"}, "residual_income": {"weight": 0.4, "beta": 1.0, "g": 0.03, "reason": "…"}}}
```

Công cụ kiểm (phương pháp có trong bảng, tỷ trọng, miền beta và g, **bội số không vượt max(hôm nay, P10)** trừ khi lý do dẫn mã chất xúc tác người gọi đưa vào
hoặc EPS tới tăng ít nhất 20%) rồi tính. Lỗi được nêu hết một lần. `rf` và `erp` do người gọi đưa vào, AI không đặt.

Cờ kèm kết quả: `EPS_BEFORE_WELFARE_FUND`, `DIVIDEND_ZERO`, `ONE_OFF_REMOVED`, `REBASED`, `MIXED_HORIZON`, `ASSOCIATES_SHARE_HIGH`, `RERATING_IN_VALUE`, `OUTSIDE_5Y_RANGE`, `MULTIPLES_STALE`.

## Giới hạn

- Chỉ phi tài chính (không bất động sản) và ngân hàng, đúng phạm vi của mô hình hiện tại; chứng khoán, bảo hiểm, bất động sản chờ bản cập nhật.
- Chưa có DCF, EV/EBITDA, DDM, SOTP: bảng đo nói rõ lý do từng phương pháp.
- Luật định giá tạm thời. Dự phóng tự động từ lịch sử BCTC, không phải khuyến nghị đầu tư (ghi chú bắt buộc của mô hình, `disclaimer` trong kết quả).

## Kiểm thử

```bash
python -m pytest tests/test_quick_model_tools.py
```
