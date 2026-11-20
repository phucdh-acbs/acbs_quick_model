"""Giải thích nguyên nhân của một ô dự phóng HOẶC một ô thực tế (độc lập UI).

Ô thực tế (lịch sử) dùng đúng công thức (trace) mà method đang chọn ghi cho dòng đó ở kỳ dự phóng đầu, với từng yếu
tố lấy từ BCTC theo `Driver.hist`; phần thực tế không khớp công thức hiện là "Khác". Vì vậy method L2/L3 mới tự có
phân rã cả lịch sử lẫn dự phóng khi gọi ctx.trace với hist đúng; yếu tố không có trong database (sản lượng, giá…)
thì cần đăng ký một kiểu hist mới (register_hist), ví dụ đọc từ kho KPI vận hành analyst nhập.

Cấu trúc dòng tổng, dòng lợi nhuận và dòng biên do nhóm BCTC khai báo (StatementFamily.subtotals / profit_lines /
margin_lines), không viết cứng cho phi tài chính.

Với một dòng và một kỳ, trả lời:
- công thức đang dùng và giá trị từng yếu tố (vd. suất sinh lời, tiền + ĐTNH đầu kỳ, số ngày);
- so với kỳ so sánh (kỳ trước, hoặc cùng kỳ năm trước): mỗi yếu tố đổi bao nhiêu và đóng góp bao nhiêu vào chênh
  lệch (thay lần lượt từng yếu tố, tổng các đóng góp = chênh lệch). Kỳ so sánh là kỳ thực tế thì yếu tố được tính
  lại từ BCTC bằng cùng công thức (chỉ số lịch sử, số dư đầu kỳ…);
- dòng tổng (lãi gộp, LNTT, LNST…) phân rã theo dòng thành phần, có thể bấm tiếp vào thành phần;
- giả định lấy từ đâu (default lịch sử nào, analyst nhập, cân nguồn tự động);
- engine quý + năm (quick_model/hybrid.py): ô quý dự phóng có công thức riêng (trace của quý); ô năm có quý = tổng các
  quý (phân rã theo quý); giả định quý = λ × EMA quý + (1 − λ) × năm.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from typing import Callable

from quick_model.families import get_family
from quick_model.methods.base import Driver, Trace
from quick_model.periods import FREQ_ANNUAL, FREQ_QUARTER, freq_of, matching_prior, period_days, shift_label
from quick_model.presentation import _history_values, _quarter_source, period_source
from quick_model.units import UNITS

SOURCE_TEXT = {"analyst": "analyst nhập", "historical_default": "default lịch sử", "explicit_policy": "chính sách khai báo",
               "needs_input": "cần nhập", "auto_balance": "cân nguồn tự động"}
PROFIT_LINES = ("operating_profit", "pbt", "npat", "npat_parent")  # phi tài chính (giữ để tương thích)

# ---------------------------------------------------------------- cách lấy yếu tố ở kỳ thực tế (mở rộng được)
HIST_RESOLVERS: dict[str, Callable] = {}


def register_hist(kind: str):
    """Đăng ký cách lấy giá trị thực tế của một kiểu Driver.hist: fn(explainer, spec, period) -> số hoặc None."""
    def deco(fn):
        HIST_RESOLVERS[kind] = fn
        return fn
    return deco


def _balance(ex, spec, period, prefix):
    q = ex.metrics.q
    vals = [q.loc[period, prefix + c] if prefix + c in q.columns else float("nan") for _, c in spec[1]]
    if all(pd.isna(v) for v in vals):
        return None
    return sum(sg * (0.0 if pd.isna(v) else v) for (sg, _), v in zip(spec[1], vals))


register_hist("metric")(lambda ex, spec, p: ex.metrics.series(spec[1])[0].get(p))
register_hist("open")(lambda ex, spec, p: _balance(ex, spec, p, "open_"))
register_hist("close")(lambda ex, spec, p: _balance(ex, spec, p, ""))
register_hist("days")(lambda ex, spec, p: float(ex.metrics.q.loc[p, "days"]))
register_hist("const")(lambda ex, spec, p: spec[1])
register_hist("line_mp")(lambda ex, spec, p: ex.value(spec[1], matching_prior(p))[0])


@register_hist("line")
def _hist_line(ex, spec, p):
    x = ex.actual(spec[1], p)
    return x * spec[2] if x is not None and len(spec) > 2 else x


@register_hist("residual_ratio")
def _hist_residual_ratio(ex, spec, p):  # tỷ lệ biến đổi thực tế ngụ ý = (chi phí − cố định) / doanh thu
    cost, rev = ex.actual(spec[1], p), ex.actual("net_revenue", p)
    return None if cost is None or not rev else (-cost - spec[2]) / rev


@register_hist("one_off")
def _hist_one_off(ex, spec, p):  # khoản bất thường analyst đã xác nhận trong kỳ (năm: cộng các quý của năm)
    from quick_model import oneoff
    from quick_model.periods import fiscal_year
    return sum(o.amount for o in oneoff.confirmed(ex.one_offs) if o.line == spec[1]
               and (o.quarter == p or (len(p) == 4 and str(fiscal_year(o.quarter)) == p)))
ANOMALY_MULTIPLE = 3.0  # quý thực tế gấp > 3 lần trung vị 8 quý trước -> bất thường
STAT_TEXT = {"median": "trung vị", "min": "thấp nhất", "max": "cao nhất", "latest": "thực tế kỳ liền trước",
             "same_period_latest": "thực tế cùng kỳ gần nhất",
             "converge": "hội tụ từ thực tế gần nhất về mức neo (EMA 5 năm)", "ema": "EMA 5 năm", "ema_rolling": "EMA 5 năm cuốn chiếu", "ema_decay": "EMA 5 năm giảm dần về mức mục tiêu",
             "latest_short": "thực tế kỳ liền trước (chưa đủ chuỗi cho EMA)", "seasonal_median": "trung vị cùng quý", "marginal_cost": "tách cố định/biến đổi theo chi phí biên"}


class _CompositeMetrics:
    """Chỉ số lịch sử năm + quý trong một khung (nhãn 'YYYY' và 'YYYYQn' không trùng) cho các kiểu hist."""

    def __init__(self, annual, quarterly=None):
        self.parts = [m for m in (annual, quarterly) if m is not None]
        self.q = pd.concat([m.q for m in self.parts], axis=0)
        self._cache: dict = {}

    def series(self, name: str):
        if name not in self._cache:
            got = [m.series(name) for m in self.parts]
            self._cache[name] = (pd.concat([g[0] for g in got]), got[0][1])
        return self._cache[name]


@dataclass
class DriverRow:
    key: str
    label: str
    unit: str
    current: float | None
    prior: float | None
    source: str = ""
    assumption_id: str | None = None
    drill: str | None = None

    @property
    def change(self) -> float | None:
        return None if self.current is None or self.prior is None else self.current - self.prior


@dataclass
class BridgeItem:
    label: str
    amount: float
    kind: str  # driver | component | residual
    line: str | None = None  # dòng thành phần (để xem tiếp)


@dataclass
class Explanation:
    line: str
    label: str
    period: str
    value: float | None
    unit: str
    compare_period: str
    compare_text: str
    prior_value: float | None
    prior_is_actual: bool
    formula: str = ""
    drivers: list[DriverRow] = field(default_factory=list)
    bridge: list[BridgeItem] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ytd: dict | None = None
    headline: str = ""

    @property
    def change(self) -> float | None:
        return None if self.value is None or self.prior_value is None else self.value - self.prior_value


def fmt(v, unit: str = "VND") -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    lab, k, _ = UNITS.get(unit, ("", 1.0, ""))
    if unit in ("ratio", "ratio_annual"):
        return f"{v * 100:,.2f}%"
    if unit == "VND":
        return f"{v * k:,.0f} tỷ"
    return f"{v * k:,.1f} {lab}"


class Explainer:
    """Giải thích các ô của một kết quả dự phóng. Tạo một lần cho mỗi kết quả, gọi nhiều lần."""

    def __init__(self, h, result):
        self.h, self.r = h, result
        s = result.scenario
        self.freq, self.origin = s.frequency, s.origin
        from quick_model.engine.registry import template_for
        from quick_model import oneoff
        tpl = template_for(s)
        self.template = tpl
        self.family = get_family(tpl.family)
        self.family.register_explain()
        self.subtotals = dict(self.family.subtotals)
        self.profit_lines = tuple(self.family.profit_lines)
        self.margin_lines = dict(self.family.margin_lines)
        # chỉ số lịch sử đã loại khoản bất thường (quyết định analyst + khoản tự áp của template); năm và quý gộp một
        # khung (nhãn 'YYYY' và 'YYYYQn' không trùng), để yếu tố của ô thực tế năm hay quý đều tính lại được
        self.one_offs = self.family.engine(tpl).effective_one_offs(h, s)
        self.metrics = _CompositeMetrics(
            self.family.metrics(h, s.origin, True, oneoff.history_adjustments(self.one_offs, tpl, True)),
            self.family.metrics(h, None, False, oneoff.history_adjustments(self.one_offs, tpl, False))
            if h.periods_by(FREQ_QUARTER) else None)
        self.q_values = result.quarter_values
        self.agg_years = set(result.aggregated_years)
        self.one_off_lines = set(oneoff.line_specs(tpl))
        self.labels = result.line_meta["label_vn"].to_dict()
        self.units = result.line_meta["unit"].to_dict()
        self._actual_cache: dict[str, pd.Series] = {}

    # ------------------------------------------------------------ số thực tế
    def actual(self, line: str, period: str) -> float | None:
        if period not in self._actual_cache:
            lines = list(self.r.line_meta.index)
            self._actual_cache[period] = _history_values(period_source(self.h, freq_of(period)), lines, [period],
                                                         self.template.template_id)[period]
        v = self._actual_cache[period].get(line)
        return None if v is None or pd.isna(v) else float(v)

    def value(self, line: str, period: str) -> tuple[float | None, bool]:
        """(giá trị, là số thực tế?) của một dòng ở một kỳ (dự phóng hoặc thực tế)."""
        if self.r.values is not None and period in self.r.periods:
            v = self.r.values.loc[line, period] if line in self.r.values.index else None
            return (None if v is None or pd.isna(v) else float(v)), False
        if self.q_values is not None and period in self.q_values.columns:
            v = self.q_values.loc[line, period] if line in self.q_values.index else None
            return (None if v is None or pd.isna(v) else float(v)), False
        return self.actual(line, period), True

    def _hist_driver(self, spec: tuple | None, period: str) -> float | None:
        """Giá trị thực tế của một yếu tố theo Driver.hist (kiểu đăng ký trong HIST_RESOLVERS)."""
        if spec is None or period not in self.metrics.q.index:
            return None
        fn = HIST_RESOLVERS.get(spec[0])
        if fn is None:
            return None
        try:
            x = fn(self, spec, period)
        except (KeyError, ZeroDivisionError, TypeError):
            return None
        return None if x is None or pd.isna(x) else float(x)

    def is_actual(self, period: str) -> bool:
        return period not in self.r.periods and (self.q_values is None or period not in self.q_values.columns)

    def template_trace(self, line: str):
        """Công thức của method đang chọn cho dòng (trace ở kỳ dự phóng đầu), dùng để phân rã ô thực tế."""
        tr = next((self.r.traces[(line, p)] for p in self.r.engine_periods if (line, p) in self.r.traces), None)
        if tr is None:
            return None
        if line in self.one_off_lines and not any(d.key == "one_off" for d in tr.drivers):
            fn = tr.fn  # dòng có thể có khoản bất thường đã xác nhận ở kỳ thực tế: thêm yếu tố đó
            tr = Trace(tr.line, tr.formula + " + khoản bất thường đã xác nhận (nếu có)",
                       tuple(tr.drivers) + (Driver("one_off", "Khoản bất thường đã xác nhận", "VND", 0.0,
                                                   ("one_off", line)),),
                       lambda x: fn(x) + x["one_off"])
        return tr

    def _derived_source(self, d, period: str) -> str:
        prev = shift_label(period, -1)
        kind = (d.hist or ("",))[0]
        if kind == "open":
            return f"số dư cuối {prev}" + (" (thực tế BCTC)" if self.is_actual(prev) else " (kết quả mô hình)")
        if kind in ("line", "line_mp"):
            return "kết quả dòng khác của mô hình"
        if kind == "days":
            return "lịch kỳ (năm tính 365 ngày)" if freq_of(period) == FREQ_ANNUAL else "số ngày thực tế của quý"
        if kind == "const" and len(d.hist) > 2:
            return d.hist[2]
        if kind == "one_off":
            return "khoản bất thường analyst đã xác nhận (thực tế − trend)"
        return "tính trong kỳ"

    def _spec_unit(self, aid: str) -> str:
        from quick_model.engine.registry import template_for
        sp = template_for(self.r.scenario).specs.get(aid)
        return sp.unit if sp is not None else "ratio"

    def _quarter_source(self, aid: str, period: str) -> str:
        qa = self.r.quarter_assumptions.get(aid)
        if qa is None:
            return ""
        b = qa.basis or {}
        unit = self._spec_unit(aid)
        mode = b.get("quarterly")
        what = ("số quý liền trước (chưa đủ chuỗi cho EMA)" if b.get("short_history_q") else
                b.get("what_q") or f"EMA {b.get('ema_span_quarters', 8):g} quý")
        if mode == "revenue_components":
            st = (b.get("per_quarter") or {}).get(period) or {}
            w = b.get("weights", {})
            if not st:
                return "default quý: doanh thu 3 thành phần"
            return (f"doanh thu quý = {w.get('qoq'):g} × QoQ mùa vụ {fmt(st['qoq'])} + {w.get('yoy'):g} × YoY "
                    f"(EMA 8 quý {fmt(st['ema_yoy'], 'ratio')} trên nền điều chỉnh mức {fmt(st['base'])}) {fmt(st['yoy'])} + "
                    f"{w.get('year'):g} × năm ({fmt(st['annual'], 'ratio')} trên nền điều chỉnh mức) {fmt(st['year'])} "
                    f"= {fmt(st['revenue'])}; tăng trưởng so cùng kỳ hiệu dụng {fmt(st['value'], 'ratio')}")
        if mode == "quarter_only":
            step = (b.get("per_quarter") or {}).get(period) or {}
            return f"default quý = {what} {fmt(step.get('ema_q'), unit)} (từ quý đi lên năm, không gộp giá trị năm)"
        if mode == "blend":
            step = (b.get("per_quarter") or {}).get(period) or {}
            if step.get("ema_q") is None:
                return f"{SOURCE_TEXT.get(qa.source, qa.source)}: giá trị năm (không có chuỗi quý hợp lệ)"
            lam = step.get("lambda", b.get("lambda"))
            return (f"default quý = {lam:g} × {what} {fmt(step['ema_q'], unit)} + "
                    f"{1 - lam:g} × năm {period[:4]} {fmt(step['annual'], unit)} ({self._source(aid, period[:4], True)})")
        if mode == "split_days":
            return f"{self._source(aid, period[:4], True)}; số năm chia theo số ngày của quý"
        if mode == "target":
            return f"analyst nhập số năm {period[:4]} (mục tiêu năm): phần chênh so với quý đã công bố dồn vào quý còn lại"
        if mode == "override":
            return f"analyst nhập số năm {period[:4]}, áp cho quý"
        return f"{self._source(aid, period[:4], True)}; giả định theo năm, quý dùng giá trị năm"

    def _source(self, aid: str | None, period: str, annual_only: bool = False) -> str:
        if not aid or aid not in self.r.assumptions:
            return ""
        if len(period) == 6 and not annual_only and aid in self.r.quarter_assumptions:
            return self._quarter_source(aid, period)
        qa = self.r.quarter_assumptions.get(aid)
        if (not annual_only and qa is not None and (qa.basis or {}).get("quarterly") == "quarter_only"
                and period in getattr(self.r.plan, "annual", [])):
            return f"giữ mức quý cuối {self.r.quarters[-1]} ({fmt(qa.values[-1], self._spec_unit(aid))}): từ quý đi lên năm"
        a = self.r.assumptions[aid]
        txt = SOURCE_TEXT.get(a.source, a.source)
        b = a.basis or {}
        if a.source == "historical_default":
            stat = b.get("statistic") or b.get("method")
            extra = STAT_TEXT.get(stat, stat or "")
            step = (b.get("path") or {}).get(period)
            if step:
                unit = self._spec_unit(aid)
                extra += (f": {step['start_period'] or 'neo'} {fmt(step['start'], unit)} → neo {fmt(step['anchor'], unit)}"
                          f", đã đi {step['weight']:.0%} quãng hội tụ")
            elif stat == "seasonal_median" and b.get("seasonal_values"):
                extra += f" {period[-2:]} ({b.get('window')} quý gần nhất)"
            elif b.get("source_period"):
                extra += f" ({b['source_period']})"
            elif b.get("per_period") and period in b["per_period"]:
                extra += f" ({b['per_period'][period]['source_period']})"
            elif b.get("window_periods"):
                extra += f" {b.get('window')} {b.get('window_unit', '')} ({'–'.join(b['window_periods'])})"
            txt += f": {extra.strip()}"
        elif a.source in ("auto_balance", "analyst") and (a.override_reason or b.get("note")):
            txt += f" — {a.override_reason or b.get('note')}"
        elif a.source == "explicit_policy" and b.get("note"):
            txt += f" — {b['note']}"
        return txt

    # ------------------------------------------------------------ giải thích một ô
    def explain(self, line: str, period: str, compare: str = "prior") -> Explanation:
        aggregated = period in self.agg_years
        cmp_p = matching_prior(period) if compare == "yoy" else shift_label(period, -1)
        cmp_text = "cùng kỳ năm trước" if compare == "yoy" else "kỳ trước"
        value, _ = self.value(line, period)
        prior, prior_actual = self.value(line, cmp_p)
        unit = self.units.get(line, "VND")
        ex = Explanation(line, self.labels.get(line, line), period, value, unit, cmp_p, cmp_text, prior, prior_actual)
        actual = self.is_actual(period)
        tr = self.template_trace(line) if actual else self.r.traces.get((line, period))
        if line in self.margin_lines:
            base, _, mlabel, _ = self.margin_lines[line]
            ex.formula = f"{self.labels.get(base, base)} × {mlabel.lower()}"
            ex.bridge, rows = self.margin_bridge(period, cmp_p, line)
            ex.drivers = rows
        elif line in self.profit_lines:
            ex.formula = self._subtotal_formula(line)
            ex.bridge = profit_bridge(self.h, self.r, period, line, compare, explainer=self)
        elif aggregated:
            self._explain_aggregate(ex, line, period, cmp_p)
        elif line in self.subtotals:
            ex.formula = self._subtotal_formula(line)
            for sg, comp in self.subtotals[line]:
                cur, _ = self.value(comp, period)
                old, _ = self.value(comp, cmp_p)
                if cur is not None and old is not None:
                    ex.bridge.append(BridgeItem(self.labels.get(comp, comp), sg * (cur - old), "component", comp))
        elif tr is not None:
            prior_tr = None if (actual or prior_actual) else self.r.traces.get((line, cmp_p))
            if prior_tr is not None:
                tr = _align_one_off(tr, prior_tr)
            ex.formula = tr.formula
            for d in tr.drivers:
                cur = self._hist_driver(d.hist, period) if actual else d.value
                old = (next((x.value for x in prior_tr.drivers if x.key == d.key), None) if prior_tr is not None
                       else self._hist_driver(d.hist, cmp_p) if prior_actual else None)
                if old is None and d.hist and d.hist[0] in ("const", "one_off") and prior_tr is not None:
                    old = 0.0 if d.hist[0] == "one_off" else d.hist[1]
                src = ("số thực tế tính lại từ BCTC" + (f" — {self._derived_source(d, period)}"
                                                        if d.hist and d.hist[0] in ("open", "one_off") else "")
                       if actual else self._source(d.assumption_id, period) or self._derived_source(d, period))
                if actual and d.hist is None:
                    src = "không có trong database (cần KPI vận hành)"
                ex.drivers.append(DriverRow(d.key, d.label, d.unit, cur, old, src, d.assumption_id, d.drill))
            ex.bridge = self._driver_bridge(tr, ex)
            if actual:
                ex.notes.append("Ô thực tế: phân rã theo công thức của method đang chọn, từng yếu tố tính lại từ BCTC; "
                                "phần số thực tế không khớp công thức (nếu có) hiện là mục riêng.")
        else:
            ex.notes.append("Dòng này giữ nguyên số dư hoặc không có công thức riêng ở method đang chọn: "
                            + str(self.r.line_meta["formula_vn"].get(line, "")))
        ex.ytd = None if actual or self.agg_years else self._ytd(line, period)
        if ex.ytd and ex.ytd.get("note"):
            ex.notes.append(ex.ytd["note"])
        ex.headline = self._headline(ex)
        return ex

    def _explain_aggregate(self, ex: Explanation, line: str, y: str, cmp_p: str) -> None:
        """Ô năm có quý: tổng các quý (dòng luồng) hoặc số dư Q4 (dòng số dư), phân rã theo từng quý."""
        from quick_model.hybrid import FIRST_LINES, LAST_LINES, MEAN_LINES
        from quick_model.periods import year_quarters
        stock = line in LAST_LINES or self.r.line_meta["statement"].get(line) == "BS" or line in (
            "total_assets", "total_resources", "balance_residual")
        qs = year_quarters(y)
        done = [q for q in qs if self.is_actual(q)]
        if stock:
            ex.formula = f"Số dư cuối {y}Q4"
            v4, a4 = self.value(line, f"{y}Q4")
            ex.drivers = [DriverRow("q4", f"{y}Q4", ex.unit, v4, None, "thực tế BCTC" if a4 else "engine quý")]
            if ex.prior_value is not None and v4 is not None:
                ex.bridge = [BridgeItem(f"{y}Q4 so với cuối {cmp_p}", v4 - ex.prior_value, "component", line)]
        else:
            how = "Trung bình" if line in MEAN_LINES else "Quý đầu" if line in FIRST_LINES else "Tổng"
            ex.formula = f"{how} 4 quý {y}" + (f" ({done[0]}–{done[-1]} thực tế, còn lại engine quý)" if done else "")
            for q in qs:
                cur, act = self.value(line, q)
                mp = q.replace(y, cmp_p, 1) if len(cmp_p) == 4 else None
                old = self.value(line, mp)[0] if mp else None
                ex.drivers.append(DriverRow(q, q, ex.unit, cur, old, "thực tế BCTC" if act else "engine quý",
                                            None, line))
                if how == "Tổng" and cur is not None and old is not None:
                    ex.bridge.append(BridgeItem(f"{q} so với cùng quý {cmp_p}", cur - old, "component", line))
        ex.notes.append("Năm có quý: số năm = tổng các quý (dòng số dư lấy Q4), cùng một vòng tính với kiểu xem Quý. "
                        "Bấm vào ô quý để xem công thức và giả định của từng quý.")

    def _subtotal_formula(self, line: str) -> str:
        return " ".join(f"{'+' if sg > 0 else '−'} {self.labels.get(c, c)}" for sg, c in self.subtotals[line]).lstrip("+ ")

    def margin_bridge(self, period: str, cmp_p: str, line: str | None = None) -> tuple[list[BridgeItem], list[DriverRow]]:
        """Dòng = cơ sở × biên (khai báo trong family.margin_lines): tác động cơ sở (giữ biên kỳ so sánh) + tác động biên."""
        line = line or next(iter(self.margin_lines), None)
        if line not in self.margin_lines:
            return [], []
        base_line, margin_aid, mlabel, margin_drill = self.margin_lines[line]
        r1, _ = self.value(base_line, period)
        r0, _ = self.value(base_line, cmp_p)
        g1, _ = self.value(line, period)
        g0, _ = self.value(line, cmp_p)
        if None in (r1, r0, g1, g0) or r0 <= 0 or r1 <= 0:
            return [], []
        m0, m1 = g0 / r0, g1 / r1
        if self.is_actual(period):
            src = "số thực tế"
        else:
            src = self._source(margin_aid, period) if margin_aid in self.r.assumptions else "kết quả method L2/L3 đang chọn"
        blabel = self.labels.get(base_line, base_line)
        short = mlabel.replace(" lợi nhuận", "")
        rows = [DriverRow("revenue", blabel, "VND", r1, r0, "số thực tế" if self.is_actual(period) else "kết quả dòng"),
                DriverRow("gm", mlabel, "ratio", m1, m0, src, margin_aid)]
        return [BridgeItem(f"{blabel} {fmt(r0)} → {fmt(r1)} (giữ {short.lower()} {fmt(m0, 'ratio')})",
                           (r1 - r0) * m0, "driver", base_line),
                BridgeItem(f"{short} {fmt(m0, 'ratio')} → {fmt(m1, 'ratio')}", (m1 - m0) * r1, "driver", margin_drill)], rows

    def _driver_bridge(self, tr, ex: Explanation) -> list[BridgeItem]:
        if ex.prior_value is None or ex.value is None:
            return []
        prior = {d.key: d.prior for d in ex.drivers}
        if any(v is None for v in prior.values()):
            missing = [d.label for d in ex.drivers if d.prior is None]
            return [BridgeItem("Thay đổi tổng (không tách được: kỳ so sánh không có số tương đương cho "
                               + ", ".join(missing) + ")", ex.value - ex.prior_value, "residual")]
        items = []
        cur = dict(prior)
        try:
            model = float(tr.fn(cur))
        except (ZeroDivisionError, TypeError):
            return [BridgeItem("Thay đổi tổng", ex.value - ex.prior_value, "residual")]
        gap = ex.prior_value - model
        if abs(gap) > max(1e6, 1e-6 * abs(ex.prior_value)):
            items.append(BridgeItem(f"Phần {ex.compare_period} không thuộc công thức (số thực tế khác cách tính)",
                                    -gap, "residual"))
        for d in ex.drivers:
            if d.current is None:
                continue
            cur[d.key] = d.current
            new = float(tr.fn(cur))
            if abs(new - model) > 0:
                items.append(BridgeItem(f"{d.label}: {fmt(d.prior, d.unit)} → {fmt(d.current, d.unit)}", new - model,
                                        "driver", d.drill))
            model = new
        rest = ex.value - model
        if abs(rest) > max(1e6, 1e-6 * abs(ex.value)):
            items.append(BridgeItem("Khác (làm tròn/giới hạn trong công thức)", rest, "residual"))
        return items

    # ------------------------------------------------------------ lũy kế năm và quý bất thường
    def _ytd(self, line: str, period: str) -> dict | None:
        if self.freq != FREQ_ANNUAL or period != shift_label(self.origin, 1):
            return None
        y = period
        qs = [q for q in self.h.periods_by(FREQ_QUARTER) if q.startswith(f"{y}Q")]
        if not qs:
            return None
        lines = list(self.r.line_meta.index)
        src = _quarter_source(self.h)
        stmt = self.r.line_meta["statement"].get(line)
        all_q = [q for q in self.h.periods_by(FREQ_QUARTER) if q <= qs[-1]]
        hist = _history_values(src, lines, all_q[-(len(qs) + 8):], self.template.template_id)
        if line not in hist.index or hist.loc[line, qs].isna().all():
            return None
        quarters = {q: float(hist.loc[line, q]) for q in qs if not pd.isna(hist.loc[line, q])}
        is_stock = stmt == "BS"
        total = quarters[qs[-1]] if is_stock else sum(quarters.values())
        out = {"label": f"{3 * len(qs)}T{y}", "value": total, "quarters": quarters, "stock": is_stock, "anomalies": []}
        if not is_stock:
            before = hist.loc[line, [q for q in hist.columns if q not in qs]].dropna()
            med = float(before.abs().median()) if len(before) else None
            for q, v in quarters.items():
                if med and abs(v) > ANOMALY_MULTIPLE * med:
                    out["anomalies"].append({"quarter": q, "value": v, "multiple": abs(v) / med, "median": med})
            fc, _ = self.value(line, period)
            msg = f"{out['label']} thực tế {fmt(total)}"
            if out["anomalies"]:
                a = max(out["anomalies"], key=lambda x: x["multiple"])
                msg += (f", trong đó {a['quarter']} là {fmt(a['value'])} — gấp {a['multiple']:,.1f} lần trung vị 8 quý "
                        f"trước ({fmt(a['median'])}/quý), nhiều khả năng là khoản bất thường")
            if fc is not None and period in self.agg_years:
                msg += (f". Cả năm {period} = {fmt(fc)} = lũy kế thực tế + các quý dự phóng của engine quý "
                        f"({fmt(fc - total)}); quý dự phóng dùng chuỗi quý đã loại khoản bất thường.")
            elif fc is not None:
                msg += (f". Dự phóng cả năm {period} = {fmt(fc)} dùng giả định năm (không lặp lại số liệu từng quý"
                        f"{'; khoản bất thường không được đưa vào' if out['anomalies'] else ''}); phần còn lại của năm "
                        f"ngụ ý {fmt(fc - total)}.")
                rest = fc - total
                if total > 0 and rest < 0 and stmt == "IS":
                    msg += (" ⚠ Phần còn lại của năm âm trong khi lũy kế dương: giả định năm đang thấp hơn mức đã thực "
                            "hiện, cần xem lại.")
            out["note"] = msg
        return out

    def _headline(self, ex: Explanation) -> str:
        if ex.value is None:
            return f"{ex.label} {ex.period}: chưa tính được."
        txt = f"{ex.label} {ex.period} = {fmt(ex.value, ex.unit)}"
        if ex.prior_value is not None:
            cost = ex.value <= 0 and ex.prior_value <= 0 and (ex.value or ex.prior_value)
            ch = (abs(ex.value) - abs(ex.prior_value)) if cost else ex.value - ex.prior_value
            pct = f" ({ch / abs(ex.prior_value):+.1%})" if ex.prior_value else ""
            what = "chi phí " if cost else ""
            txt += (f", {what}{'tăng' if ch >= 0 else 'giảm'} {fmt(abs(ch), ex.unit)}{pct} so với {ex.compare_text} "
                    f"{ex.compare_period}{' (thực tế)' if ex.prior_is_actual else ''} = {fmt(ex.prior_value, ex.unit)}")
        main = [b for b in ex.bridge if b.kind != "residual"] or ex.bridge
        if main and ex.prior_value is not None:
            top = max(main, key=lambda b: abs(b.amount))
            txt += (f". Yếu tố chính: {top.label} ({'+' if top.amount >= 0 else '−'}{fmt(abs(top.amount), ex.unit)} vào "
                    f"giá trị dòng)")
        return txt + "."


def _align_one_off(tr, prior_tr):
    """Kỳ so sánh có khoản bất thường mà kỳ này không có: thêm yếu tố đó (= 0) để cầu nối tách được phần đảo chiều."""
    if prior_tr is None or any(d.key == "one_off" for d in tr.drivers):
        return tr
    old = next((d for d in prior_tr.drivers if d.key == "one_off"), None)
    if old is None:
        return tr
    fn = tr.fn
    d = Driver("one_off", old.label, "VND", 0.0, ("const", 0.0, "kỳ này không có khoản bất thường"))
    return Trace(tr.line, tr.formula + " (kỳ so sánh có khoản bất thường)", tuple(tr.drivers) + (d,),
                 lambda x: fn(x) + x["one_off"])


def profit_bridge(h, result, period: str, line: str = "npat_parent", compare: str = "prior",
                  explainer: Explainer | None = None) -> list[BridgeItem]:
    """Phân rã thay đổi một dòng lợi nhuận về các yếu tố gốc của KQKD: lãi gộp tách tác động doanh thu và biên gộp,
    các dòng chi phí/thu nhập còn lại theo chênh lệch của từng dòng (bấm vào dòng để xem yếu tố của dòng đó)."""
    ex = explainer or Explainer(h, result)
    cmp_p = matching_prior(period) if compare == "yoy" else shift_label(period, -1)
    out: list[BridgeItem] = []

    def one_off(l: str, p: str) -> float:
        if ex.is_actual(p):
            return ex._hist_driver(("one_off", l), p) or 0.0 if l in ex.one_off_lines else 0.0
        t = result.traces.get((l, p))
        d = next((x for x in t.drivers if x.key == "one_off"), None) if t is not None else None
        return d.value if d is not None else 0.0

    def walk(l: str, sign: int):
        if l in ex.margin_lines:
            items, _ = ex.margin_bridge(period, cmp_p, l)
            out.extend(BridgeItem(b.label, sign * b.amount, "driver", l) for b in items)
            return
        if l in ex.subtotals:
            for sg, comp in ex.subtotals[l]:
                walk(comp, sign * sg)
            return
        cur, _ = ex.value(l, period)
        old, _ = ex.value(l, cmp_p)
        if cur is not None and old is not None:
            k1, k0 = one_off(l, period), one_off(l, cmp_p)
            if k1 or k0:  # khoản bất thường đã xác nhận (kỳ này hoặc kỳ so sánh): tách thành một bước riêng
                lab = "Khoản bất thường đã ghi nhận"
                out.append(BridgeItem(f"{lab} ({ex.labels.get(l, l)})", sign * (k1 - k0), "driver", l))
                out.append(BridgeItem(f"{ex.labels.get(l, l)} (không gồm khoản bất thường)",
                                      sign * ((cur - k1) - (old - k0)), "component", l))
            else:
                out.append(BridgeItem(ex.labels.get(l, l), sign * (cur - old), "component", l))

    walk(line, 1)
    return sorted(out, key=lambda b: -abs(b.amount))


__all__ = ["Explainer", "Explanation", "BridgeItem", "DriverRow", "profit_bridge", "fmt", "period_days",
           "register_hist", "HIST_RESOLVERS"]
