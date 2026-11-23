"""Engine liên kết IS – BS của nhóm ngân hàng (không có LCTT; owner 2026-11-17).

Thứ tự mỗi kỳ: dòng giữ nguyên số dư -> cho vay -> huy động (trần LDR) -> dự trữ, tiền mặt -> chứng khoán đầu tư ->
dự phòng -> TS Có khác / nợ khác -> thu nhập lãi thuần (bình quân; liên NH lấy đầu kỳ) -> ngoài lãi -> TOI -> chi phí
hoạt động -> LN trước dự phòng -> LNTT -> thuế, cổ đông không kiểm soát, cổ tức, vốn chủ -> cân thanh khoản qua liên
ngân hàng (dòng cân khai báo, có sàn/trần, funding gap).

Mỗi dòng BS có chủ sở hữu (config/families/bank/bs_lines.csv); dòng "carry" giữ nguyên số dư và được công bố. Không
có biến cân ẩn: tổng TS = tổng nguồn vốn nhờ dòng liên ngân hàng tự động, số tiền được công bố từng kỳ.
"""

from __future__ import annotations

import pandas as pd

from quick_model import oneoff
from quick_model.engine.assumptions import AssumptionValue, Scenario, resolve
from quick_model.engine.validation import Issue, validate_inputs
from quick_model.families.base import ForecastResult, PeriodEngine
from quick_model.methods.base import CalcError, PeriodContext, method_class
from quick_model.periods import (FREQ_ANNUAL, FREQ_QUARTER, fiscal_year, freq_of, is_year_end, period_days,
                                 shift_label, year_quarters)

SUBTOTALS = {
    "nii": ((1, "interest_income"), (1, "interest_expense")),
    "toi": ((1, "nii"), (1, "net_fee_income"), (1, "other_non_interest_income")),
    "ppop": ((1, "toi"), (1, "opex")),
    "pbt": ((1, "ppop"), (1, "provision_expense")),
    "npat": ((1, "pbt"), (1, "income_tax")),
    "npat_parent": ((1, "npat"), (-1, "npat_minority")),
}

NEGATIVE_CHECK = ("cash", "sbv_deposits", "gross_loans", "investment_securities", "other_assets", "customer_deposits",
                  "valuable_papers", "other_liabilities")
AUTO_GROWTH_LIMIT = 0.15  # vay liên NH tự động tăng > 15% so với kỳ trước -> cảnh báo (như ngưỡng nợ vay phi tài chính)


class BankEngine(PeriodEngine):
    BLOCK_ORDER = ("loans", "funding", "liquidity", "securities", "asset_quality", "other_bs", "nii", "non_interest",
                   "opex", "tax_equity", "balancing")
    YTD_LINES = (("toi", "ISB38", "Tổng thu nhập hoạt động"), ("npat_parent", "ISA22", "LNST công ty mẹ"))

    def run(self, h, scenario: Scenario, defaults: dict[str, AssumptionValue]) -> ForecastResult:
        """Như phi tài chính: kịch bản năm, vòng tính theo quý đến hết Q4 năm sau quý công bố mới nhất rồi theo năm."""
        from quick_model import hybrid
        tpl = self.template
        plan = hybrid.make_plan(h, scenario.origin, scenario.horizon)
        periods = plan.years
        meta = self.family.line_meta(tpl, scenario.methods)
        levels = {b: tpl.level_of(m) for b, m in scenario.methods.items() if m in tpl.methods_by_id}
        self.traces = {}
        extra = {}

        def done(values, resolved, issues, engine_values=None):
            return ForecastResult(scenario, periods, resolved, values, meta, issues,
                                  self.status(engine_values if engine_values is not None else values, issues, levels,
                                              h, scenario), tpl.template_id, self.traces, plan=plan, **extra)

        try:
            tpl.check_method_selection(scenario.methods)
        except Exception as e:  # MethodUnavailable
            return done(None, {}, [Issue("error", "method_unavailable", None, None, None, str(e))])

        resolved = resolve(scenario, tpl.specs, defaults)
        issues = validate_inputs(resolved, tpl.specs, defaults, periods)
        opening, open_issues = self.opening_balances(h, scenario.origin)
        issues += open_issues
        if plan.hybrid:
            q_open, q_issues = self.opening_balances(h, plan.q_origin)
            issues += [i for i in q_issues if i.severity == "error"]
        issues += self.frequency_notes(h, scenario, plan)
        if any(i.severity == "error" for i in issues):
            return done(None, resolved, issues)

        a = {k: list(v.values) for k, v in resolved.items()}
        try:
            if plan.hybrid:
                one_offs = self.effective_one_offs(h, scenario)
                qdef = hybrid.quarter_defaults(h, plan, tpl, self.family, defaults, one_offs)
                arrays, qvals = hybrid.period_arrays(plan, tpl, resolved, qdef)
                memo = self.quarter_memo(h, scenario, plan)
                engine_values = self.compute(scenario, plan.periods, arrays, q_open, memo, plan.q_origin)
                for _ in range(hybrid.TARGET_ROUNDS):  # mục tiêu năm khi analyst ghi đè: số vòng cố định, không lặp
                    table = hybrid.quarter_table(h, plan, engine_values, tpl.template_id)
                    if not hybrid.apply_targets(plan, tpl, resolved, qvals, arrays, table):
                        break
                    self.traces = {}
                    memo = self.quarter_memo(h, scenario, plan)
                    engine_values = self.compute(scenario, plan.periods, arrays, q_open, memo, plan.q_origin)
                first = dict(opening)
                first["total_assets"], first["total_resources"] = self.totals(opening)
                first["balance_residual"] = first["total_assets"] - first["total_resources"]
                values = hybrid.annual_table(h, plan, engine_values, first, tpl.template_id, self.stock_lines())
                extra.update(quarter_values=engine_values[plan.quarters], quarter_assumptions=qvals,
                             engine_values=engine_values)
            else:
                memo = self.opening_memo(h, scenario)
                memo["one_offs"] = oneoff.forecast_amounts(self.effective_one_offs(h, scenario), periods,
                                                           scenario.frequency)
                engine_values = values = self.compute(scenario, periods, a, opening, memo)
        except CalcError as e:
            issues.append(e.issue)
            return done(None, resolved, issues)
        issues += self.output_checks(engine_values, list(engine_values.columns[1:]), h.policy.abs_tolerance, memo)
        issues += self.ytd_checks(h, scenario, values)
        issues += self.one_off_issues(h, scenario, periods)
        return done(values, resolved, issues, engine_values)

    def quarter_memo(self, h, s: Scenario, plan) -> dict:
        """Bộ nhớ đầu vòng tính quý: số dư 3 quý thực tế trước quý gốc (gốc YoY của các quý dự phóng)."""
        memo = self.opening_memo(h, s, plan.q_origin)
        memo["one_offs"] = {}  # khoản bất thường nằm trong quý thực tế; quý dự phóng dùng chuỗi đã làm sạch
        have = set(h.balances_by(FREQ_QUARTER).index)
        for q in [shift_label(plan.q_origin, -i) for i in range(1, 4)]:
            if q in have:
                memo["bs_by_period"][q] = self.opening_balances(h, q)[0]
        return memo

    # ------------------------------------------------------------ vòng kỳ
    def compute(self, s: Scenario, periods: list[str], a: dict[str, list], opening: dict[str, float],
                memo: dict, origin_label: str | None = None) -> pd.DataFrame:
        tpl = self.template
        methods = {b: method_class(tpl.kind_of(s.methods[b]))(tpl.methods_by_id[s.methods[b]]) for b in self.BLOCK_ORDER}
        for b, m in methods.items():
            if m.block != b:
                raise ValueError(f"Kind {m.kind} thuộc block {m.block}, không dùng được cho {b}")
        owners = self.bs_lines.set_index("line_id")["owner"]
        sides = self.bs_lines.set_index("line_id")["side"]
        bal = dict(opening)
        state = dict(memo)
        first = dict(opening)
        first["total_assets"], first["total_resources"] = self.totals(opening)
        first["balance_residual"] = first["total_assets"] - first["total_resources"]
        origin_label = origin_label or s.origin
        cols = {origin_label: first}
        state["bs_by_period"] = by_period = dict(state.get("bs_by_period", {}))  # số dư theo kỳ (gốc YoY)
        by_period[origin_label] = dict(opening)
        state["other_income_base"] = hist = dict(state["other_income_base"])

        for t, p in enumerate(periods):
            v: dict[str, float] = {}
            new: dict[str, float] = {line: bal[line] for line, o in owners.items() if o == "carry"}
            ctx = PeriodContext(p, t, period_days(p), {k: x[t] for k, x in a.items()}, v, bal, new, state, {},
                                extra={"sides": sides})
            for b in ("loans", "funding", "liquidity", "securities", "asset_quality", "other_bs", "nii",
                      "non_interest"):
                methods[b].compute(ctx)
            v["toi"] = v["nii"] + v["net_fee_income"] + v["other_non_interest_income"]
            methods["opex"].compute(ctx)
            v["ppop"] = v["toi"] + v["opex"]
            v["pbt"] = v["ppop"] + v["provision_expense"]
            methods["tax_equity"].compute(ctx)
            methods["balancing"].compute(ctx)

            missing = [l for l in owners.index if l not in new]
            if missing:
                ctx.fail("unowned_line", None, ",".join(missing), f"Dòng BS chưa có method ghi: {missing}")
            v.update(new)
            v["total_assets"], v["total_resources"] = self.totals(new)
            v["balance_residual"] = v["total_assets"] - v["total_resources"]
            cols[p] = v
            by_period[p] = dict(new)
            bal = new
            for line, tr in ctx.traces.items():
                self.traces[(line, p)] = tr
            if freq_of(p) == FREQ_QUARTER and is_year_end(p):  # năm đủ 4 quý: gốc YoY cho năm chạy theo năm sau
                y = str(fiscal_year(p))
                by_period[y] = dict(new)
                qs = year_quarters(y)
                if all(q in hist for q in qs):
                    hist[y] = sum(hist[q] for q in qs)
        return pd.DataFrame(cols)

    def opening_memo(self, h, s: Scenario, origin: str | None = None) -> dict:
        """Lợi nhuận chưa phân phối đầu kỳ; thu nhập ngoài lãi khác lịch sử theo kỳ (đã loại khoản bất thường) làm gốc
        tăng trưởng cùng kỳ năm trước; vốn chủ / tổng TS thấp nhất lịch sử (để cảnh báo). origin: kỳ số dư đầu (quý
        công bố mới nhất khi chạy quý; mặc định năm gốc)."""
        origin = origin or s.origin
        freq = freq_of(origin)
        row = h.balances_by(freq).loc[origin]
        re = row.get("BSA90")
        metrics = self.family.metrics(h, origin, freq == FREQ_ANNUAL,
                                      oneoff.history_adjustments(self.effective_one_offs(h, s), self.template,
                                                                 freq == FREQ_ANNUAL))
        other = metrics.series("other_income_amount")[0].dropna()
        eq = self.family.metrics(h, s.origin, True).series("equity_to_assets")[0].dropna()
        return {"bs_by_period": {},
                "retained_earnings": float("nan") if pd.isna(re) else float(re),
                "other_income_base": {k: float(x) for k, x in other.items()},
                "equity_to_assets_min": float(eq.min()) if len(eq) else None}

    # ------------------------------------------------------------ kiểm tra
    def ytd_checks(self, h, s: Scenario, values: pd.DataFrame) -> list[Issue]:
        qs = self.ytd_quarters(h, s)
        if not qs:
            return []
        y = str(int(s.origin) + 1)
        flows = h.flows("IS")
        out = []
        for line, code, name in self.YTD_LINES:
            ytd = flows.loc[qs, code].sum(min_count=1) if code in flows.columns else None
            fy = values.loc[line, y] if y in values.columns else None
            if ytd is None or pd.isna(ytd) or fy is None or ytd <= 0:
                continue
            if fy < ytd:
                out.append(Issue("warning", "REV-10", None, line, y,
                                 f"{name} dự phóng cả năm {y} ({fy / 1e9:,.0f} tỷ) thấp hơn lũy kế thực tế "
                                 f"{qs[0]}–{qs[-1]} ({ytd / 1e9:,.0f} tỷ): xem lại giả định năm."))
        return out

    @staticmethod
    def output_checks(values: pd.DataFrame, periods: list[str], tol: float, memo: dict) -> list[Issue]:
        out = []
        prev_auto = 0.0
        for p in periods:
            c = values[p]
            if abs(c["balance_residual"]) > tol:
                out.append(Issue("error", "unbalanced", None, "balance_residual", p,
                                 f"Bảng cân đối lệch {c['balance_residual']:,.0f} VND"))
            for line in NEGATIVE_CHECK:
                if c[line] < -tol:
                    out.append(Issue("error", "BANK-BS-01", None, line, p, f"{line} âm: {c[line] / 1e9:,.0f} tỷ"))
            if c["funding_gap"] > tol:
                out.append(Issue("warning", "BANK-FUND-01", "balancing", "funding_gap", p,
                                 f"Thiếu vốn {c['funding_gap'] / 1e9:,.0f} tỷ: vay liên NH tự động đã chạm trần "
                                 f"({c['interbank_cap'] / 1e9:,.0f} tỷ); tiền gửi & cho vay liên NH dưới sàn. "
                                 "Kịch bản không khả thi nếu không có nguồn vốn khai báo."))
            if c["ldr_cap_uplift"] > tol:
                out.append(Issue("info", "BANK-LIQ-01", "funding", "customer_deposits", p,
                                 f"Dư nợ / tiền gửi chạm trần LDR: tiền gửi KH nâng thêm {c['ldr_cap_uplift'] / 1e9:,.0f} "
                                 "tỷ so với đường tăng trưởng."))
            auto = c["auto_interbank_borrowing"]
            if auto > tol and (prev_auto <= tol or auto > prev_auto * (1 + AUTO_GROWTH_LIMIT)):
                out.append(Issue("warning", "BANK-LIQ-02", "balancing", "auto_interbank_borrowing", p,
                                 f"Vay liên NH tự động {auto / 1e9:,.0f} tỷ"
                                 + (f" (tăng {auto / prev_auto - 1:.0%} so với kỳ trước)" if prev_auto > tol else "")
                                 + ": nguồn vốn không đủ để giữ liên NH (TS) ở mức sàn."))
            prev_auto = auto
            if c["implied_write_off"] < -tol:
                out.append(Issue("warning", "BANK-AQ-01", "asset_quality", "implied_write_off", p,
                                 f"Xoá nợ ngụ ý âm ({c['implied_write_off'] / 1e9:,.0f} tỷ): dự phòng cuối kỳ cao hơn "
                                 "đầu kỳ + trích lập; xem lại chi phí tín dụng hoặc tỷ lệ bao phủ."))
            eq_min = memo.get("equity_to_assets_min")
            ta = c["total_assets"]
            ratio = (c["equity_parent"] + c["minority_interest"] + c["minority_interest_pre2015"]) / ta if ta else None
            if eq_min is not None and ratio is not None and ratio < eq_min - 1e-9:
                out.append(Issue("warning", "BANK-CAP-01", "tax_equity", "equity_parent", p,
                                 f"Vốn chủ / tổng TS {ratio:.1%} thấp hơn mức thấp nhất lịch sử {eq_min:.1%}. "
                                 "CAR không có trong dữ liệu nên không kiểm tra được."))
            if c["npat"] < 0:
                out.append(Issue("warning", "negative_npat", None, "npat", p, "LNST âm"))
            if c["equity_parent"] < 0:
                out.append(Issue("warning", "negative_equity", "tax_equity", "equity_parent", p, "Vốn chủ âm"))
        return out

    @staticmethod
    def status(values: pd.DataFrame | None, issues: list[Issue], levels: dict, h, s: Scenario) -> dict:
        errors = [i for i in issues if i.severity == "error"]
        st = {
            "calculated": values is not None, "hard_errors": len(errors),
            "warnings": sum(i.severity == "warning" for i in issues), "levels": levels,
            "mixed_levels": len(set(levels.values())) > 1,
            "history_point_in_time": h.provenance.get("point_in_time"),
            "latest_published": (h.periods_by(FREQ_QUARTER) or [None])[-1], "frequency": s.frequency,
            "has_cash_flow": False, "cash_reconciled": None,
        }
        if values is None:
            st.update(balanced=None, feasible=None, funding_gap_max=None, first_gap_period=None, publishable=False,
                      auto_funding_max=None)
            return st
        fc = values.iloc[:, 1:]
        tol = h.policy.abs_tolerance
        gaps = fc.loc["funding_gap"]
        st["balance_max_residual"] = float(fc.loc["balance_residual"].abs().max())
        st["balanced"] = st["balance_max_residual"] <= tol
        st["funding_gap_max"] = float(gaps.max())
        st["first_gap_period"] = next((p for p, g in gaps.items() if g > tol), None)
        st["feasible"] = st["first_gap_period"] is None
        st["auto_funding_max"] = float(fc.loc["auto_interbank_borrowing"].max())
        st["publishable"] = (not errors) and st["balanced"] and st["feasible"]
        return st
