"""Chuyển đổi đơn vị hiển thị và áp chỉnh sửa ô assumption thành override (độc lập UI)."""

from __future__ import annotations

import math

from quick_model.engine.assumptions import AssumptionSpec, AssumptionValue, Scenario

from quick_model.units import UNITS  # noqa: E402

# đơn vị engine -> (nhãn hiển thị, hệ số hiển thị)
UNIT_DISPLAY = {u: (lab, k) for u, (lab, k, _) in UNITS.items()}


def to_display(v: float | None, unit: str) -> float | None:
    return None if v is None else v * UNIT_DISPLAY[unit][1]


def from_display(v, unit: str) -> float | None:
    """Ô trống (None/NaN) giữ là None — trống khác 0."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v) / UNIT_DISPLAY[unit][1]


def same_values(a: tuple, b: tuple) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        if (x is None) != (y is None):
            return False
        if x is not None and not math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12):
            return False
    return True


def apply_edits(s: Scenario, specs: list[AssumptionSpec], defaults: dict[str, AssumptionValue],
                periods: list[str], edited_rows: dict, reason: str | None, stamp: str) -> Scenario:
    """edited_rows: {chỉ số dòng: {tên cột kỳ: giá trị hiển thị}}; dòng theo thứ tự specs."""
    for row_idx, changes in edited_rows.items():
        sp = specs[int(row_idx)]
        cur = s.overrides.get(sp.assumption_id, defaults.get(sp.assumption_id))
        vals = list(cur.values)
        for col, v in changes.items():
            if col in periods:
                vals[periods.index(col)] = from_display(v, sp.unit)
        if not same_values(tuple(vals), cur.values):
            s = s.with_override(AssumptionValue(sp.assumption_id, tuple(vals), "analyst",
                                                override_reason=reason or None, updated_at=stamp))
    return s
