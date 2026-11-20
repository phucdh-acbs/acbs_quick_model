"""Khóa bản chốt L1 phi tài chính v2.0 (v1.0 owner chốt 2026-11-17; v1.1 2026-11-18 sáng: chỉ engine năm + phân bổ;
v2.0 2026-11-18: engine quý + năm, default quý = 0,65 × EMA 8 quý + 0,35 × năm, tự làm sạch khoản bất thường;
v2.1 cùng ngày: chuỗi chưa đủ cho EMA dùng số kỳ liền trước; v2.2: doanh thu quý 3 thành phần; v2.3: default ngoài miền chỉ cảnh báo, giới hạn doanh thu dồn cục; v2.4 (2026-11-19): co biên độ mùa vụ doanh thu α = 0,6; v2.5 (2026-11-19): EMA quý có trọng số, biên gộp quý TTM + 0,25 quán tính, DT tài chính theo mức; v2.5.1: DT tài chính bình quân 4 quý, khoản bất thường theo đột biến cô lập; v2.6: neo năm trước theo số quý còn lại, LDLK theo mức; v2.6.1 (2026-11-20): trọng số neo theo số quý tới; v2.6.2: lãi suất trên dư nợ bình quân; docs/L1_phi_tai_chinh.md).

Dấu vân tay chỉ gồm phần tính toán của L1 trong template gốc `nonfinancial` (method L1, giả định L1/dùng chung và
default, `default_paths`, `one_off`, tần suất engine, `quarterly_engine`); sửa nhãn/mô tả không làm đỏ test. Muốn đổi L1: owner duyệt, cập nhật tài liệu
(tăng phiên bản), rồi mới cập nhật FROZEN_FINGERPRINT.
"""
import hashlib
import json
from pathlib import Path

from quick_model.paths import CONFIG_DIR

TEMPLATE = CONFIG_DIR / "templates" / "nonfinancial" / "template.json"
L1_VERSION = "2.6.3"
FROZEN_FINGERPRINT = "6cf8d249744146b0"

METHOD_KEYS = ("method_id", "block", "level", "kind", "version", "required_items", "outputs", "roles")
ASSUMPTION_KEYS = ("assumption_id", "method_id", "unit", "valid_range", "per_period", "shared", "optional",
                   "default", "auto_balance", "anchor_for")


def l1_contract() -> dict:
    t = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    l1 = sorted((m for m in t["methods"] if m.get("level") == "L1"), key=lambda m: m["method_id"])
    ids = {m["method_id"] for m in l1}
    assumptions = sorted((a for a in t["assumptions"]
                          if a.get("method_id") in ids or str(a.get("method_id", "")).endswith(".*")),
                         key=lambda a: a["assumption_id"])
    strip = lambda d: {k: v for k, v in d.items() if k != "description_vn"}  # noqa: E731
    return {
        "methods": [{k: m[k] for k in METHOD_KEYS if k in m} for m in l1],
        "assumptions": [{k: a[k] for k in ASSUMPTION_KEYS if k in a} for a in assumptions],
        "default_paths": strip(t["default_paths"]),
        "one_off": strip(t["one_off"]),
        "frequencies": {k: v for k, v in t["frequencies"].items() if k in ("implemented", "default")},
        "quarterly_engine": strip(t["quarterly_engine"]),
    }


def fingerprint() -> str:
    blob = json.dumps(l1_contract(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def test_l1_nonfinancial_is_frozen():
    assert fingerprint() == FROZEN_FINGERPRINT, (
        f"L1 phi tài chính v{L1_VERSION} đã chốt nhưng cấu hình L1 thay đổi (vân tay {fingerprint()}). "
        "Cần owner duyệt, cập nhật docs/L1_phi_tai_chinh.md rồi mới cập nhật FROZEN_FINGERPRINT.")


def test_l1_contract_covers_all_blocks():
    c = l1_contract()
    assert {m["block"] for m in c["methods"]} == {"revenue", "cogs", "sga", "working_capital", "ppe", "debt",
                                                  "non_operating", "tax_equity"}
    assert len(c["methods"]) == 8
