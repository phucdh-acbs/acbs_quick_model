"""Lớp cơ sở cho method: mỗi kind trong thư viện là một class, đăng ký theo kind.

Engine của nhóm BCTC gọi method theo kind của method đang chọn trong template; method chỉ đọc/ghi qua
PeriodContext (giả định của kỳ, giá trị đang tính, số dư đầu/cuối kỳ, bộ nhớ qua các kỳ). Thêm cách tính
mới = viết một class con + khai báo kind trong config/methods/library.json, không sửa engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from quick_model.engine.validation import Issue


class CalcError(Exception):
    """Đại lượng tính ra không hợp lệ ở một kỳ: dừng tính toán và báo lỗi."""

    def __init__(self, issue: Issue):
        super().__init__(issue.message)
        self.issue = issue


@dataclass(frozen=True)
class Driver:
    """Một yếu tố của công thức một dòng dự phóng (để giải thích nguyên nhân thay đổi).

    hist: cách lấy cùng yếu tố ở một kỳ THỰC TẾ để so sánh:
      ("metric", tên chỉ số lịch sử) · ("open", ((dấu, mã), ...)) số dư đầu kỳ · ("close", ...) số dư cuối kỳ
      ("line", line_id) giá trị thực tế của dòng · ("line_mp", line_id) dòng đó ở kỳ tương ứng năm trước
      ("days",) số ngày của kỳ · ("const", x) hằng số · ("one_off", line_id) khoản bất thường đã xác nhận
      · ("residual_ratio", line_id, cố định) tỷ lệ biến đổi ngụ ý · None: không có số thực tế tương đương.
    Kiểu mới (ví dụ KPI vận hành analyst nhập cho L2/L3) đăng ký bằng quick_model.explain.register_hist, không sửa
    Explainer. drill: dòng mô hình mà yếu tố này đại diện (ví dụ doanh thu một phân khúc ở L3) để bấm xem tiếp.
    """

    key: str
    label: str
    unit: str
    value: float | None
    hist: tuple | None = None
    assumption_id: str | None = None  # giả định nguồn (để chỉ ra cách lấy default / analyst nhập)
    drill: str | None = None  # line_id để xem tiếp nguyên nhân (dòng thành phần, phân khúc)


@dataclass
class Trace:
    """Dấu vết tính một dòng ở một kỳ: công thức đọc được + hàm tính lại từ các yếu tố."""

    line: str
    formula: str
    drivers: tuple[Driver, ...]
    fn: object  # callable(dict key->giá trị) -> giá trị dòng; không lưu ra file


@dataclass
class PeriodContext:
    period: str
    t: int  # thứ tự kỳ dự phóng (0 = kỳ đầu)
    days: int  # số ngày của kỳ (quý: thực tế; năm: quy ước 365)
    at: dict  # giá trị giả định của kỳ
    v: dict  # dòng IS/CF/SCH/OPS đang tính
    bal: dict  # số dư BS đầu kỳ
    new: dict  # số dư BS cuối kỳ
    state: dict  # bộ nhớ qua các kỳ (nguyên giá, số cửa hàng, backlog, vay tự động…)
    revenue_by_period: dict  # doanh thu thực tế + dự phóng theo nhãn kỳ (gốc tăng trưởng)
    extra: dict = field(default_factory=dict)
    traces: dict = field(default_factory=dict)  # line_id -> Trace của kỳ này

    def fail(self, code: str, block: str | None, target: str | None, message: str) -> None:
        raise CalcError(Issue("error", code, block, target, self.period, message))

    def trace(self, line: str, formula: str, fn, *drivers: Driver) -> None:
        self.traces[line] = Trace(line, formula, tuple(drivers), fn)

    def a(self, aid: str, label: str, unit: str, hist: tuple | None = None, key: str | None = None) -> Driver:
        """Driver lấy từ giả định của kỳ."""
        return Driver(key or aid, label, unit, self.at.get(aid), hist, aid)

    def days_driver(self) -> Driver:
        return Driver("days", "Số ngày của kỳ", "days", float(self.days), ("days",))


class Method(ABC):
    """Một cách tính của một block. Thuộc tính lớp: kind (khớp thư viện), block (block áp dụng)."""

    kind: ClassVar[str]
    block: ClassVar[str]

    def __init__(self, spec):
        self.spec = spec  # MethodSpec của template (nhãn, level, line_formulas…)

    @abstractmethod
    def compute(self, ctx: PeriodContext) -> None:
        ...


_REGISTRY: dict[str, type[Method]] = {}


def register(cls: type[Method]) -> type[Method]:
    if cls.kind in _REGISTRY and _REGISTRY[cls.kind] is not cls:
        raise ValueError(f"Kind {cls.kind} đã có class {_REGISTRY[cls.kind].__name__}")
    _REGISTRY[cls.kind] = cls
    return cls


def method_class(kind: str) -> type[Method]:
    if kind not in _REGISTRY:
        raise KeyError(f"Chưa có class cho kind {kind!r}")
    return _REGISTRY[kind]


def registered_kinds() -> dict[str, type[Method]]:
    return dict(_REGISTRY)
