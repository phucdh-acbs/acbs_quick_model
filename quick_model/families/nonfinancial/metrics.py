"""Chỉ số lịch sử của nhóm phi tài chính: chuỗi theo kỳ dùng làm default giả định.

Template khai báo default của từng giả định bằng tên chỉ số (metric) và cách thống kê (stat); lớp này tính
chuỗi lịch sử của chỉ số theo đúng tần suất (năm: báo cáo cả năm; quý: quý riêng lẻ). Mẫu số ≤ 0 hoặc ô trống
cho mẫu không hợp lệ (NaN), không thay bằng 0.
"""

from __future__ import annotations

import pandas as pd

from quick_model.data.history import History  # noqa: F401
from quick_model.families.metrics_base import MetricsBase, annual_frame, quarterly_frame  # noqa: F401
from quick_model.periods import matching_prior


class NonFinancialMetrics(MetricsBase):
    """Chỉ số lịch sử nhóm phi tài chính (phần dùng chung ở MetricsBase)."""

    # ---- doanh thu, biên, chi phí
    def m_revenue_yoy(self):
        rev = self.c("ISA3")
        yoy = pd.Series(index=rev.index, dtype="float64")
        for lab in rev.index:
            prev = matching_prior(lab)
            if prev in rev.index and rev[lab] > 0 and rev[prev] > 0:
                yoy[lab] = rev[lab] / rev[prev] - 1
        return yoy, ("ISA3[năm] / ISA3[năm trước] − 1 (số cả năm)" if self.annual else "ISA3[t] / ISA3[t−4] − 1")

    def m_gross_margin(self):
        return self.c("ISA5") / self.pos(self.c("ISA3")), "ISA5 / ISA3"

    def m_selling_expense_ratio(self):
        return -self.c("ISA9") / self.pos(self.c("ISA3")), "−ISA9 / ISA3"

    def m_admin_expense_ratio(self):
        return -self.c("ISA10") / self.pos(self.c("ISA3")), "−ISA10 / ISA3"

    # số tuyệt đối cho hồi quy cố định + biến đổi (default CPBH/CPQL L1)
    def m_revenue_amount(self):
        return self.pos(self.c("ISA3")), "ISA3"

    def m_selling_expense_amount(self):
        return -self.c("ISA9"), "−ISA9"

    def m_admin_expense_amount(self):
        return -self.c("ISA10"), "−ISA10"

    def m_other_financial_expense_ratio(self):
        return -(self.c("ISA7") - self.c("ISA8")) / self.pos(self.c("ISA3")), "−(ISA7 − ISA8) / ISA3"

    def m_effective_tax_rate(self):
        return -self.c("ISA19") / self.pos(self.c("ISA16")), "−ISA19 / ISA16, khi ISA16 > 0"

    def m_minority_share(self):
        return self.c("ISA21") / self.pos(self.c("ISA20")), "ISA21 / ISA20, khi ISA20 > 0"

    def p_minority_profit(self):  # L1 v2.6.3: cặp (CĐKKS, LNST) cho tỷ lệ CĐKKS quý = tổng 4 quý / tổng 4 quý
        return self.c("ISA21"), self.c("ISA20")

    # ---- vốn lưu động
    def _cogs(self):
        return -self.c("ISA4")

    def _purchases(self):
        return self._cogs() + (self.c("BSA15") - self.c("open_BSA15")) - self.c("CFA2")

    def m_dso_days(self):
        return self.c("BSA9") / self.pos(self.c("ISA3")) * self.q["days"], "BSA9 / ISA3 × số ngày của kỳ"

    def m_dio_days(self):
        return self.c("BSA15") / self.pos(self._cogs()) * self.q["days"], "BSA15 / (−ISA4) × số ngày của kỳ"

    def m_dpo_days(self):
        return self.c("BSA57") / self.pos(self._cogs()) * self.q["days"], "BSA57 / (−ISA4) × số ngày của kỳ"

    def m_dpo_purchases_days(self):
        return (self.c("BSA57") / self.pos(self._purchases()) * self.q["days"],
                "BSA57 / mua hàng × số ngày; mua hàng = −ISA4 + ΔBSA15 − CFA2")

    def m_prepayment_days(self):
        return self.c("BSA10") / self.pos(self._purchases()) * self.q["days"], "BSA10 / mua hàng × số ngày"

    def m_customer_advance_days(self):
        return self.c("BSA58") / self.pos(self.c("ISA3")) * self.q["days"], "BSA58 / ISA3 × số ngày"

    # ---- TSCĐ
    def m_capex_to_revenue(self):
        return -self.c("CFA19") / self.pos(self.c("ISA3")), "−CFA19 / ISA3"

    def m_depreciation_rate(self):
        base = self.pos(self.c("open_BSA29") + self.c("open_BSA163"))
        return (self.c("CFA2") / base * 365 / self.q["days"],
                "CFA2 / (BSA29 + BSA163)[đầu kỳ] × 365 / số ngày")

    def m_capex_amount(self):
        capex = -self.c("CFA19")
        return capex.where(capex >= 0), "−CFA19"

    def _gross(self):
        gross = self.c("BSA31").fillna(0) + self.c("BSA34").fillna(0) + self.c("BSA37").fillna(0)
        return gross.where(self.c("BSA31").notna())

    def m_cip_transfer_rate(self):
        gross = self._gross()
        open_gross = gross.shift(1).where(self.c("open_BSA31").notna())
        transfer = gross - open_gross
        capex = -self.c("CFA19")
        rate = transfer / self.pos(self.c("open_BSA163") + capex)
        return (rate.where((transfer >= 0) & (rate <= 1)),
                "Δ nguyên giá (BSA31+BSA34+BSA37) / (BSA163 đầu kỳ + capex); bỏ kỳ âm hoặc > 1")

    def m_useful_life_years(self):
        open_gross = self._gross().shift(1).where(self.c("open_BSA31").notna())
        return (open_gross / self.pos(self.c("CFA2") * 365 / self.q["days"]),
                "Nguyên giá đầu kỳ / (CFA2 × 365 / số ngày)")

    # ---- nợ vay, tiền, chi phí vốn
    def m_interest_rate(self):
        return (-self.c("ISA8") / self.pos(self.c("open_BSA56") + self.c("open_BSA71")) * 365 / self.q["days"],
                "−ISA8 / (BSA56 + BSA71)[đầu kỳ] × 365 / số ngày")

    def m_interest_rate_avg(self):  # L1 v2.6.2: lãi suất trên dư nợ bình quân (đầu + cuối kỳ) / 2
        avg = (self.c("open_BSA56") + self.c("open_BSA71") + self.c("BSA56") + self.c("BSA71")) / 2
        return (-self.c("ISA8") / self.pos(avg) * 365 / self.q["days"],
                "−ISA8 / [(BSA56 + BSA71) đầu kỳ + cuối kỳ] / 2 × 365 / số ngày")

    def m_financial_income_run_rate(self):
        return self.c("ISA6") * 365 / self.q["days"], "ISA6 × 365 / số ngày của kỳ (mức theo năm)"

    def m_jv_associates_run_rate(self):  # v2.6: LDLK theo mức
        return self.c("ISA102") * 365 / self.q["days"], "ISA102 × 365 / số ngày của kỳ (mức theo năm)"

    # ---- cặp tử số / mẫu số cho EMA quý có trọng số (v2.5): tỷ lệ trên doanh thu hoặc giá vốn
    def _per_day(self, s: pd.Series) -> pd.Series:
        return self.pos(s) / self.q["days"]

    def p_gross_margin(self):
        return self.c("ISA5"), self.pos(self.c("ISA3"))

    def p_selling_expense_ratio(self):
        return -self.c("ISA9"), self.pos(self.c("ISA3"))

    def p_admin_expense_ratio(self):
        return -self.c("ISA10"), self.pos(self.c("ISA3"))

    def p_other_financial_expense_ratio(self):
        return -(self.c("ISA7") - self.c("ISA8")), self.pos(self.c("ISA3"))

    def p_dso_days(self):
        return self.c("BSA9"), self._per_day(self.c("ISA3"))

    def p_dio_days(self):
        return self.c("BSA15"), self._per_day(self._cogs())

    def p_dpo_days(self):
        return self.c("BSA57"), self._per_day(self._cogs())

    def m_financial_income_yield(self):
        return (self.c("ISA6") / self.pos(self.c("open_BSA2") + self.c("open_BSA5")) * 365 / self.q["days"],
                "ISA6 / (BSA2 + BSA5)[đầu kỳ] × 365 / số ngày")

    def m_cash_to_revenue(self):
        return self.c("BSA2") / self.pos(self.c("ISA3")), "BSA2 cuối kỳ / ISA3 của kỳ"

    def m_st_debt_share(self):
        total = self.c("BSA56").fillna(0) + self.c("BSA71").fillna(0)
        return self.c("BSA56") / self.pos(total), "BSA56 / (BSA56 + BSA71)"

    def m_debt_to_assets(self):
        total = self.c("BSA56").fillna(0) + self.c("BSA71").fillna(0)
        return total / self.pos(self.c("BSA53")), "(BSA56 + BSA71) / BSA53"
