"""Kiểm tra assumption đầu vào và kết quả dự phóng.

error: chặn tính toán hoặc làm kết quả không hợp lệ (thiếu input, số analyst nhập ngoài miền hợp lệ,
       BS mất cân đối, LCTT không khớp tiền, trả nợ vượt dư nợ…).
warning: cần analyst xem lại (ngoài khoảng lịch sử, mẫu yếu, LNST/vốn chủ/FCF âm…).
info: thông tin tham khảo.
Không áp sàn lợi nhuận hay trần tăng trưởng tùy tiện; chỉ cảnh báo.
"""

from __future__ import annotations

from dataclasses import dataclass

from quick_model.engine.assumptions import AssumptionSpec, AssumptionValue

HIGH_GROWTH = 0.5  # |g| vượt ngưỡng này -> cảnh báo
VOLATILE_RATIO = 3.0  # max/min mẫu lịch sử vượt ngưỡng -> cảnh báo default kém tin cậy


@dataclass(frozen=True)
class Issue:
    severity: str  # error | warning | info
    code: str
    block: str | None
    target: str | None  # assumption_id hoặc line_id
    period: str | None
    message: str


def validate_inputs(resolved: dict[str, AssumptionValue], specs: dict[str, AssumptionSpec],
                    defaults: dict[str, AssumptionValue], periods: list[str]) -> list[Issue]:
    issues = []
    for aid, val in resolved.items():
        spec = specs[aid]
        if len(val.values) != len(periods):
            issues.append(Issue("error", "wrong_length", spec.block, aid, None,
                                f"{spec.label_vn}: cần {len(periods)} giá trị, nhận {len(val.values)}"))
            continue
        if val.source == "analyst" and spec.override_reason_required and not val.override_reason:
            issues.append(Issue("error", "override_reason_missing", spec.block, aid, None,
                                f"{spec.label_vn}: cần ghi lý do khi ghi đè"))
        for p, v in zip(periods, val.values):
            if v is None and spec.optional:
                continue  # tùy chọn: trống nghĩa là không dùng
            if v is None:
                reason = val.basis.get("note", "") if val.source == "needs_input" else ""
                issues.append(Issue("error", "needs_input", spec.block, aid, p,
                                    f"{spec.label_vn}: chưa có giá trị. {reason}".strip()))
            elif spec.out_of_range(v):
                # owner 2026-11-18: default (lịch sử/chính sách) ngoài miền chỉ cảnh báo để mô hình vẫn chạy được với mã
                # chưa cover; số analyst nhập ngoài miền vẫn chặn
                if val.source in ("analyst", "auto_balance"):
                    issues.append(Issue("error", "out_of_range", spec.block, aid, p,
                                        f"{spec.label_vn} = {v:g} ngoài miền hợp lệ {spec.valid_range}"))
                else:
                    issues.append(Issue("warning", "default_out_of_range", spec.block, aid, p,
                                        f"{spec.label_vn}: default = {v:g} ngoài miền hợp lệ {spec.valid_range}; mô hình "
                                        "vẫn chạy với giá trị này, cần xem lại"))
        issues += _review_warnings(spec, val, defaults.get(aid), periods)
    return issues


def _review_warnings(spec: AssumptionSpec, val: AssumptionValue, default: AssumptionValue | None,
                     periods: list[str]) -> list[Issue]:
    out = []
    ref = default.basis if default is not None else {}
    if val.source == "historical_default" and val.basis.get("weak_sample"):
        out.append(Issue("warning", "weak_sample", spec.block, spec.assumption_id, None,
                         f"{spec.label_vn}: default chỉ có {val.basis.get('count')} mẫu hợp lệ"))
    lo, hi = ref.get("min"), ref.get("max")
    if val.source == "historical_default" and lo and hi and lo > 0 and hi / lo > VOLATILE_RATIO:
        out.append(Issue("warning", "volatile_history", spec.block, spec.assumption_id, None,
                         f"{spec.label_vn}: lịch sử biến động mạnh ({lo:.4g}–{hi:.4g}); default "
                         + ("EMA" if val.basis.get("statistic") in ("ema", "ema_rolling", "ema_decay") else "trung vị")
                         + " có thể không đại diện, nên xem lại"))
    for p, v in zip(periods, val.values):
        if v is None:
            continue
        if lo is not None and hi is not None and not (lo <= v <= hi) and val.source != "historical_default":
            out.append(Issue("warning", "outside_history", spec.block, spec.assumption_id, p,
                             f"{spec.label_vn} = {v:.4g} ngoài khoảng lịch sử [{lo:.4g}, {hi:.4g}]"))
        if spec.assumption_id == "revenue_yoy_growth" and abs(v) > HIGH_GROWTH:
            out.append(Issue("warning", "high_growth", spec.block, spec.assumption_id, p,
                             f"Tăng trưởng doanh thu {v:.1%} vượt ±{HIGH_GROWTH:.0%}"))
    return out
