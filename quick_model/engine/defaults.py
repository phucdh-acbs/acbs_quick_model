"""Default giả định: chính sách đề xuất, cấu hình được, chưa phải chuẩn đã được analyst duyệt.

Cách lấy default của từng giả định khai báo trong template (`default`): lịch sử (chỉ số + thống kê), chính sách
khai báo, cần nhập, để trống (tùy chọn) hoặc sao từ giả định khác. Chỉ số lịch sử do nhóm BCTC tính
(quick_model/families/<nhóm>/metrics.py) theo đúng tần suất: chạy năm dùng số năm, chạy quý dùng số quý.

Thống kê:
- median / min / max: trên cửa sổ gần nhất (năm: annual_ratio_window; quý: ratio_window);
- latest: giá trị của kỳ liền trước kỳ dự phóng (kỳ gốc), ví dụ lãi suất vay và suất sinh lời thực tế kỳ trước;
- same_period_latest: quý -> mỗi quý dự phóng lấy YoY thực tế của cùng quý trong 4 quý gần nhất; năm -> YoY năm gần nhất.
- converge (owner duyệt 2026-11-17, thay giữ nguyên một giá trị): kỳ dự phóng đầu = mức thực tế gần nhất (`start`),
  sau đó hội tụ tuyến tính về mức neo trong `fade_years` năm; neo = trung vị cả chu kỳ (`anchor_window_years` năm).
  Chạy quý: mức đầu và neo lấy theo cùng quý trong năm (mùa vụ), riêng tăng trưởng YoY neo theo mọi quý.
- Chạy quý, stat median: trung vị theo cùng quý trong năm trên `seasonal_years` năm gần nhất (mùa vụ).
- cost_structure: hồi quy chi phí theo doanh thu (cố định = hệ số chặn, biến đổi = độ dốc); hồi quy không hợp lệ
  thì cố định 0 và tỷ lệ = trung vị chi phí/doanh thu, ghi rõ.
Mẫu không hợp lệ (mẫu số ≤ 0, ô trống) bị loại, không thay bằng 0. Không có mẫu hợp lệ -> needs_input.
Không tự cắt (cap) giá trị cực đoan; cảnh báo nằm ở validation.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quick_model.data.history import History
from quick_model.engine.assumptions import AssumptionValue
from quick_model.engine.registry import get_template
from quick_model.families import get_family
from quick_model.families.nonfinancial.metrics import annual_frame, quarterly_frame  # noqa: F401  (tương thích)
from quick_model.periods import FREQ_ANNUAL, freq_of, shift_label
from quick_model.periods import shift_quarter_label as _shift_label


# Giá trị dùng khi template không khai báo default_paths
PATH_DEFAULTS = {"fade_years": 4, "anchor_window_years": 10, "seasonal_years": 3, "cost_structure_window_years": 8,
                 "cost_structure_window_quarters": 12, "cost_structure_min_obs": 4,
                 "anchor_stat": "ema", "ema_span_years": 5, "short_history": "latest"}


@dataclass(frozen=True)
class DefaultPolicy:
    # same_quarter_last_year: dùng thống kê khai báo trong template cho tăng trưởng (mặc định: YoY cùng kỳ gần nhất)
    # median_window: ép tăng trưởng = trung vị YoY trong cửa sổ
    growth_method: str = "same_quarter_last_year"
    growth_window: int = 12  # số quý tham chiếu cho YoY (khoảng min–max; median_window)
    ratio_window: int = 8  # số quý cho các tỷ lệ
    min_samples: int = 4  # ít hơn -> vẫn dùng nhưng gắn cờ mẫu yếu
    statistic: str = "median"
    annual_growth_window: int = 5  # số năm tham chiếu cho YoY năm
    annual_ratio_window: int = 3  # số năm cho các tỷ lệ
    annual_min_samples: int = 2

    def __post_init__(self):
        if self.statistic != "median":
            raise ValueError("Hiện chỉ hỗ trợ statistic = 'median'")
        if self.growth_method not in ("same_quarter_last_year", "median_window"):
            raise ValueError("growth_method phải là 'same_quarter_last_year' hoặc 'median_window'")
        if min(self.growth_window, self.ratio_window, self.min_samples, self.annual_growth_window,
               self.annual_ratio_window, self.annual_min_samples) < 1:
            raise ValueError("Cửa sổ và số mẫu tối thiểu phải >= 1")


def compute_defaults(h: History, origin: str, horizon: int, policy: DefaultPolicy = DefaultPolicy(),
                     template_id: str | None = None, one_offs: tuple = (),
                     anchors: dict | None = None) -> dict[str, AssumptionValue]:
    """one_offs: khoản bất thường analyst đã xác nhận; được loại khỏi dòng nguồn trước khi tính chỉ số lịch sử.
    anchors: {assumption_id mức neo: giá trị analyst nhập} (ví dụ long_run_growth), thay số chính sách khi tính đường
    hội tụ của giả định dùng mức neo đó."""
    from quick_model import oneoff
    tpl = get_template(template_id)
    family = get_family(tpl.family)
    annual = freq_of(origin) == FREQ_ANNUAL
    metrics = family.metrics(h, origin, annual, oneoff.history_adjustments(one_offs, tpl, annual))
    touched = oneoff.affected_metrics(one_offs, tpl)
    if annual:
        g_window, r_window, min_n, unit = (policy.annual_growth_window, policy.annual_ratio_window,
                                           policy.annual_min_samples, "năm")
    else:
        g_window, r_window, min_n, unit = policy.growth_window, policy.ratio_window, policy.min_samples, "quý"

    paths = {**PATH_DEFAULTS, **(tpl.default_paths or {})}
    out: dict[str, AssumptionValue] = {}
    copies = {}
    refs = ({(x.get("anchor") or {}).get("assumption") for x in tpl.default_specs.values()}
            | {x.get("cap_by") for x in tpl.default_specs.values()}) - {None}
    ordered = sorted(tpl.default_specs.items(), key=lambda kv: kv[0] not in refs)  # mức neo tính trước
    for aid, d in ordered:
        kind = d["type"]
        if kind == "history":
            series, formula = metrics.series(d["metric"])
            stat = d["stat"]
            if aid == "revenue_yoy_growth" and policy.growth_method == "median_window":
                stat = "median"
            window = g_window if d["metric"] == "revenue_yoy" else r_window
            if d.get("window_years"):  # cửa sổ khai báo theo năm (quý: × 4), vd. trần LDR = cao nhất 3 năm
                window = int(d["window_years"]) * (1 if annual else 4)
            if stat == "converge":
                fixed_anchor = None
                ref = (d.get("anchor") or {}).get("assumption")
                if ref:
                    if ref in (anchors or {}):
                        fixed_anchor = float(anchors[ref])
                    elif ref in out:  # default của giả định mức neo (ví dụ min(EMA 5 năm YoY, 8%))
                        fixed_anchor = next((x for x in out[ref].values if x is not None), None)
                out[aid] = _converge_default(aid, series, formula, d, origin, horizon, annual, paths, policy, min_n,
                                             fixed_anchor, ref, ref in (anchors or {}), tpl.specs[aid].valid_range)
                _describe_path(out[aid], tpl.specs[aid].unit)
            elif stat in ("ema", "ema_rolling"):
                out[aid] = _ema_default(aid, series, formula, d, origin, horizon, annual, paths, min_n,
                                        tpl.specs[aid].unit, rolling=stat == "ema_rolling")
            elif stat == "ema_cap_by":
                out[aid] = _ema_cap_by_default(aid, series, formula, d, origin, horizon, annual, paths, min_n,
                                               tpl.specs[aid].unit, out.get(d["cap_by"]))
            elif stat == "ema_decay":
                out[aid] = _ema_decay_default(aid, series, formula, d, origin, horizon, annual, paths, min_n,
                                              tpl.specs[aid].unit)
            elif stat == "median" and not annual and d["metric"] != "revenue_yoy":  # YoY đã khử mùa vụ
                out[aid] = _seasonal_median(aid, series, formula, origin, horizon, paths, min_n)
                _describe_seasonal(out[aid], tpl.specs[aid].unit)
            else:
                out[aid] = _history_default(aid, series, window, formula, horizon, stat, origin, unit, min_n, policy)
            if out[aid].source == "needs_input" and d.get("if_no_history"):  # vd không có nợ vay -> lãi suất 0
                x = d["if_no_history"]
                out[aid] = AssumptionValue(aid, (float(x["value"]),) * horizon, "explicit_policy",
                                           {**out[aid].basis, "note": x.get("note", ""), "no_history": True})
            b = out[aid].basis
            in_use = (set(b.get("used_periods", ())) or set(b.get("sample", {}))) if stat in (
                "median", "mean", "min", "max", "converge", "ema", "ema_rolling", "ema_decay", "ema_cap_by") else {b.get("source_period")} | {
                x.get("source_period") for x in b.get("per_period", {}).values()}
            used = [o for o in touched.get(d["metric"], ())
                    if (str(oneoff.fiscal_year(o.quarter)) if annual else o.quarter) in in_use]
            if used:
                v = out[aid]
                note = "Đã loại khoản bất thường analyst xác nhận khỏi lịch sử: " + "; ".join(
                    f"{o.quarter} {o.amount / 1e9:+,.0f} tỷ" for o in used)
                out[aid] = AssumptionValue(aid, v.values, v.source, {
                    **v.basis, "one_off_excluded": [o.to_dict() for o in used],
                    "note": (v.basis.get("note", "") + " " + note).strip()})
        elif kind == "cost_structure":
            out[aid] = _cost_structure_default(aid, metrics, d, origin, horizon, annual, paths)
        elif kind == "explicit":
            out[aid] = AssumptionValue(aid, (float(d["value"]),) * horizon, "explicit_policy", {"note": d.get("note", "")})
        elif kind == "needs_input":
            out[aid] = AssumptionValue(aid, (None,) * horizon, "needs_input", {"note": d.get("note", "")})
        elif kind == "blank":
            out[aid] = AssumptionValue(aid, (None,) * horizon, "explicit_policy", {"note": d.get("note", "")})
        elif kind == "copy":
            copies[aid] = d
    for aid, d in copies.items():
        base = out[d["from"]]
        out[aid] = AssumptionValue(aid, base.values, base.source,
                                   {**base.basis, "note": d.get("note", ""), "copied_from": d["from"]})
    missing = set(tpl.specs) - set(out)
    if missing:
        raise RuntimeError(f"Thiếu cách tính default cho {sorted(missing)}")
    return out


def _window_basis(series: pd.Series, window: int, formula: str, unit: str, stat: str, min_n: int) -> tuple[pd.Series, dict]:
    recent = series.iloc[-window:]
    valid = recent.dropna()
    basis = {
        "statistic": stat,
        "window": window,
        "window_unit": unit,
        "window_periods": [recent.index[0], recent.index[-1]] if len(recent) else [],
        "count": int(len(valid)),
        "excluded": [p for p in recent.index if p not in valid.index],
        "sample": {p: float(v) for p, v in valid.items()},
        "min": float(valid.min()) if len(valid) else None,
        "max": float(valid.max()) if len(valid) else None,
        "formula": formula,
        "weak_sample": len(valid) < min_n and stat in ("median", "min", "max"),
    }
    return valid, basis


def _history_default(aid, series, window, formula, horizon, stat, origin, unit, min_n, policy) -> AssumptionValue:
    valid, basis = _window_basis(series, window, formula, unit, stat, min_n)
    if stat == "same_period_latest":
        return _same_period_latest(aid, series, origin, horizon, basis)
    if stat == "latest":
        last = series.loc[:origin].dropna()
        if last.empty:
            basis["note"] = "Không có giá trị lịch sử hợp lệ của kỳ liền trước."
            return AssumptionValue(aid, (None,) * horizon, "needs_input", basis)
        p, v = last.index[-1], float(last.iloc[-1])
        basis.update(source_period=p, weak_sample=False,
                     note=("Giá trị kỳ liền trước" if p == origin else f"Kỳ gốc {origin} không có mẫu hợp lệ; dùng {p}"))
        return AssumptionValue(aid, (v,) * horizon, "historical_default", basis)
    if valid.empty:
        basis["note"] = "Không có mẫu lịch sử hợp lệ trong cửa sổ."
        return AssumptionValue(aid, (None,) * horizon, "needs_input", basis)
    value = {"median": valid.median(), "min": valid.min(), "max": valid.max(), "mean": valid.mean()}[stat]
    return AssumptionValue(aid, (float(value),) * horizon, "historical_default", basis)


def _same_period_latest(aid: str, yoy: pd.Series, origin: str, horizon: int, window_basis: dict) -> AssumptionValue:
    """Quý: mỗi quý dự phóng lấy YoY thực tế của cùng quý gần nhất (4 quý gần nhất). Năm: YoY năm gần nhất."""
    annual = freq_of(origin) == FREQ_ANNUAL
    values, per_period = [], {}
    for i in range(1, horizon + 1):
        p = shift_label(origin, i)
        if annual:
            src = origin
        else:
            src = p
            while src > origin:  # nhãn 'YYYYQn' so sánh được theo thứ tự chữ
                src = _shift_label(src, -4)
        v = yoy.get(src)
        v = None if v is None or pd.isna(v) else float(v)
        values.append(v)
        per_period[p] = {"source_period": src, "value": v}
    basis = {
        "method": "same_period_latest",
        "per_period": per_period,
        "formula": ("YoY năm gần nhất: ISA3[năm gốc] / ISA3[năm trước] − 1" if annual
                    else "YoY của cùng quý gần nhất: ISA3[q] / ISA3[q−4] − 1"),
        "min": window_basis.get("min"), "max": window_basis.get("max"),
        "window": window_basis.get("window"), "window_unit": window_basis.get("window_unit"),
        "window_periods": window_basis.get("window_periods", []),
        "count": sum(v is not None for v in values),
        "weak_sample": False,
    }
    if all(v is None for v in values):
        basis["note"] = "Không có YoY hợp lệ của kỳ cùng kỳ."
        return AssumptionValue(aid, tuple(values), "needs_input", basis)
    if any(v is None for v in values):
        basis["note"] = "Một số kỳ không có YoY cùng kỳ hợp lệ; cần analyst nhập."
    return AssumptionValue(aid, tuple(values), "historical_default", basis)


# ---------------------------------------------------------------- đường default L1 (2026-11-17)


def _quarter_of(label: str) -> str:
    return label[-2:]


def _same_period_source(series: pd.Series, p: str, origin: str, annual: bool) -> str | None:
    """Kỳ thực tế gần nhất cùng vị trí với kỳ dự phóng p (năm: kỳ gốc; quý: cùng quý), bỏ qua mẫu trống."""
    hist = series.loc[:origin]
    src = origin if annual else p
    while not annual and src > origin:
        src = _shift_label(src, -4)
    step = -1 if annual else -4
    for _ in range(12):
        if src in hist.index and pd.notna(hist.get(src)):
            return src
        src = shift_label(src, step) if annual else _shift_label(src, step)
        if src < hist.index[0]:
            break
    return None


def _converge_default(aid, series, formula, d, origin, horizon, annual, paths, policy, min_n,
                      fixed_anchor=None, anchor_ref=None, anchor_by_analyst=False,
                      valid_range=(None, None)) -> AssumptionValue:
    """Mức thực tế gần nhất -> mức neo trong fade_years năm (quý: mùa vụ theo cùng quý).

    Mức neo: trung vị (hoặc phân vị của dải) cả chu kỳ; hoặc số nhập tay khi giả định khai báo anchor.assumption.
    Số năm hội tụ tính theo khoảng cách từ kỳ dự phóng đầu, không theo số năm dự phóng: dự phóng 2 năm với
    fade 7 năm thì năm thứ 2 mới đi 1/7 quãng."""
    per_year = 1 if annual else 4
    fade = float(d.get("fade_years", paths["fade_years"]))
    years = int(paths["anchor_window_years"])
    hist = series.loc[:origin]
    window = hist.iloc[-years * per_year:]
    valid = window.dropna()
    q = None if fixed_anchor is not None else 0.5
    seasonal = not annual and d["metric"] != "revenue_yoy"  # YoY đã khử mùa vụ: neo theo mọi quý
    basis = {
        "statistic": "converge", "window": years, "window_unit": "năm",
        "window_periods": [window.index[0], window.index[-1]] if len(window) else [],
        "count": int(len(valid)), "sample": {p: float(v) for p, v in valid.items()},
        "min": float(valid.min()) if len(valid) else None, "max": float(valid.max()) if len(valid) else None,
        "formula": formula, "weak_sample": len(valid) < max(min_n, 2 * per_year),
        "fade_years": fade, "quantile": q, "favourable": d.get("favourable"),
        "anchor_stat": paths["anchor_stat"], "ema_span_years": paths["ema_span_years"],
        "start_rule": d["start"], "seasonal": seasonal,
    }
    if fixed_anchor is not None:  # neo nhập tay: không dùng mẫu lịch sử làm neo, dải kịch bản không đổi neo này
        basis.update(anchor_assumption=anchor_ref, anchor_value=fixed_anchor,
                     anchor_source="analyst" if anchor_by_analyst else "historical_default", seasonal=False)
        seasonal = False
    if valid.empty and fixed_anchor is None:
        basis["note"] = "Không có mẫu lịch sử hợp lệ trong cửa sổ neo."
        return AssumptionValue(aid, (None,) * horizon, "needs_input", basis)
    anchors, path, values, used = {}, {}, [], set()
    if fixed_anchor is not None:
        anchors["all"] = fixed_anchor
    for i in range(horizon):
        p = shift_label(origin, i + 1)
        key = _quarter_of(p) if seasonal else "all"
        if key not in anchors:
            smp = valid[[x for x in valid.index if _quarter_of(x) == key]] if seasonal else valid
            if seasonal and smp.empty:  # chưa có mẫu của quý này: neo theo mọi quý, ghi rõ
                smp = valid
                basis.setdefault("seasonal_fallback", []).append(key)
            anchors[key] = _anchor_value(smp, q, paths, yearly=annual or seasonal) if len(smp) else None
            used.update(smp.index)
        anchor = anchors[key]
        src = _same_period_source(hist, p, origin, annual) if (d["start"] == "same_period_latest" or seasonal) else (
            origin if pd.notna(hist.get(origin)) else _same_period_source(hist, origin, origin, True))
        start = float(hist[src]) if src is not None else None
        # không có mức thực tế cùng kỳ mà neo là số nhập tay: không tự lấy neo làm mức đầu -> cần analyst nhập
        if (anchor is None and start is None) or (start is None and fixed_anchor is not None):
            values.append(None)
            path[p] = {"start_period": None, "start": None, "anchor": anchor, "weight": None, "value": None}
            continue
        if start is None:
            start, src = anchor, None
        if anchor is None:
            anchor = start
        w = min(1.0, (i / per_year) / fade) if fade > 0 else 1.0
        v = start + (anchor - start) * w
        values.append(v)
        used.add(src) if src else None
        path[p] = {"start_period": src, "start": start, "anchor": anchor, "weight": w, "value": v}
    basis["path"] = path
    basis["anchors"] = anchors
    basis["used_periods"] = sorted(x for x in used if x)
    first = next(iter(path.values()))
    basis["source_period"] = first["start_period"]
    if any(x["start_period"] is None for x in path.values()):
        basis["note"] = "Một số kỳ không có mức thực tế cùng kỳ: bắt đầu từ mức neo."
    src = "needs_input" if all(v is None for v in values) else "historical_default"
    return AssumptionValue(aid, tuple(values), src, basis)


def _seasonal_median(aid, series, formula, origin, horizon, paths, min_n) -> AssumptionValue:
    """Chạy quý: trung vị của cùng quý trong năm trên seasonal_years năm gần nhất."""
    years = int(paths["seasonal_years"])
    window = series.loc[:origin].iloc[-years * 4:]
    valid = window.dropna()
    by_q = {k: float(valid[[x for x in valid.index if _quarter_of(x) == k]].median())
            for k in ("Q1", "Q2", "Q3", "Q4") if any(_quarter_of(x) == k for x in valid.index)}
    overall = float(valid.median()) if len(valid) else None
    missing = sorted({_quarter_of(shift_label(origin, i + 1)) for i in range(horizon)} - set(by_q))
    values = tuple(by_q.get(_quarter_of(shift_label(origin, i + 1)), overall) for i in range(horizon))
    basis = {
        "statistic": "seasonal_median", "window": years * 4, "window_unit": "quý",
        "window_periods": [window.index[0], window.index[-1]] if len(window) else [],
        "count": int(len(valid)), "sample": {p: float(v) for p, v in valid.items()},
        "min": float(valid.min()) if len(valid) else None, "max": float(valid.max()) if len(valid) else None,
        "formula": formula, "weak_sample": len(valid) < min_n, "seasonal_values": by_q,
    }
    if all(v is None for v in values):
        basis["note"] = "Không có mẫu lịch sử hợp lệ trong cửa sổ."
        return AssumptionValue(aid, (None,) * horizon, "needs_input", basis)
    if missing and overall is not None:
        basis["seasonal_fallback"] = missing
        basis["note"] = f"Quý {', '.join(missing)} chưa có mẫu cùng quý: dùng trung vị mọi quý."
    return AssumptionValue(aid, values, "historical_default", basis)


def _cost_structure_default(aid, metrics, d, origin, horizon, annual, paths) -> AssumptionValue:
    """Tách chi phí cố định + biến đổi theo chi phí biên (phương pháp chênh lệch).

    Tỷ lệ biến đổi = hồi quy qua gốc của Δchi phí theo Δdoanh thu (năm: so với năm trước; quý: so với cùng quý năm
    trước để khử mùa vụ) trên cửa sổ gần nhất. Cố định = chi phí kỳ gốc − tỷ lệ × doanh thu kỳ gốc. Dùng chênh lệch
    thay vì số dư để quy mô tăng theo thời gian (mở rộng công suất) không làm hệ số chặn âm. Không hợp lệ (thiếu mẫu,
    tỷ lệ hoặc cố định âm) thì cố định = 0 và tỷ lệ = trung vị chi phí/doanh thu, ghi rõ."""
    exp, f_exp = metrics.series(d["expense_metric"])
    rev, _ = metrics.series("revenue_amount")
    ratio, f_ratio = metrics.series(d["ratio_metric"])
    lag = 1 if annual else 4
    n = int(paths["cost_structure_window_years"] if annual else paths["cost_structure_window_quarters"])
    lv = pd.concat({"exp": exp, "rev": rev}, axis=1).loc[:origin]
    diff = (lv - lv.shift(lag)).dropna().iloc[-n:]
    unit = "năm" if annual else "quý"
    basis = {
        "statistic": "marginal_cost", "window": n, "window_unit": unit,
        "window_periods": [diff.index[0], diff.index[-1]] if len(diff) else [], "count": int(len(diff)),
        "formula": (f"Tỷ lệ biến đổi = Σ(Δchi phí × Δdoanh thu) / Σ(Δdoanh thu²), Δ so với "
                    f"{'năm trước' if annual else 'cùng quý năm trước'} ({f_exp}, ISA3); cố định = chi phí kỳ gốc − "
                    "tỷ lệ × doanh thu kỳ gốc"),
    }
    rs = ratio.loc[:origin].iloc[-n:].dropna()
    basis["min"], basis["max"] = (float(rs.min()), float(rs.max())) if len(rs) else (None, None)
    ok, fixed, variable = False, None, None
    last = lv.dropna()
    if len(diff) >= int(paths["cost_structure_min_obs"]) and len(last) and (diff["rev"] ** 2).sum() > 0:
        variable = float((diff["exp"] * diff["rev"]).sum() / (diff["rev"] ** 2).sum())
        e0, r0 = float(last["exp"].iloc[-1]), float(last["rev"].iloc[-1])
        fixed = e0 - variable * r0
        basis["anchor_period"] = last.index[-1]
        basis["estimate"] = {"fixed": fixed, "variable": variable, "expense": e0, "revenue": r0}
        ok = variable >= 0 and fixed >= 0
    if ok:
        basis["note"] = (f"Chi phí biên {variable:.2%} doanh thu; cố định {fixed / 1e9:,.0f} tỷ/{unit} "
                         f"(= chi phí {basis['anchor_period']} − biến đổi).")
    else:
        med = float(rs.median()) if len(rs) else None
        fixed, variable = 0.0, med
        basis["note"] = ("Không tách được cố định/biến đổi (thiếu mẫu hoặc hệ số âm): cố định = 0, tỷ lệ = trung vị "
                         f"{f_ratio} trong cửa sổ.")
        basis["fallback"] = True
    v = fixed if d["part"] == "fixed" else variable
    if v is None:
        return AssumptionValue(aid, (None,) * horizon, "needs_input", basis)
    basis["sample"] = {p: float(x) for p, x in rs.items()} if d["part"] == "variable" else {}
    return AssumptionValue(aid, (v,) * horizon, "historical_default", basis)


def _fmt_unit(v, unit: str) -> str:
    if v is None:
        return "—"
    if unit in ("ratio", "ratio_annual"):
        return f"{v:.2%}"
    if unit == "days":
        return f"{v:,.1f} ngày"
    if unit == "VND":
        return f"{v / 1e9:,.0f} tỷ"
    if unit == "VND_per_year":
        return f"{v / 1e9:,.0f} tỷ/năm"
    return f"{v:,.4g}"


def _describe_path(v: AssumptionValue, unit: str) -> None:
    b = v.basis
    path = b.get("path") or {}
    if not path or b.get("note"):
        return
    first, last = next(iter(path.values())), list(path.values())[-1]
    anchors = b.get("anchors", {})
    neo = (" / ".join(f"{k} {_fmt_unit(x, unit)}" for k, x in anchors.items()) if b.get("seasonal")
           else _fmt_unit(first["anchor"], unit))
    if b.get("anchor_assumption"):
        who = "analyst nhập" if b.get("anchor_source") == "analyst" else "tự động: min(EMA 5 năm YoY, trần)"
        target = f"mức dài hạn {_fmt_unit(b['anchor_value'], unit)} ({who})"
    else:
        q = b.get("quantile", 0.5)
        if b.get("anchor_stat") == "ema" and b.get("count", 0) < float(b.get("ema_span_years") or 0):
            stat = f"số kỳ liền trước (chỉ {b.get('count')} năm, chưa đủ chuỗi cho EMA)"
        elif b.get("anchor_stat") == "ema":
            stat = f"EMA {b.get('ema_span_years'):g} năm" + ("" if q == 0.5 else f" ± chênh lệch phân vị {q:.0%}")
        else:
            stat = "trung vị" if q == 0.5 else f"phân vị {q:.0%}"
        target = f"{stat} ({'–'.join(b.get('window_periods') or [])}: {neo})"
    last_p = list(path)[-1]
    b["note"] = (f"Từ thực tế {first['start_period']} ({_fmt_unit(first['start'], unit)}) hội tụ về {target} trong "
                 f"{b.get('fade_years'):g} năm kể từ kỳ đầu; {last_p}: {_fmt_unit(last['value'], unit)} "
                 f"(đi {last['weight']:.0%} quãng).")


def _describe_seasonal(v: AssumptionValue, unit: str) -> None:
    b = v.basis
    if b.get("note") or not b.get("seasonal_values"):
        return
    b["note"] = "Trung vị theo cùng quý: " + "; ".join(f"{k} {_fmt_unit(x, unit)}" for k, x in b["seasonal_values"].items())


def anchor_inputs(s, tpl=None) -> dict[str, float]:
    """Mức neo analyst đã nhập trong kịch bản (giả định khai báo anchor_for), dùng để tính lại default hội tụ."""
    tpl = tpl or get_template(s.template_id)
    refs = {(d.get("anchor") or {}).get("assumption") for d in tpl.default_specs.values()} - {None}
    out = {}
    for ref in refs:
        v = s.overrides.get(ref)
        if v is not None and v.source == "analyst":
            x = next((y for y in v.values if y is not None), None)
            if x is not None:
                out[ref] = float(x)
    return out


def defaults_for_scenario(h: History, s, policy: DefaultPolicy = DefaultPolicy()) -> dict[str, AssumptionValue]:
    """Default của một kịch bản: khoản bất thường đã xác nhận + mức neo analyst nhập (một nơi cho app, dải, hồ sơ)."""
    tpl = get_template(s.template_id)
    one_offs = get_family(tpl.family).engine(tpl).effective_one_offs(h, s)
    return compute_defaults(h, s.origin, s.horizon, policy, template_id=s.template_id, one_offs=one_offs,
                            anchors=anchor_inputs(s))


def _ema(values: pd.Series, span: float) -> float:
    """EMA theo thứ tự thời gian (span tính theo số quan sát), giá trị tại quan sát cuối."""
    return float(values.ewm(span=span, adjust=True).mean().iloc[-1])


def _ema_decay_default(aid, series, formula, d, origin, horizon, annual, paths, min_n, unit) -> AssumptionValue:
    """Kỳ dự phóng đầu = EMA 5 năm (quý: EMA 20 quý); giảm tuyến tính về `target` (tốc độ năm, so với cùng kỳ năm
    trước) ở năm thứ `target_year` (quý: kỳ thứ (target_year − 1) × 4 + 1) rồi giữ."""
    base = _ema_default(aid, series, formula, {}, origin, horizon, annual, paths, min_n, unit)
    start = base.values[0]
    if start is None:
        return base
    target, n = float(d["target"]), max(1, int(d.get("target_year", 3)))
    steps = (n - 1) * (1 if annual else 4)
    vals = tuple(start + (target - start) * min(1.0, i / steps) if steps else target for i in range(horizon))
    basis = {**base.basis, "statistic": "ema_decay", "target": target, "target_year": n,
             "formula": f"EMA span {paths['ema_span_years']} năm của {formula}, giảm tuyến tính về {_fmt_unit(target, unit)}"
                        f" ở năm thứ {n}"}
    first = (f"số kỳ liền trước {base.basis.get('source_period')} (chưa đủ chuỗi cho EMA)"
             if base.basis.get("short_history") else f"EMA {paths['ema_span_years']} năm")
    basis["note"] = (f"Năm 1 = {first} {_fmt_unit(start, unit)}; giảm tuyến tính về "
                     f"{_fmt_unit(target, unit)} ở năm thứ {n}, giữ sau đó: " + ", ".join(_fmt_unit(x, unit) for x in vals) + ".")
    return AssumptionValue(aid, vals, "historical_default", basis)


def _ema_cap_by_default(aid, series, formula, d, origin, horizon, annual, paths, min_n, unit,
                        ref: AssumptionValue | None) -> AssumptionValue:
    """min(EMA 5 năm, giả định tham chiếu `cap_by` cùng năm) (owner 2026-11-23): mức tăng không vượt tham chiếu
    (vd CK đầu tư không tăng nhanh hơn tiền gửi KH). Tham chiếu thiếu giá trị năm nào thì năm đó dùng EMA."""
    base = _ema_default(aid, series, formula, {}, origin, horizon, annual, paths, min_n, unit)
    if base.source != "historical_default" or ref is None:
        return base
    ema = base.values[0]
    vals = tuple(ema if r is None else min(ema, r) for r in (ref.values + (None,) * horizon)[:horizon])
    capped = [i for i, (v, r) in enumerate(zip(vals, ref.values)) if r is not None and r < ema]
    basis = {**base.basis, "statistic": "ema_cap_by", "cap_by": d["cap_by"], "cap_values": list(ref.values),
             "capped_years": capped, "ema": ema,
             "formula": f"min(EMA span {paths['ema_span_years']} năm của {formula}, {d['cap_by']} cùng năm)"}
    basis["note"] = (f"EMA {paths['ema_span_years']} năm = {_fmt_unit(ema, unit)}, không vượt {d['cap_by']} cùng năm"
                     + (f" (bị cắt {len(capped)}/{horizon} năm)" if capped else "") + ": "
                     + ", ".join(_fmt_unit(x, unit) for x in vals) + ".")
    return AssumptionValue(aid, vals, "historical_default", basis)


def _anchor_value(smp: pd.Series, q: float, paths: dict, yearly: bool) -> float:
    """Mức neo: EMA span ema_span_years năm (mẫu theo năm: span = số năm; mẫu theo quý: span × 4).

    q khác 0,5: EMA + (phân vị q − trung vị) của cùng mẫu (giữ cho cấu hình anchor_stat khác)."""
    if paths.get("anchor_stat", "ema") != "ema":
        return float(smp.quantile(q))
    span = float(paths["ema_span_years"]) * (1 if yearly else 4)
    if paths.get("short_history") == "latest" and len(smp) < span:  # chưa đủ độ dài cho EMA: số kỳ liền trước
        return float(smp.iloc[-1])
    base = _ema(smp, span)
    return base if q == 0.5 else base + float(smp.quantile(q) - smp.median())


def _ema_default(aid, series, formula, d, origin, horizon, annual, paths, min_n, unit,
                 rolling: bool = False) -> AssumptionValue:
    """EMA ema_span_years năm của chuỗi (cửa sổ anchor_window_years), có trần nếu khai báo.

    rolling=False: một giá trị cho mọi kỳ. rolling=True (cuốn chiếu): kỳ dự phóng thứ i = EMA của cửa sổ cùng độ dài
    gồm cả giá trị đã dự phóng của các kỳ trước (giá trị mới nối vào cuối chuỗi, giá trị cũ nhất rơi khỏi cửa sổ)."""
    per_year = 1 if annual else 4
    window = series.loc[:origin].iloc[-int(paths["anchor_window_years"]) * per_year:]
    valid = window.dropna()
    basis = {
        "statistic": "ema", "ema_span_years": paths["ema_span_years"], "window": int(paths["anchor_window_years"]),
        "window_unit": "năm", "window_periods": [window.index[0], window.index[-1]] if len(window) else [],
        "count": int(len(valid)), "sample": {p: float(v) for p, v in valid.items()},
        "min": float(valid.min()) if len(valid) else None, "max": float(valid.max()) if len(valid) else None,
        "formula": f"EMA span {paths['ema_span_years']} năm của {formula}", "weak_sample": len(valid) < min_n,
    }
    if valid.empty:
        basis["note"] = "Không có mẫu lịch sử hợp lệ để tính EMA."
        return AssumptionValue(aid, (None,) * horizon, "needs_input", basis)
    span = float(paths["ema_span_years"]) * per_year
    short = paths.get("short_history") == "latest" and len(valid) < span
    raw = float(valid.iloc[-1]) if short else _ema(valid, span)
    cap = d.get("cap")
    v = raw if cap is None else min(raw, float(cap))
    basis.update(ema=raw, cap=cap, capped=cap is not None and raw > float(cap), short_history=short)
    if short:
        basis.update(source_period=valid.index[-1], statistic="latest_short")
    basis["note"] = ((f"Chưa đủ {span:g} kỳ cho EMA ({len(valid)} kỳ): dùng số kỳ liền trước {valid.index[-1]} = "
                      f"{_fmt_unit(raw, unit)}" if short else f"EMA {paths['ema_span_years']} năm = {_fmt_unit(raw, unit)}")
                     + (f"; áp trần {_fmt_unit(float(cap), unit)}" if basis["capped"] else "")
                     + f" → {_fmt_unit(v, unit)}.")
    if not rolling or short:
        return AssumptionValue(aid, (v,) * horizon, "historical_default", basis)
    n, vals, chain = len(valid), [], list(valid.astype(float))
    for _ in range(horizon):
        x = chain[-1] if short else _ema(pd.Series(chain[-n:]), span)
        x = x if cap is None else min(x, float(cap))
        vals.append(x)
        chain.append(x)
    basis.update(statistic="ema_rolling", formula=f"EMA span {paths['ema_span_years']} năm cuốn chiếu của {formula}")
    basis["note"] = (f"EMA {paths['ema_span_years']} năm cuốn chiếu (cửa sổ {n} quan sát, nối giá trị dự phóng): "
                     + ", ".join(_fmt_unit(x, unit) for x in vals) + ".")
    return AssumptionValue(aid, tuple(vals), "historical_default", basis)
