"""Hàm thuần dùng chung cho mọi kiểu method (kind) và mọi ngành.

Không phụ thuộc tần suất: kỳ được mô tả bằng số ngày của kỳ (`period_days`); lãi suất, tỷ lệ khấu
hao là số năm. Không tự đổi dấu: hàm trả về độ lớn, engine quyết định dấu theo quy ước database.
"""

from __future__ import annotations

YEAR_DAYS = 365


def growth(base: float, g: float) -> float:
    return base * (1 + g)


def ratio_of(driver: float, ratio: float) -> float:
    return ratio * driver


def margin_cost(revenue: float, margin: float) -> float:
    """Giá vốn (số âm) từ biên gộp: −(1 − biên) × doanh thu."""
    return -(1 - margin) * revenue


def fixed_plus_variable(variable_rate: float, driver: float, fixed: float) -> float:
    return variable_rate * driver + fixed


def volume_price(volume: float, price: float) -> float:
    return volume * price


def days_balance(flow: float, days: float, period_days: float) -> float:
    """Số dư cuối kỳ = dòng tiền của kỳ × số ngày / số ngày của kỳ."""
    return flow * days / period_days


def rate_on_balance(balance: float, annual_rate: float, period_days: float, year_days: float = YEAR_DAYS) -> float:
    return annual_rate * balance * period_days / year_days


def straight_line_depreciation(gross_cost: float, useful_life_years: float, period_days: float,
                               year_days: float = YEAR_DAYS) -> float:
    return gross_cost / useful_life_years * period_days / year_days


def rollforward(opening: float, additions: float = 0.0, reductions: float = 0.0) -> float:
    return opening + additions - reductions


def average_balance(opening: float, closing: float) -> float:
    return (opening + closing) / 2


def schedule_sum(items) -> float:
    return float(sum(items))
