"""Điểm vào dự phóng: chọn nhóm BCTC và engine theo template của kịch bản.

Logic tính nằm trong quick_model/families/<nhóm>/ (engine theo kỳ, method theo kind). Module này giữ các hàm
dùng chung (tạo kịch bản, kỳ dự phóng, chạy dự phóng) cho UI, công cụ và test.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from quick_model.data.history import History
from quick_model.engine.assumptions import AssumptionValue, Scenario
from quick_model.engine.registry import DEFAULT_TEMPLATE_ID, get_template, template_for
from quick_model.families import ForecastResult, LineDef, all_families, get_family
from quick_model.families.base import forecast_periods
from quick_model.methods.base import registered_kinds
from quick_model.methods.library import load_library
from quick_model.periods import FREQ_ANNUAL, freq_of

DEFAULT_ANNUAL_HORIZON = 5  # năm; đề xuất cấu hình được (model card analyst thường dự phóng 5–6 năm)


def _family(template_id: str | None = None):
    return get_family(get_template(template_id).family)


# Kind engine chạy được = kind có class đăng ký và đã đánh dấu triển khai trong thư viện
all_families()  # nạp mọi nhóm BCTC để class method của từng nhóm đã đăng ký
_lib = load_library()
ENGINE_KINDS = frozenset(k for k in registered_kinds() if k in _lib.kinds and _lib.kinds[k].implemented)
BLOCK_KINDS: dict[str, tuple[str, ...]] = {}
for _k, _cls in registered_kinds().items():
    BLOCK_KINDS[_cls.block] = BLOCK_KINDS.get(_cls.block, ()) + (_k,)


def lines_for(template_id: str | None = None) -> list[LineDef]:
    return _family(template_id).lines_for(get_template(template_id))


LINES = lines_for(DEFAULT_TEMPLATE_ID)
LINES_BY_ID = {l.line_id: l for l in LINES}


def load_bs_lines(path: Path | None = None, template_id: str | None = None) -> pd.DataFrame:
    if path is not None:
        from quick_model.families.base import _parse_terms
        cfg = pd.read_csv(path, dtype=str, encoding="utf-8").fillna("")
        cfg["terms"] = cfg["codes"].map(_parse_terms)
        return cfg
    return _family(template_id).bs_lines


def default_horizon(origin: str) -> int:
    """Năm: DEFAULT_ANNUAL_HORIZON năm. (Nhãn quý chỉ còn dùng cho kiểu xem; giữ để tương thích: đến hết năm sau.)"""
    if freq_of(origin) == FREQ_ANNUAL:
        return DEFAULT_ANNUAL_HORIZON
    return (4 - int(origin[-1])) + 4


def default_origin(h: History, frequency: str) -> str | None:
    """Kỳ gốc = kỳ thực tế công bố gần nhất theo tần suất: quý mới nhất, hoặc năm có báo cáo cả năm mới nhất."""
    periods = h.periods_by(frequency)
    return periods[-1] if periods else None


def new_scenario(company: str, scenario_id: str, origin: str, horizon: int | None = None,
                 template_id: str | None = None) -> Scenario:
    """Kịch bản mới: mọi block ở L1 mặc định, chưa có override. Tần suất suy từ nhãn kỳ gốc."""
    tpl = get_template(template_id)
    return Scenario(company, scenario_id, origin, horizon or default_horizon(origin), dict(tpl.default_methods),
                    template_id=tpl.template_id)


def run_forecast(h: History, scenario: Scenario, defaults: dict[str, AssumptionValue],
                 bs_lines: pd.DataFrame | None = None) -> ForecastResult:
    """Kịch bản tạo theo năm. Vòng tính chạy theo quý đến hết Q4 năm sau quý công bố mới nhất rồi theo năm
    (quick_model/hybrid.py, owner 2026-11-18), nên số năm = tổng các quý. Kịch bản gốc quý không chạy."""
    tpl = template_for(scenario)
    engine = get_family(tpl.family).engine(tpl, bs_lines)
    if scenario.frequency != FREQ_ANNUAL:
        from quick_model.engine.validation import Issue
        return ForecastResult(scenario, forecast_periods(scenario.origin, scenario.horizon), {}, None,
                              get_family(tpl.family).line_meta(tpl, scenario.methods),
                              [Issue("error", "frequency_unavailable", None, None, scenario.origin,
                                     "Tạo kịch bản theo năm (năm gốc = báo cáo cả năm mới nhất): engine tự chạy theo quý "
                                     "đến hết Q4 năm sau rồi theo năm; chọn kiểu xem Quý để xem các quý.")], {},
                              tpl.template_id)
    return engine.run(h, scenario, defaults)


def line_meta(bs_lines: pd.DataFrame | None = None, methods: dict[str, str] | None = None,
              template_id: str | None = None) -> pd.DataFrame:
    tpl = get_template(template_id)
    return get_family(tpl.family).line_meta(tpl, methods or dict(tpl.default_methods))


__all__ = ["ForecastResult", "LINES", "LINES_BY_ID", "forecast_periods", "load_bs_lines", "new_scenario",
           "run_forecast", "default_origin", "default_horizon", "lines_for", "line_meta"]
