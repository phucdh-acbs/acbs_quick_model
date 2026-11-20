"""Lớp cơ sở cho chỉ số lịch sử của mọi nhóm BCTC: khung dữ liệu theo kỳ (IS/CF/BS + số dư đầu kỳ) và truy cập chỉ số.

Mỗi nhóm (phi tài chính, ngân hàng…) kế thừa và khai báo chỉ số bằng phương thức `m_<tên>` trả về (chuỗi theo kỳ,
công thức). Nhóm không có LCTT (ngân hàng) vẫn dùng được: cột CF đơn giản là không có.
"""

from __future__ import annotations

import pandas as pd

from quick_model.data.history import History
from quick_model.periods import ANNUAL_DAY_COUNT, FREQ_ANNUAL, shift_label
from quick_model.periods import shift_quarter_label


def quarterly_frame(h: History, origin: str | None = None) -> pd.DataFrame:
    """IS/CF quý, số dư BS cuối quý và số dư đầu kỳ (cuối quý trước) theo nhãn quý."""
    is_q, cf_q, bal = h.flows("IS"), h.flows("CF"), h.balances()
    days = h.quarter_periods().set_index("period_label")["period_days"]
    df = pd.concat([is_q, cf_q, bal], axis=1)
    df["days"] = days
    opening = bal.shift(1).add_prefix("open_")
    labels = list(bal.index)
    contiguous = [i > 0 and shift_quarter_label(labels[i - 1], 1) == labels[i] for i in range(len(labels))]
    opening[~pd.Series(contiguous, index=bal.index)] = float("nan")
    df = pd.concat([df, opening], axis=1)
    if origin is not None:
        if origin not in df.index:
            raise ValueError(f"Kỳ gốc {origin} không có trong lịch sử")
        df = df.loc[:origin]
    return df


def annual_frame(h: History, origin: str | None = None) -> pd.DataFrame:
    """IS/CF cả năm (FY), số dư 31/12 và số dư đầu năm theo nhãn 'YYYY'; kỳ năm tính ANNUAL_DAY_COUNT ngày."""
    is_y, cf_y, bal = h.flows_by("IS", FREQ_ANNUAL), h.flows_by("CF", FREQ_ANNUAL), h.balances_by(FREQ_ANNUAL)
    df = pd.concat([is_y, cf_y], axis=1)
    df = pd.concat([df, bal.reindex(df.index)], axis=1)
    df["days"] = float(ANNUAL_DAY_COUNT)
    opening = bal.reindex([shift_label(y, -1) for y in df.index])
    opening.index = df.index
    df = pd.concat([df, opening.add_prefix("open_")], axis=1)
    if origin is not None:
        if origin not in df.index:
            raise ValueError(f"Năm gốc {origin} không có báo cáo cả năm trong lịch sử")
        df = df.loc[:origin]
    return df


class MetricsBase:
    """Chuỗi chỉ số lịch sử đến kỳ gốc. `series(name)` -> (chuỗi theo kỳ, công thức)."""

    def __init__(self, h: History, origin: str, annual: bool, adjustments: dict | None = None):
        """adjustments: {(mã IS, kỳ): số cần trừ} — khoản bất thường analyst đã xác nhận (quick_model/oneoff.py)."""
        self.annual = annual
        self.q = annual_frame(h, origin) if annual else quarterly_frame(h, origin)
        self.adjusted: dict[tuple[str, str], float] = {}
        if adjustments:
            self.q = self.q.copy()
            for (code, period), k in adjustments.items():
                if code in self.q.columns and period in self.q.index and pd.notna(self.q.loc[period, code]):
                    self.q.loc[period, code] -= k
                    self.adjusted[(code, period)] = k
        self._cache: dict[str, tuple[pd.Series, str]] = {}

    def c(self, code: str) -> pd.Series:
        q = self.q
        return q[code] if code in q.columns else pd.Series(float("nan"), index=q.index)

    @staticmethod
    def pos(s: pd.Series) -> pd.Series:
        return s.where(s > 0)

    def names(self) -> list[str]:
        return [n[2:] for n in dir(self) if n.startswith("m_")]

    def series(self, name: str) -> tuple[pd.Series, str]:
        if name not in self._cache:
            fn = getattr(self, f"m_{name}", None)
            if fn is None:
                raise KeyError(f"Chỉ số lịch sử không có: {name}")
            self._cache[name] = fn()
        return self._cache[name]

    def pair(self, name: str) -> tuple[pd.Series, pd.Series] | None:
        """(tử số, mẫu số) của chỉ số dạng tỷ lệ, nếu family khai báo `p_<name>` (dùng cho EMA quý có trọng số:
        EMA tử số / EMA mẫu số); mẫu số ≤ 0 là NaN. None: chỉ số không khai báo cặp."""
        fn = getattr(self, f"p_{name}", None)
        return None if fn is None else fn()

    def avg_of(self, *terms: tuple[int, str]) -> pd.Series:
        """Bình quân (đầu kỳ + cuối kỳ) / 2 của tổng có dấu các mã BS; thiếu đầu kỳ -> NaN."""
        close = sum(sg * self.c(c).fillna(0) for sg, c in terms)
        opening = sum(sg * self.c("open_" + c).fillna(0) for sg, c in terms)
        has_open = pd.concat([self.c("open_" + c).notna() for _, c in terms], axis=1).any(axis=1)
        return ((close + opening) / 2).where(has_open)

    def total(self, *terms: tuple[int, str]) -> pd.Series:
        return sum(sg * self.c(c).fillna(0) for sg, c in terms)
