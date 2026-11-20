"""Engine liên kết IS – BS – CF của nhóm phi tài chính (dùng cho mọi template ngành phi tài chính).

Thứ tự mỗi kỳ: doanh thu -> giá vốn -> CPBH/CPQL -> TSCĐ -> vốn lưu động -> lãi vay (dư nợ đầu kỳ) -> ngoài HĐKD
-> LNTT -> thuế, thiểu số, phân phối -> LCTT trước tài trợ -> tài trợ (cân nguồn tự động ở L1 hoặc lịch vay
L2) -> tiền cuối kỳ = tiền trên BS.

Mỗi dòng BS có chủ sở hữu (config/families/nonfinancial/bs_lines.csv). Block sở hữu phải ghi dòng đó mỗi kỳ;
dòng "carry" giữ nguyên số dư và được công bố. Không có khoản cân bằng ẩn: tiền tính từ LCTT, cân đối kế toán
chỉ là phép kiểm tra. Ở L1, nợ vay tự cân nguồn bằng phương án khai báo (tiền tối thiểu, trần nợ/tài sản, tỷ
trọng ngắn/dài hạn); thiếu vượt trần thì báo funding gap.
"""

from __future__ import annotations

import pandas as pd

from quick_model.data.history import History
from quick_model.engine.assumptions import AssumptionValue, Scenario, resolve
from quick_model.engine.rules import input_issues, review_issues, selection_issues
from quick_model.engine.validation import Issue, validate_inputs
from quick_model.families.base import ForecastResult, PeriodEngine, forecast_periods  # noqa: F401
from quick_model.methods.base import CalcError, PeriodContext, method_class
from quick_model import oneoff
from quick_model.periods import (FREQ_ANNUAL, FREQ_QUARTER, fiscal_year, freq_of, is_year_end, period_days, shift_label,
                                 year_quarters)

# Dòng tổng = tổng có dấu của các dòng thành phần (dùng để phân rã nguyên nhân thay đổi)
SUBTOTALS = {
    "gross_profit": ((1, "net_revenue"), (1, "cogs")),
    "financial_expense": ((1, "interest_expense"), (1, "other_financial_expense")),
    "operating_profit": ((1, "gross_profit"), (1, "financial_income"), (1, "financial_expense"), (1, "jv_associates"),
                         (1, "selling_expense"), (1, "admin_expense")),
    "pbt": ((1, "operating_profit"), (1, "other_profit")),
    "npat": ((1, "pbt"), (1, "income_tax")),
    "npat_parent": ((1, "npat"), (-1, "npat_minority")),
    "cfo": ((1, "cf_pbt"), (1, "cf_depreciation"), (1, "cf_chg_receivables"), (1, "cf_chg_inventories"),
            (1, "cf_chg_payables"), (1, "cf_tax_paid"), (1, "cf_other_operating")),
    "net_cash_flow": ((1, "cfo"), (1, "cfi"), (1, "cff")),
}

NEGATIVE_CHECK = ("trade_receivables", "inventories", "trade_payables", "prepayments_to_suppliers",
                  "advances_from_customers", "fixed_assets", "cip", "st_borrowings", "lt_borrowings",
                  "bonus_welfare_fund")


class NonFinancialEngine(PeriodEngine):
    BLOCK_ORDER = ("revenue", "cogs", "sga", "ppe", "working_capital", "debt", "non_operating", "tax_equity")

    # ------------------------------------------------------------ chạy
    def run(self, h: History, scenario: Scenario, defaults: dict[str, AssumptionValue]) -> ForecastResult:
        """Kịch bản năm; vòng tính chạy theo quý đến hết Q4 năm sau quý công bố mới nhất rồi theo năm
        (quick_model/hybrid.py). values = bảng năm (năm có quý = tổng quý); quarter_values = các quý dự phóng."""
        from quick_model import hybrid
        tpl = self.template
        plan = hybrid.make_plan(h, scenario.origin, scenario.horizon)
        periods = plan.years
        meta = self.family.line_meta(tpl, scenario.methods)
        levels = {b: tpl.level_of(m) for b, m in scenario.methods.items() if m in tpl.methods_by_id}

        self.traces = {}
        extra = {}

        def done(values, resolved, issues, engine_values=None):
            res = ForecastResult(scenario, periods, resolved, values, meta, issues,
                                 self.status(engine_values if engine_values is not None else values, issues, levels, h,
                                             scenario), tpl.template_id, self.traces, plan=plan, **extra)
            return res

        try:
            tpl.check_method_selection(scenario.methods)
        except Exception as e:  # MethodUnavailable
            return done(None, {}, [Issue("error", "method_unavailable", None, None, None, str(e))])

        resolved = resolve(scenario, tpl.specs, defaults)
        issues = validate_inputs(resolved, tpl.specs, defaults, periods)
        issues += selection_issues(scenario, resolved, tpl)
        issues += input_issues(scenario, resolved, periods, tpl)
        opening, open_issues = self.opening_balances(h, scenario.origin)
        issues += open_issues
        if plan.hybrid:
            q_open, q_issues = self.opening_balances(h, plan.q_origin)
            issues += [i for i in q_issues if i.severity == "error"]
        issues += self.frequency_notes(h, scenario, plan)
        if any(i.severity == "error" for i in issues):
            return done(None, resolved, issues)

        a = {k: list(v.values) for k, v in resolved.items()}
        revenue_hist = {k: float(v) for k, v in h.flows_by("IS", FREQ_ANNUAL)["ISA3"].loc[:scenario.origin].items()
                        if pd.notna(v)}
        try:
            if plan.hybrid:
                one_offs = self.effective_one_offs(h, scenario)
                qdef = hybrid.quarter_defaults(h, plan, tpl, self.family, defaults, one_offs)
                arrays, qvals = hybrid.period_arrays(plan, tpl, resolved, qdef)
                memo = self.opening_memo(h, plan.q_origin)
                memo["one_offs"] = {}  # khoản bất thường nằm trong quý thực tế; quý dự phóng dùng chuỗi đã làm sạch
                rev_q = {k: float(v) for k, v in h.flows_by("IS", FREQ_QUARTER)["ISA3"].loc[:plan.q_origin].items()
                         if pd.notna(v)}
                engine_values = self.compute(scenario, plan.periods, arrays, q_open, memo, rev_q, plan.q_origin)
                for _ in range(hybrid.TARGET_ROUNDS):  # mục tiêu năm khi analyst ghi đè: số vòng cố định, không lặp
                    table = hybrid.quarter_table(h, plan, engine_values, tpl.template_id)
                    if not hybrid.apply_targets(plan, tpl, resolved, qvals, arrays, table):
                        break
                    self.traces = {}
                    engine_values = self.compute(scenario, plan.periods, arrays, q_open, memo, rev_q, plan.q_origin)
                values = hybrid.annual_table(h, plan, engine_values, self.origin_column(h, scenario.origin, opening),
                                             tpl.template_id, self.stock_lines())
                extra.update(quarter_values=engine_values[plan.quarters], quarter_assumptions=qvals,
                             engine_values=engine_values)
            else:
                memo = self.opening_memo(h, scenario.origin)
                memo["one_offs"] = oneoff.forecast_amounts(scenario.one_offs, periods, FREQ_ANNUAL)
                engine_values = values = self.compute(scenario, periods, a, opening, memo, revenue_hist)
        except CalcError as e:
            issues.append(e.issue)
            return done(None, resolved, issues)
        issues += self.output_checks(engine_values, list(engine_values.columns[1:]), h.policy.abs_tolerance)
        ir = resolved.get("interest_rate")
        if ir is not None and ir.basis.get("no_history") and "total_debt" in engine_values.index:
            debt = engine_values.loc["total_debt"].iloc[1:]
            if (debt > h.policy.abs_tolerance).any():
                p0 = debt[debt > h.policy.abs_tolerance].index[0]
                issues.append(Issue("warning", "INT-01", "debt", "interest_rate", p0,
                                    "Lịch sử không có nợ vay nên lãi suất default = 0, nhưng mô hình phát sinh nợ vay từ "
                                    f"{p0}: lãi vay đang tính 0, cần nhập lãi suất."))
        issues += self.ytd_checks(h, scenario, values)
        issues += self.one_off_issues(h, scenario, periods)
        issues += review_issues(scenario, values, periods, scenario.origin, defaults, revenue_hist, a, tpl)
        return done(values, resolved, issues, engine_values)

    def origin_column(self, h: History, origin: str, opening: dict[str, float]) -> dict:
        memo = self.opening_memo(h, origin)
        first = {**opening, "total_debt": opening["st_borrowings"] + opening["lt_borrowings"],
                 "ppe_and_cip": opening["fixed_assets"] + opening["cip"], "gross_cost": memo.get("gross_cost")}
        first["total_assets"], first["total_resources"] = self.totals(opening)
        first["balance_residual"] = first["total_assets"] - first["total_resources"]
        return first

    # ------------------------------------------------------------ vòng kỳ
    def compute(self, s: Scenario, periods: list[str], a: dict[str, list], opening: dict[str, float],
                memo: dict, revenue_hist: dict[str, float], origin_label: str | None = None) -> pd.DataFrame:
        tpl, bs_lines = self.template, self.bs_lines
        methods = {b: method_class(tpl.kind_of(s.methods[b]))(tpl.methods_by_id[s.methods[b]]) for b in self.BLOCK_ORDER}
        for b, m in methods.items():
            if m.block != b:
                raise ValueError(f"Kind {m.kind} thuộc block {m.block}, không dùng được cho {b}")
        owners = bs_lines.set_index("line_id")["owner"]
        sides = bs_lines.set_index("line_id")["side"]
        revenue = dict(revenue_hist)
        bal = dict(opening)
        state = dict(memo)
        first = {**opening, "total_debt": opening["st_borrowings"] + opening["lt_borrowings"],
                 "ppe_and_cip": opening["fixed_assets"] + opening["cip"], "gross_cost": state.get("gross_cost")}
        first["total_assets"], first["total_resources"] = self.totals(opening)
        first["balance_residual"] = first["total_assets"] - first["total_resources"]
        cols = {origin_label or s.origin: first}

        for t, p in enumerate(periods):
            v: dict[str, float] = {}
            new: dict[str, float] = {}
            ctx = PeriodContext(p, t, period_days(p), {k: x[t] for k, x in a.items()}, v, bal, new, state, revenue)
            methods["revenue"].compute(ctx)
            revenue[p] = v["net_revenue"]
            methods["cogs"].compute(ctx)
            v["gross_profit"] = v["net_revenue"] + v["cogs"]
            methods["sga"].compute(ctx)
            methods["ppe"].compute(ctx)
            methods["working_capital"].compute(ctx)
            methods["debt"].compute(ctx)  # lãi vay trên dư nợ đầu kỳ
            methods["non_operating"].compute(ctx)
            v["operating_profit"] = (v["gross_profit"] + v["financial_income"] + v["financial_expense"]
                                     + v["jv_associates"] + v["selling_expense"] + v["admin_expense"])
            v["pbt"] = v["operating_profit"] + v["other_profit"]
            methods["tax_equity"].compute(ctx)

            # LCTT gián tiếp trước tài trợ
            v["cf_pbt"] = v["pbt"]
            v["cf_depreciation"] = v["depreciation"]
            v["cf_chg_receivables"] = -((new["trade_receivables"] - bal["trade_receivables"])
                                        + (new["prepayments_to_suppliers"] - bal["prepayments_to_suppliers"]))
            v["cf_chg_inventories"] = -(new["inventories"] - bal["inventories"])
            v["cf_chg_payables"] = ((new["trade_payables"] - bal["trade_payables"])
                                    + (new["advances_from_customers"] - bal["advances_from_customers"]))
            v["cf_tax_paid"] = v["income_tax"]
            v["cfo"] = (v["cf_pbt"] + v["cf_depreciation"] + v["cf_chg_receivables"] + v["cf_chg_inventories"]
                        + v["cf_chg_payables"] + v["cf_tax_paid"] + v["cf_other_operating"])
            v["cfi"] = v["capex"]
            for line, owner in owners.items():
                if owner == "carry":
                    new[line] = bal[line]

            # tài trợ
            cash_pre = bal["cash"] + v["cfo"] + v["cfi"] + v["share_issue"] + v["dividends_paid"]
            assets_ex_cash = sum(new[k] for k in sides.index if sides[k] == "asset" and k != "cash")
            methods["debt"].finance(ctx, cash_pre, assets_ex_cash)
            v["cff"] = v["share_issue"] + v["borrowings_drawn"] + v["borrowings_repaid"] + v["dividends_paid"]
            v["net_cash_flow"] = v["cfo"] + v["cfi"] + v["cff"]
            v["cash_open"] = bal["cash"]
            v["fx_effect"] = 0.0
            v["cash_close"] = v["cash_open"] + v["net_cash_flow"] + v["fx_effect"]
            v["pre_financing_cash"] = v["cash_open"] + v["cfo"] + v["cfi"]
            new["cash"] = v["cash_close"]
            # tiền tối thiểu là ràng buộc cuối năm: quý 1–3 chỉ kiểm tiền không âm
            v["funding_gap"] = max(0.0, (v["min_cash_level"] if is_year_end(p) else 0.0) - v["cash_close"])

            missing = [l for l in owners.index if l not in new]
            if missing:
                ctx.fail("unowned_line", None, ",".join(missing), f"Dòng BS chưa có method ghi: {missing}")
            v.update(new)
            v["total_debt"] = new["st_borrowings"] + new["lt_borrowings"]
            v["ppe_and_cip"] = new["fixed_assets"] + new["cip"]
            v["total_assets"], v["total_resources"] = self.totals(new)
            v["balance_residual"] = v["total_assets"] - v["total_resources"]
            cols[p] = v
            bal = new
            for line, tr in ctx.traces.items():
                self.traces[(line, p)] = tr
            if freq_of(p) == FREQ_QUARTER and is_year_end(p):  # năm đủ 4 quý: doanh thu năm làm gốc tăng trưởng năm sau
                qs = year_quarters(fiscal_year(p))
                if all(q in revenue for q in qs):
                    revenue[str(fiscal_year(p))] = sum(revenue[q] for q in qs)
        return pd.DataFrame(cols)

    # ------------------------------------------------------------ số dư kỳ gốc (riêng phi tài chính)
    @staticmethod
    def opening_memo(h: History, origin: str) -> dict:
        row = h.balances_by(freq_of(origin)).loc[origin]
        vals = [row.get(c) for c in ("BSA31", "BSA34", "BSA37")]
        gross = None if all(pd.isna(x) for x in vals) else sum(0.0 if pd.isna(x) else float(x) for x in vals)
        re = row.get("BSA90")
        return {"gross_cost": gross, "retained_earnings": float("nan") if pd.isna(re) else float(re)}

    def ytd_checks(self, h: History, s: Scenario, values: pd.DataFrame) -> list[Issue]:
        qs = self.ytd_quarters(h, s)
        if not qs:
            return []
        y = str(int(s.origin) + 1)
        flows = h.flows("IS")
        out = []
        for line, code, name in (("net_revenue", "ISA3", "Doanh thu"), ("npat_parent", "ISA22", "LNST công ty mẹ")):
            ytd = flows.loc[qs, code].sum(min_count=1) if code in flows.columns else None
            fy = values.loc[line, y] if y in values.columns else None
            if ytd is None or pd.isna(ytd) or fy is None or ytd <= 0:
                continue
            if fy < ytd:
                out.append(Issue("warning", "REV-10", "revenue" if line == "net_revenue" else None, line, y,
                                 f"{name} dự phóng cả năm {y} ({fy / 1e9:,.0f} tỷ) thấp hơn lũy kế thực tế "
                                 f"{qs[0]}–{qs[-1]} ({ytd / 1e9:,.0f} tỷ): xem lại giả định năm."))
        return out

    @staticmethod
    def output_checks(values: pd.DataFrame, periods: list[str], tol: float) -> list[Issue]:
        out = []
        for p in periods:
            c = values[p]
            if abs(c["balance_residual"]) > tol:
                out.append(Issue("error", "unbalanced", None, "balance_residual", p,
                                 f"Bảng cân đối lệch {c['balance_residual']:,.0f} VND"))
            if abs(c["cash_close"] - c["cash"]) > tol:
                out.append(Issue("error", "cash_not_reconciled", None, "cash", p, "Tiền cuối kỳ LCTT khác tiền trên BS"))
            for line in NEGATIVE_CHECK:
                if c[line] < -tol:
                    out.append(Issue("error", "negative_balance", None, line, p, f"{line} âm: {c[line]:,.0f}"))
            if c["funding_gap"] > tol:
                out.append(Issue("warning", "funding_gap", "debt", "funding_gap", p,
                                 f"Thiếu hụt vốn {c['funding_gap']:,.0f} VND (tiền cuối kỳ {c['cash_close']:,.0f}); "
                                 "kịch bản không khả thi nếu không có nguồn tài trợ khai báo"))
            if c["gross_profit"] < 0:
                out.append(Issue("warning", "negative_gross_profit", "cogs", "gross_profit", p, "Lợi nhuận gộp âm"))
            if c["npat"] < 0:
                out.append(Issue("warning", "negative_npat", None, "npat", p, "LNST âm"))
            if c["equity_parent"] < 0:
                out.append(Issue("warning", "negative_equity", "tax_equity", "equity_parent", p, "Vốn chủ âm"))
            if c["cfo"] + c["cfi"] < 0:
                out.append(Issue("warning", "negative_fcf", None, "cfo", p, "Dòng tiền tự do (CFO + CFI) âm"))
        return out

    @staticmethod
    def status(values: pd.DataFrame | None, issues: list[Issue], levels: dict, h: History, s: Scenario) -> dict:
        errors = [i for i in issues if i.severity == "error"]
        st = {
            "calculated": values is not None,
            "hard_errors": len(errors),
            "warnings": sum(i.severity == "warning" for i in issues),
            "levels": levels,
            "mixed_levels": len(set(levels.values())) > 1,
            "history_point_in_time": h.provenance.get("point_in_time"),
            "latest_published": (h.periods_by(FREQ_QUARTER) or [None])[-1],
            "frequency": s.frequency,
        }
        if values is None:
            st.update(balanced=None, cash_reconciled=None, feasible=None, funding_gap_max=None,
                      first_gap_period=None, publishable=False, auto_funding_max=None)
            return st
        fc = values.iloc[:, 1:]
        tol = h.policy.abs_tolerance
        gaps = fc.loc["funding_gap"]
        st["balance_max_residual"] = float(fc.loc["balance_residual"].abs().max())
        st["balanced"] = st["balance_max_residual"] <= tol
        st["cash_reconciled"] = bool(((fc.loc["cash_close"] - fc.loc["cash"]).abs() <= tol).all())
        st["funding_gap_max"] = float(gaps.max())
        st["first_gap_period"] = next((p for p, g in gaps.items() if g > tol), None)
        st["feasible"] = st["first_gap_period"] is None
        st["auto_funding_max"] = float(fc.loc["auto_outstanding"].max()) if "auto_outstanding" in fc.index else 0.0
        st["publishable"] = (not errors) and st["balanced"] and st["cash_reconciled"] and st["feasible"]
        return st
