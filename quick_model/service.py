"""Lớp thư viện cho hệ thống khác gọi mô hình L1 (team IT, sub-agent, API).

Đầu vào: BCTC thô của một mã theo đúng tên bảng/cột GBQ (DataFrame hoặc thư mục CSV). Đầu ra: `ForecastOutput`
(bảng DataFrame và `to_dict()` cho JSON). Thư viện không gọi BigQuery. Tất định: cùng dữ liệu, cùng kết quả.
Đơn vị: tiền VND; tỷ lệ dạng thập phân; kỳ năm "2026", kỳ quý "2026Q3". Không phải khuyến nghị đầu tư.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from quick_model.data.history import History, build_history, load_items
from quick_model.data.raw_loader import (STATEMENTS, TABLE_TO_STATEMENT, RawDataError, load_raw_statement,
                                         raw_statement_from_frame)
from quick_model.engine.assumptions import AssumptionValue
from quick_model.engine.defaults import defaults_for_scenario
from quick_model.engine.forecast import DEFAULT_ANNUAL_HORIZON, default_origin, lines_for, new_scenario, run_forecast
from quick_model.engine.registry import get_template
from quick_model.families import get_family
from quick_model.families.base import ForecastResult, forecast_periods
from quick_model.paths import CONFIG_DIR
from quick_model.periods import FREQ_ANNUAL, FREQ_QUARTER

MODEL_VERSION = "L1 v2.6.6"
FAMILIES = ("nonfinancial", "bank")  # template mặc định của nhóm trùng tên nhóm
REQUIRED_TABLES = {"nonfinancial": ("INCOME_STATEMENT", "BALANCE_SHEET", "CASH_FLOW"),
                   "bank": ("INCOME_STATEMENT", "BALANCE_SHEET")}
OUTPUT_STATEMENTS = ("IS", "BS", "CF", "SCH")
DISCLAIMER = "Mô hình L1 tự động, không phải khuyến nghị đầu tư."


class ServiceError(ValueError):
    """Lỗi đầu vào của phía gọi (nhóm, dữ liệu, tham số)."""


def _check_family(family: str) -> None:
    if family not in FAMILIES:
        raise ServiceError(f"Nhóm BCTC không hỗ trợ: {family!r}. Chọn một trong {list(FAMILIES)}")


def data_contract(family: str) -> dict:
    """{bảng GBQ: {"required", "id_column", "metadata_columns", "item_columns", "item_labels"}}."""
    _check_family(family)
    m = pd.read_csv(CONFIG_DIR / "manifests" / f"{family}.csv", dtype=str, encoding="utf-8-sig")
    out = {}
    for table, g in m.groupby("bq_table", sort=False):
        _, _, id_col, meta = STATEMENTS[TABLE_TO_STATEMENT[table]]
        out[table] = {"required": table in REQUIRED_TABLES[family], "id_column": id_col,
                      "metadata_columns": list(meta), "item_columns": g["code"].tolist(),
                      "item_labels": dict(zip(g["code"], g["desc_vn"]))}
    return out


def assumption_catalog(family: str) -> list[dict]:
    """Giả định của L1 có thể đặt qua `overrides`: ID, nhãn, đơn vị, miền hợp lệ, cách tính mặc định."""
    _check_family(family)
    tpl = get_template(family)
    active = set(tpl.default_methods.values())
    return [{"assumption_id": aid, "label": sp.label_vn, "block": sp.block, "unit": sp.unit,
             "valid_min": sp.valid_range[0], "valid_max": sp.valid_range[1], "default_policy": sp.default_policy}
            for aid, sp in tpl.specs.items()
            if sp.method_id in active or (sp.shared and sp.block in tpl.default_methods)]


def _load(ticker: str, family: str, frames: dict | None, folder: str | Path | None) -> History:
    """frames: {bảng GBQ: DataFrame | list[dict]} (lọc theo mã); folder: thư mục `<mã>_<is|bs|cf|note|notes>.csv`."""
    if (frames is None) == (folder is None):
        raise ServiceError("Truyền đúng một trong hai: frames hoặc folder")
    bundle = {}
    for table, spec in data_contract(family).items():
        st = TABLE_TO_STATEMENT[table]
        if frames is not None:
            if table not in frames:
                continue
            df = pd.DataFrame(frames[table])
            col = "TICKER" if "TICKER" in df.columns else "Ticker"  # bảng thuyết minh dùng tên cột CamelCase
            df = df[df[col].astype(str).str.upper() == ticker] if col in df.columns else df.iloc[0:0]
            if df.empty:
                continue
            bundle[st] = raw_statement_from_frame(df, st, spec["item_columns"], f"<{table}>", ticker,
                                                  drop_incomplete=True)  # dữ liệu 2012–2015 thiếu LENGTH_SERIES
        else:
            path = Path(folder) / f"{ticker.lower()}_{STATEMENTS[st][0]}.csv"
            if path.is_file():
                bundle[st] = load_raw_statement(path, st, spec["item_columns"], ticker, drop_incomplete=True)
    missing = [t for t in REQUIRED_TABLES[family] if TABLE_TO_STATEMENT[t] not in bundle]
    if missing:
        raise ServiceError(f"{ticker}: thiếu dữ liệu bảng bắt buộc {missing}")
    manifest = pd.read_csv(CONFIG_DIR / "manifests" / f"{family}.csv", dtype=str, encoding="utf-8-sig")
    return build_history(bundle, ticker, items=load_items(), manifest=manifest)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if math.isnan(f) or math.isinf(f) else f


@dataclass
class ForecastOutput:
    """Bảng kết quả (DataFrame) của một lần dự phóng; `to_dict()` cho JSON (NaN -> null)."""
    meta: dict
    status: dict
    annual: pd.DataFrame | None = None       # dòng × năm: "2025 A" thực tế, "6T2026 A" lũy kế, "2026 F" dự phóng
    quarterly: pd.DataFrame | None = None    # dòng × quý: "2026Q2 A", "2026Q3 F"
    ratios: dict = field(default_factory=dict)  # {"annual", "quarterly"}: tỷ lệ chính, đơn vị %
    assumptions: pd.DataFrame | None = None  # giả định × năm dự phóng, kèm nguồn
    issues: pd.DataFrame | None = None
    one_offs: pd.DataFrame | None = None     # khoản bất thường đã tự loại khỏi lịch sử
    _history: History | None = field(default=None, repr=False)
    _result: ForecastResult | None = field(default=None, repr=False)

    def explain(self, line: str, period: str) -> dict:
        """Giải thích một ô: công thức, yếu tố, đóng góp vào thay đổi so với kỳ so sánh."""
        from quick_model.explain import Explainer
        if self._result.values is None:
            raise ServiceError("Dự phóng có lỗi chặn, không giải thích được")
        if line not in self._result.line_meta.index:
            raise ServiceError(f"Dòng không tồn tại: {line!r}")
        ex = Explainer(self._history, self._result).explain(line, period)
        return {"line": ex.line, "label": ex.label, "period": ex.period, "value": _num(ex.value), "unit": ex.unit,
                "headline": ex.headline, "formula": ex.formula, "compare_period": ex.compare_period,
                "prior_value": _num(ex.prior_value), "change": _num(ex.change),
                "drivers": [{"label": d.label, "unit": d.unit, "current": _num(d.current), "prior": _num(d.prior),
                             "source": d.source, "assumption_id": d.assumption_id} for d in ex.drivers],
                "bridge": [{"label": b.label, "amount": _num(b.amount), "kind": b.kind} for b in ex.bridge],
                "notes": list(ex.notes)}

    def to_dict(self) -> dict:
        def grid(df):
            cols = [c for c in df.columns if c not in ("line_id", "label", "statement", "unit")]
            return {"columns": cols, "rows": [{**{k: r[k] for k in ("line_id", "label", "statement", "unit")},
                                               "values": {c: _num(r[c]) for c in cols}} for _, r in df.iterrows()]}

        def records(df):
            return [{k: _num(v) for k, v in row.items()} for row in df.to_dict("records")]
        out = {"meta": self.meta, "status": self.status}
        if self.annual is not None:
            out["annual"], out["quarterly"] = grid(self.annual), grid(self.quarterly)
            out["ratios"] = {k: {"columns": list(v.columns), "rows": [
                {"ratio": i, "values": {c: _num(x) for c, x in row.items()}} for i, row in v.iterrows()]}
                for k, v in self.ratios.items()}
            out["assumptions"], out["one_offs"] = records(self.assumptions), records(self.one_offs)
        out["issues"] = records(self.issues)
        return out


def _statement_frame(h: History, r: ForecastResult, freq: str, n_hist: int) -> pd.DataFrame:
    from quick_model.presentation import _period_values, _quarter_values, statement_lines
    units = {l.line_id: l.unit for l in lines_for(r.template_id)}
    frames = []
    for st in OUTPUT_STATEMENTS:
        lines = statement_lines(st, r.template_id)
        if not lines:
            continue
        vals = (_quarter_values if freq == FREQ_QUARTER else _period_values)(h, r, lines, n_hist)[0].reindex(lines)
        head = pd.DataFrame({"line_id": lines, "label": r.line_meta["label_vn"].reindex(lines).fillna("").values,
                             "statement": st, "unit": [units.get(l, "VND") for l in lines]}, index=lines)
        frames.append(pd.concat([head, vals], axis=1))
    return pd.concat(frames).reset_index(drop=True)


def _assumption_frame(r: ForecastResult) -> pd.DataFrame:
    specs = get_template(r.template_id).specs
    periods = forecast_periods(r.scenario.origin, r.scenario.horizon)
    return pd.DataFrame([{"assumption_id": aid, "label": specs[aid].label_vn, "unit": specs[aid].unit,
                          "source": v.source, "note": str(v.basis.get("note", "")), **dict(zip(periods, v.values))}
                         for aid, v in r.assumptions.items()])


def _one_off_frame(h: History, r: ForecastResult) -> pd.DataFrame:
    from quick_model import oneoff
    tpl = get_template(r.template_id)
    labels = r.line_meta["label_vn"].to_dict()
    return pd.DataFrame([{"line": o.line, "quarter": o.quarter, "amount": o.amount, "rule": oneoff.rule_of(o),
                          "description": oneoff.describe(o, labels)}
                         for o in get_family(tpl.family).engine(tpl).effective_one_offs(h, r.scenario)],
                        columns=["line", "quarter", "amount", "rule", "description"])


def forecast(ticker: str, family: str = "nonfinancial", *, frames: dict | None = None,
             folder: str | Path | None = None, years: int | None = None,
             overrides: dict | None = None) -> ForecastOutput:
    """Dự phóng một mã bằng L1: giả định tự tính từ lịch sử, khoản bất thường tự loại.

    years: số năm dự phóng từ năm gốc (năm có báo cáo cả năm mới nhất), mặc định 5. Engine chạy theo quý từ sau quý
           công bố mới nhất đến hết Q4 năm sau, rồi theo năm.
    overrides: {assumption_id: số (mọi năm) | list đủ số năm | {"2026": số}} do analyst/agent đặt, đơn vị engine;
               là mục tiêu cả năm.
    """
    _check_family(family)
    ticker = str(ticker).upper().strip()
    try:
        h = _load(ticker, family, frames, folder)
    except RawDataError as e:
        raise ServiceError(str(e)) from None
    origin = default_origin(h, FREQ_ANNUAL)
    if origin is None:
        raise ServiceError(f"{ticker}: chưa có báo cáo cả năm để làm năm gốc")
    s = new_scenario(ticker, "service", origin, years or DEFAULT_ANNUAL_HORIZON, family)
    defaults = defaults_for_scenario(h, s)
    periods = forecast_periods(origin, s.horizon)
    specs = get_template(family).specs
    for aid, given in (overrides or {}).items():
        if aid not in specs:
            raise ServiceError(f"Giả định không tồn tại: {aid!r}")
        vals = list(defaults[aid].values) if aid in defaults else [None] * len(periods)
        if isinstance(given, dict):
            for p, x in given.items():
                if str(p) not in periods:
                    raise ServiceError(f"override {aid}: kỳ {p!r} không thuộc kỳ dự phóng {periods}")
                vals[periods.index(str(p))] = float(x)
        elif isinstance(given, (list, tuple)):
            if len(given) != len(periods):
                raise ServiceError(f"override {aid}: cần {len(periods)} giá trị cho {periods}")
            vals = [float(x) for x in given]
        else:
            vals = [float(given)] * len(periods)
        s = s.with_override(AssumptionValue(aid, tuple(vals), "analyst"))
    r = run_forecast(h, s, defaults)
    quarters = list(h.periods_by(FREQ_QUARTER))
    errors = sum(i.severity == "error" for i in r.issues)
    meta = {"ticker": ticker, "family": family, "model_version": MODEL_VERSION, "origin_year": origin,
            "last_reported_quarter": quarters[-1] if quarters else None, "forecast_years": list(r.periods),
            "forecast_quarters": r.quarters, "overrides": sorted(overrides or {}), "disclaimer": DISCLAIMER}
    status = {**r.status, "ok": r.values is not None and errors == 0, "errors": errors,
              "warnings": sum(i.severity == "warning" for i in r.issues)}
    out = ForecastOutput(meta, status, issues=r.issues_frame(), _history=h, _result=r)
    if r.values is None:
        return out
    from quick_model.presentation import driver_grid
    out.annual, out.quarterly = _statement_frame(h, r, FREQ_ANNUAL, 5), _statement_frame(h, r, FREQ_QUARTER, 8)
    out.ratios = {"annual": driver_grid(h, r, 5, FREQ_ANNUAL)[0], "quarterly": driver_grid(h, r, 8, FREQ_QUARTER)[0]}
    out.assumptions, out.one_offs = _assumption_frame(r), _one_off_frame(h, r)
    return out
