"""Kỳ báo cáo FiinX và cơ sở kỳ (period basis).

LENGTH_REPORT (đã xác nhận với chủ dự án và trên dữ liệu HPG):
  1..4 -> quý riêng lẻ (standalone), 5 -> cả năm, 6 -> lũy kế 6 tháng, 9 -> lũy kế 9 tháng.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum


class Basis(str, Enum):
    QUARTER = "Q"
    FULL_YEAR = "FY"
    YTD_6M = "YTD6"
    YTD_9M = "YTD9"


LENGTH_TO_BASIS = {
    1: Basis.QUARTER,
    2: Basis.QUARTER,
    3: Basis.QUARTER,
    4: Basis.QUARTER,
    5: Basis.FULL_YEAR,
    6: Basis.YTD_6M,
    9: Basis.YTD_9M,
}

# Số tháng kỳ vọng của từng LENGTH_REPORT, dùng để đối chiếu với LENGTH_SERIES
EXPECTED_MONTHS = {1: 3, 2: 3, 3: 3, 4: 3, 5: 12, 6: 6, 9: 9}

# Các quý cấu thành mỗi kỳ lũy kế, dùng cho đối chiếu tổng quý
YTD_COMPONENTS = {
    5: (1, 2, 3, 4),
    6: (1, 2),
    9: (1, 2, 3),
}


class PeriodError(ValueError):
    pass


def basis_of(length_report: int) -> Basis:
    try:
        return LENGTH_TO_BASIS[int(length_report)]
    except (KeyError, ValueError, TypeError):
        raise PeriodError(f"LENGTH_REPORT không hợp lệ: {length_report!r}") from None


def shift_quarter_label(label: str, quarters: int) -> str:
    """'2026Q2' dịch +1 -> '2026Q3'; dịch −4 -> '2025Q2'."""
    y, q = int(label[:4]), int(label[-1])
    if label[4] != "Q" or not 1 <= q <= 4:
        raise PeriodError(f"Nhãn quý không hợp lệ: {label!r}")
    idx = y * 4 + (q - 1) + quarters
    return f"{idx // 4}Q{idx % 4 + 1}"


def quarter_bounds(label: str) -> tuple[date, date, int]:
    """(ngày đầu, ngày cuối, số ngày) của quý dương lịch (năm tài chính = năm dương lịch)."""
    shift_quarter_label(label, 0)
    y, q = int(label[:4]), int(label[-1])
    start = date(y, 3 * q - 2, 1)
    end = date(y + 1, 1, 1) if q == 4 else date(y, 3 * q + 1, 1)
    end = end - timedelta(days=1)
    return start, end, (end - start).days + 1


def period_label(fiscal_year: int, length_report: int) -> str:
    basis = basis_of(length_report)
    if basis is Basis.QUARTER:
        return f"{fiscal_year}Q{length_report}"
    return {
        Basis.FULL_YEAR: f"FY{fiscal_year}",
        Basis.YTD_6M: f"6M{fiscal_year}",
        Basis.YTD_9M: f"9M{fiscal_year}",
    }[basis]


# ---------------------------------------------------------------- tần suất dự phóng (năm / quý)
# Nhãn kỳ: quý "YYYYQn", năm "YYYY" (năm tài chính = năm dương lịch, đã xác nhận trên HPG).
# Engine năm và engine quý dùng chung assumption (tỷ lệ theo năm); chỉ khác lịch kỳ dưới đây.

FREQ_QUARTER = "Q"
FREQ_ANNUAL = "Y"
FREQUENCIES = (FREQ_ANNUAL, FREQ_QUARTER)
FREQUENCY_LABELS = {FREQ_ANNUAL: "Năm", FREQ_QUARTER: "Quý"}
# Quy ước số ngày của kỳ năm cho số ngày vốn lưu động, lãi suất, khấu hao: 365 ngày (kể cả năm nhuận),
# để "số ngày phải thu" và "lãi suất × dư nợ" theo năm khớp cách analyst tính trong mô hình năm.
ANNUAL_DAY_COUNT = 365


def freq_of(label: str) -> str:
    s = str(label)
    if len(s) == 6 and s[:4].isdigit() and s[4] == "Q" and s[5] in "1234":
        return FREQ_QUARTER
    if len(s) == 4 and s.isdigit():
        return FREQ_ANNUAL
    raise PeriodError(f"Nhãn kỳ không hợp lệ: {label!r} (cần 'YYYY' hoặc 'YYYYQn')")


def shift_label(label: str, n: int) -> str:
    """Dịch n kỳ cùng tần suất: '2025' +1 -> '2026'; '2026Q2' +1 -> '2026Q3'."""
    if freq_of(label) == FREQ_QUARTER:
        return shift_quarter_label(label, n)
    return str(int(label) + n)


def matching_prior(label: str) -> str:
    """Kỳ tương ứng năm trước: năm trước (năm) hoặc cùng quý năm trước (quý)."""
    return shift_label(label, -4 if freq_of(label) == FREQ_QUARTER else -1)


def periods_per_year(freq: str) -> int:
    return 4 if freq == FREQ_QUARTER else 1


def period_days(label: str) -> int:
    if freq_of(label) == FREQ_QUARTER:
        return quarter_bounds(label)[2]
    return ANNUAL_DAY_COUNT


def fiscal_year(label: str) -> int:
    freq_of(label)
    return int(str(label)[:4])


def is_year_end(label: str) -> bool:
    """Kỳ kết thúc năm tài chính: kỳ năm, hoặc quý 4. Ràng buộc cuối năm (trần LDR, trần nợ/tài sản, tiền tối thiểu,
    sàn/trần liên ngân hàng) chỉ kiểm ở kỳ này (owner 2026-11-18)."""
    return freq_of(label) == FREQ_ANNUAL or str(label).endswith("Q4")


def year_quarters(year: int | str) -> list[str]:
    return [f"{int(year)}Q{q}" for q in range(1, 5)]


def year_days(year: int | str) -> int:
    """Số ngày thực tế của năm dương lịch (tổng số ngày 4 quý)."""
    return sum(quarter_bounds(q)[2] for q in year_quarters(year))
