"""Chỉ số lịch sử nhóm ngân hàng: chuỗi theo kỳ dùng làm default giả định (EMA 5 năm…).

Mã nguồn đã đối chiếu với mapping_fiin_all.xlsx (nguồn *-hop-nhat-nhom-ngan-hang) qua sql/tcb/columns_manifest.csv.
Quy ước dấu như database: chi phí lãi (ISB26), chi phí hoạt động (ISB39), chi phí dự phòng (ISB41), thuế (ISA19) âm;
dự phòng cho vay (BSB105) âm; ở nhóm ngân hàng ISA21 (lợi ích CĐ không kiểm soát) mang dấu âm (ISA22 = ISA20 + ISA21).
Tỷ lệ theo năm trên số dư bình quân đầu–cuối kỳ được quy năm bằng 365 / số ngày của kỳ. Mẫu số ≤ 0 -> NaN.
"""

from __future__ import annotations

import pandas as pd

from quick_model.families.metrics_base import MetricsBase
from quick_model.periods import matching_prior

# Tài sản sinh lãi và nợ chịu lãi (mã BS). Cùng định nghĩa với engine (IEA_LINES / IBL_LINES).
IEA_CODES = ((1, "BSB97"), (1, "BSB98"), (1, "BSB99"), (1, "BSB104"), (1, "BSB261"), (1, "BSB106"))
IBL_CODES = ((1, "BSB111"), (1, "BSB112"), (1, "BSB113"), (1, "BSB115"), (1, "BSB116"))
OTHER_INCOME_CODES = ("ISB31", "ISB32", "ISB33", "ISB36", "ISB37")


class BankMetrics(MetricsBase):
    """Chuỗi chỉ số lịch sử ngân hàng đến kỳ gốc."""

    def annualise(self) -> pd.Series:
        return 365 / self.q["days"]

    def yoy(self, s: pd.Series) -> pd.Series:
        out = pd.Series(index=s.index, dtype="float64")
        for lab in s.index:
            prev = matching_prior(lab)
            if prev in s.index and pd.notna(s[lab]) and pd.notna(s[prev]) and s[prev] > 0:
                out[lab] = s[lab] / s[prev] - 1
        return out

    def _per(self) -> str:
        return "năm" if self.annual else "kỳ cùng quý năm trước"

    # ---- tăng trưởng bảng cân đối
    def m_loan_yoy(self):
        return self.yoy(self.c("BSB104")), f"BSB104 / BSB104[{self._per()}] − 1 (dư nợ gộp)"

    def m_deposit_yoy(self):
        return self.yoy(self.c("BSB113")), f"BSB113 / BSB113[{self._per()}] − 1"

    def funding(self) -> pd.Series:
        """Tổng huy động = tiền gửi KH + vốn tài trợ, ủy thác + GTCG phát hành (không gồm liên NH, vay NHNN)."""
        return self.c("BSB113") + self.c("BSB115").fillna(0) + self.c("BSB116").fillna(0)

    def m_ldr(self):
        return (self.c("BSB104") / self.pos(self.funding()),
                "BSB104 / (BSB113 + BSB115 + BSB116) (dư nợ gộp / tổng huy động: tiền gửi KH + vốn ủy thác + GTCG)")

    def m_valuable_papers_ratio(self):
        return self.c("BSB116") / self.pos(self.c("BSB113")), "BSB116 / BSB113"

    def m_sbv_reserve_ratio(self):
        return (self.c("BSB97") / self.pos(self.c("BSB113") + self.c("BSB116").fillna(0)),
                "BSB97 / (BSB113 + BSB116)")

    def m_cash_to_deposits(self):
        return self.c("BSA2") / self.pos(self.c("BSB113")), "BSA2 / BSB113"

    def m_inv_securities_yoy(self):
        return self.yoy(self.c("BSB106")), f"BSB106 / BSB106[{self._per()}] − 1 (CK đầu tư ròng)"

    def m_inv_securities_to_loans(self):
        return self.c("BSB106") / self.pos(self.c("BSB104")), "BSB106 / BSB104"

    def m_other_assets_to_loans(self):
        return self.c("BSB110") / self.pos(self.c("BSB104")), "BSB110 / BSB104"

    def m_other_liab_to_deposits(self):
        return self.c("BSB117") / self.pos(self.c("BSB113")), "BSB117 / BSB113"

    def m_interbank_assets_to_deposits(self):
        return self.c("BSB98") / self.pos(self.c("BSB113")), "BSB98 / BSB113"

    def m_interbank_liab_to_assets(self):
        return self.c("BSB112") / self.pos(self.c("BSA53")), "BSB112 / BSA53"

    def m_equity_to_assets(self):
        return self.c("BSA78") / self.pos(self.c("BSA53")), "BSA78 / BSA53 (gồm lợi ích CĐ không kiểm soát)"

    # ---- chất lượng tài sản
    def m_credit_cost(self):
        return (-self.c("ISB41") / self.pos(self.c("BSB104")) * self.annualise(),
                "−ISB41 / BSB104 cuối kỳ × 365 / số ngày")

    def m_llr_coverage(self):
        return -self.c("BSB105") / self.pos(self.c("BSB104")), "−BSB105 / BSB104"

    # ---- thu nhập lãi
    def m_yield_iea(self):
        return (self.c("ISB25") / self.pos(self.avg_of(*IEA_CODES)) * self.annualise(),
                "ISB25 / BQ(BSB97 + BSB98 + BSB99 + BSB104 + BSB261 + BSB106) × 365 / số ngày")

    def m_cost_of_funds(self):
        return (-self.c("ISB26") / self.pos(self.avg_of(*IBL_CODES)) * self.annualise(),
                "−ISB26 / BQ(BSB111 + BSB112 + BSB113 + BSB115 + BSB116) × 365 / số ngày")

    def m_nim(self):
        return (self.c("ISB27") / self.pos(self.avg_of(*IEA_CODES)) * self.annualise(),
                "ISB27 / BQ(tài sản sinh lãi) × 365 / số ngày")

    # ---- ngoài lãi, chi phí
    def m_fee_to_loans(self):
        return (self.c("ISB30") / self.pos(self.avg_of((1, "BSB104"))) * self.annualise(),
                "ISB30 / BQ(BSB104) × 365 / số ngày")

    def other_income(self) -> pd.Series:
        parts = pd.concat([self.c(c) for c in OTHER_INCOME_CODES], axis=1)
        return parts.sum(axis=1, min_count=1)

    def m_other_income_amount(self):
        return self.other_income(), "ISB31 + ISB32 + ISB33 + ISB36 + ISB37"

    def m_other_income_run_rate(self):
        return (self.other_income() * self.annualise(),
                "(ISB31 + ISB32 + ISB33 + ISB36 + ISB37) × 365 / số ngày của kỳ (mức theo năm)")

    def m_other_income_yoy(self):
        return (self.yoy(self.other_income()),
                f"(ISB31 + ISB32 + ISB33 + ISB36 + ISB37) / cùng tổng [{self._per()}] − 1; gốc ≤ 0 bị loại")

    def m_cir(self):
        return -self.c("ISB39") / self.pos(self.c("ISB38")), "−ISB39 / ISB38"

    def m_toi_amount(self):
        return self.c("ISB38"), "ISB38"

    # ---- thuế, cổ đông không kiểm soát
    def m_effective_tax_rate(self):
        return -self.c("ISA19") / self.pos(self.c("ISA16")), "−ISA19 / ISA16, khi ISA16 > 0"

    def m_minority_share(self):
        return -self.c("ISA21") / self.pos(self.c("ISA20")), "−ISA21 / ISA20, khi ISA20 > 0 (ISA21 âm ở nhóm ngân hàng)"
