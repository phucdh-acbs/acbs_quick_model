"""Ma trận quy tắc vi phạm khi trộn L1 / L2 giữa các block.

Mỗi quy tắc có mã cố định để UI, test và tài liệu tham chiếu cùng một nguồn:
- DEP (dependency): method/ô nhập cần driver mà block khác không cung cấp -> lỗi, chặn tính toán.
- OWN (ownership): method đang chạy sở hữu đầu ra; giá trị của method không chạy được giữ nhưng không dùng.
- IN  (input): tổ hợp đầu vào mâu thuẫn -> lỗi.
- CAL (calculation): đại lượng tính ra không hợp lệ (mẫu số ≤ 0, vượt số dư) -> lỗi.
- REV (review): cần analyst xem lại -> cảnh báo, không chặn.
- CON (consistency): bất biến được kiểm thử (không chạy lúc nhập liệu).

`trigger` mô tả tổ hợp level kích hoạt: {block: (level, ...)}; "*" là mọi level. Ma trận tương thích
được dựng tự động từ `trigger` để tài liệu không lệch với code.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quick_model.engine.assumptions import AssumptionValue, Scenario
from quick_model.engine.registry import BLOCK_LABELS, BLOCKS, METHODS, template_for
from quick_model.engine.validation import Issue


UNIT_ERROR_RATIO = 10.0  # doanh thu L2 gấp >10 lần (hoặc <1/10) cùng quý năm trước: nghi sai đơn vị


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str  # dependency | ownership | input | calculation | review | consistency
    stage: str  # selection | input | calculation | output | ui | test
    severity: str  # error | warning | info
    trigger: dict  # {block: (level, ...)}
    condition_vn: str
    effect_vn: str


RULES = [
    Rule("DEP-01", "dependency", "selection", "error", {"cogs": ("L2",), "revenue": ("L1",)},
         "Giá vốn L2 cần sản lượng; doanh thu L1 không cung cấp sản lượng.",
         "Chặn tính toán; chọn doanh thu L2/L3 hoặc đưa giá vốn về L1."),
    Rule("DEP-02", "dependency", "selection", "error", {"sga": ("L2",), "revenue": ("L1",)},
         "Ô CPBH/tấn (tùy chọn) được điền trong khi doanh thu L1 không có sản lượng.",
         "Chặn tính toán; xóa trống CPBH/tấn hoặc chọn doanh thu L2/L3."),
    Rule("OWN-01", "ownership", "selection", "info", {"*": ("*",)},
         "Kịch bản còn giá trị analyst đã nhập cho method không đang chạy của block.",
         "Giữ lại để chuyển về, nhưng không ảnh hưởng kết quả."),
    Rule("OWN-02", "ownership", "ui", "info", {"revenue": ("L2",)},
         "Doanh thu L2 đang chạy: doanh thu tổng và tăng trưởng L1 là kết quả, không phải ô nhập.",
         "Ẩn ô nhập L1; không cho ghi đè đồng thời tổng và chi tiết."),
    Rule("IN-01", "input", "input", "error", {"revenue": ("L2",)},
         "Giá bán ≤ 0 trong khi sản lượng > 0.",
         "Chặn tính toán."),
    Rule("IN-02", "input", "input", "error", {"ppe": ("L2",)},
         "Thời gian sử dụng bình quân ≤ 0 năm.",
         "Chặn tính toán (không chia được nguyên giá)."),
    Rule("IN-03", "input", "calculation", "error", {"debt": ("L2",)},
         "Trả gốc vay dài hạn vượt dư nợ dài hạn đầu kỳ + giải ngân trong kỳ.",
         "Chặn tính toán tại kỳ vi phạm."),
    Rule("IN-04", "input", "calculation", "error", {"debt": ("L2",)},
         "Thay đổi ròng vay ngắn hạn làm dư nợ ngắn hạn âm.",
         "Chặn tính toán tại kỳ vi phạm."),
    Rule("IN-05", "input", "calculation", "error", {"tax_equity": ("L2",)},
         "Chi quỹ khen thưởng phúc lợi vượt số dư quỹ đầu kỳ + trích trong kỳ.",
         "Chặn tính toán tại kỳ vi phạm."),
    Rule("CAL-01", "calculation", "calculation", "error", {"working_capital": ("L2",)},
         "Mua hàng ước tính (giá vốn + Δ tồn kho − khấu hao trong giá vốn) ≤ 0.",
         "Mẫu số không hợp lệ cho số ngày phải trả: chặn tính toán."),
    Rule("CAL-02", "calculation", "calculation", "error", {"ppe": ("L2",)},
         "Khấu hao trong kỳ vượt TSCĐ ròng đầu kỳ + phần chuyển từ dở dang.",
         "Chặn tính toán; xem lại thời gian sử dụng / tỷ lệ chuyển."),
    Rule("CAL-03", "calculation", "calculation", "error", {"revenue": ("L1",)},
         "Không có doanh thu cùng quý năm trước hợp lệ để áp tăng trưởng YoY.",
         "Chặn tính toán; nhập doanh thu L2 hoặc kiểm tra lịch sử."),
    Rule("REV-01", "review", "input", "warning", {"revenue": ("L2",), "cogs": ("L2",)},
         "Giá bán bình quân thấp hơn chi phí biến đổi/tấn (biên đóng góp âm).",
         "Cảnh báo; biên âm không bị coi là bất khả."),
    Rule("REV-02", "review", "input", "warning", {"revenue": ("L2",)},
         "Sản lượng vượt công suất analyst đã điền.",
         "Cảnh báo."),
    Rule("REV-03", "review", "output", "warning", {"revenue": ("L2",)},
         "Tăng trưởng YoY ngụ ý từ sản lượng × giá ngoài khoảng lịch sử; gấp >10 lần hoặc <1/10 thì nghi sai đơn vị.",
         "Cảnh báo."),
    Rule("REV-04", "review", "output", "warning", {"cogs": ("L2",)},
         "Biên gộp ngụ ý từ chi phí/tấn ngoài khoảng biên gộp lịch sử.",
         "Cảnh báo."),
    Rule("REV-05", "review", "output", "warning", {"cogs": ("L2",), "ppe": ("*",)},
         "Chi phí sản xuất cố định thấp hơn khấu hao phân bổ vào giá vốn.",
         "Cảnh báo: chi phí cố định trong giá vốn thường đã gồm khấu hao."),
    Rule("REV-06", "review", "output", "warning", {"sga": ("L2",)},
         "Tỷ lệ CPBH hoặc CPQL / doanh thu ngụ ý ngoài khoảng lịch sử.",
         "Cảnh báo."),
    Rule("REV-07", "review", "output", "warning", {"tax_equity": ("L2",)},
         "Cổ tức tiền vượt LNST chưa phân phối khả dụng (đầu kỳ + LNST công ty mẹ trong kỳ − trích quỹ).",
         "Cảnh báo."),
    Rule("REV-08", "review", "output", "warning", {"working_capital": ("L2",)},
         "Số ngày phải trả quy đổi theo giá vốn (L2) ngoài khoảng lịch sử của L1.",
         "Cảnh báo."),
    Rule("REV-09", "review", "output", "warning", {"ppe": ("L2",)},
         "Khấu hao ngụ ý / (TSCĐ + dở dang) đầu kỳ ngoài khoảng tỷ lệ khấu hao lịch sử.",
         "Cảnh báo."),
    Rule("IN-07", "input", "calculation", "error", {"revenue": ("L2",)},
         "Số cửa hàng cuối kỳ âm (đóng nhiều hơn số đang có + mở mới).",
         "Chặn tính toán tại kỳ vi phạm."),
    Rule("INT-01", "review", "output", "warning", {"debt": ("*",)},
         "Lịch sử không có nợ vay nên lãi suất default = 0, nhưng mô hình phát sinh nợ vay (vay tự động).",
         "Cảnh báo; lãi vay đang tính 0, analyst nhập lãi suất."),
    Rule("REV-10", "review", "output", "warning", {"*": ("*",)},
         "Chạy theo năm: dự phóng cả năm đầu tiên thấp hơn lũy kế thực tế các quý đã công bố của năm đó.",
         "Cảnh báo; xem lại giả định năm."),
    Rule("ONE-01", "review", "output", "warning", {"non_operating": ("*",)},
         "Quý đã công bố của năm hiện tại có khoản bất thường ở dòng ngoài hoạt động lõi (|thực tế − trend| > ngưỡng × σ "
         "và vượt mức trọng yếu) chưa được analyst xác nhận hoặc bỏ qua.",
         "Cảnh báo; analyst xác nhận (chạy năm: cộng k vào số cả năm; mọi tần suất: loại khỏi default) hoặc bỏ qua."),
    Rule("ONE-02", "review", "output", "info", {"non_operating": ("*",)},
         "Khoản bất thường đã được analyst xác nhận và áp vào mô hình.",
         "Thông tin; công bố số k, trend, σ, z và lý do."),
    Rule("AUTO-01", "funding", "calculation", "info", {"debt": ("L1",)},
         "Cân nguồn tự động L1 đã vay ngắn/dài hạn hoặc điều chỉnh giả định default theo ràng buộc tỷ lệ.",
         "Thông tin; mọi điều chỉnh ghi thành override nguồn auto_balance kèm lý do."),
    Rule("AUTO-02", "funding", "calculation", "warning", {"debt": ("L1",)},
         "Sau cân nguồn tự động, tổng nợ vay vẫn tăng quá ngưỡng so với kỳ trước hoặc còn funding gap.",
         "Cảnh báo; analyst xem lại giả định hoặc chuyển nợ vay sang L2 để tự cân nguồn."),
    Rule("FUND-L2", "funding", "calculation", "warning", {"debt": ("L2",)},
         "Nợ vay chạy L2 (lịch vay): thiếu tiền được báo là funding gap, không tự vay.",
         "Analyst cân nguồn bằng nhánh cân nguồn hoặc sửa lịch vay."),
    Rule("CON-01", "consistency", "test", "info", {"*": ("L1", "L2")},
         "Đầu vào L2 tương đương L1 (ví dụ giá × sản lượng = doanh thu L1, phần cố định = 0) cho cùng kết quả.",
         "Kiểm thử tự động."),
]
RULES_BY_ID = {r.rule_id: r for r in RULES}


def _issue(rule_id: str, block: str | None, target: str | None, period: str | None, message: str,
           severity: str | None = None) -> Issue:
    return Issue(severity or RULES_BY_ID[rule_id].severity, rule_id, block, target, period, message)


# ---------------------------------------------------------------- giai đoạn chọn method

def selection_issues(s: Scenario, resolved: dict[str, AssumptionValue], tpl=None) -> list[Issue]:
    tpl = tpl or template_for(s)
    METHODS_BY_ID, SPECS, REQUIRES_WHEN_FILLED = tpl.methods_by_id, tpl.specs, tpl.requires_when_filled
    out = []
    provided = {d for mid in s.methods.values() if mid in METHODS_BY_ID for d in METHODS_BY_ID[mid].provides}
    for block, mid in s.methods.items():
        m = METHODS_BY_ID.get(mid)
        if m is None:
            continue
        missing = [d for d in m.requires if d not in provided]
        if missing:
            out.append(_issue("DEP-01", block, None, None,
                              f"{m.label_vn} ({m.level}) cần {', '.join(missing)}; chưa block nào cung cấp "
                              "(chọn doanh thu L2/L3 hoặc đưa block này về L1)."))
    for aid, needs in REQUIRES_WHEN_FILLED.items():
        val = resolved.get(aid)
        if val is not None and val.filled:
            missing = [d for d in needs if d not in provided]
            if missing:
                sp = SPECS[aid]
                out.append(_issue("DEP-02", sp.block, aid, None,
                                  f"Đã điền {sp.label_vn} nhưng chưa có {', '.join(missing)} "
                                  "(xóa trống ô này hoặc chọn doanh thu L2/L3)."))
    for aid, val in s.overrides.items():
        sp = SPECS.get(aid)
        if sp is None or sp.shared or sp.method_id == s.methods.get(sp.block):
            continue
        lvl = METHODS_BY_ID[sp.method_id].level if sp.method_id in METHODS_BY_ID else "?"
        out.append(_issue("OWN-01", sp.block, aid, None,
                          f"Giá trị analyst của {sp.label_vn} ({lvl}) được giữ nhưng không dùng vì block đang chạy "
                          f"{METHODS_BY_ID[s.methods[sp.block]].level}."))
    return out


# ---------------------------------------------------------------- giai đoạn nhập liệu

def input_issues(s: Scenario, resolved: dict[str, AssumptionValue], periods: list[str], tpl=None) -> list[Issue]:
    tpl = tpl or template_for(s)
    kind_of = tpl.kind_of
    out = []
    g = lambda aid: resolved[aid].values if aid in resolved else (None,) * len(periods)  # noqa: E731
    if kind_of(s.methods.get("revenue")) == "volume_x_price":
        vol, asp, cap = g("sales_volume"), g("asp"), g("capacity")
        for p, v, a, c in zip(periods, vol, asp, cap):
            if v is not None and a is not None and v > 0 and a <= 0:
                out.append(_issue("IN-01", "revenue", "asp", p, "Giá bán ≤ 0 trong khi sản lượng > 0."))
            if v is not None and c is not None and v > c:
                out.append(_issue("REV-02", "revenue", "sales_volume", p,
                                  f"Sản lượng {v:,.0f} tấn vượt công suất {c:,.0f} tấn."))
        if kind_of(s.methods.get("cogs")) == "unit_cost_plus_fixed":
            for p, a, vc in zip(periods, asp, g("variable_cost_per_tonne")):
                if a is not None and vc is not None and a < vc:
                    out.append(_issue("REV-01", "cogs", "variable_cost_per_tonne", p,
                                      f"Giá bán {a / 1e6:,.2f} < chi phí biến đổi {vc / 1e6:,.2f} triệu VND/tấn "
                                      "(biên đóng góp âm)."))
    if kind_of(s.methods.get("ppe")) == "cip_rollforward_useful_life":
        for p, life in zip(periods, g("useful_life_years")):
            if life is not None and life <= 0:
                out.append(_issue("IN-02", "ppe", "useful_life_years", p, "Thời gian sử dụng phải > 0 năm."))
    return out


# ---------------------------------------------------------------- giai đoạn sau tính toán

def review_issues(s: Scenario, values: pd.DataFrame, periods: list[str], origin: str,
                  defaults: dict[str, AssumptionValue], revenue_hist: dict[str, float],
                  a: dict[str, list], tpl=None) -> list[Issue]:
    tpl = tpl or template_for(s)
    kind_of = tpl.kind_of
    out = []
    v = values
    rng = lambda aid: (defaults[aid].basis.get("min"), defaults[aid].basis.get("max")) if aid in defaults else (None, None)  # noqa: E731,E501

    def outside(x, lo, hi):
        return lo is not None and hi is not None and x is not None and not pd.isna(x) and not (lo <= x <= hi)

    rev_all = dict(revenue_hist)
    rev_all.update({p: v.loc["net_revenue", p] for p in periods})
    if kind_of(s.methods.get("revenue")) == "volume_x_price":
        lo, hi = rng("revenue_yoy_growth")
        from quick_model.periods import matching_prior
        for p in periods:
            base = rev_all.get(matching_prior(p))
            cur = rev_all.get(p)
            if base is None or base <= 0 or cur is None:
                continue
            ratio = cur / base
            if ratio > UNIT_ERROR_RATIO or ratio < 1 / UNIT_ERROR_RATIO:
                out.append(_issue("REV-03", "revenue", "net_revenue", p,
                                  f"Doanh thu gấp {ratio:,.1f} lần kỳ tương ứng năm trước: kiểm tra đơn vị sản lượng "
                                  "(tấn) và giá (VND/tấn)."))
            elif outside(ratio - 1, lo, hi):
                out.append(_issue("REV-03", "revenue", "net_revenue", p,
                                  f"Tăng trưởng ngụ ý {ratio - 1:.1%} ngoài khoảng lịch sử [{lo:.1%}, {hi:.1%}]."))
    if kind_of(s.methods.get("cogs")) == "unit_cost_plus_fixed":
        lo, hi = rng("gross_margin")
        for t, p in enumerate(periods):
            r = v.loc["net_revenue", p]
            gm = v.loc["gross_profit", p] / r if r > 0 else None
            if outside(gm, lo, hi):
                out.append(_issue("REV-04", "cogs", "gross_profit", p,
                                  f"Biên gộp ngụ ý {gm:.1%} ngoài khoảng lịch sử [{lo:.1%}, {hi:.1%}]."))
            share = a["noncash_cogs_share"][t] if "noncash_cogs_share" in a else 1.0
            dep_in_cogs = share * v.loc["depreciation", p]
            fixed = a["fixed_production_cost"][t]
            if fixed < dep_in_cogs:
                out.append(_issue("REV-05", "cogs", "fixed_production_cost", p,
                                  f"Chi phí cố định {fixed / 1e9:,.0f} tỷ < khấu hao phân bổ vào giá vốn "
                                  f"{dep_in_cogs / 1e9:,.0f} tỷ."))
    if kind_of(s.methods.get("sga")) == "expense_fixed_variable":
        for line, aid, name in (("selling_expense", "selling_expense_ratio", "CPBH"),
                                ("admin_expense", "admin_expense_ratio", "CPQL")):
            lo, hi = rng(aid)
            for p in periods:
                r = v.loc["net_revenue", p]
                x = -v.loc[line, p] / r if r > 0 else None
                if outside(x, lo, hi):
                    out.append(_issue("REV-06", "sga", line, p,
                                      f"{name}/doanh thu ngụ ý {x:.2%} ngoài khoảng lịch sử [{lo:.2%}, {hi:.2%}]."))
    if kind_of(s.methods.get("tax_equity")) == "distribution_schedule":
        for p in periods:
            avail = v.loc["retained_earnings_available", p]
            div = -v.loc["dividends_paid", p]
            if div > 0 and div > avail:
                out.append(_issue("REV-07", "tax_equity", "cash_dividend_amount", p,
                                  f"Cổ tức {div / 1e9:,.0f} tỷ vượt LNST chưa phân phối khả dụng {avail / 1e9:,.0f} tỷ."))
    if kind_of(s.methods.get("working_capital")) == "wc_days_purchases":
        lo, hi = rng("dpo_days")
        from quick_model.periods import period_days
        for p in periods:
            cogs = -v.loc["cogs", p]
            x = v.loc["trade_payables", p] / cogs * period_days(p) if cogs > 0 else None
            if outside(x, lo, hi):
                out.append(_issue("REV-08", "working_capital", "trade_payables", p,
                                  f"Số ngày phải trả quy theo giá vốn {x:.0f} ngoài khoảng lịch sử L1 "
                                  f"[{lo:.0f}, {hi:.0f}]."))
    if kind_of(s.methods.get("ppe")) == "cip_rollforward_useful_life":
        lo, hi = rng("depreciation_rate")
        from quick_model.periods import period_days
        prev = origin
        for p in periods:
            base = v.loc["fixed_assets", prev] + v.loc["cip", prev]
            x = v.loc["depreciation", p] / base * 365 / period_days(p) if base > 0 else None
            if outside(x, lo, hi):
                out.append(_issue("REV-09", "ppe", "depreciation", p,
                                  f"Khấu hao ngụ ý {x:.1%}/năm trên tài sản đầu kỳ, ngoài khoảng lịch sử "
                                  f"[{lo:.1%}, {hi:.1%}]."))
            prev = p
    return out


# ---------------------------------------------------------------- ma trận để hiển thị / tài liệu

def _levels(block: str, methods=None) -> list[str]:
    return sorted({m.level for m in (methods or METHODS) if m.block == block and m.implemented})


def compatibility_matrix() -> pd.DataFrame:
    """Hàng: block × level đang chọn; cột: block × level khác; ô: mã quy tắc có thể phát sinh."""
    keys = [f"{BLOCK_LABELS[b]} {lv}" for b in BLOCKS for lv in _levels(b)]
    mat = pd.DataFrame("", index=keys, columns=keys)
    icon = {"error": "⛔", "warning": "⚠️", "info": "ℹ️"}
    for r in RULES:
        if "*" in r.trigger or r.stage in ("ui", "test"):
            continue
        items = list(r.trigger.items())
        a_block, a_levels = items[0]
        others = items[1:] or [(a_block, a_levels)]
        for b_block, b_levels in others:
            for la in a_levels:
                for lb in (_levels(b_block) if b_levels == ("*",) else b_levels):
                    row, col = f"{BLOCK_LABELS[a_block]} {la}", f"{BLOCK_LABELS[b_block]} {lb}"
                    if row in mat.index and col in mat.columns:
                        tag = f"{icon[r.severity]} {r.rule_id}"
                        mat.loc[row, col] = (mat.loc[row, col] + " " + tag).strip()
    keep_rows = [i for i in mat.index if mat.loc[i].str.len().sum() > 0]
    keep_cols = [c for c in mat.columns if mat[c].str.len().sum() > 0]
    mat = mat.loc[keep_rows, keep_cols]
    mat.index.name = "Đang chọn ↓ / với →"
    return mat


def rules_table() -> pd.DataFrame:
    def trig(t):
        if "*" in t:
            return "Mọi block"
        return " + ".join(f"{BLOCK_LABELS[b]} {'/'.join(lv) if lv != ('*',) else 'mọi level'}" for b, lv in t.items())
    return pd.DataFrame([{
        "Mã": r.rule_id, "Nhóm": r.category, "Mức": r.severity, "Khi nào": trig(r.trigger),
        "Điều kiện": r.condition_vn, "Hệ quả": r.effect_vn, "Giai đoạn": r.stage,
    } for r in RULES])
