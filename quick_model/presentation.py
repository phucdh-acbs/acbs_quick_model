"""Bảng trình bày Thực tế (A) / Dự phóng (F) theo năm hoặc quý, độc lập với UI.

Engine quý + năm (quick_model/hybrid.py, owner 2026-11-18). Xem theo năm: cột A là số cả năm đã công bố, cột lũy kế
các quý đã công bố của năm dự phóng đầu, cột F là số năm (năm có quý = tổng các quý thực tế + dự phóng). Xem theo quý:
cột A là quý thực tế, cột F là quý dự phóng của engine, đến hết Q4 năm sau; cộng các quý của một năm = số năm.
Khi gộp quý: dòng số dư lấy cuối kỳ, tiền đầu kỳ lấy quý đầu, funding gap lấy lớn nhất, đơn giá bình quân gia quyền
theo sản lượng; còn lại cộng dồn. Đơn vị theo dòng: tiền tỷ VND, sản lượng nghìn tấn, đơn giá triệu VND/tấn.
Một số dòng LCTT dự phóng là phiên bản giản lược nên phạm vi hẹp hơn số lịch sử cùng mã (đánh dấu *).
"""

from __future__ import annotations

import pandas as pd

from quick_model.data.history import History
from quick_model.editing import UNIT_DISPLAY
from quick_model.engine.forecast import LINES_BY_ID, ForecastResult, lines_for, load_bs_lines  # noqa: F401
from quick_model.engine.registry import get_template
from quick_model.families import get_family
from quick_model.periods import (FREQ_ANNUAL, FREQ_QUARTER, freq_of, matching_prior, period_days, quarter_bounds,
                                 shift_label)

SCALE_BN = 1e9

# Dòng không có mã nguồn trực tiếp: công thức lịch sử từ mã FiinX
_HIST_FORMULAS = {
    "other_financial_expense": [(1, "ISA7"), (-1, "ISA8")],
    "depreciation": [(1, "CFA2")],
    "pre_financing_cash": [(1, "CFA36"), (1, "CFA18"), (1, "CFA26")],
    "total_debt": [(1, "BSA56"), (1, "BSA71")],
    "ppe_and_cip": [(1, "BSA29"), (1, "BSA163")],
    "gross_cost": [(1, "BSA31"), (1, "BSA34"), (1, "BSA37")],
    "balance_residual": [(1, "BSA53"), (-1, "BSA96")],
}

# Dòng LCTT dự phóng hẹp hơn dòng lịch sử cùng mã
SIMPLIFIED_CF = {
    "cf_chg_receivables": "Dự phóng gồm phải thu KH và trả trước người bán",
    "cf_chg_inventories": "Dự phóng theo tồn kho ròng",
    "cf_chg_payables": "Dự phóng gồm phải trả người bán và người mua trả trước",
    "cf_tax_paid": "Dự phóng: thuế nộp = chi phí thuế trong kỳ",
    "cf_other_operating": "Dự phóng: chỉ gồm chi quỹ khen thưởng phúc lợi",
    "cfo": "Dự phóng giản lược: không gồm các khoản điều chỉnh khác",
}

_LAST = {"total_assets", "total_resources", "balance_residual", "total_debt", "cash_close",
         "pre_financing_cash", "ppe_and_cip", "gross_cost", "retained_earnings_available",
         # ngân hàng: chỉ tiêu dạng số dư trong lịch cân thanh khoản -> lấy quý cuối
         "deposits_before_cap", "ldr_cap_uplift", "liquidity_surplus", "interbank_floor", "interbank_cap",
         "auto_interbank_borrowing"}
_MEAN = {"avg_loans", "avg_iea", "avg_ibl"}  # ngân hàng: số dư bình quân quý -> bình quân năm
_FIRST = {"cash_open"}
_MAX = {"funding_gap"}
_WEIGHTED = {"asp": "sales_volume", "unit_cogs": "sales_volume"}

_RATIO_INPUTS = ["net_revenue", "cogs", "gross_profit", "selling_expense", "admin_expense",
                 "trade_receivables", "inventories", "trade_payables", "capex", "pbt", "income_tax"]


def history_window(h: History, origin: str, n: int) -> list[str]:
    """n kỳ lịch sử gần nhất (đến kỳ gốc, cùng tần suất với kỳ gốc) có trong dữ liệu."""
    labels = [shift_label(origin, -i) for i in range(n - 1, -1, -1)]
    have = set(h.periods_by(freq_of(origin)))
    return [p for p in labels if p in have]


def view_frequencies(result: ForecastResult) -> tuple[str, ...]:
    """Kiểu xem: năm và quý (quý dự phóng của engine quý + năm)."""
    return (FREQ_ANNUAL, FREQ_QUARTER)


def statement_lines(statement: str, template_id: str | None = None) -> list[str]:
    if statement == "BS":
        return [*load_bs_lines(template_id=template_id)["line_id"], "total_assets", "total_resources",
                "balance_residual"]
    return [l.line_id for l in lines_for(template_id) if l.statement == statement]


def line_unit(line: str, template_id: str | None = None) -> str:
    units = {l.line_id: l.unit for l in lines_for(template_id)}
    return units.get(line, "VND")


def statement_grid(h: History, result: ForecastResult, statement: str, n_hist: int = 8,
                   freq: str = "Q", scale: float = SCALE_BN) -> tuple[pd.DataFrame, list[str]]:
    """(bảng, cột dự phóng). freq='Q': n_hist quý lịch sử; freq='Y': n_hist năm lịch sử."""
    _check_view(result, freq)
    tid = result.template_id or result.scenario.template_id
    lines = statement_lines(statement, tid)
    values, f_cols = (_quarter_values(h, result, lines, n_hist) if freq == FREQ_QUARTER
                      else _period_values(h, result, lines, n_hist))
    values = values.reindex(lines)
    unit_of = {l: line_unit(l, tid) for l in lines}
    factors = pd.Series({l: (1 / scale if unit_of[l] == "VND" else UNIT_DISPLAY[unit_of[l]][1]) for l in lines})
    grid = values.mul(factors, axis=0)
    labels = result.line_meta["label_vn"].reindex(lines)
    marked = labels.index.isin(list(SIMPLIFIED_CF))
    labels = labels.where(~marked, labels + " *")
    units = pd.Series(unit_of)
    non_money = units != "VND"
    labels = labels.where(~non_money, labels + " (" + units.map(lambda u: UNIT_DISPLAY.get(u, (u,))[0]) + ")")
    grid.insert(0, "Khoản mục", labels)
    return grid, f_cols


def driver_grid(h: History, result: ForecastResult, n_hist: int = 8,
                freq: str = "Q") -> tuple[pd.DataFrame, list[str]]:
    """Các tỷ lệ chính, cùng công thức cho cả A và F."""
    _check_view(result, freq)
    family = get_family(get_template(_tid(result)).family)
    quarterly = freq == FREQ_QUARTER
    if family.ratio_inputs:  # nhóm có bộ chỉ số riêng (ngân hàng): cùng công thức cho A và F, NIM tính ngược
        lines = list(family.ratio_inputs)
        frame, f_cols = (_quarter_values(h, result, lines, n_hist + 4) if quarterly
                         else _period_values(h, result, lines, n_hist + 1))
        hist_cols = [c for c in frame.columns if c not in f_cols and not _is_ytd(c)]
        frame = frame[hist_cols + list(f_cols)]
        days = pd.Series([period_days(c.split()[0]) for c in frame.columns], index=frame.columns)
        out = family.ratio_table(frame, days, None, 4 if quarterly else 1)  # tỷ lệ quy năm 365 / số ngày kỳ
        return out[hist_cols[-n_hist:] + list(f_cols)], f_cols
    extra = 4 if quarterly else 1  # thêm kỳ lịch sử làm gốc tăng trưởng
    frame, f_cols = (_quarter_values(h, result, _RATIO_INPUTS, n_hist + extra) if quarterly
                     else _period_values(h, result, _RATIO_INPUTS, n_hist + extra))
    ytd_cols = [c for c in frame.columns if _is_ytd(c)]  # cột lũy kế năm dự phóng đầu, vd. "6T2026 A"
    days = pd.Series([_ytd_days(c) if c in ytd_cols else period_days(c.split()[0]) for c in frame.columns],
                     index=frame.columns)
    prev = {c: None if c in ytd_cols else _lookup(frame, "net_revenue", matching_prior(c.split()[0]))
            for c in frame.columns}
    out = _ratios(frame, days, pd.Series(prev, dtype="float64"))
    hist_cols = [c for c in frame.columns if c not in f_cols and c not in ytd_cols]
    keep = hist_cols[-n_hist:] + ytd_cols + list(f_cols)
    return out[keep], f_cols


# ---------------------------------------------------------------- dựng giá trị

def _check_view(result: ForecastResult, freq: str) -> None:
    if freq not in view_frequencies(result):
        raise ValueError(f"Kịch bản chạy theo {result.scenario.frequency} không xem được theo {freq}")


def _tid(result) -> str | None:
    return result.template_id or result.scenario.template_id


def _quarter_values(h, result, lines, n_hist):
    """Xem theo quý: n_hist quý thực tế gần nhất + quý dự phóng của engine (result.quarter_values)."""
    quarters = list(h.periods_by(FREQ_QUARTER))
    hist_periods = quarters[-n_hist:]
    hist = _history_values(_quarter_source(h), lines, hist_periods, _tid(result))
    cols = {f"{p} A": hist[p] for p in hist_periods}
    f_cols = []
    if result.quarter_values is not None:
        for q in result.quarter_values.columns:
            cols[f"{q} F"] = result.quarter_values[q].reindex(lines)
            f_cols.append(f"{q} F")
    return pd.DataFrame(cols, index=lines), f_cols


def _period_values(h, result, lines, n_hist):
    """Cột lịch sử và dự phóng cùng tần suất với kịch bản."""
    hist_periods = history_window(h, result.scenario.origin, n_hist)
    hist = _history_values(period_source(h, result.scenario.frequency), lines, hist_periods, _tid(result))
    cols = {f"{p} A": hist[p] for p in hist_periods}
    ytd = _ytd_column(h, result, lines)
    if ytd is not None:
        cols[ytd[0]] = ytd[1]
    f_cols = []
    if result.values is not None:
        for p in result.periods:
            cols[f"{p} F"] = result.values[p].reindex(lines)
            f_cols.append(f"{p} F")
    return pd.DataFrame(cols, index=lines), f_cols


def _is_ytd(col: str) -> bool:
    label = col.split()[0]
    return "T" in label and label.split("T")[0].isdigit()


def _ytd_days(col: str) -> int:
    """Số ngày của cột lũy kế 'nTYYYY': tổng số ngày các quý từ quý 1."""
    months, y = col.split()[0].split("T")
    return sum(quarter_bounds(f"{y}Q{q}")[2] for q in range(1, int(months) // 3 + 1))


def _ytd_column(h, result, lines) -> tuple[str, pd.Series] | None:
    """Chạy theo năm: lũy kế các quý đã công bố của năm dự phóng đầu (vd. '6T2026 A'); số dư lấy quý mới nhất."""
    if result.scenario.frequency != FREQ_ANNUAL:
        return None
    y = int(result.scenario.origin) + 1
    qs = [q for q in h.periods_by(FREQ_QUARTER) if q.startswith(f"{y}Q")]
    if not qs:
        return None
    hist_q = _history_values(_quarter_source(h), lines, qs, _tid(result))
    return f"{3 * len(qs)}T{y} A", _aggregate(hist_q, lines, _tid(result))


def _aggregate(q: pd.DataFrame, lines: list[str], template_id: str | None = None) -> pd.Series:
    bs = set(load_bs_lines(template_id=template_id)["line_id"])
    out = {}
    for line in lines:
        row = q.loc[line]
        if line in bs or line in _LAST:
            out[line] = row.iloc[-1]
        elif line in _FIRST:
            out[line] = row.iloc[0]
        elif line in _MEAN:
            out[line] = row.mean()
        elif line in _MAX:
            out[line] = row.max()
        elif line in _WEIGHTED and _WEIGHTED[line] in q.index:
            w = q.loc[_WEIGHTED[line]]
            out[line] = (row * w).sum(min_count=1) / w.sum(min_count=1) if w.sum() else float("nan")
        else:
            out[line] = row.sum(min_count=1)
    return pd.Series(out)


def period_source(h: History, freq: str) -> pd.DataFrame:
    """IS/CF và số dư BS theo tần suất: quý riêng lẻ, hoặc số cả năm (FY) và số dư 31/12."""
    return pd.concat([h.flows_by("IS", freq), h.flows_by("CF", freq), h.balances_by(freq)], axis=1)


def _quarter_source(h: History) -> pd.DataFrame:
    return period_source(h, FREQ_QUARTER)


def _source_terms(code: str) -> list[tuple[int, str]]:
    """Mã nguồn của một dòng: 'ISA3', '-ISA21' hoặc biểu thức '+ISB31 +ISB32'."""
    toks = str(code).split()
    if len(toks) == 1 and toks[0][0] not in "+-":
        return [(1, toks[0])]
    return [(-1 if t[0] == "-" else 1, t.lstrip("+-")) for t in toks]


def _history_values(src: pd.DataFrame, lines: list[str], periods: list[str],
                    template_id: str | None = None) -> pd.DataFrame:
    """Giá trị lịch sử theo dòng của nhóm BCTC của template (mặc định: template mặc định, phi tài chính)."""
    src = src.reindex(periods)
    bs = load_bs_lines(template_id=template_id).set_index("line_id")
    by_id = {l.line_id: l for l in lines_for(template_id)}
    nonfin = get_template(template_id).family == "nonfinancial"
    out = {}
    for line in lines:
        if line in bs.index:
            terms = bs.loc[line, "terms"]
        elif line in ("total_assets", "total_resources"):
            terms = [(1, "BSA53" if line == "total_assets" else "BSA96")]
        elif line in _HIST_FORMULAS and (nonfin or line == "balance_residual"):
            terms = _HIST_FORMULAS[line]
        elif line in by_id and by_id[line].source_code:
            terms = _source_terms(by_id[line].source_code)
        else:
            out[line] = pd.Series(float("nan"), index=periods)
            continue
        vals = [src[c] * s if c in src.columns else pd.Series(float("nan"), index=periods) for s, c in terms]
        out[line] = pd.concat(vals, axis=1).sum(axis=1, min_count=1)  # tất cả trống -> trống, không thành 0
    return pd.DataFrame(out, index=periods).T.reindex(lines)


# ---------------------------------------------------------------- tỷ lệ

def _ratios(frame: pd.DataFrame, days: pd.Series, prev_rev: pd.Series) -> pd.DataFrame:
    pos = lambda s: s.where(s > 0)  # noqa: E731
    r = frame.loc["net_revenue"]
    cogs = -frame.loc["cogs"]
    rows = {
        "Tăng trưởng doanh thu YoY (%)": (r / pos(prev_rev) - 1) * 100,
        "Biên lợi nhuận gộp (%)": frame.loc["gross_profit"] / pos(r) * 100,
        "CPBH / doanh thu (%)": -frame.loc["selling_expense"] / pos(r) * 100,
        "CPQL / doanh thu (%)": -frame.loc["admin_expense"] / pos(r) * 100,
        "Số ngày phải thu": frame.loc["trade_receivables"] / pos(r) * days,
        "Số ngày tồn kho": frame.loc["inventories"] / pos(cogs) * days,
        "Số ngày phải trả (theo giá vốn)": frame.loc["trade_payables"] / pos(cogs) * days,
        "Capex / doanh thu (%)": -frame.loc["capex"] / pos(r) * 100,
        "Thuế suất hiệu dụng (%)": -frame.loc["income_tax"] / pos(frame.loc["pbt"]) * 100,
    }
    return pd.DataFrame(rows).T


def _lookup(frame: pd.DataFrame, line: str, period: str) -> float | None:
    for c in frame.columns:
        if c.split()[0] == period and _full_year(c):
            return frame.loc[line, c]
    return None


def _full_year(col: str) -> bool:
    return "Q)" not in col


