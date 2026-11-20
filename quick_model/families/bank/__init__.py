"""Nhóm ngân hàng: BCTC riêng (cho vay, huy động, thu nhập lãi), cân BS bằng IS, không có LCTT.

Kế thừa khung chung: StatementFamily (dòng, bs_lines, line_meta), PeriodEngine (số dư kỳ gốc, khoản bất thường, ghi
chú chạy theo năm), MetricsBase (khung dữ liệu theo kỳ), đường default (EMA), giải thích ô, dải kịch bản.
"""

from __future__ import annotations

import pandas as pd

from quick_model.families.bank import methods as _methods  # noqa: F401  (đăng ký class theo kind)
from quick_model.families.bank.engine import SUBTOTALS, BankEngine
from quick_model.families.bank.metrics import BankMetrics
from quick_model.families.bank.ratios import RATIO_INPUTS, ratio_table
from quick_model.families.base import StatementFamily
from quick_model.methods.base import registered_kinds


# Quy tắc kiểm tra nhóm ngân hàng (mã, mức, điều kiện, hệ quả); kiểm tra nằm ở BankEngine.output_checks.
BANK_RULES = (
    ("unbalanced", "error", "Tổng tài sản khác tổng nguồn vốn sau cân thanh khoản.", "Chặn công bố."),
    ("BANK-BS-01", "error", "Một dòng tài sản/nợ có method (tiền mặt, TG NHNN, dư nợ, CK đầu tư, TS Có khác, tiền gửi KH, "
                            "GTCG, nợ khác) bị âm.", "Chặn công bố; xem lại giả định."),
    ("BANK-FUND-01", "warning", "Vay liên NH tự động chạm trần nợ liên NH / tổng TS; tiền gửi & cho vay liên NH dưới sàn.",
     "Kịch bản không khả thi (funding gap), không tự bù."),
    ("BANK-LIQ-01", "info", "Dư nợ / tiền gửi KH vượt trần LDR: tiền gửi KH được nâng lên bằng dư nợ / trần.",
     "Công bố phần nâng thêm từng năm."),
    ("BANK-LIQ-02", "warning", "Có vay liên NH tự động, hoặc vay tự động tăng > 15% so với kỳ trước.",
     "Cảnh báo; xem lại tăng trưởng tiền gửi, tỷ lệ CK đầu tư, sàn liên NH."),
    ("BANK-AQ-01", "warning", "Xoá nợ ngụ ý âm: dự phòng cuối kỳ > đầu kỳ + trích lập.", "Cảnh báo; xem lại chi phí tín dụng / "
                                                                                          "tỷ lệ bao phủ."),
    ("BANK-CAP-01", "warning", "Vốn chủ / tổng TS thấp hơn mức thấp nhất lịch sử (CAR không có trong dữ liệu).", "Cảnh báo."),
    ("REV-10", "warning", "Dự phóng cả năm đầu thấp hơn lũy kế các quý đã công bố (TOI, LNST mẹ).", "Cảnh báo."),
    ("ONE-01", "warning", "Có khoản bất thường ở thu nhập ngoài lãi khác chưa được xử lý (chỉ khi template tắt tự áp).",
     "Xác nhận để loại khỏi chuỗi làm default hoặc bỏ qua."),
    ("ONE-02", "info", "Khoản bất thường đã được xác nhận và áp vào mô hình.", "Công bố k, trend, σ, z, lý do."),
    ("ONE-03", "info", "Khoản quý lệch trend được tự loại khỏi chuỗi làm default (năm: trước khi cộng thành năm và tính "
                       "EMA; quý: EMA 8 quý). Quý đã công bố giữ số thực tế trong tổng năm.",
     "Công bố từng khoản; số thực tế giữ nguyên; analyst bỏ qua được."),
)


class BankFamily(StatementFamily):
    family_id = "bank"
    label_vn = "Ngân hàng"
    has_cash_flow = False
    subtotals = SUBTOTALS
    profit_lines = ("toi", "ppop", "pbt", "npat", "npat_parent")
    margin_lines = {}
    statement_tabs = (("IS", "KQKD"), ("BS", "Cân đối kế toán"), ("SCH", "Lịch phụ trợ"))
    band_lines = (("toi", "Tổng thu nhập hoạt động"), ("ppop", "LN trước dự phòng"),
                  ("npat_parent", "LNST cổ đông công ty mẹ"))
    ratio_inputs = tuple(RATIO_INPUTS)

    def engine(self, template, bs_lines=None) -> BankEngine:
        problems = self.validate_template(template)
        if problems:
            raise ValueError(f"Template {template.template_id} không chạy được với nhóm {self.label_vn}: "
                             + "; ".join(problems))
        return BankEngine(self, template, bs_lines)

    def metrics(self, history, origin: str, annual: bool, adjustments: dict | None = None) -> BankMetrics:
        return BankMetrics(history, origin, annual, adjustments)

    def rules_table(self) -> pd.DataFrame:
        return pd.DataFrame([{"Mã": c, "Mức": sv, "Điều kiện": cond, "Hệ quả": eff} for c, sv, cond, eff in BANK_RULES])

    def register_explain(self) -> None:
        _register_hist_kinds()

    def ratio_table(self, frame: pd.DataFrame, days: pd.Series | None = None,
                    contiguous: pd.Series | None = None, growth_lag: int = 1) -> pd.DataFrame:
        return ratio_table(frame, days, contiguous, growth_lag)

    def validate_template(self, template) -> list[str]:
        problems = []
        if template.family != self.family_id:
            problems.append(f"template thuộc nhóm {template.family}")
        kinds = registered_kinds()
        for m in template.methods:
            if not m.implemented:
                continue
            cls = kinds.get(m.kind)
            if cls is None:
                problems.append(f"{m.method_id}: chưa có class cho kind {m.kind}")
            elif cls.block != m.block:
                problems.append(f"{m.method_id}: kind {m.kind} thuộc block {cls.block}")
        names = {n[2:] for n in dir(BankMetrics) if n.startswith("m_")}
        for aid, d in template.default_specs.items():
            if d.get("type") == "history" and d.get("metric") not in names:
                problems.append(f"{aid}: chỉ số lịch sử không có {d.get('metric')!r}")
        return problems


_HIST_REGISTERED = False


def _register_hist_kinds() -> None:
    """Kiểu yếu tố lịch sử riêng ngân hàng cho giải thích ô (đăng ký, không sửa Explainer). Gọi khi Explainer khởi tạo
    (StatementFamily.register_explain) để tránh import vòng."""
    global _HIST_REGISTERED
    if _HIST_REGISTERED:
        return
    _HIST_REGISTERED = True
    from quick_model import explain, oneoff
    from quick_model.periods import fiscal_year, matching_prior

    @explain.register_hist("avg")
    def _avg(ex, spec, p):  # bình quân đầu–cuối kỳ của tổng có dấu các mã BS
        q = ex.metrics.q
        if p not in q.index:
            return None
        close = [q.loc[p, c] if c in q.columns else float("nan") for _, c in spec[1]]
        opening = [q.loc[p, "open_" + c] if "open_" + c in q.columns else float("nan") for _, c in spec[1]]
        if all(pd.isna(x) for x in opening) or all(pd.isna(x) for x in close):
            return None
        tot = lambda xs: sum(sg * (0.0 if pd.isna(x) else x) for (sg, _), x in zip(spec[1], xs))  # noqa: E731
        return (tot(close) + tot(opening)) / 2

    @explain.register_hist("close_mp")
    def _close_mp(ex, spec, p):  # số dư cuối kỳ cùng kỳ năm trước (gốc tăng trưởng YoY của số dư)
        q = ex.metrics.q
        mp = matching_prior(p)
        if mp not in q.index:
            return None
        vals = [q.loc[mp, c] if c in q.columns else float("nan") for _, c in spec[1]]
        if all(pd.isna(x) for x in vals):
            return None
        return sum(sg * (0.0 if pd.isna(x) else x) for (sg, _), x in zip(spec[1], vals))

    @explain.register_hist("metric_mp")
    def _metric_mp(ex, spec, p):  # chỉ số ở kỳ tương ứng năm trước (vd. gốc tăng trưởng đã loại bất thường)
        return ex.metrics.series(spec[1])[0].get(matching_prior(p))

    @explain.register_hist("one_off_sum")
    def _one_off_sum(ex, spec, p):  # tổng khoản bất thường đã xác nhận của các dòng trong kỳ
        return sum(o.amount for o in oneoff.confirmed(ex.one_offs) if o.line in spec[1]
                   and (o.quarter == p or (len(p) == 4 and str(fiscal_year(o.quarter)) == p)))

