# Hướng dẫn nhanh cho agent: khi nào dùng kết quả dự phóng

Áp dụng từ v2.6.8. Một trang, dành cho team IT chạy dự phóng bằng agent.

## 1. Quy tắc một dòng

**Dùng kết quả khi `status.usable = true`.** Thiếu vốn hay LNST âm không làm thư viện từ chối: mô hình vẫn trả đủ báo cáo, kèm cờ để bạn biết.

## 2. Ba trường cần đọc

| Trường | Ý nghĩa |
|---|---|
| `status.usable` | Có kết quả và không có lỗi chặn. **Vẫn `true` khi thiếu vốn hay LNST âm** |
| `status.flags` | Danh sách lưu ý: `funding_gap` (thiếu vốn), `not_balanced` (bảng cân đối lệch), `negative_earnings` (LNST âm ở kỳ dự phóng) |
| `status.publishable` | Chỉ `true` khi cân đối, đủ vốn và không lỗi. Dùng để quyết định **đăng ra ngoài**, không phải để quyết định chạy hay dùng |

## 3. Mẫu logic cho agent

```python
st = resp["status"]
if not st["usable"]:
    # lỗi chặn: không có báo cáo; đọc resp["issues"], chuyển Phòng Phân tích
    ...
else:
    use(resp)                                   # luôn dùng được
    if "funding_gap" in st["flags"]:
        note(f"Thiếu vốn tối đa {st['funding_gap_max']:,.0f} đồng từ {st['first_gap_period']}")
    if "negative_earnings" in st["flags"]:
        skip_pe_multiple()                      # không nhân P/E với LNST âm
    if not st["publishable"]:
        hold_for_analyst()                      # chỉ chặn đăng tự động, không chặn việc dùng số
```

## 4. Hai tình huống hay gặp

- **Thiếu vốn (`funding_gap`).** Bảng cân đối vẫn cân, nhưng doanh nghiệp cần vay vượt trần cho phép (hoặc ngân hàng cần huy động liên ngân hàng vượt trần). Kết quả là kịch bản "nếu không có vốn bổ sung". Hiển thị cảnh báo và số thiếu hụt, đừng bỏ kết quả.
- **LNST âm (`negative_earnings`).** Số âm là kết quả đúng của mô hình. P/E trên LNST ≤ 0 không có nghĩa, nên bước định giá phải tự xử lý (bỏ qua P/E hoặc dùng chỉ tiêu khác). Thư viện không chặn số âm.

## 5. Khi nào mới là lỗi

Chỉ khi `usable = false` (có lỗi chặn trong `issues`) hoặc API trả HTTP 422 (đầu vào sai: thiếu bảng hay cột, sai `family`, override không tồn tại, không có dữ liệu của mã). Thiếu vốn không bao giờ gây 422.

## 6. Lưu ý khi đọc kết quả

- Luôn kèm `meta.model_version` và `meta.disclaimer` trong đầu ra. Kết quả là dự phóng tự động, không phải khuyến nghị đầu tư.
- Với doanh nghiệp có nhiều quý lỗ hoặc doanh thu biến động mạnh, sai số lợi nhuận thường rất lớn (80–100% trong backtest). Nên hiển thị `flags` và `issues` cạnh con số.
- Cần rà soát, kiểm chứng trước khi dùng ngoài bộ phận.
