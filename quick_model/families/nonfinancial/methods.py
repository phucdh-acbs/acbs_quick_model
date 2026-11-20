"""Method của nhóm phi tài chính: mỗi kind một class.

L1 (chung mọi DN phi tài chính): tăng trưởng, biên gộp, tỷ lệ CPBH/CPQL, số ngày vốn lưu động, capex theo %
doanh thu, cân nguồn tự động, chính sách ngoài HĐKD, thuế và phân phối.
L2/L3 chung: CPBH/CPQL cố định + biến đổi, vốn lưu động theo mua hàng, roll-forward TSCĐ, lịch vay, lịch phân phối.
L2/L3 riêng ngành (template ngành chọn dùng): sản lượng × giá, chi phí/tấn (sản xuất), số cửa hàng × doanh thu/cửa
hàng (bán lẻ), backlog (xây dựng).

Quy ước dấu giống database: chi phí âm, LCTT theo chiều tiền. Lãi vay tính trên dư nợ đầu kỳ.
"""

from __future__ import annotations

import pandas as pd

from quick_model.methods import primitives as P
from quick_model.methods.base import Driver, Method, PeriodContext, register
from quick_model.periods import fiscal_year, is_year_end, matching_prior, year_quarters

Y = P.YEAR_DAYS
OPEN_DEBT = ("open", ((1, "BSA56"), (1, "BSA71")))
OPEN_PPE = ("open", ((1, "BSA29"), (1, "BSA163")))


def _rev(ctx) -> Driver:
    return Driver("revenue", "Doanh thu thuần của kỳ", "VND", ctx.v["net_revenue"], ("line", "net_revenue"))


def _cogs_mag(ctx) -> Driver:
    return Driver("cogs", "Giá vốn của kỳ (độ lớn)", "VND", -ctx.v["cogs"], ("line", "cogs", -1))

# ================================================================ doanh thu


class RevenueMethod(Method):
    block = "revenue"


@register
class GrowthMatchingPeriod(RevenueMethod):
    kind = "growth_matching_period"

    def compute(self, ctx: PeriodContext) -> None:
        base_label = matching_prior(ctx.period)
        base = ctx.revenue_by_period.get(base_label)
        if base is None or base <= 0:
            ctx.fail("CAL-03", "revenue", "net_revenue", f"Không có doanh thu hợp lệ của {base_label} làm gốc tăng trưởng")
        ctx.v["net_revenue"] = P.growth(base, ctx.at["revenue_yoy_growth"])
        # nhãn không gắn kỳ cụ thể: cùng trace dùng để phân rã cả ô thực tế (quick_model/explain.py)
        ctx.trace("net_revenue", "Doanh thu kỳ tương ứng năm trước × (1 + tăng trưởng)", lambda d: d["base"] * (1 + d["g"]),
                  Driver("base", "Doanh thu kỳ tương ứng năm trước", "VND", base,
                         ("line_mp", "net_revenue")),
                  ctx.a("revenue_yoy_growth", "Tăng trưởng so với kỳ tương ứng năm trước", "ratio",
                        ("metric", "revenue_yoy"), key="g"))


@register
class VolumeTimesPrice(RevenueMethod):
    kind = "volume_x_price"

    def compute(self, ctx: PeriodContext) -> None:
        at, v = ctx.at, ctx.v
        vol, asp, other = at["sales_volume"], at["asp"], at["other_revenue"]
        v["sales_volume"], v["asp"] = vol, asp
        v["volume_revenue"] = P.volume_price(vol, asp)
        v["other_revenue"] = other
        v["net_revenue"] = v["volume_revenue"] + other
        ctx.trace("net_revenue", "Sản lượng × giá bán + doanh thu khác", lambda d: d["vol"] * d["asp"] + d["other"],
                  ctx.a("sales_volume", "Sản lượng bán", "tonnes", key="vol"),
                  ctx.a("asp", "Giá bán bình quân", "VND_per_tonne", key="asp"),
                  ctx.a("other_revenue", "Doanh thu ngoài sản lượng", "VND", key="other"))


@register
class StoreNetwork(RevenueMethod):
    """Bán lẻ: số cửa hàng (roll-forward) × doanh thu/cửa hàng/năm quy theo số ngày của kỳ."""

    kind = "units_x_productivity"

    def compute(self, ctx: PeriodContext) -> None:
        at, v, st = ctx.at, ctx.v, ctx.state
        opening = st.get("store_count")
        if opening is None:
            opening = at["stores_opening"]
        close = P.rollforward(opening, at["stores_new"], at["stores_closed"])
        if close < 0:
            ctx.fail("IN-07", "revenue", "stores_closed", f"Số cửa hàng cuối kỳ âm ({close:,.0f})")
        avg = P.average_balance(opening, close)
        store_rev = avg * at["revenue_per_store"] * ctx.days / P.YEAR_DAYS
        st["store_count"] = close
        v.update(store_count=close, avg_store_count=avg, revenue_per_store=at["revenue_per_store"],
                 store_revenue=store_rev, other_revenue=at["retail_other_revenue"])
        v["net_revenue"] = store_rev + at["retail_other_revenue"]
        ctx.trace("net_revenue", "Số cửa hàng bình quân × doanh thu/cửa hàng/năm × ngày/365 + doanh thu khác",
                  lambda d: d["stores"] * d["rps"] * d["days"] / Y + d["other"],
                  Driver("stores", "Số cửa hàng bình quân", "stores", avg),
                  ctx.a("revenue_per_store", "Doanh thu / cửa hàng / năm", "VND_per_store_year", key="rps"),
                  ctx.days_driver(), ctx.a("retail_other_revenue", "Doanh thu ngoài chuỗi", "VND", key="other"))


@register
class BacklogRollforward(RevenueMethod):
    """Xây dựng: ghi nhận từ backlog đầu kỳ (tỷ lệ/năm) và hợp đồng ký mới (tỷ lệ ngay trong kỳ)."""

    kind = "backlog_rollforward"

    def compute(self, ctx: PeriodContext) -> None:
        at, v, st = ctx.at, ctx.v, ctx.state
        opening = st.get("backlog")
        if opening is None:
            opening = at["backlog_opening"]
        burn = min(1.0, at["backlog_burn_rate"] * ctx.days / P.YEAR_DAYS)
        rev = opening * burn + at["new_contracts"] * at["new_contract_burn"]
        close = P.rollforward(opening, at["new_contracts"], rev)
        st["backlog"] = close
        v.update(backlog_open=opening, new_contracts=at["new_contracts"], backlog_revenue=rev, backlog_close=close,
                 other_revenue=at["construction_other_revenue"])
        v["net_revenue"] = rev + at["construction_other_revenue"]
        ctx.trace("net_revenue", "Backlog đầu kỳ × tỷ lệ ghi nhận × ngày/365 + HĐ mới × tỷ lệ kỳ đầu + doanh thu khác",
                  lambda d: d["backlog"] * min(1.0, d["burn"] * d["days"] / Y) + d["new"] * d["new_burn"] + d["other"],
                  Driver("backlog", "Backlog đầu kỳ", "VND", opening),
                  ctx.a("backlog_burn_rate", "Tỷ lệ ghi nhận backlog / năm", "ratio_annual", key="burn"),
                  ctx.days_driver(), ctx.a("new_contracts", "Hợp đồng ký mới", "VND", key="new"),
                  ctx.a("new_contract_burn", "Tỷ lệ ghi nhận HĐ mới trong kỳ", "ratio", key="new_burn"),
                  ctx.a("construction_other_revenue", "Doanh thu ngoài xây lắp", "VND", key="other"))


# ================================================================ giá vốn


class CogsMethod(Method):
    block = "cogs"


@register
class GrossMargin(CogsMethod):
    kind = "gross_margin"

    def compute(self, ctx: PeriodContext) -> None:
        ctx.v["cogs"] = P.margin_cost(ctx.v["net_revenue"], ctx.at["gross_margin"])
        ctx.trace("cogs", "−(1 − biên gộp) × doanh thu", lambda d: -(1 - d["gm"]) * d["revenue"], _rev(ctx),
                  ctx.a("gross_margin", "Biên lợi nhuận gộp", "ratio", ("metric", "gross_margin"), key="gm"))


@register
class UnitCostPlusFixed(CogsMethod):
    kind = "unit_cost_plus_fixed"

    def compute(self, ctx: PeriodContext) -> None:
        at, v = ctx.at, ctx.v
        vol = v["sales_volume"]
        variable = at["variable_cost_per_tonne"] * vol
        other_cost = (1 - at["other_revenue_gross_margin"]) * v.get("other_revenue", 0.0)
        v["cogs"] = -(variable + at["fixed_production_cost"] + other_cost)
        v["unit_cogs"] = (variable + at["fixed_production_cost"]) / vol if vol > 0 else float("nan")
        ctx.trace("cogs", "−(chi phí biến đổi/tấn × sản lượng + cố định + (1 − biên khác) × doanh thu khác)",
                  lambda d: -(d["vc"] * d["vol"] + d["fixed"] + (1 - d["ogm"]) * d["other"]),
                  Driver("vol", "Sản lượng bán", "tonnes", vol),
                  ctx.a("variable_cost_per_tonne", "Chi phí biến đổi / tấn", "VND_per_tonne", key="vc"),
                  ctx.a("fixed_production_cost", "Chi phí sản xuất cố định", "VND", key="fixed"),
                  Driver("other", "Doanh thu ngoài sản lượng", "VND", v.get("other_revenue", 0.0)),
                  ctx.a("other_revenue_gross_margin", "Biên gộp doanh thu khác", "ratio", key="ogm"))


# ================================================================ CPBH, CPQL


class SgaMethod(Method):
    block = "sga"


@register
class ExpenseCostStructure(SgaMethod):
    """L1 (owner duyệt 2026-11-17, thay tỷ lệ cố định trên doanh thu): chi phí = cố định + tỷ lệ biến đổi × doanh thu.

    Default ước lượng bằng hồi quy chi phí theo doanh thu trên lịch sử (quick_model/engine/defaults.py), nên doanh thu
    tăng thì biên mở rộng và ngược lại (đòn bẩy hoạt động). So với kỳ thực tế: phần cố định coi như không đổi, tỷ lệ
    biến đổi thực tế ngụ ý = (chi phí − cố định) / doanh thu."""

    kind = "expense_cost_structure"

    def compute(self, ctx: PeriodContext) -> None:
        rev = ctx.v["net_revenue"]
        for line, ratio_id, fixed_id, label in (("selling_expense", "selling_expense_ratio", "selling_fixed_cost", "CPBH"),
                                                ("admin_expense", "admin_expense_ratio", "admin_fixed_cost", "CPQL")):
            fixed = ctx.at[fixed_id]
            ctx.v[line] = -P.fixed_plus_variable(ctx.at[ratio_id], rev, fixed)
            ctx.trace(line, f"−({label} cố định + tỷ lệ biến đổi × doanh thu)",
                      lambda d: -(d["fixed"] + d["ratio"] * d["revenue"]), _rev(ctx),
                      ctx.a(fixed_id, f"{label} cố định", "VND", ("const", fixed), key="fixed"),
                      ctx.a(ratio_id, f"{label} biến đổi / doanh thu", "ratio", ("residual_ratio", line, fixed),
                            key="ratio"))


@register
class ExpenseFixedVariable(SgaMethod):
    kind = "expense_fixed_variable"

    def compute(self, ctx: PeriodContext) -> None:
        at, v = ctx.at, ctx.v
        rev = v["net_revenue"]
        per_tonne = at.get("selling_cost_per_tonne")  # chỉ có ở template sản xuất (tùy chọn)
        vol_cost = per_tonne * v["sales_volume"] if per_tonne is not None else 0.0
        v["selling_expense"] = -(at["selling_variable_ratio"] * rev + vol_cost + at["selling_fixed"])
        v["admin_expense"] = -P.fixed_plus_variable(at["admin_variable_ratio"], rev, at["admin_fixed"])
        ctx.trace("selling_expense", "−(tỷ lệ × doanh thu + chi phí/tấn × sản lượng + cố định)",
                  lambda d: -(d["ratio"] * d["revenue"] + d["vol_cost"] + d["fixed"]), _rev(ctx),
                  ctx.a("selling_variable_ratio", "CPBH biến đổi / doanh thu", "ratio", key="ratio"),
                  Driver("vol_cost", "CPBH theo sản lượng", "VND", vol_cost),
                  ctx.a("selling_fixed", "CPBH cố định", "VND", key="fixed"))
        ctx.trace("admin_expense", "−(tỷ lệ × doanh thu + cố định)", lambda d: -(d["ratio"] * d["revenue"] + d["fixed"]),
                  _rev(ctx), ctx.a("admin_variable_ratio", "CPQL biến đổi / doanh thu", "ratio", key="ratio"),
                  ctx.a("admin_fixed", "CPQL cố định", "VND", key="fixed"))


# ================================================================ TSCĐ


class PpeMethod(Method):
    block = "ppe"


@register
class CapexIntensity(PpeMethod):
    kind = "capex_intensity_depreciation_rate"

    def compute(self, ctx: PeriodContext) -> None:
        at, v, bal, new = ctx.at, ctx.v, ctx.bal, ctx.new
        fa, cip = bal["fixed_assets"], bal["cip"]
        dep = P.rate_on_balance(max(0.0, fa + cip), at["depreciation_rate"], ctx.days)
        capex = P.ratio_of(v["net_revenue"], at["capex_to_revenue"])
        new["fixed_assets"] = fa + capex - dep
        new["cip"] = cip
        v["cip_transfer"] = 0.0
        v["gross_cost"] = float("nan")
        v["depreciation"] = dep
        v["capex"] = -capex
        ctx.trace("depreciation", "Tỷ lệ khấu hao năm × (TSCĐ + dở dang) đầu kỳ × ngày / 365",
                  lambda d: d["rate"] * max(0.0, d["base"]) * d["days"] / Y,
                  ctx.a("depreciation_rate", "Tỷ lệ khấu hao năm", "ratio_annual", ("metric", "depreciation_rate"),
                        key="rate"),
                  Driver("base", "TSCĐ + dở dang dài hạn đầu kỳ", "VND", fa + cip, OPEN_PPE), ctx.days_driver())
        ctx.trace("capex", "−Capex/doanh thu × doanh thu", lambda d: -d["ratio"] * d["revenue"], _rev(ctx),
                  ctx.a("capex_to_revenue", "Capex / doanh thu", "ratio", ("metric", "capex_to_revenue"), key="ratio"))


@register
class CipRollforward(PpeMethod):
    kind = "cip_rollforward_useful_life"

    def compute(self, ctx: PeriodContext) -> None:
        at, v, bal, new, state = ctx.at, ctx.v, ctx.bal, ctx.new, ctx.state
        fa, cip = bal["fixed_assets"], bal["cip"]
        gross = state.get("gross_cost")
        if gross is None or pd.isna(gross):
            ctx.fail("CAL-02", "ppe", "gross_cost", "Thiếu nguyên giá TSCĐ kỳ gốc (BSA31 + BSA34 + BSA37) để tính khấu hao L2")
        capex = at["capex_amount"]
        transfer = at["cip_transfer_rate"] * (cip + capex)
        dep = P.straight_line_depreciation(gross, at["useful_life_years"], ctx.days)
        available = fa + transfer
        if dep > available + 1e-6:
            ctx.fail("CAL-02", "ppe", "depreciation",
                     f"Khấu hao {dep / 1e9:,.0f} tỷ vượt TSCĐ ròng sẵn có {available / 1e9:,.0f} tỷ")
        new["fixed_assets"] = available - dep
        new["cip"] = cip + capex - transfer
        state["gross_cost"] = gross + transfer
        v["cip_transfer"] = transfer
        v["gross_cost"] = state["gross_cost"]
        v["depreciation"] = dep
        v["capex"] = -capex
        ctx.trace("depreciation", "Nguyên giá đầu kỳ / thời gian sử dụng × ngày / 365",
                  lambda d: d["gross"] / d["life"] * d["days"] / Y, Driver("gross", "Nguyên giá TSCĐ đầu kỳ", "VND", gross),
                  ctx.a("useful_life_years", "Thời gian sử dụng bình quân", "years", ("metric", "useful_life_years"),
                        key="life"), ctx.days_driver())
        ctx.trace("capex", "−Capex khai báo", lambda d: -d["capex"],
                  ctx.a("capex_amount", "Capex trong kỳ", "VND", ("metric", "capex_amount"), key="capex"))


# ================================================================ vốn lưu động


class WorkingCapitalMethod(Method):
    block = "working_capital"

    def _receivables_inventory(self, ctx: PeriodContext) -> tuple[float, float]:
        rev, cogs_mag = ctx.v["net_revenue"], -ctx.v["cogs"]
        ctx.new["trade_receivables"] = P.days_balance(rev, ctx.at["dso_days"], ctx.days)
        ctx.new["inventories"] = P.days_balance(cogs_mag, ctx.at["dio_days"], ctx.days)
        ctx.trace("trade_receivables", "Doanh thu × số ngày phải thu / số ngày của kỳ",
                  lambda d: d["revenue"] * d["dso"] / d["days"], _rev(ctx),
                  ctx.a("dso_days", "Số ngày phải thu", "days", ("metric", "dso_days"), key="dso"), ctx.days_driver())
        ctx.trace("inventories", "Giá vốn × số ngày tồn kho / số ngày của kỳ",
                  lambda d: d["cogs"] * d["dio"] / d["days"], _cogs_mag(ctx),
                  ctx.a("dio_days", "Số ngày tồn kho", "days", ("metric", "dio_days"), key="dio"), ctx.days_driver())
        return rev, cogs_mag


@register
class DaysCogsProxy(WorkingCapitalMethod):
    kind = "wc_days_cogs_proxy"

    def compute(self, ctx: PeriodContext) -> None:
        _, cogs_mag = self._receivables_inventory(ctx)
        ctx.v["purchases"] = float("nan")
        ctx.new["trade_payables"] = P.days_balance(cogs_mag, ctx.at["dpo_days"], ctx.days)
        ctx.trace("trade_payables", "Giá vốn × số ngày phải trả / số ngày của kỳ (xấp xỉ theo giá vốn)",
                  lambda d: d["cogs"] * d["dpo"] / d["days"], _cogs_mag(ctx),
                  ctx.a("dpo_days", "Số ngày phải trả (theo giá vốn)", "days", ("metric", "dpo_days"), key="dpo"),
                  ctx.days_driver())
        ctx.new["prepayments_to_suppliers"] = ctx.bal["prepayments_to_suppliers"]
        ctx.new["advances_from_customers"] = ctx.bal["advances_from_customers"]


@register
class DaysPurchases(WorkingCapitalMethod):
    kind = "wc_days_purchases"

    def compute(self, ctx: PeriodContext) -> None:
        rev, cogs_mag = self._receivables_inventory(ctx)
        at, new = ctx.at, ctx.new
        purchases = cogs_mag + (new["inventories"] - ctx.bal["inventories"]) - at["noncash_cogs_share"] * ctx.v["depreciation"]
        if purchases <= 0:
            ctx.fail("CAL-01", "working_capital", "purchases",
                     f"Mua hàng ước tính {purchases / 1e9:,.0f} tỷ ≤ 0: không tính được số ngày phải trả")
        ctx.v["purchases"] = purchases
        new["trade_payables"] = P.days_balance(purchases, at["dpo_purchases_days"], ctx.days)
        ctx.trace("trade_payables", "Mua hàng × số ngày phải trả / số ngày của kỳ",
                  lambda d: d["purchases"] * d["dpo"] / d["days"], Driver("purchases", "Mua hàng ước tính", "VND", purchases),
                  ctx.a("dpo_purchases_days", "Số ngày phải trả (theo mua hàng)", "days",
                        ("metric", "dpo_purchases_days"), key="dpo"), ctx.days_driver())
        new["prepayments_to_suppliers"] = P.days_balance(purchases, at["prepayment_days"], ctx.days)
        new["advances_from_customers"] = P.days_balance(rev, at["customer_advance_days"], ctx.days)


# ================================================================ nợ vay (hai pha: lãi đầu kỳ, rồi tài trợ)


class DebtMethod(Method):
    """Pha 1 `compute`: lãi vay trên dư nợ đầu kỳ. Pha 2 `finance`: dòng vay/trả sau khi biết tiền trước tài trợ."""

    block = "debt"

    def finance(self, ctx: PeriodContext, cash_pre: float, assets_ex_cash: float) -> None:
        raise NotImplementedError

    @staticmethod
    def _no_auto(ctx: PeriodContext) -> None:
        ctx.v.update(auto_draw=0.0, auto_repay=0.0, auto_outstanding=0.0, debt_cap=float("nan"))


@register
class DeclaredRevolver(DebtMethod):
    """L1: dư nợ hiện hữu giữ nguyên; vay/trả tự động theo tiền tối thiểu, trong trần nợ/tổng tài sản."""

    kind = "declared_revolver"

    def compute(self, ctx: PeriodContext) -> None:
        st, lt = ctx.bal["st_borrowings"], ctx.bal["lt_borrowings"]
        ctx.v["interest_expense"] = -P.rate_on_balance(st + lt, ctx.at["interest_rate"], ctx.days)
        ctx.trace("interest_expense", "−Lãi suất vay năm × tổng vay đầu kỳ × ngày / 365",
                  lambda d: -d["rate"] * d["debt"] * d["days"] / Y,
                  ctx.a("interest_rate", "Lãi suất vay bình quân năm", "ratio_annual", ("metric", "interest_rate"),
                        key="rate"),
                  Driver("debt", "Tổng vay đầu kỳ", "VND", st + lt, OPEN_DEBT), ctx.days_driver())

    def finance(self, ctx: PeriodContext, cash_pre: float, assets_ex_cash: float) -> None:
        """Ràng buộc cuối năm (owner 2026-11-18): tiền tối thiểu = tỷ lệ × doanh thu CẢ NĂM và trần nợ/tổng tài sản chỉ
        áp ở kỳ cuối năm (năm, hoặc Q4). Quý 1–3: chỉ vay tự động khi tiền trước tài trợ âm (về 0), không trả nợ."""
        at, v, state = ctx.at, ctx.v, ctx.state
        st, lt = ctx.bal["st_borrowings"], ctx.bal["lt_borrowings"]
        year_end = is_year_end(ctx.period)
        year_rev = _year_revenue(ctx) if year_end else 0.0
        min_cash = max(0.0, at["min_cash_ratio"] * year_rev) if year_end else 0.0
        cap = at["max_debt_to_assets"] if year_end else 1.0
        out_st, out_lt = state.get("auto_st", 0.0), state.get("auto_lt", 0.0)
        draw = st_draw = lt_draw = rep_st = rep_lt = 0.0
        assets_pre = assets_ex_cash + cash_pre
        headroom = max(0.0, (cap * assets_pre - (st + lt)) / (1 - cap)) if cap < 1 else float("inf")
        if cash_pre < min_cash:
            need = min_cash - cash_pre
            draw = min(need, headroom)
            st_draw = draw * at["auto_st_share"]
            lt_draw = draw - st_draw
        elif year_end:
            surplus = cash_pre - min_cash
            rep_st = min(surplus, out_st)
            rep_lt = min(surplus - rep_st, out_lt)
        ctx.new["st_borrowings"] = st + st_draw - rep_st
        ctx.new["lt_borrowings"] = lt + lt_draw - rep_lt
        state["auto_st"], state["auto_lt"] = out_st + st_draw - rep_st, out_lt + lt_draw - rep_lt
        v["borrowings_drawn"] = draw
        v["borrowings_repaid"] = -(rep_st + rep_lt)
        v["lt_borrowing_draw"] = lt_draw
        if year_end:
            ctx.trace("borrowings_drawn", "Vay tự động = min(tiền tối thiểu − tiền trước tài trợ, khoảng trống trần nợ)",
                      lambda d: max(0.0, min(d["min_cash"] - d["cash_pre"], d["headroom"])),
                      Driver("min_cash", "Tiền tối thiểu (tỷ lệ × doanh thu cả năm)", "VND", min_cash),
                      Driver("cash_pre", "Tiền trước tài trợ (đầu kỳ + CFO + CFI + phát hành + cổ tức)", "VND", cash_pre),
                      Driver("headroom", "Khoảng trống đến trần nợ/tổng tài sản", "VND",
                             headroom if cap < 1 else draw))
        else:
            ctx.trace("borrowings_drawn", "Quý giữa năm: vay tự động chỉ khi tiền trước tài trợ âm (ràng buộc kiểm ở Q4)",
                      lambda d: max(0.0, -d["cash_pre"]),
                      Driver("cash_pre", "Tiền trước tài trợ (đầu kỳ + CFO + CFI + phát hành + cổ tức)", "VND", cash_pre))
        v.update(min_cash_level=min_cash, auto_draw=draw, auto_repay=rep_st + rep_lt,
                 auto_outstanding=state["auto_st"] + state["auto_lt"],
                 debt_cap=cap * (assets_ex_cash + cash_pre + draw) if year_end else float("nan"))


def _year_revenue(ctx: PeriodContext) -> float:
    """Doanh thu cả năm của kỳ cuối năm: kỳ năm = doanh thu kỳ; Q4 = tổng 4 quý (quý thực tế + dự phóng)."""
    if len(ctx.period) == 4:
        return ctx.v["net_revenue"]
    vals = [ctx.revenue_by_period.get(q) for q in year_quarters(fiscal_year(ctx.period))]
    return sum(vals) if all(x is not None for x in vals) else ctx.v["net_revenue"] * 4


@register
class FacilitySchedule(DebtMethod):
    """L2: lịch vay do analyst khai báo; thiếu tiền thì báo funding gap để analyst cân nguồn."""

    kind = "debt_facility_schedule"

    def compute(self, ctx: PeriodContext) -> None:
        st, lt = ctx.bal["st_borrowings"], ctx.bal["lt_borrowings"]
        ctx.v["interest_expense"] = -(ctx.at["st_interest_rate"] * st + ctx.at["lt_interest_rate"] * lt) * ctx.days / 365
        ctx.trace("interest_expense", "−(lãi suất NH × vay NH đầu kỳ + lãi suất DH × vay DH đầu kỳ) × ngày / 365",
                  lambda d: -(d["r_st"] * d["st"] + d["r_lt"] * d["lt"]) * d["days"] / 365,
                  ctx.a("st_interest_rate", "Lãi suất vay ngắn hạn", "ratio_annual", key="r_st"),
                  Driver("st", "Vay ngắn hạn đầu kỳ", "VND", st, ("open", ((1, "BSA56"),))),
                  ctx.a("lt_interest_rate", "Lãi suất vay dài hạn", "ratio_annual", key="r_lt"),
                  Driver("lt", "Vay dài hạn đầu kỳ", "VND", lt, ("open", ((1, "BSA71"),))), ctx.days_driver())

    def finance(self, ctx: PeriodContext, cash_pre: float, assets_ex_cash: float) -> None:
        at, v = ctx.at, ctx.v
        st, lt = ctx.bal["st_borrowings"], ctx.bal["lt_borrowings"]
        chg, draw, repay = at["st_net_change"], at["lt_drawdown"], at["lt_repayment"]
        if st + chg < -1e-6:
            ctx.fail("IN-04", "debt", "st_net_change",
                     f"Vay ngắn hạn âm: đầu kỳ {st / 1e9:,.0f} tỷ, thay đổi {chg / 1e9:,.0f} tỷ")
        if repay > lt + draw + 1e-6:
            ctx.fail("IN-03", "debt", "lt_repayment",
                     f"Trả gốc {repay / 1e9:,.0f} tỷ vượt dư nợ dài hạn {(lt + draw) / 1e9:,.0f} tỷ")
        ctx.new["st_borrowings"], ctx.new["lt_borrowings"] = st + chg, lt + draw - repay
        v["borrowings_drawn"] = max(chg, 0.0) + draw
        v["borrowings_repaid"] = min(chg, 0.0) - repay
        v["lt_borrowing_draw"] = draw
        v["min_cash_level"] = at["min_cash"]
        self._no_auto(ctx)


# ================================================================ ngoài HĐKD


def _one_off_tracer(ctx: PeriodContext, one_offs: dict):
    """ctx.trace có thêm yếu tố 'khoản bất thường đã xác nhận' cho dòng có khoản đó (kỳ so sánh: 0)."""
    def trace(line, formula, fn, *drivers):
        items = one_offs.get(line)
        if not items:
            return ctx.trace(line, formula, fn, *drivers)
        k = sum(o.amount for o in items)
        quarters = ", ".join(o.quarter for o in items)
        d = Driver("one_off", f"Khoản bất thường đã ghi nhận ({quarters})", "VND", k, ("one_off", line))
        ctx.trace(line, f"{formula} + khoản bất thường đã ghi nhận", lambda x: fn(x) + x["one_off"], *drivers, d)
    return trace


@register
class NonOperatingPolicies(Method):
    kind = "non_operating_policies"
    block = "non_operating"

    def compute(self, ctx: PeriodContext) -> None:
        at, v = ctx.at, ctx.v
        v["financial_income"] = at["financial_income_run_rate"] * ctx.days / 365  # v2.5: theo mức, không theo số dư
        v["other_financial_expense"] = -at["other_financial_expense_ratio"] * v["net_revenue"]
        v["jv_associates"] = at["jv_associates_run_rate"] * ctx.days / 365  # v2.6: theo mức
        v["other_profit"] = at["other_profit"]
        one_offs = ctx.state.get("one_offs", {}).get(ctx.period, {})
        for line, items in one_offs.items():  # khoản bất thường analyst đã xác nhận (quick_model/oneoff.py)
            v[line] += sum(o.amount for o in items)
        v["financial_expense"] = v["interest_expense"] + v["other_financial_expense"]
        trace = _one_off_tracer(ctx, one_offs)
        trace("financial_income", "Mức doanh thu tài chính theo năm × ngày / 365",
                  lambda d: d["rate"] * d["days"] / 365,
                  ctx.a("financial_income_run_rate", "Mức doanh thu tài chính theo năm", "VND_per_year",
                        ("metric", "financial_income_run_rate"), key="rate"),
                  ctx.days_driver())
        trace("other_financial_expense", "−Tỷ lệ × doanh thu", lambda d: -d["ratio"] * d["revenue"], _rev(ctx),
                  ctx.a("other_financial_expense_ratio", "Chi phí tài chính ngoài lãi vay / doanh thu", "ratio",
                        ("metric", "other_financial_expense_ratio"), key="ratio"))
        trace("jv_associates", "Mức lãi/lỗ LDLK theo năm × ngày / 365", lambda d: d["rate"] * d["days"] / 365,
                  ctx.a("jv_associates_run_rate", "Mức lãi/lỗ LDLK theo năm", "VND_per_year",
                        ("metric", "jv_associates_run_rate"), key="rate"),
                  ctx.days_driver())
        trace("other_profit", "Số khai báo", lambda d: d["x"],
                  ctx.a("other_profit", "Lợi nhuận khác", "VND", ("line", "other_profit"), key="x"))


# ================================================================ thuế, vốn chủ, phân phối


class TaxEquityMethod(Method):
    block = "tax_equity"

    def compute(self, ctx: PeriodContext) -> None:
        at, v = ctx.at, ctx.v
        v["income_tax"] = -at["effective_tax_rate"] * v["pbt"] if v["pbt"] > 0 else 0.0
        v["npat"] = v["pbt"] + v["income_tax"]
        v["npat_minority"] = at["minority_share"] * v["npat"]
        v["npat_parent"] = v["npat"] - v["npat_minority"]
        ctx.trace("income_tax", "−Thuế suất hiệu dụng × LNTT (khi LNTT > 0; lỗ thì 0)",
                  lambda d: -d["rate"] * d["pbt"] if d["pbt"] > 0 else 0.0,
                  Driver("pbt", "Lợi nhuận trước thuế", "VND", v["pbt"], ("line", "pbt")),
                  ctx.a("effective_tax_rate", "Thuế suất hiệu dụng", "ratio", ("metric", "effective_tax_rate"), key="rate"))
        ctx.trace("npat_minority", "Tỷ lệ cổ đông không kiểm soát × LNST", lambda d: d["share"] * d["npat"],
                  Driver("npat", "Lợi nhuận sau thuế", "VND", v["npat"], ("line", "npat")),
                  ctx.a("minority_share", "Tỷ lệ LNST của cổ đông không kiểm soát", "ratio", ("metric", "minority_share"),
                        key="share"))
        self.distribute(ctx)

    def distribute(self, ctx: PeriodContext) -> None:
        raise NotImplementedError

    @staticmethod
    def _equity(ctx: PeriodContext, div: float, appropriation: float) -> None:
        v, state, bal = ctx.v, ctx.state, ctx.bal
        parent, issue_cash = v["npat_parent"], ctx.at["share_issue_proceeds"]
        re_open = state.get("retained_earnings", float("nan"))
        v["retained_earnings_available"] = re_open + parent - appropriation
        state["retained_earnings"] = v["retained_earnings_available"] + div
        v["welfare_appropriation"] = appropriation
        v["dividends_paid"] = div
        v["share_issue"] = issue_cash
        ctx.new["equity_parent"] = bal["equity_parent"] + parent + issue_cash + div - appropriation
        ctx.new["minority_interest"] = bal["minority_interest"] + v["npat_minority"]


@register
class TaxPayoutPolicies(TaxEquityMethod):
    kind = "tax_payout_policies"

    def distribute(self, ctx: PeriodContext) -> None:
        parent = ctx.v["npat_parent"]
        div = -ctx.at["cash_dividend_payout"] * parent if parent > 0 else 0.0
        ctx.new["bonus_welfare_fund"] = ctx.bal["bonus_welfare_fund"]
        ctx.v["cf_other_operating"] = 0.0
        self._equity(ctx, div, 0.0)


@register
class DistributionSchedule(TaxEquityMethod):
    kind = "distribution_schedule"

    def distribute(self, ctx: PeriodContext) -> None:
        at, v = ctx.at, ctx.v
        parent = v["npat_parent"]
        div = -at["cash_dividend_amount"]
        appropriation = at["welfare_fund_rate"] * parent if parent > 0 else 0.0
        usage = at["welfare_fund_usage"]
        fund_avail = ctx.bal["bonus_welfare_fund"] + appropriation
        if usage > fund_avail + 1e-6:
            ctx.fail("IN-05", "tax_equity", "welfare_fund_usage",
                     f"Chi quỹ {usage / 1e9:,.0f} tỷ vượt số dư quỹ {fund_avail / 1e9:,.0f} tỷ")
        ctx.new["bonus_welfare_fund"] = fund_avail - usage
        v["cf_other_operating"] = -usage
        self._equity(ctx, div, appropriation)
