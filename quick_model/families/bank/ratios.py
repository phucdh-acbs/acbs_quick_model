"""Bộ tính chỉ số ngân hàng trên bảng giá trị theo dòng (độc lập UI), dùng chung cho cột thực tế (A) và dự phóng (F).

Cùng một công thức cho cả hai loại cột, nên NIM, lợi suất, chi phí vốn… của năm dự phóng được TÍNH NGƯỢC từ BCTC dự
phóng và so được trực tiếp với lịch sử. Số dư bình quân = (cột trước + cột này) / 2 theo thứ tự cột; cột đầu không có
bình quân. Tỷ lệ theo năm quy năm bằng 365 / số ngày của kỳ.

Lưu ý: engine tính thu nhập/chi phí lãi của phần liên ngân hàng (dòng cân thanh khoản) trên số dư ĐẦU kỳ để tránh
vòng lặp, còn NIM tính ngược dùng bình quân đầy đủ như lịch sử; chênh lệch nhỏ này là chủ ý và được công bố.
"""

from __future__ import annotations

import pandas as pd

IEA_LINES = ("sbv_deposits", "interbank_assets", "trading_securities", "gross_loans", "purchased_debt",
             "investment_securities")
IBL_LINES = ("gov_sbv_debt", "interbank_liabilities", "customer_deposits", "entrusted_funds", "valuable_papers")
RATIO_INPUTS = sorted({*IEA_LINES, *IBL_LINES, "interest_income", "interest_expense", "nii", "net_fee_income",
                       "other_non_interest_income", "toi", "opex", "provision_expense", "loan_loss_reserve", "pbt",
                       "income_tax", "npat", "npat_parent", "equity_parent", "minority_interest", "total_assets",
                       "cash"})

# (tên hiển thị, khóa, kiểu): kiểu "pct" nhân 100
RATIO_ROWS = (
    ("Tăng trưởng dư nợ cho vay (%)", "loan_growth"),
    ("Tăng trưởng tiền gửi KH (%)", "deposit_growth"),
    ("LDR – dư nợ gộp / tổng huy động (%)", "ldr"),
    ("GTCG / tiền gửi KH (%)", "papers_ratio"),
    ("TG NHNN / (tiền gửi KH + GTCG) (%)", "sbv_ratio"),
    ("Lợi suất TS sinh lãi (%)", "yield_iea"),
    ("Chi phí vốn (%)", "cost_of_funds"),
    ("NIM – thu nhập lãi thuần / TS sinh lãi BQ (%)", "nim"),
    ("Phí dịch vụ / dư nợ BQ (%)", "fee_to_loans"),
    ("Tăng trưởng thu nhập ngoài lãi khác (%)", "other_income_growth"),
    ("Thu nhập lãi thuần / TOI (%)", "nii_share"),
    ("CIR – chi phí hoạt động / TOI (%)", "cir"),
    ("Chi phí tín dụng / dư nợ cuối kỳ (%)", "credit_cost"),
    ("Thuế suất hiệu dụng (%)", "tax_rate"),
    ("ROAA (%)", "roaa"),
    ("ROAE – cổ đông công ty mẹ (%)", "roae"),
    ("Vốn chủ / tổng tài sản (%)", "equity_to_assets"),
)


def _sum(frame: pd.DataFrame, lines) -> pd.Series:
    rows = [frame.loc[l] for l in lines if l in frame.index]
    return pd.concat(rows, axis=1).sum(axis=1, min_count=1) if rows else pd.Series(float("nan"), index=frame.columns)


def ratio_values(frame: pd.DataFrame, days: pd.Series | None = None,
                 contiguous: pd.Series | None = None, growth_lag: int = 1) -> pd.DataFrame:
    """frame: index = line_id, cột = kỳ theo thứ tự thời gian. contiguous[c] = cột c liền sau cột trước (để lấy bình quân,
    tăng trưởng); mặc định coi mọi cột liền nhau. Trả về bảng chỉ số dạng tỷ lệ (chưa nhân 100)."""
    cols = list(frame.columns)
    days = pd.Series(365.0, index=cols) if days is None else days.reindex(cols)
    cont = pd.Series([i > 0 for i in range(len(cols))], index=cols) if contiguous is None else contiguous.reindex(cols)
    pos = lambda s: s.where(s > 0)  # noqa: E731
    get = lambda l: frame.loc[l] if l in frame.index else pd.Series(float("nan"), index=cols)  # noqa: E731
    prev = lambda s: s.shift(1).where(cont)  # noqa: E731
    # tăng trưởng so với cùng kỳ năm trước: năm = cột trước; quý = 4 cột trước (growth_lag = 4)
    prev_g = (lambda s: s.shift(growth_lag)) if growth_lag != 1 else prev
    avg = lambda s: ((s + prev(s)) / 2)  # noqa: E731
    ann = 365 / days
    loans, dep, papers = get("gross_loans"), get("customer_deposits"), get("valuable_papers")
    iea, ibl = _sum(frame, IEA_LINES), _sum(frame, IBL_LINES)
    toi = get("toi")
    other = get("other_non_interest_income")
    eq_parent = get("equity_parent")
    out = {
        "loan_growth": loans / pos(prev_g(loans)) - 1,
        "deposit_growth": dep / pos(prev_g(dep)) - 1,
        "ldr": loans / pos(dep + get("entrusted_funds").fillna(0) + papers.fillna(0)),
        "papers_ratio": papers / pos(dep),
        "sbv_ratio": get("sbv_deposits") / pos(dep + papers.fillna(0)),
        "yield_iea": get("interest_income") / pos(avg(iea)) * ann,
        "cost_of_funds": -get("interest_expense") / pos(avg(ibl)) * ann,
        "nim": get("nii") / pos(avg(iea)) * ann,
        "fee_to_loans": get("net_fee_income") / pos(avg(loans)) * ann,
        "other_income_growth": other / pos(prev_g(other)) - 1,
        "nii_share": get("nii") / pos(toi),
        "cir": -get("opex") / pos(toi),
        "credit_cost": -get("provision_expense") / pos(loans) * ann,
        "tax_rate": -get("income_tax") / pos(get("pbt")),
        "roaa": get("npat") / pos(avg(get("total_assets"))) * ann,
        "roae": get("npat_parent") / pos(avg(eq_parent)) * ann,
        "equity_to_assets": (eq_parent + get("minority_interest").fillna(0)) / pos(get("total_assets")),
    }
    return pd.DataFrame(out).T


def ratio_table(frame: pd.DataFrame, days: pd.Series | None = None,
                contiguous: pd.Series | None = None, growth_lag: int = 1) -> pd.DataFrame:
    """Bảng hiển thị: dòng = tên chỉ số (đơn vị %, tỷ lệ theo năm đã quy năm 365 / số ngày), cột như frame."""
    r = ratio_values(frame, days, contiguous, growth_lag) * 100
    return r.rename(index=dict((k, n) for n, k in RATIO_ROWS)).reindex([n for n, _ in RATIO_ROWS])
