"""Engine quý + năm (owner 2026-11-18, thay phân bổ năm -> quý).

Kịch bản tạo theo năm (năm gốc = báo cáo cả năm mới nhất). Một vòng tính duy nhất:
- các quý từ sau quý công bố mới nhất đến hết Q4 năm sau năm của quý đó chạy theo QUÝ (số dư đầu = BS quý công bố
  mới nhất, lãi trên số dư đầu quý, ngày thực tế của quý);
- các năm sau đó chạy theo NĂM, nối tiếp số dư Q4.
Số năm của một năm có quý = tổng các quý (quý đã công bố + quý dự phóng; dòng số dư lấy Q4), nên số năm luôn giống nhau
dù xem theo năm hay theo quý.

Default quý của giả định có chỉ số lịch sử: x_q = λ × EMA span 8 quý liên tiếp (cuốn chiếu: giá trị dự phóng nối vào
chuỗi) + (1 − λ) × default năm của năm chứa quý (cách tính năm giữ nguyên). λ = 0,65. Chuỗi quý đã loại khoản bất
thường. Giả định trong `annual_only` (dồn cục theo năm, ràng buộc) dùng thẳng giá trị năm. Số tiền/sản lượng dạng luồng
(đơn vị VND, tấn, cửa hàng; trừ giả định số dư) chia theo số ngày của quý.

Analyst ghi đè số năm của một năm có quý: coi là mục tiêu năm. Quý còn lại nhận giá trị sao cho chỉ tiêu năm (tính như
chỉ số lịch sử năm) bằng số nhập, phần chênh so với các quý đã công bố dồn vào các quý còn lại (`targets`: tỷ lệ trên
cơ sở, số ngày, tăng trưởng dòng luồng, số tiền). Lượt 1 chạy với số nhập cho mọi quý để lấy cơ sở của quý dự phóng;
sau đó giải và chạy lại đúng TARGET_ROUNDS = 2 vòng (vòng 2 xử lý các giả định ghi đè tác động lẫn nhau, ví dụ tăng
trưởng và biên gộp cùng ghi đè); không lặp đến hội tụ, sai số còn lại rất nhỏ. Giả định không khai báo mục tiêu dùng số nhập cho mọi quý (tăng trưởng số dư so cùng kỳ: Q4 khớp
đúng số năm).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quick_model.engine.assumptions import AssumptionValue, OVERRIDE_SOURCES
from quick_model.periods import (FREQ_QUARTER, fiscal_year, period_days, shift_label, year_days, year_quarters)

DEFAULT_LAMBDA = 0.65
DEFAULT_SPAN = 8
TARGET_ROUNDS = 2  # số vòng giải mục tiêu năm (cố định; vòng 2 xử lý các giả định ghi đè tác động lẫn nhau)
FLOW_UNITS = ("VND", "tonnes", "stores")
HISTORY_QUARTERS = 40  # cửa sổ chuỗi quý cho EMA (10 năm)

# Gộp quý -> năm: dòng số dư lấy Q4; tiền đầu kỳ, backlog đầu kỳ lấy quý đầu; số dư bình quân lấy trung bình; đơn giá
# bình quân gia quyền theo sản lượng; còn lại cộng.
LAST_LINES = {"total_assets", "total_resources", "balance_residual", "total_debt", "cash_close", "ppe_and_cip",
              "gross_cost", "retained_earnings_available", "min_cash_level", "auto_outstanding", "debt_cap",
              "funding_gap", "store_count", "backlog_close", "deposits_before_cap", "ldr_cap_uplift",
              "liquidity_surplus", "interbank_floor", "interbank_cap", "auto_interbank_borrowing", "revenue_per_store"}
FIRST_LINES = {"cash_open", "backlog_open"}
MEAN_LINES = {"avg_loans", "avg_iea", "avg_ibl", "avg_store_count"}
WEIGHTED_LINES = {"asp": "sales_volume", "unit_cogs": "sales_volume"}


@dataclass
class Plan:
    origin: str  # năm gốc
    years: list[str]  # mọi năm dự phóng
    q_origin: str | None = None  # quý công bố mới nhất (số dư đầu của phần quý)
    quarters: list[str] = field(default_factory=list)  # quý dự phóng
    q_years: list[str] = field(default_factory=list)  # năm có quý (số năm = tổng quý)
    annual: list[str] = field(default_factory=list)  # năm chạy theo năm
    actual: dict = field(default_factory=dict)  # năm có quý -> quý đã công bố của năm đó
    note: str = ""

    @property
    def periods(self) -> list[str]:
        return self.quarters + self.annual

    @property
    def hybrid(self) -> bool:
        return bool(self.quarters)

    def year_index(self, label: str) -> int:
        return fiscal_year(label) - int(self.origin) - 1


def make_plan(h, origin: str, horizon: int) -> Plan:
    """Lịch kỳ: quý đến hết Q4 năm sau năm của quý công bố mới nhất (không quá năm dự phóng cuối), rồi năm."""
    years = [str(int(origin) + i) for i in range(1, horizon + 1)]
    out = Plan(origin, years, annual=list(years))
    qs = list(h.periods_by(FREQ_QUARTER))
    if not qs:
        out.note = "Không có số quý: chạy theo năm."
        return out
    last = qs[-1]
    if last < f"{origin}Q4":
        out.note = f"Quý công bố mới nhất {last} trước Q4 năm gốc {origin}: chạy theo năm."
        return out
    end_year = min(fiscal_year(last) + 1, int(years[-1]))
    quarters, q = [], shift_label(last, 1)
    while fiscal_year(q) <= end_year:
        quarters.append(q)
        q = shift_label(q, 1)
    if not quarters:
        return out
    q_years = sorted({str(fiscal_year(x)) for x in quarters})
    have = set(qs)
    actual = {}
    for y in q_years:
        done = [x for x in year_quarters(y) if x <= last]
        if any(x not in have for x in done):
            out.note = f"Thiếu số quý của năm {y}: chạy theo năm."
            return out
        actual[y] = done
    if int(q_years[0]) != int(origin) + 1:
        out.note = f"Quý công bố mới nhất {last} không liền năm gốc {origin}: chạy theo năm."
        return out
    return Plan(origin, years, last, quarters, q_years, [y for y in years if int(y) > int(q_years[-1])], actual)


# ---------------------------------------------------------------- giả định theo kỳ

def policy(tpl) -> dict:
    cfg = dict(getattr(tpl, "quarterly_engine", {}) or {})
    cfg.setdefault("lambda", DEFAULT_LAMBDA)
    cfg.setdefault("ema_span_quarters", DEFAULT_SPAN)
    cfg["stocks"] = set(cfg.get("stock_assumptions", ())) | set(cfg.get("extra_stock_assumptions", ()))
    cfg["annual_only"] = set(cfg.get("annual_only", ()))
    cfg["quarter_only"] = set(cfg.get("quarter_only", ()))  # λ = 1: từ quý đi lên năm (ngân hàng: AEY, COF)
    # v2.5: EMA quý có trọng số cho chỉ số khai báo cặp tử số/mẫu số; thống kê quý riêng theo giả định
    # (ttm_momentum, median, mean; kèm window, phi, lambda)
    cfg["weighted_ema"] = bool(cfg.get("weighted_ema", False))
    cfg["quarter_stats"] = dict(cfg.get("quarter_stats", {}))
    # v2.6: neo default quý về cùng quý năm trước, trọng số theo số quý còn lại của năm (anchor_weight)
    cfg["anchor"] = dict(cfg.get("anchor", {}) or {})
    cfg.setdefault("targets", {})
    cfg["caps"] = dict(cfg.get("caps", {}) or {})
    return cfg


def anchor_weight(plan: Plan, cfg: dict, period: str) -> float:
    """Trọng số neo năm trước cho một kỳ của vòng tính. Năm đang chạy (năm của quý dự phóng đầu tiên):
    weight_by = 'remaining' (v2.6): w = weight × số quý còn phải dự phóng / 4, như nhau cho mọi quý của năm;
    weight_by = 'horizon' (v2.6.1): w = weight × min(h, 4) / 4, h = số quý tính từ quý công bố mới nhất (quý gần thì
    neo ít). Năm sau theo year_decay (năm +1, +2 trở đi)."""
    an = cfg.get("anchor") or {}
    if not an.get("assumptions") or not plan.quarters:
        return 0.0
    base = float(an.get("weight", 0.0))
    decay = list(an.get("year_decay", [1.0]))
    y0 = fiscal_year(plan.quarters[0])
    k = fiscal_year(period) - y0
    if k <= 0:
        if an.get("weight_by", "remaining") == "horizon" and period in plan.quarters:
            return base * min(plan.quarters.index(period) + 1, 4) / 4.0
        remaining = sum(1 for q in plan.quarters if fiscal_year(q) == y0)
        return base * remaining / 4.0
    return base * float(decay[min(k, len(decay) - 1)])


def anchor_trend(an: dict, values: dict, idx: int) -> float:
    """Điểm neo của tăng trưởng doanh thu so cùng kỳ: 'zero' (v2.6) = 0; revenue_target = id giả định (v2.6.1, vd
    long_run_growth) = giá trị năm idx của giả định đó (không có thì 0)."""
    src = an.get("revenue_target")
    av = values.get(src) if src else None
    if av is None or not av.values:
        return 0.0
    v = av.values[min(max(idx, 0), len(av.values) - 1)]
    return 0.0 if v is None else float(v)


def _anchor_quarters(plan: Plan, cfg: dict, out: dict, hist: dict, annual: dict | None = None) -> None:
    """x_q = (1 − w) × default quý + w × neo_q; neo_q = giá trị cùng quý năm trước (thực tế nếu đã công bố, ngược lại
    giá trị đã neo của quý đó); kiểu 'zero' (tăng trưởng so cùng kỳ) neo về 0."""
    an = cfg.get("anchor") or {}
    for aid, kind in (an.get("assumptions") or {}).items():
        av = out.get(aid)
        if av is None or not av.values:
            continue
        series = hist.get(aid)
        vals, per = list(av.values), {}
        done: dict[str, float] = {}
        for i, q in enumerate(plan.quarters):
            x, w = vals[i], anchor_weight(plan, cfg, q)
            ref = None
            if kind == "zero":
                ref = anchor_trend(an, annual or {}, plan.year_index(q))
            else:
                mp = shift_label(q, -4)
                if mp in done:
                    ref = done[mp]
                elif series is not None and mp in series.index and pd.notna(series[mp]):
                    ref = float(series[mp])
                    den = hist.get(f"{aid}__den")
                    if an.get("den_weighted") and den is not None and mp in den.index and pd.notna(den[mp]):
                        recent = den.loc[:plan.q_origin].dropna().iloc[-4:]  # neo theo trọng số mẫu số của quý năm trước
                        if len(recent) and recent.abs().mean() > 0:
                            w = w * min(1.0, abs(float(den[mp])) / float(recent.abs().mean()))
            if x is not None and ref is not None and w > 0:
                x = (1 - w) * x + w * ref
                per[q] = {"weight": w, "anchor": ref, "default": vals[i], "value": x}
            vals[i] = x
            if x is not None:
                done[q] = x
        if per:
            tr = an.get("revenue_target")
            what = ((f"tăng trưởng xu hướng ({tr})" if tr else "0 (doanh thu = cùng quý năm trước)")
                    if kind == "zero" else "giá trị cùng quý năm trước")
            wtxt = ("min(số quý tới, 4) / 4" if an.get("weight_by", "remaining") == "horizon"
                    else "số quý còn lại của năm / 4")
            note = (f"Neo năm trước (L1 v2.6): (1 − w) × default quý + w × {what}; w = "
                    f"{float(an.get('weight', 0)):g} × {wtxt}, năm sau giảm dần"
                    + ("; tỷ lệ trên doanh thu: w × min(1, mẫu số quý năm trước / bình quân 4 quý gần nhất) (v2.6.3)."
                       if an.get("den_weighted") and kind != "zero" else "."))
            basis = {**av.basis, "anchor": per,
                     "formula_q": f"{av.basis.get('formula_q', '')}; {note}".strip("; "),
                     "note_q": f"{av.basis.get('note_q', '')} {note}".strip()}
            out[aid] = AssumptionValue(aid, tuple(vals), av.source, basis)


def _cap_quarters(cfg: dict, out: dict) -> None:
    """caps {giả định: giả định tham chiếu} (owner 2026-11-23): giá trị quý không vượt giá trị quý cùng kỳ của giả định
    tham chiếu. Bỏ qua khi giả định là số analyst nhập (số nhập thắng)."""
    for aid, ref in (cfg.get("caps") or {}).items():
        av, rv = out.get(aid), out.get(ref)
        if av is None or rv is None or av.source in OVERRIDE_SOURCES:
            continue
        vals = tuple(x if x is None or r is None else min(x, r) for x, r in zip(av.values, rv.values))
        if vals != tuple(av.values):
            note = f"Không vượt {ref} cùng quý (caps, owner 2026-11-23)."
            out[aid] = AssumptionValue(aid, vals, av.source, {
                **av.basis, "cap_q": ref, "formula_q": f"{av.basis.get('formula_q', '')}; {note}".strip("; "),
                "note_q": f"{av.basis.get('note_q', '')} {note}".strip()})


def is_flow(spec, cfg) -> bool:
    return spec.unit in FLOW_UNITS and spec.assumption_id not in cfg["stocks"]


def _split(value, q: str) -> float | None:
    return None if value is None else value * period_days(q) / year_days(fiscal_year(q))


def _ema(values: list[float], span: float) -> float:
    return float(pd.Series(values, dtype="float64").ewm(span=span, adjust=True).mean().iloc[-1])


def quarter_defaults(h, plan: Plan, tpl, family, annual: dict[str, AssumptionValue], one_offs: tuple) -> dict[str, AssumptionValue]:
    """Default từng quý dự phóng (số theo thứ tự plan.quarters) từ default năm và chuỗi quý lịch sử."""
    from quick_model import oneoff
    cfg = policy(tpl)
    lam, span = float(cfg["lambda"]), float(cfg["ema_span_quarters"])
    qm = family.metrics(h, plan.q_origin, False, oneoff.history_adjustments(one_offs, tpl, False))
    days = qm.q["days"]
    out: dict[str, AssumptionValue] = {}
    copies = {}
    hist: dict[str, pd.Series] = {}  # chuỗi quý lịch sử của giả định (neo năm trước, v2.6)
    specs = tpl.specs
    for aid, spec in specs.items():
        av = annual.get(aid)
        if av is None:
            continue
        d = tpl.default_specs.get(aid, {})
        yearly = [av.values[plan.year_index(q)] for q in plan.quarters]
        if d.get("type") == "copy":
            copies[aid] = d
            continue
        if is_flow(spec, cfg):
            vals = tuple(_split(v, q) for v, q in zip(yearly, plan.quarters))
            out[aid] = AssumptionValue(aid, vals, av.source, {**av.basis, "quarterly": "split_days",
                                                             "note_q": "Số năm chia theo số ngày của quý."})
            continue
        series = pair = None
        st = cfg["quarter_stats"].get(aid, {})
        if aid not in cfg["annual_only"]:
            if d.get("type") == "history":
                series, formula = qm.series(d["metric"])
                if st.get("pair"):  # cặp tử số/mẫu số khai báo riêng (vd CĐKKS/LNST gộp nhiều quý)
                    pair = qm.pair(st["pair"])
                elif cfg["weighted_ema"] or st.get("stat") == "ttm_momentum":
                    pair = qm.pair(d["metric"])
            elif d.get("type") == "cost_structure" and d.get("part") == "variable":
                fixed_id = next((k for k, x in tpl.default_specs.items() if x.get("type") == "cost_structure"
                                 and x.get("part") == "fixed" and x.get("expense_metric") == d["expense_metric"]), None)
                fixed = annual[fixed_id].values[0] if fixed_id in annual else None
                if fixed is not None:
                    exp, _ = qm.series(d["expense_metric"])
                    rev, _ = qm.series("revenue_amount")
                    series = ((exp - fixed * days / 365.0) / rev.where(rev > 0))  # doanh thu ≤ 0: NaN, không sinh ±inf
                    formula = "(chi phí quý − cố định năm × ngày/365) / doanh thu quý"
                    if cfg["weighted_ema"]:
                        pair = (exp - fixed * days / 365.0, rev.where(rev > 0))
        if series is not None:
            hist[aid] = ((pair[0] / pair[1]) if pair is not None else series).replace([np.inf, -np.inf], np.nan)
            if pair is not None:
                hist[f"{aid}__den"] = pair[1]
        if series is None:
            out[aid] = AssumptionValue(aid, tuple(yearly), av.source,
                                       {**av.basis, "quarterly": "annual",
                                        "note_q": "Giả định theo năm (λ = 0): quý dùng giá trị năm."
                                        if aid in cfg["annual_only"] else "Quý dùng giá trị năm."})
            continue
        stat = st.get("stat", "ema")
        if pair is not None:  # chuỗi (tử số, mẫu số) cùng kỳ, mẫu số > 0
            num, den = pair
            ok = num.loc[:plan.q_origin].notna() & den.loc[:plan.q_origin].notna() & (den.loc[:plan.q_origin] != 0)
            idx = list(num.loc[:plan.q_origin][ok].index)[-HISTORY_QUARTERS:]
            cn, cd = [float(num[i]) for i in idx], [float(den[i]) for i in idx]
            chain = [a / b for a, b in zip(cn, cd)]
        else:
            chain = [float(x) for x in series.loc[:plan.q_origin].iloc[-HISTORY_QUARTERS:].dropna()]
        win = int(st.get("window", 4))
        short = 0 < len(chain) < (span if stat == "ema" else win)  # chưa đủ chuỗi: số kỳ liền trước (owner 2026-11-18)
        lam_a = 1.0 if aid in cfg["quarter_only"] else float(st.get("lambda", lam))
        vals, per = [], {}
        for q, y in zip(plan.quarters, yearly):
            if not chain:
                e = None
            elif short:
                e = chain[-1]
            elif stat == "ttm_momentum" and pair is not None:  # TTM có trọng số + φ × (quý gần nhất − TTM)
                ttm = sum(cn[-win:]) / sum(cd[-win:])
                if st.get("momentum_by_den"):  # quán tính theo trọng số mẫu số: quý doanh thu gần 0 gần như không tác động
                    e = ttm + float(st.get("phi", 0.25)) * (cn[-1] - ttm * cd[-1]) / (sum(cd[-win:]) / win)
                else:
                    e = ttm + float(st.get("phi", 0.25)) * (chain[-1] - ttm)
            elif stat == "median":
                e = float(pd.Series(chain[-win:]).median())
            elif stat == "mean":
                e = float(pd.Series(chain[-win:]).mean())
            elif pair is not None:  # EMA có trọng số: EMA tử số / EMA mẫu số
                e = _ema(cn, span) / _ema(cd, span)
            else:
                e = _ema(chain, span)
            if e is None:
                x = y
            elif y is None:
                x = e if lam_a == 1.0 else None
            else:
                x = lam_a * e + (1 - lam_a) * y
            vals.append(x)
            per[q] = {"ema_q": e, "annual": y, "value": x, "lambda": lam_a if e is not None else 0.0}
            if x is not None:
                chain.append(x)
                if pair is not None:  # quý dự phóng nối vào với mẫu số trung tính (bình quân gần đây)
                    d_bar = (sum(cd[-win:]) / len(cd[-win:])) if stat == "ttm_momentum" else _ema(cd, span)
                    cn.append(x * d_bar)
                    cd.append(d_bar)
        what = ("số quý liền trước" if short else
                {"ttm_momentum": f"TTM {win} quý (Σ tử số / Σ mẫu số) + {float(st.get('phi', 0.25)):g} × (quý gần nhất − TTM)",
                 "median": f"trung vị {win} quý", "mean": f"bình quân {win} quý"}.get(stat)
                or (f"EMA {span:g} quý có trọng số (EMA tử số / EMA mẫu số)" if pair is not None else f"EMA {span:g} quý"))
        basis = {**av.basis, "quarterly": "quarter_only" if lam_a == 1.0 else "blend", "lambda": lam_a,
                 "ema_span_quarters": span, "per_quarter": per, "short_history_q": short, "what_q": what,
                 "formula_q": (f"{what} cuốn chiếu ({formula}); từ quý đi lên năm, không gộp giá trị năm"
                               if lam_a == 1.0 else f"{lam_a:g} × {what} ({formula}) + {1 - lam_a:g} × giá trị năm"),
                 "sample_q": {p: float(v) for p, v in series.loc[:plan.q_origin].iloc[-HISTORY_QUARTERS:].dropna().items()}}
        if not any(p["ema_q"] is not None for p in per.values()):
            basis["note_q"] = "Không có chuỗi quý hợp lệ: quý dùng giá trị năm."
        out[aid] = AssumptionValue(aid, tuple(vals), av.source, basis)
    rc = cfg.get("revenue_components")
    if rc and rc.get("assumption") in out and rc.get("assumption") in annual:
        aid = rc["assumption"]
        rev = revenue_components(h, plan, rc, annual[aid])
        if rev is not None:
            # quý không dựng được từ 3 thành phần (nền ≤ 0, thiếu quý): dùng default quý thông thường, không để trống
            base_vals = out[aid].values
            gaps = [q for q, v in zip(plan.quarters, rev.values) if v is None]
            vals = tuple(b if v is None else v for v, b in zip(rev.values, base_vals))
            out[aid] = AssumptionValue(aid, vals, rev.source, {**rev.basis, "fallback_quarters": gaps}) if gaps else rev
    _anchor_quarters(plan, cfg, out, hist, annual)
    _cap_quarters(cfg, out)
    for aid, d in copies.items():
        base = out.get(d["from"])
        if base is not None:
            out[aid] = AssumptionValue(aid, base.values, base.source, {**base.basis, "copied_from": d["from"]})
    return out


def seasonal_shares(rev: pd.Series, last: str, span_years: float = 5, window_years: int = 10,
                    stat: str = "ema") -> tuple[dict, dict]:
    """Tỷ trọng quý trong năm của dòng doanh thu trên tối đa `window_years` năm đủ 4 quý (năm có quý ≤ 0 bị loại),
    chuẩn hóa tổng = 1. stat "ema": EMA span `span_years` năm; "median": trung vị các năm trong cửa sổ. Không có năm
    hợp lệ: theo số ngày của quý."""
    years = sorted({q[:4] for q in rev.index if q <= last})
    rows = {}
    for y in years:
        qs = year_quarters(y)
        if all(q in rev.index and pd.notna(rev[q]) and rev[q] > 0 and q <= last for q in qs):
            tot = sum(float(rev[q]) for q in qs)
            rows[y] = [float(rev[q]) / tot for q in qs]
    rows = dict(list(rows.items())[-window_years:])
    if not rows:
        d = [period_days(f"2025Q{i}") for i in range(1, 5)]
        return {i + 1: d[i] / sum(d) for i in range(4)}, {"method": "days", "years": []}
    df = pd.DataFrame(rows).T
    if stat == "median":
        sh = {i + 1: float(df[i].median()) for i in range(4)}
    else:
        sh = {i + 1: float(df[i].ewm(span=span_years, adjust=True).mean().iloc[-1]) for i in range(4)}
    tot = sum(sh.values())
    return {k: v / tot for k, v in sh.items()}, {"method": stat, "years": list(rows), "span_years": span_years}


def revenue_components(h, plan: Plan, rc: dict, annual_av: AssumptionValue) -> AssumptionValue | None:
    """Doanh thu quý (owner 2026-11-18, phương án E): R_q = w_qoq × QoQ + w_yoy × YoY + w_năm × năm.

    - Mức theo năm sau khử mùa vụ: L(t) = R(t) / S(quý của t); mức hiện hành L_run = bình quân L của `level_quarters`
      quý liền trước (thực tế hoặc dự phóng, cuốn chiếu) — một quý đột biến đơn lẻ chỉ tác động một nửa.
    - Nền đã điều chỉnh mức (thay cho cùng quý năm trước) B_q = L_run / (1 + g_năm)^(d/4) × S(q), d = số quý từ giữa
      các quý dùng tính L_run tới quý q − 4: mức hiện hành lùi về thời điểm cùng kỳ năm trước theo tăng trưởng năm.
    - QoQ = R(q − 1) × S(q) / S(q − 1) × (1 + g_năm)^(1/4); YoY = B_q × (1 + EMA 8 quý của YoY điều chỉnh);
      năm = B_q × (1 + g_năm). Chuỗi YoY lịch sử là YoY thực tế; quý dự phóng nối YoY điều chỉnh R_q / B_q − 1, nên
      bước nhảy mức không bị nhân tiếp sang năm sau.
    Giá trị giả định quý = R_q / R(q − 4) − 1 (tăng trưởng so cùng kỳ hiệu dụng), để method L1 tái tạo đúng R_q."""
    code = rc.get("code", "ISA3")
    flows = h.flows("IS")
    if code not in flows.columns:
        return None
    R = {k: float(v) for k, v in flows[code].loc[:plan.q_origin].items() if pd.notna(v)}
    hist = [q for q in R if q <= plan.q_origin]
    n = int(rc.get("level_quarters", 2))
    if len(hist) < n + 4 or any(shift_label(plan.q_origin, -i) not in R for i in range(n)):
        return None
    w = rc.get("weights", {})
    wq, wy, wa = float(w.get("qoq", 0.5)), float(w.get("yoy", 0.15)), float(w.get("year", 0.35))
    span = float(rc.get("ema_span_quarters", DEFAULT_SPAN))
    cap_m = rc.get("max_to_avg4")
    S, s_basis = seasonal_shares(pd.Series(R), plan.q_origin, float(rc.get("seasonal_span_years", 5)),
                                 int(rc.get("seasonal_window_years", 10)), rc.get("seasonal_stat", "ema"))
    alpha = rc.get("seasonal_shrink")
    S_qoq = S
    if alpha is not None:  # co biên độ mùa vụ về phía đều: S' = 0,25 + α × (S − 0,25) (tổng vẫn = 1)
        shrunk = {k: 0.25 + float(alpha) * (v - 0.25) for k, v in S.items()}
        if rc.get("seasonal_shrink_scope", "all") == "qoq":  # chỉ bước QoQ; quy mức năm dùng tỷ trọng gốc
            S_qoq = shrunk
        else:
            S = S_qoq = shrunk
        s_basis = {**s_basis, "shrink": float(alpha), "shrink_scope": rc.get("seasonal_shrink_scope", "all")}
    sq = lambda q: S[int(q[-1])]  # noqa: E731
    chain = []
    for q in hist[-HISTORY_QUARTERS:]:
        mp = shift_label(q, -4)
        if mp in R and R[mp] > 0 and R[q] > 0:
            chain.append(R[q] / R[mp] - 1)
    vals, per = [], {}
    for i, q in enumerate(plan.quarters):
        g = annual_av.values[plan.year_index(q)]
        mp, prev = shift_label(q, -4), shift_label(q, -1)
        if g is None or g <= -1 or mp not in R or prev not in R or R[mp] <= 0:  # g ≤ −100%: (1 + g)^x suy biến / số phức
            vals.append(None)
            continue
        last = [shift_label(q, -k) for k in range(1, n + 1)]
        l_run = sum(R[x] / sq(x) for x in last) / n
        d = 4 - (n + 1) / 2  # giữa các quý dùng tính mức hiện hành (q − 1,5 khi n = 2) lùi tới q − 4: 2,5 quý
        base = l_run / (1 + g) ** (d / 4) * sq(q)
        if base <= 0:  # nền doanh thu ≤ 0 (các quý liền trước không có doanh thu): không có cơ sở tỷ lệ YoY
            vals.append(None)
            continue
        e = (chain[-1] if 0 < len(chain) < span else _ema(chain, span)) if chain else g
        qoq = R[prev] * S_qoq[int(q[-1])] / S_qoq[(int(q[-1]) - 2) % 4 + 1] * (1 + g) ** 0.25
        yoy = base * (1 + e)
        year = base * (1 + g)
        r = wq * qoq + wy * yoy + wa * year
        last4 = [R.get(shift_label(q, -k)) for k in range(1, 5)]
        cap = None
        if cap_m and all(x is not None for x in last4) and sum(last4) > 0:
            # doanh thu dồn cục: không vượt cap_m × bình quân 4 quý gần nhất × (4 × tỷ trọng mùa vụ của quý) — giới hạn
            # theo mức doanh thu gần đây, không phải trần tăng trưởng; chặn chuỗi nhân liên tiếp khi nền gần 0
            cap = cap_m * sum(last4) / 4 * 4 * sq(q)
        capped = cap is not None and r > cap
        if capped:
            r = cap
        R[q] = r
        chain.append(r / base - 1)
        x = r / R[mp] - 1
        vals.append(x)
        per[q] = {"qoq": qoq, "yoy": yoy, "year": year, "base": base, "level_run": l_run, "ema_yoy": e,
                  "annual": g, "revenue": r, "value": x, "prior": R[mp], "cap": cap, "capped": capped}
    basis = {**annual_av.basis, "quarterly": "revenue_components", "weights": {"qoq": wq, "yoy": wy, "year": wa},
             "level_quarters": n, "max_to_avg4": cap_m, "seasonal_shares": S, "seasonal_basis": s_basis, "per_quarter": per,
             "formula_q": (f"Doanh thu quý = {wq:g} × QoQ mùa vụ + {wy:g} × YoY (EMA {span:g} quý trên nền điều chỉnh "
                           f"mức) + {wa:g} × tăng trưởng năm (trên nền điều chỉnh mức); giả định = doanh thu quý / "
                           "cùng quý năm trước − 1"),
             "note_q": ("Nền điều chỉnh mức = bình quân doanh thu khử mùa vụ của 2 quý liền trước, lùi về cùng kỳ năm "
                        "trước theo tăng trưởng năm, × tỷ trọng mùa vụ của quý.")}
    return AssumptionValue(annual_av.assumption_id, tuple(vals), annual_av.source, basis)


def period_arrays(plan: Plan, tpl, resolved: dict[str, AssumptionValue], qdefaults: dict[str, AssumptionValue]
                  ) -> tuple[dict[str, list], dict[str, AssumptionValue]]:
    """Mảng giả định theo kỳ của vòng tính (quý rồi năm) và giá trị quý hiệu lực (lượt 1: số năm analyst nhập áp đều)."""
    cfg = policy(tpl)
    specs = tpl.specs
    a: dict[str, list] = {}
    qvals: dict[str, AssumptionValue] = {}
    for aid, av in resolved.items():
        ann = [av.values[plan.year_index(y)] for y in plan.annual]
        if av.source in OVERRIDE_SOURCES or aid not in qdefaults:
            yearly = [av.values[plan.year_index(q)] for q in plan.quarters]
            flow = aid in specs and is_flow(specs[aid], cfg)
            if flow:
                # analyst nhập số năm: cả số năm chia cho các quý còn lại theo ngày (khai báo dòng thực tế thì lượt
                # sau trừ lũy kế đã công bố); default năm: chia theo tỷ trọng ngày của quý trong năm
                ytd = {y: 0.0 for y in plan.q_years} if av.source in OVERRIDE_SOURCES else None
                q = _amount_split(plan, cfg.get("targets", {}).get(aid), av, ytd)
            else:
                q = tuple(yearly)
            basis = {**av.basis, "quarterly": "override" if av.source in OVERRIDE_SOURCES else "annual"}
            qvals[aid] = AssumptionValue(aid, tuple(q), av.source, basis, av.override_reason)
        else:
            qvals[aid] = qdefaults[aid]
            if aid in cfg["quarter_only"] and qvals[aid].values and qvals[aid].values[-1] is not None:
                ann = [qvals[aid].values[-1]] * len(plan.annual)  # năm sau phần quý: giữ mức quý cuối
            kind = (cfg["anchor"].get("assumptions") or {}).get(aid)
            if kind and plan.annual:  # v2.6: phần chạy theo năm neo về năm trước (bình quân 4 quý cuối / 0)
                last4 = [v for v in qvals[aid].values[-4:] if v is not None]
                prev = 0.0 if kind == "zero" else (sum(last4) / len(last4) if last4 else None)
                for j, y in enumerate(plan.annual):
                    w = anchor_weight(plan, cfg, y)
                    if kind == "zero":
                        prev = anchor_trend(cfg["anchor"], resolved, plan.year_index(y))
                    if prev is not None and ann[j] is not None and w > 0:
                        ann[j] = (1 - w) * ann[j] + w * prev
                    if kind != "zero" and ann[j] is not None:
                        prev = ann[j]
        a[aid] = list(qvals[aid].values) + ann
    return a, qvals


def _amount_split(plan: Plan, target: dict | None, av: AssumptionValue, ytd: dict | None) -> tuple:
    """Số tiền năm -> quý: năm có quý đã công bố và có dòng thực tế: (số năm − lũy kế) chia các quý còn lại theo ngày."""
    out = []
    for q in plan.quarters:
        y = str(fiscal_year(q))
        v = av.values[plan.year_index(q)]
        if v is None:
            out.append(None)
            continue
        if ytd is not None and y in ytd:
            rem = [x for x in plan.quarters if str(fiscal_year(x)) == y]
            out.append((v - ytd[y]) * period_days(q) / sum(period_days(x) for x in rem))
        else:
            out.append(_split(v, q))
    return tuple(out)


# ---------------------------------------------------------------- mục tiêu năm khi analyst ghi đè

def _signed(expr: str) -> tuple[int, str]:
    return (-1, expr[1:]) if expr.startswith("-") else (1, expr)


def apply_targets(plan: Plan, tpl, resolved: dict, qvals: dict, a: dict, quarter_table: pd.DataFrame) -> list[str]:
    """Lượt 2: giải giá trị quý còn lại của giả định analyst ghi đè để chỉ tiêu năm = số nhập. Sửa `a` và `qvals`
    tại chỗ; trả về ghi chú. quarter_table: dòng × quý (thực tế các năm liên quan + quý dự phóng của lượt 1)."""
    cfg = policy(tpl)
    notes = []
    nq = len(plan.quarters)
    for aid, av in resolved.items():
        if av.source not in OVERRIDE_SOURCES or aid not in cfg["targets"]:
            continue
        t = cfg["targets"][aid]
        vals = list(qvals[aid].values)
        changed = False
        for y in plan.q_years:
            T = av.values[plan.year_index(y)]
            if T is None:
                continue
            rem = [q for q in plan.quarters if str(fiscal_year(q)) == y]
            done = plan.actual.get(y, [])
            idx = [plan.quarters.index(q) for q in rem]
            try:
                new = _solve(t, T, y, rem, done, quarter_table, resolved, plan, vals)
            except (KeyError, ZeroDivisionError, TypeError, ValueError):
                new = None
            if new is None:
                continue
            for i, x in zip(idx, new):
                vals[i] = x
            changed = True
        if changed:
            qvals[aid] = AssumptionValue(aid, tuple(vals), av.source,
                                         {**qvals[aid].basis, "quarterly": "target",
                                          "note_q": "Số năm analyst nhập là mục tiêu năm: phần chênh so với các quý "
                                                    "đã công bố dồn vào các quý còn lại."}, av.override_reason)
            a[aid][:nq] = vals
            notes.append(aid)
    return notes


def _line(table: pd.DataFrame, expr: str, cols) -> pd.Series:
    sg, line = _signed(expr)
    return sg * table.loc[line, list(cols)].astype(float)


def _solve(t: dict, T: float, y: str, rem: list[str], done: list[str], table: pd.DataFrame, resolved: dict,
           plan: Plan, vals: list) -> list[float] | None:
    kind = t["kind"]
    allq = year_quarters(y)
    if kind == "rate":
        x0 = [vals[plan.quarters.index(q)] for q in rem]
        if any(x is None or abs(x) < 1e-12 for x in x0):
            return None
        num = _line(table, t["num"], allq)
        fixed_q = pd.Series(0.0, index=allq)
        if t.get("fixed"):
            fv = resolved[t["fixed"]].values[plan.year_index(y)] or 0.0
            fixed_q = pd.Series({q: fv * period_days(q) / year_days(y) for q in allq})
        base = _base(t["base"], y, table)
        n_act = float(num[done].sum()) if done else 0.0
        units = [(float(num[q]) - fixed_q[q]) / x for q, x in zip(rem, x0)]
        u = sum(units)
        if abs(u) < 1e-9:
            return None
        c = (T * base + float(fixed_q.sum()) - n_act - float(fixed_q[rem].sum())) / u
        return [c] * len(rem)
    if kind == "days":
        q4 = f"{y}Q4"
        if q4 not in rem:
            return None
        flow = _line(table, t["flow"], allq)
        fy, f4 = float(flow.sum()), float(flow[q4])
        if f4 <= 0 or fy <= 0:
            return None
        out = [T] * len(rem)
        out[rem.index(q4)] = T * fy / 365.0 * period_days(q4) / f4
        return out
    if kind == "flow_growth":
        prev = [shift_label(q, -4) for q in allq]
        cur = _line(table, t["line"], allq)
        last = _line(table, t["line"], prev)
        base_rem = float(sum(last[shift_label(q, -4)] for q in rem))
        if base_rem <= 0:
            return None
        c = (float(last.sum()) * (1 + T) - float(cur[done].sum() if done else 0.0)) / base_rem - 1
        return [c] * len(rem)
    if kind == "amount":
        sg, line = _signed(t["line"])
        ytd = float((sg * table.loc[line, done].astype(float)).sum()) if done else 0.0
        tot = sum(period_days(q) for q in rem)
        return [(T - ytd) * period_days(q) / tot for q in rem]
    if kind == "run_rate":  # mức theo năm (× ngày / 365): số cả năm = T × số ngày năm / 365
        sg, line = _signed(t["line"])
        ytd = float((sg * table.loc[line, done].astype(float)).sum()) if done else 0.0
        tot = sum(period_days(q) for q in rem)
        return [(T * year_days(y) / 365.0 - ytd) * 365.0 / tot] * len(rem)
    return None


def _base(spec: dict, y: str, table: pd.DataFrame) -> float:
    (kind, lines), = spec.items()
    allq = year_quarters(y)
    prior_q4 = f"{int(y) - 1}Q4"
    if kind == "flow":
        return float(table.loc[lines, allq].astype(float).sum().sum())
    if kind == "open":
        return float(table.loc[lines, prior_q4].astype(float).sum())
    if kind == "close":
        return float(table.loc[lines, f"{y}Q4"].astype(float).sum())
    if kind == "avg":
        return float((table.loc[lines, prior_q4].astype(float).sum() + table.loc[lines, f"{y}Q4"].astype(float).sum()) / 2)
    raise ValueError(kind)


# ---------------------------------------------------------------- gộp quý -> năm

def aggregate(q: pd.DataFrame, stock: set[str]) -> pd.Series:
    """Một năm từ bảng dòng × 4 quý (thực tế + dự phóng)."""
    out = {}
    for line in q.index:
        row = q.loc[line].astype(float)
        if line in stock or line in LAST_LINES:
            out[line] = row.iloc[-1]
        elif line in FIRST_LINES:
            out[line] = row.dropna().iloc[0] if row.notna().any() else float("nan")
        elif line in MEAN_LINES:
            out[line] = row.mean()
        elif line in WEIGHTED_LINES and WEIGHTED_LINES[line] in q.index:
            w = q.loc[WEIGHTED_LINES[line]].astype(float)
            out[line] = (row * w).sum(min_count=1) / w.sum() if w.sum() else float("nan")
        else:
            out[line] = row.sum(min_count=1)
    s = pd.Series(out)
    if {"pre_financing_cash", "cash_open", "cfo", "cfi"} <= set(s.index):
        s["pre_financing_cash"] = s["cash_open"] + s["cfo"] + s["cfi"]
    return s


def history_quarters(h, lines: list[str], quarters: list[str], template_id: str) -> pd.DataFrame:
    from quick_model.presentation import _history_values, _quarter_source
    return _history_values(_quarter_source(h), lines, quarters, template_id)


def quarter_table(h, plan: Plan, values: pd.DataFrame, template_id: str) -> pd.DataFrame:
    """Dòng × quý: quý thực tế của năm trước năm có quý đầu và các năm có quý, cùng quý dự phóng."""
    lines = list(values.index)
    first = int(plan.q_years[0]) - 1
    hist_q = [q for q in h.periods_by(FREQ_QUARTER) if first <= fiscal_year(q) and q <= plan.q_origin]
    hist = history_quarters(h, lines, hist_q, template_id)
    return pd.concat([hist, values[plan.quarters]], axis=1)


def annual_table(h, plan: Plan, values: pd.DataFrame, origin_col: dict, template_id: str,
                 stock: set[str]) -> pd.DataFrame:
    """Bảng năm: cột năm gốc + năm có quý (tổng quý) + năm chạy theo năm."""
    table = quarter_table(h, plan, values, template_id)
    cols = {plan.origin: pd.Series(origin_col).reindex(values.index)}
    for y in plan.q_years:
        cols[y] = aggregate(table[year_quarters(y)], stock).reindex(values.index)
    for y in plan.annual:
        cols[y] = values[y]
    return pd.DataFrame(cols)


__all__ = ["Plan", "make_plan", "quarter_defaults", "period_arrays", "apply_targets", "annual_table", "quarter_table",
           "aggregate", "policy", "DEFAULT_LAMBDA", "DEFAULT_SPAN"]
