"""Hợp đồng assumption: đặc tả (spec), giá trị (value) và kịch bản (scenario).

Assumption là dữ liệu có cấu trúc, không phải code. Mỗi giá trị ghi rõ nguồn:
- analyst: analyst nhập/ghi đè;
- historical_default: default tính từ lịch sử (kèm cửa sổ, số mẫu, công thức);
- explicit_policy: chính sách khai báo rõ (ví dụ: không vay thêm, cổ tức tiền = 0);
- needs_input: không có default đáng tin cậy, cần analyst nhập;
- auto_balance: cân nguồn tự động L1 điều chỉnh (chỉ thay default, không bao giờ thay số analyst nhập),
  kèm lý do và bước cân trong basis.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

SOURCES = ("analyst", "historical_default", "explicit_policy", "needs_input", "auto_balance")
OVERRIDE_SOURCES = ("analyst", "auto_balance")


@dataclass(frozen=True)
class AssumptionSpec:
    assumption_id: str
    block: str
    method_id: str
    label_vn: str
    description_vn: str
    unit: str  # ratio | ratio_annual | days | VND | tonnes | VND_per_tonne | years
    valid_range: tuple[float | None, float | None]  # (min, max), None = không giới hạn
    default_policy: str  # mô tả cách lấy default
    per_period: bool = True
    editable: bool = True
    override_reason_required: bool = False
    optional: bool = False  # được để trống; trống = không dùng (không phải 0)
    shared: bool = False  # dùng chung mọi method của block (method_id dạng "<block>.*")

    def out_of_range(self, v: float) -> bool:
        lo, hi = self.valid_range
        return (lo is not None and v < lo) or (hi is not None and v > hi)


@dataclass(frozen=True)
class AssumptionValue:
    assumption_id: str
    values: tuple[float | None, ...]  # mỗi kỳ dự phóng một giá trị; None = trống
    source: str
    basis: dict = field(default_factory=dict)  # window, count, statistic, sample, formula, note
    override_reason: str | None = None
    updated_at: str | None = None  # ISO timestamp do lớp UI/lưu trữ gán; engine không đọc đồng hồ

    def __post_init__(self):
        if self.source not in SOURCES:
            raise ValueError(f"source không hợp lệ: {self.source!r}")
        for v in self.values:
            if v is not None and (not isinstance(v, (int, float)) or math.isnan(v)):
                raise ValueError(f"{self.assumption_id}: giá trị phải là số hoặc None, nhận {v!r}")

    @property
    def missing(self) -> bool:
        return self.source == "needs_input" or any(v is None for v in self.values)

    @property
    def filled(self) -> bool:
        """Có ít nhất một kỳ được điền (dùng cho assumption tùy chọn)."""
        return any(v is not None for v in self.values)


@dataclass(frozen=True)
class Scenario:
    company: str
    scenario_id: str
    origin: str  # kỳ thực tế cuối cùng: "2025" (chạy theo năm) hoặc "2026Q2" (chạy theo quý)
    horizon: int  # số kỳ dự phóng (năm hoặc quý theo tần suất)
    methods: dict[str, str]  # block -> method_id đang chọn
    overrides: dict[str, AssumptionValue] = field(default_factory=dict)  # giữ cả của method không active
    template_id: str = "manufacturing"  # template ngành (kế thừa template của nhóm BCTC)
    one_offs: tuple = ()  # quick_model.oneoff.OneOff: khoản bất thường analyst đã xác nhận/bỏ qua (kèm bằng chứng)

    @property
    def frequency(self) -> str:
        """'Y' hoặc 'Q', suy từ nhãn kỳ gốc (một nguồn duy nhất, không lưu trùng)."""
        from quick_model.periods import freq_of
        return freq_of(self.origin)

    def with_override(self, value: AssumptionValue) -> "Scenario":
        if value.source not in OVERRIDE_SOURCES:
            raise ValueError(f"Override phải có source thuộc {OVERRIDE_SOURCES}")
        if len(value.values) != self.horizon:
            raise ValueError(f"{value.assumption_id}: cần {self.horizon} giá trị, nhận {len(value.values)}")
        return replace(self, overrides={**self.overrides, value.assumption_id: value})

    def without_auto_balance(self) -> "Scenario":
        """Bỏ mọi điều chỉnh của cân nguồn tự động (chạy lại cân nguồn từ default)."""
        return replace(self, overrides={k: v for k, v in self.overrides.items() if v.source != "auto_balance"})

    def with_one_off(self, decision) -> "Scenario":
        """Ghi quyết định (xác nhận/bỏ qua) cho một khoản bất thường; thay quyết định cũ của cùng dòng và quý."""
        rest = tuple(o for o in self.one_offs if (o.line, o.quarter) != (decision.line, decision.quarter))
        return replace(self, one_offs=rest + (decision,))

    def without_one_off(self, line: str, quarter: str) -> "Scenario":
        return replace(self, one_offs=tuple(o for o in self.one_offs if (o.line, o.quarter) != (line, quarter)))

    def reset_assumption(self, assumption_id: str) -> "Scenario":
        return replace(self, overrides={k: v for k, v in self.overrides.items() if k != assumption_id})

    def reset_block(self, block: str, specs: dict[str, AssumptionSpec]) -> "Scenario":
        keep = {k: v for k, v in self.overrides.items() if specs.get(k) is None or specs[k].block != block}
        return replace(self, overrides=keep)

    def select_method(self, block: str, method_id: str) -> "Scenario":
        return replace(self, methods={**self.methods, block: method_id})


def is_active(spec: AssumptionSpec, scenario: Scenario) -> bool:
    if spec.shared:
        return spec.block in scenario.methods
    return spec.method_id in set(scenario.methods.values())


def resolve(scenario: Scenario, specs: dict[str, AssumptionSpec],
            defaults: dict[str, AssumptionValue]) -> dict[str, AssumptionValue]:
    """Giá trị hiệu lực cho các method đang active: override của analyst > default."""
    out = {}
    for aid, spec in specs.items():
        if not is_active(spec, scenario):
            continue  # assumption của method không active được giữ nhưng không ảnh hưởng tính toán
        if aid in scenario.overrides:
            out[aid] = scenario.overrides[aid]
        elif aid in defaults:
            out[aid] = defaults[aid]
        else:
            out[aid] = AssumptionValue(aid, (None,) * scenario.horizon, "needs_input",
                                       {"note": "Không có default"})
    return out
