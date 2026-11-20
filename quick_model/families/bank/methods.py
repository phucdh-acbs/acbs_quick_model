"""Method L1 nhóm ngân hàng: mỗi kind một class, đăng ký theo kind (quick_model.methods.base.register).

Mọi method đọc/ghi qua PeriodContext như nhóm phi tài chính; mỗi dòng dự phóng ghi `ctx.trace` với yếu tố có `hist`
để giải thích được cả ô thực tế. Kiểu hist mới của ngân hàng: ("avg", mã BS) = bình quân đầu–cuối kỳ (đăng ký ở
quick_model.families.bank.__init__ qua explain.register_hist). Thuế và cổ đông không kiểm soát dùng lại
TaxEquityMethod của nhóm phi tài chính.
"""

from __future__ import annotations

from quick_model.families.bank.ratios import IBL_LINES, IEA_LINES
from quick_model.families.nonfinancial.methods import TaxEquityMethod
from quick_model.methods.base import Driver, Method, PeriodContext, register
from quick_model.periods import is_year_end

Y = 365.0
LOANS = ((1, "BSB104"),)
DEPOSITS = ((1, "BSB113"),)
BALANCING_LINES = ("interbank_assets", "interbank_liabilities")
IEA_CODES = {"sbv_deposits": "BSB97", "interbank_assets": "BSB98", "trading_securities": "BSB99",
             "gross_loans": "BSB104", "purchased_debt": "BSB261", "investment_securities": "BSB106"}
IBL_CODES = {"gov_sbv_debt": "BSB111", "interbank_liabilities": "BSB112", "customer_deposits": "BSB113",
             "entrusted_funds": "BSB115", "valuable_papers": "BSB116"}


def _avg(ctx: PeriodContext, line: str) -> float:
    """Bình quân đầu–cuối kỳ (quý: TB 2 quý liền nhau)."""
    return (ctx.bal[line] + ctx.new[line]) / 2


def _prior(ctx: PeriodContext, line: str) -> float:
    """Số dư cùng kỳ năm trước (năm: cuối năm trước; quý: cùng quý năm trước) — gốc của tăng trưởng YoY."""
    from quick_model.periods import matching_prior
    row = ctx.state.get("bs_by_period", {}).get(matching_prior(ctx.period))
    if row is None or line not in row:
        ctx.fail("CAL-03", None, line, f"Không có số dư cùng kỳ năm trước ({matching_prior(ctx.period)}) của {line}.")
    return row[line]


# ================================================================ cho vay


@register
class LoanGrowth(Method):
    kind = "bank_loan_growth"
    block = "loans"

    def compute(self, ctx: PeriodContext) -> None:
        g, base, open_ = ctx.at["loan_growth"], _prior(ctx, "gross_loans"), ctx.bal["gross_loans"]
        ctx.new["gross_loans"] = base * (1 + g)
        ctx.v["avg_loans"] = _avg(ctx, "gross_loans")
        ctx.trace("gross_loans", "Dư nợ cùng kỳ năm trước × (1 + tăng trưởng)", lambda d: d["base"] * (1 + d["g"]),
                  Driver("base", "Dư nợ cho vay KH cùng kỳ năm trước", "VND", base, ("close_mp", LOANS)),
                  ctx.a("loan_growth", "Tăng trưởng dư nợ", "ratio", ("metric", "loan_yoy"), key="g"))
        ctx.trace("avg_loans", "(Dư nợ đầu kỳ + cuối kỳ) / 2", lambda d: (d["o"] + d["c"]) / 2,
                  Driver("o", "Dư nợ đầu kỳ", "VND", open_, ("open", LOANS)),
                  Driver("c", "Dư nợ cuối kỳ", "VND", ctx.new["gross_loans"], ("close", LOANS), drill="gross_loans"))


# ================================================================ huy động


@register
class DepositGrowthLdrCap(Method):
    kind = "bank_deposit_growth_ldr_cap"
    block = "funding"

    def compute(self, ctx: PeriodContext) -> None:
        at, v, new = ctx.at, ctx.v, ctx.new
        open_ = _prior(ctx, "customer_deposits")
        by_growth = open_ * (1 + at["deposit_growth"])
        cap, r = at["max_ldr"], at["valuable_papers_ratio"]
        # trần LDR trên tổng huy động = tiền gửi × (1 + tỷ lệ GTCG) + vốn ủy thác (dòng giữ nguyên, đã tính trước)
        entrusted = new.get("entrusted_funds", 0.0) or 0.0
        # trần LDR là ràng buộc cuối năm (owner 2026-11-18): chỉ áp ở kỳ năm hoặc Q4
        need = (max(0.0, (new["gross_loans"] / cap - entrusted) / (1 + r))
                if cap and cap > 0 and is_year_end(ctx.period) else 0.0)
        v["deposits_before_cap"] = by_growth
        v["ldr_cap_uplift"] = max(0.0, need - by_growth)
        new["customer_deposits"] = by_growth + v["ldr_cap_uplift"]
        new["valuable_papers"] = r * new["customer_deposits"]
        ctx.trace("customer_deposits", "Tiền gửi cùng kỳ năm trước × (1 + tăng trưởng) + phần nâng do trần LDR",
                  lambda d: d["base"] * (1 + d["g"]) + d["uplift"],
                  Driver("base", "Tiền gửi KH cùng kỳ năm trước", "VND", open_, ("close_mp", DEPOSITS)),
                  ctx.a("deposit_growth", "Tăng trưởng tiền gửi KH", "ratio", ("metric", "deposit_yoy"), key="g"),
                  Driver("uplift", "Nâng thêm do chạm trần LDR", "VND", v["ldr_cap_uplift"], ("const", 0.0)))
        ctx.trace("valuable_papers", "Tỷ lệ GTCG × tiền gửi KH", lambda d: d["r"] * d["dep"],
                  ctx.a("valuable_papers_ratio", "GTCG / tiền gửi KH", "ratio", ("metric", "valuable_papers_ratio"),
                        key="r"),
                  Driver("dep", "Tiền gửi KH cuối kỳ", "VND", new["customer_deposits"], ("close", DEPOSITS),
                         drill="customer_deposits"))


# ================================================================ dự trữ, tiền mặt


@register
class LiquidityRatios(Method):
    kind = "bank_liquidity_ratios"
    block = "liquidity"

    def compute(self, ctx: PeriodContext) -> None:
        at, new = ctx.at, ctx.new
        base = new["customer_deposits"] + new["valuable_papers"]
        new["sbv_deposits"] = at["sbv_reserve_ratio"] * base
        new["cash"] = at["cash_to_deposits"] * new["customer_deposits"]
        ctx.trace("sbv_deposits", "Tỷ lệ dự trữ × (tiền gửi KH + GTCG)", lambda d: d["r"] * d["base"],
                  ctx.a("sbv_reserve_ratio", "TG NHNN / (tiền gửi KH + GTCG)", "ratio", ("metric", "sbv_reserve_ratio"),
                        key="r"),
                  Driver("base", "Tiền gửi KH + GTCG cuối kỳ", "VND", base, ("close", ((1, "BSB113"), (1, "BSB116")))))
        ctx.trace("cash", "Tỷ lệ × tiền gửi KH", lambda d: d["r"] * d["dep"],
                  ctx.a("cash_to_deposits", "Tiền mặt / tiền gửi KH", "ratio", ("metric", "cash_to_deposits"), key="r"),
                  Driver("dep", "Tiền gửi KH cuối kỳ", "VND", new["customer_deposits"], ("close", DEPOSITS),
                         drill="customer_deposits"))


# ================================================================ chứng khoán đầu tư


@register
class SecuritiesToLoans(Method):
    kind = "bank_securities_to_loans"
    block = "securities"

    def compute(self, ctx: PeriodContext) -> None:
        open_ = _prior(ctx, "investment_securities")
        ctx.new["investment_securities"] = open_ * (1 + ctx.at["inv_securities_growth"])
        ctx.trace("investment_securities", "CK đầu tư cùng kỳ năm trước × (1 + tăng trưởng)",
                  lambda d: d["base"] * (1 + d["g"]),
                  Driver("base", "CK đầu tư cùng kỳ năm trước", "VND", open_, ("close_mp", ((1, "BSB106"),))),
                  ctx.a("inv_securities_growth", "Tăng trưởng CK đầu tư", "ratio", ("metric", "inv_securities_yoy"),
                        key="g"))


# ================================================================ dự phòng


@register
class CreditCostCoverage(Method):
    kind = "bank_credit_cost_coverage"
    block = "asset_quality"

    def compute(self, ctx: PeriodContext) -> None:
        at, v, new, bal = ctx.at, ctx.v, ctx.new, ctx.bal
        v["provision_expense"] = -at["credit_cost"] * new["gross_loans"] * ctx.days / Y
        new["loan_loss_reserve"] = bal["loan_loss_reserve"]  # giản lược: giữ nguyên số dư kỳ gốc
        # dự phòng mang dấu âm: |đầu kỳ| + trích lập − |cuối kỳ| = phần đã sử dụng (xoá nợ ngụ ý)
        v["implied_write_off"] = -bal["loan_loss_reserve"] - v["provision_expense"] + new["loan_loss_reserve"]
        ctx.trace("provision_expense", "−Chi phí tín dụng × dư nợ cuối kỳ × ngày / 365",
                  lambda d: -d["cc"] * d["loans"] * d["days"] / Y,
                  ctx.a("credit_cost", "Chi phí tín dụng", "ratio_annual", ("metric", "credit_cost"), key="cc"),
                  Driver("loans", "Dư nợ gộp cuối kỳ", "VND", new["gross_loans"], ("close", LOANS), drill="gross_loans"),
                  ctx.days_driver())
        ctx.trace("loan_loss_reserve", "Giữ nguyên số dư đầu kỳ", lambda d: d["o"],
                  Driver("o", "Dự phòng cho vay đầu kỳ", "VND", bal["loan_loss_reserve"], ("open", ((1, "BSB105"),))))


# ================================================================ TS Có khác, nợ khác


@register
class OtherBsRatios(Method):
    kind = "bank_other_bs_ratios"
    block = "other_bs"

    def compute(self, ctx: PeriodContext) -> None:
        at, new = ctx.at, ctx.new
        new["other_assets"] = at["other_assets_to_loans"] * new["gross_loans"]
        new["other_liabilities"] = at["other_liab_to_deposits"] * new["customer_deposits"]
        ctx.trace("other_assets", "Tỷ lệ × dư nợ gộp", lambda d: d["r"] * d["loans"],
                  ctx.a("other_assets_to_loans", "TS Có khác / dư nợ", "ratio", ("metric", "other_assets_to_loans"),
                        key="r"),
                  Driver("loans", "Dư nợ gộp cuối kỳ", "VND", new["gross_loans"], ("close", LOANS), drill="gross_loans"))
        ctx.trace("other_liabilities", "Tỷ lệ × tiền gửi KH", lambda d: d["r"] * d["dep"],
                  ctx.a("other_liab_to_deposits", "Nợ khác / tiền gửi KH", "ratio", ("metric", "other_liab_to_deposits"),
                        key="r"),
                  Driver("dep", "Tiền gửi KH cuối kỳ", "VND", new["customer_deposits"], ("close", DEPOSITS),
                         drill="customer_deposits"))


# ================================================================ thu nhập lãi thuần


def _avg_base(ctx: PeriodContext, lines) -> float:
    """Bình quân đầu–cuối kỳ của các dòng có method; dòng cân thanh khoản (liên NH) lấy số dư đầu kỳ (không vòng lặp)."""
    return sum(ctx.bal[l] if l in BALANCING_LINES else _avg(ctx, l) for l in lines)


@register
class YieldCostOfFunds(Method):
    kind = "bank_yield_cof"
    block = "nii"

    def compute(self, ctx: PeriodContext) -> None:
        at, v = ctx.at, ctx.v
        v["avg_iea"], v["avg_ibl"] = _avg_base(ctx, IEA_LINES), _avg_base(ctx, IBL_LINES)
        v["interest_income"] = at["yield_iea"] * v["avg_iea"] * ctx.days / Y
        v["interest_expense"] = -at["cost_of_funds"] * v["avg_ibl"] * ctx.days / Y
        v["nii"] = v["interest_income"] + v["interest_expense"]
        iea = tuple((1, IEA_CODES[l]) for l in IEA_LINES)
        ibl = tuple((1, IBL_CODES[l]) for l in IBL_LINES)
        ctx.trace("interest_income", "Lợi suất × TS sinh lãi bình quân × ngày / 365",
                  lambda d: d["y"] * d["avg"] * d["days"] / Y,
                  ctx.a("yield_iea", "Lợi suất TS sinh lãi", "ratio_annual", ("metric", "yield_iea"), key="y"),
                  Driver("avg", "TS sinh lãi bình quân", "VND", v["avg_iea"], ("avg", iea), drill="avg_iea"),
                  ctx.days_driver())
        ctx.trace("interest_expense", "−Chi phí vốn × nợ chịu lãi bình quân × ngày / 365",
                  lambda d: -d["c"] * d["avg"] * d["days"] / Y,
                  ctx.a("cost_of_funds", "Chi phí vốn", "ratio_annual", ("metric", "cost_of_funds"), key="c"),
                  Driver("avg", "Nợ chịu lãi bình quân", "VND", v["avg_ibl"], ("avg", ibl), drill="avg_ibl"),
                  ctx.days_driver())


# ================================================================ thu nhập ngoài lãi


@register
class NonInterestIncome(Method):
    kind = "bank_non_interest"
    block = "non_interest"

    def compute(self, ctx: PeriodContext) -> None:
        at, v, state = ctx.at, ctx.v, ctx.state
        v["net_fee_income"] = at["fee_to_loans"] * v["avg_loans"] * ctx.days / Y
        hist = state["other_income_base"]  # {kỳ: thu nhập ngoài lãi khác không gồm khoản bất thường}
        rate = at["other_income_run_rate"]
        if rate is None:
            ctx.fail("CAL-03", "non_interest", "other_non_interest_income",
                     "Chưa có mức thu nhập ngoài lãi khác theo năm.")
        core = rate * ctx.days / Y  # owner 2026-11-19: theo mức (bình quân 2 năm), không theo tăng trưởng
        hist[ctx.period] = core
        items = state.get("one_offs", {}).get(ctx.period, {})
        k = sum(o.amount for lst in items.values() for o in lst)
        v["other_non_interest_income"] = core + k
        ctx.trace("net_fee_income", "Tỷ lệ phí × dư nợ bình quân × ngày / 365",
                  lambda d: d["r"] * d["avg"] * d["days"] / Y,
                  ctx.a("fee_to_loans", "Phí dịch vụ / dư nợ BQ", "ratio_annual", ("metric", "fee_to_loans"), key="r"),
                  Driver("avg", "Dư nợ bình quân", "VND", v["avg_loans"], ("avg", LOANS), drill="avg_loans"),
                  ctx.days_driver())
        drivers = [ctx.a("other_income_run_rate", "Mức thu nhập ngoài lãi khác theo năm", "VND_per_year",
                         ("metric", "other_income_run_rate"), key="rate"),
                   ctx.days_driver()]
        if k:
            drivers.append(Driver("one_off", "Khoản bất thường đã xác nhận", "VND", k,
                                  ("one_off_sum", tuple(sorted(items)))))
            ctx.trace("other_non_interest_income", "Mức theo năm × ngày / 365 + khoản bất thường đã xác nhận",
                      lambda d: d["rate"] * d["days"] / Y + d["one_off"], *drivers)
        else:
            ctx.trace("other_non_interest_income", "Mức theo năm × ngày / 365",
                      lambda d: d["rate"] * d["days"] / Y, *drivers)


# ================================================================ chi phí hoạt động


@register
class CostIncomeRatio(Method):
    kind = "bank_cir"
    block = "opex"

    def compute(self, ctx: PeriodContext) -> None:
        v = ctx.v
        v["opex"] = -ctx.at["cir"] * v["toi"]
        ctx.trace("opex", "−CIR × tổng thu nhập hoạt động", lambda d: -d["cir"] * d["toi"],
                  ctx.a("cir", "CIR", "ratio", ("metric", "cir"), key="cir"),
                  Driver("toi", "Tổng thu nhập hoạt động", "VND", v["toi"], ("line", "toi"), drill="toi"))


# ================================================================ thuế, vốn chủ


@register
class BankTaxPayout(TaxEquityMethod):
    """Dùng lại thuế/thiểu số của phi tài chính; ngân hàng không trích quỹ khen thưởng phúc lợi riêng ở BS."""

    kind = "bank_tax_payout_policies"

    def distribute(self, ctx: PeriodContext) -> None:
        parent = ctx.v["npat_parent"]
        div = -ctx.at["cash_dividend_payout"] * parent if parent > 0 else 0.0
        self._equity(ctx, div, 0.0)


# ================================================================ cân thanh khoản


@register
class InterbankBalancing(Method):
    """Dòng cân thanh khoản khai báo (thay biến cân 'tiền mặt' của model card).

    Thặng dư = nguồn vốn (không kể vay liên NH tự động) − TS không kể liên NH. Tiền gửi & cho vay liên NH (TS) nhận
    thặng dư, không thấp hơn sàn = tỷ lệ × tiền gửi KH; thiếu thì vay liên NH tự động, trong trần nợ liên NH / tổng TS;
    vượt trần thì phần thiếu là funding gap (kịch bản không khả thi, không bù). Vay tự động tính lại mỗi kỳ, nên khi
    thặng dư tăng thì phần vay tự động được trả trước."""

    kind = "bank_interbank_balancing"
    block = "balancing"

    def compute(self, ctx: PeriodContext) -> None:
        at, v, new, state = ctx.at, ctx.v, ctx.new, ctx.state
        sides = ctx.extra["sides"]
        base_liab = state.setdefault("interbank_liab_base", ctx.bal["interbank_liabilities"])
        assets = sum(new[k] for k, sd in sides.items() if sd == "asset" and k != "interbank_assets")
        resources = sum(new[k] for k, sd in sides.items() if sd != "asset" and k != "interbank_liabilities")
        surplus = resources + base_liab - assets
        # sàn liên NH (TS) và trần vay liên NH là ràng buộc cuối năm; quý 1–3: chỉ giữ liên NH (TS) không âm
        year_end = is_year_end(ctx.period)
        floor = at["min_interbank_assets_ratio"] * new["customer_deposits"] if year_end else 0.0
        c = at["max_interbank_liab_to_assets"] if year_end else 1.0
        need = max(0.0, floor - surplus)
        room = max(0.0, (c * (assets + surplus) - base_liab) / (1 - c)) if c < 1 else float("inf")
        auto = min(need, room)
        new["interbank_liabilities"] = base_liab + auto
        new["interbank_assets"] = surplus + auto
        v["liquidity_surplus"] = surplus
        v["interbank_floor"] = floor
        v["interbank_cap"] = c * (assets + new["interbank_assets"])
        v["auto_interbank_borrowing"] = auto
        v["funding_gap"] = max(0.0, floor - new["interbank_assets"])
        ctx.trace("interbank_assets", "Thặng dư thanh khoản + vay liên NH tự động (không thấp hơn sàn)",
                  lambda d: d["surplus"] + d["auto"],
                  Driver("surplus", "Thặng dư thanh khoản (nguồn vốn − TS khác)", "VND", surplus, None,
                         drill="liquidity_surplus"),
                  Driver("auto", "Vay liên NH tự động", "VND", auto, None, drill="auto_interbank_borrowing"))
        ctx.trace("interbank_liabilities", "Nợ liên NH hiện hữu + vay tự động", lambda d: d["base"] + d["auto"],
                  Driver("base", "Nợ liên NH hiện hữu (giữ nguyên)", "VND", base_liab, None),
                  Driver("auto", "Vay liên NH tự động", "VND", auto, None, drill="auto_interbank_borrowing"))
