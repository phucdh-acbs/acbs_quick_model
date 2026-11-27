"""Định nghĩa công cụ cho AI (dạng JSON Schema của Claude API: name, description, input_schema)."""
from . import settings

FORECAST = {
    "name": "quick_model_forecast",
    "description": "Dự phóng báo cáo tài chính của một mã (phi tài chính không gồm bất động sản, ngân hàng) bằng mô hình L1. Chứng khoán, bảo hiểm, bất động sản bị từ chối FAMILY_UNSUPPORTED (chờ bản cập nhật mô hình). Trả lời ngắn (tỷ đồng). Dùng số khi usable = true; flags là lưu ý (thiếu vốn, LNST âm...), không chặn; usable = false thì reasons nói lý do (NEEDS_INPUT: truyền overrides). Không có giá.",
    "input_schema": {"type": "object", "additionalProperties": False, "required": ["op"], "properties": {
        "op": {"enum": ["run", "view", "explain", "assumptions"]},
        "ticker": {"type": "string"},
        "com_type_code": {"type": "string", "description": "run, bắt buộc: COM_TYPE_CODE của dòng ORGANIZATION có STATUS = 1 (CT, NH; CK và BH chưa hỗ trợ)"},
        "icb_code": {"type": "string", "description": "run, bắt buộc: ICB_CODE 4 chữ số của dòng ORGANIZATION (86xx là bất động sản, chưa hỗ trợ)"},
        "family": {"enum": ["nonfinancial", "bank"], "description": "assumptions: bắt buộc; run: không cần, nếu có phải khớp nhóm suy từ com_type_code"},
        "dataset_dir": {"type": "string", "description": "run: thư mục có <ticker>_is.csv … (cột GBQ)"}, "store": {"type": "string", "description": "thư mục lưu lượt chạy, giữ giữa các lần gọi"},
        "years": {"type": "integer", "minimum": 1, "maximum": 10}, "overrides": {"type": "object", "description": "{mã giả định: số | [số mỗi năm] | {năm: số}}; tỷ lệ là phân số"},
        "rebase": {"type": "boolean", "description": "run: đặt lại giả định khi cổng báo đổi phạm vi hợp nhất; chỉ dùng sau lượt bị SCOPE_SUSPECT"},
        "run_id": {"type": "string"}, "view": {"enum": ["brief", "thesis", "table", "assumptions", "issues", "one_offs", "ratios"]}, "freq": {"enum": ["year", "quarter"]},
        "line": {"type": "string"}, "period": {"type": "string"}}}}

CHOICE = {"type": "object", "additionalProperties": False, "required": ["methods"], "properties": {
    "decided_by": {"type": "string", "description": "vai đã chọn"},
    "methods": {"type": "object", "minProperties": 1, "maxProperties": 3, "propertyNames": {"enum": ["pe", "pb", "pb_roe", "residual_income"]},
                "additionalProperties": {"type": "object", "additionalProperties": False, "required": ["weight", "reason"], "properties": {
                    "weight": {"type": "number", "exclusiveMinimum": 0, "maximum": 1}, "multiple": {"type": "number", "exclusiveMinimum": 0, "description": "pe, pb"},
                    "beta": {"type": "number", "description": "pb_roe, residual_income"}, "g": {"type": "number", "description": "pb_roe, residual_income: tăng trưởng dài hạn"},
                    "reason": {"type": "string", "minLength": 30, "maxLength": 600}}}}}}

VALUATION = {
    "name": "quick_model_valuation",
    "description": "Định giá từ một lượt dự phóng. Gọi không kèm choice: bảng đo (không có giá) để AI chọn phương pháp, tỷ trọng, bội số. Gọi kèm choice: kiểm rồi tính. Không có mặc định nào. Luật tạm thời, chưa phải công thức cuối cùng Phòng Phân tích chốt.",
    "input_schema": {"type": "object", "additionalProperties": False, "required": ["run_id", "store", "valuation_date", "multiples_csv", "shares_million", "shares_source"], "properties": {
        "run_id": {"type": "string"}, "store": {"type": "string"}, "valuation_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "multiples_csv": {"type": "string", "description": "CSV lịch sử bội số theo ngày: date, pe, pb (ít nhất " + str(settings.VALUATION["min_days"]) + " phiên)"},
        "shares_million": {"type": "number", "exclusiveMinimum": 0}, "shares_source": {"type": "string"},
        "market": {"type": "object", "required": ["rf", "erp"], "properties": {"rf": {"type": "number"}, "erp": {"type": "number"}}, "description": "để dựng chi phí vốn chủ cho pb_roe và residual_income"},
        "eps_deduction": {"type": "number", "description": "phần LNST trích quỹ khen thưởng, phúc lợi"}, "dps": {"type": "number", "description": "cổ tức tiền mặt mỗi năm, đồng/cp"},
        "keep_one_offs": {"type": "boolean"}, "catalysts": {"type": "array", "items": {"type": "object", "required": ["code", "text"], "properties": {"code": {"type": "string"}, "text": {"type": "string"}}}},
        "choice": CHOICE}}}


def tool_definitions() -> list[dict]:
    return [FORECAST, VALUATION]
