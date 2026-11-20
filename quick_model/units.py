"""Đơn vị engine: nhãn hiển thị, hệ số hiển thị và định dạng Excel (một nguồn cho UI, template, hồ sơ)."""

from __future__ import annotations

# đơn vị engine -> (nhãn hiển thị, hệ số hiển thị, định dạng số Excel)
UNITS = {
    "ratio": ("%", 100.0, "0.00%"),
    "ratio_annual": ("%/năm", 100.0, "0.00%"),
    "days": ("ngày", 1.0, "#,##0.0"),
    "VND": ("tỷ VND", 1e-9, "#,##0.0;(#,##0.0);-"),
    "VND_per_year": ("tỷ VND/năm", 1e-9, "#,##0.0;(#,##0.0);-"),  # mức theo năm, dùng × số ngày / 365 (không chia quý)
    "tonnes": ("nghìn tấn", 1e-3, "#,##0.0"),
    "VND_per_tonne": ("triệu VND/tấn", 1e-6, "#,##0.00"),
    "years": ("năm", 1.0, "#,##0.0"),
    "stores": ("cửa hàng", 1.0, "#,##0"),
    "VND_per_store_year": ("triệu VND/cửa hàng/năm", 1e-6, "#,##0.0"),
}
KNOWN_UNITS = frozenset(UNITS)
