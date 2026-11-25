"""Hai công cụ cho AI dùng trên mô hình L1 (`quick_model.service`), không sửa gì của mô hình:
  forecast_tool   dự phóng một mã, lưu lượt chạy theo nội dung, cổng kiểm, câu trả lời ngắn
  valuation_tool  bảng đo (phương pháp, bội số, căn cứ) cho AI định giá chọn; AI chọn xong thì kiểm và tính
Máy đo và tính, AI chọn. Không gọi mô hình AI, không gọi mạng, cùng đầu vào cho cùng kết quả."""
from . import forecast_tool, valuation_tool
from .schemas import tool_definitions

__all__ = ["forecast_tool", "valuation_tool", "tool_definitions"]
