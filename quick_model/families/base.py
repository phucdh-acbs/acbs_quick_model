"""Nhóm BCTC (statement family) và engine theo kỳ: lớp cơ sở dùng chung.

Bốn nhóm có cấu trúc BCTC khác nhau: phi tài chính, ngân hàng, chứng khoán, bảo hiểm. Mỗi nhóm khai báo
danh mục dòng (lines.csv), dòng số dư và chủ sở hữu (bs_lines.csv), mã nguồn (items.csv) trong
config/families/<nhóm>/, cung cấp engine tính theo kỳ và bộ chỉ số lịch sử dùng làm default.
Template ngành thuộc đúng một nhóm và chỉ thay method L2/L3; L1 là chung của nhóm.
"""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

import pandas as pd

from quick_model import oneoff
from quick_model.engine.assumptions import AssumptionValue, Scenario
from quick_model.engine.validation import Issue
from quick_model.periods import FREQ_ANNUAL, FREQ_QUARTER, fiscal_year, freq_of, shift_label

from quick_model.paths import CONFIG_DIR  # noqa: E402

FAMILIES_DIR = CONFIG_DIR / "families"


@dataclass(frozen=True)
class LineDef:
    line_id: str
    statement: str  # IS | CF | SCH | OPS | BS | CHK
    label_vn: str
    block: str | None
    source_code: str | None
    formula: tuple[str, tuple[str, ...]] = ("", ())  # công thức mặc định; method có thể ghi đè
    unit: str = "VND"


@dataclass
class ForecastResult:
    scenario: Scenario
    periods: list[str]
    assumptions: dict[str, AssumptionValue]
    values: pd.DataFrame | None  # index = dòng (line_id), cột = kỳ (origin + dự phóng)
    line_meta: pd.DataFrame
    issues: list[Issue]
    status: dict = field(default_factory=dict)
    template_id: str = ""
    traces: dict = field(default_factory=dict, repr=False)  # (line_id, kỳ) -> Trace (công thức + yếu tố)
    # engine quý + năm (quick_model/hybrid.py): lịch kỳ, quý dự phóng, giả định quý hiệu lực, bảng kỳ của vòng tính
    plan: object = field(default=None, repr=False)
    quarter_values: pd.DataFrame | None = field(default=None, repr=False)  # dòng × quý dự phóng
    quarter_assumptions: dict = field(default_factory=dict, repr=False)
    engine_values: pd.DataFrame | None = field(default=None, repr=False)  # dòng × (quý gốc, quý dự phóng, năm sau đó)

    @property
    def quarters(self) -> list[str]:
        return list(self.quarter_values.columns) if self.quarter_values is not None else []

    @property
    def aggregated_years(self) -> list[str]:
        """Năm có quý: số năm = tổng các quý (không có trace riêng)."""
        return list(getattr(self.plan, "q_years", []) or []) if self.quarter_values is not None else []

    @property
    def engine_periods(self) -> list[str]:
        """Kỳ của vòng tính có trace: quý dự phóng rồi năm chạy theo năm."""
        if self.plan is not None and self.quarter_values is not None:
            return list(self.plan.periods)
        return list(self.periods)

    def issues_frame(self) -> pd.DataFrame:
        return pd.DataFrame([i.__dict__ for i in self.issues],
                            columns=["severity", "code", "block", "target", "period", "message"])


def _parse_terms(expr: str) -> list[tuple[int, str]]:
    terms = []
    for tok in str(expr).split():
        if tok[0] not in "+-" or len(tok) < 2:
            raise ValueError(f"Biểu thức mã không hợp lệ: {expr!r}")
        terms.append((1 if tok[0] == "+" else -1, tok[1:]))
    return terms


class StatementFamily(ABC):
    """Một nhóm BCTC. Lớp con khai báo id, nhãn và engine."""

    family_id: str = ""
    label_vn: str = ""
    implemented: bool = True
    reason: str = ""
    # Cấu trúc phân rã nguyên nhân (quick_model/explain.py), riêng từng nhóm BCTC:
    # subtotals: dòng tổng = tổng có dấu các dòng thành phần; profit_lines: dòng lợi nhuận phân rã tới yếu tố gốc;
    # margin_lines: dòng = cơ sở × biên -> tách tác động cơ sở và biên {dòng: (dòng cơ sở, giả định biên, nhãn biên,
    # dòng để xem tiếp phần biên)}.
    subtotals: dict = {}
    profit_lines: tuple = ()
    margin_lines: dict = {}
    # Trình bày (dashboard): nhóm có LCTT hay không, các tab báo cáo, dòng của dải kịch bản, đầu vào bảng chỉ số.
    has_cash_flow: bool = True
    statement_tabs: tuple = (("IS", "KQKD"), ("BS", "Cân đối kế toán"), ("CF", "Lưu chuyển tiền tệ"),
                             ("SCH", "Lịch phụ trợ"), ("OPS", "Driver vận hành"))
    band_lines: tuple = (("net_revenue", "Doanh thu thuần"), ("npat_parent", "LNST cổ đông công ty mẹ"),
                         ("gross_profit", "Lợi nhuận gộp"))
    ratio_inputs: tuple = ()
    def register_explain(self) -> None:
        """Đăng ký kiểu hist riêng của nhóm cho quick_model.explain (gọi khi Explainer khởi tạo)."""

    def ratio_table(self, frame, days=None, contiguous=None, growth_lag=1):
        """Bảng chỉ số trên giá trị theo dòng (cùng công thức cho cột A và F). None: dùng bảng tỷ lệ mặc định."""
        return None

    @property
    def config_dir(self) -> Path:
        return FAMILIES_DIR / self.family_id

    @property
    def items_path(self) -> Path:
        return self.config_dir / "items.csv"

    @cached_property
    def bs_lines(self) -> pd.DataFrame:
        cfg = pd.read_csv(self.config_dir / "bs_lines.csv", dtype=str, encoding="utf-8").fillna("")
        if cfg["line_id"].duplicated().any():
            raise ValueError(f"{self.family_id}/bs_lines.csv có line_id trùng")
        cfg["terms"] = cfg["codes"].map(_parse_terms)
        return cfg

    @cached_property
    def lines(self) -> tuple[LineDef, ...]:
        out = []
        with open(self.config_dir / "lines.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out.append(LineDef(r["line_id"], r["statement"], r["label_vn"], r["block"] or None,
                                   r["source_code"] or None, (r["formula_vn"], tuple(r["assumptions"].split())),
                                   r["unit"] or "VND"))
        return tuple(out)

    def lines_for(self, template) -> list[LineDef]:
        """Dòng của nhóm + dòng vận hành riêng của template ngành."""
        extra = [LineDef(x["line_id"], "OPS", x["label_vn"], x.get("block"), x.get("source_code"),
                         ("", ()), x.get("unit", "VND")) for x in template.extra_lines]
        ids = {l.line_id for l in self.lines}
        return [*self.lines, *[l for l in extra if l.line_id not in ids]]

    def line_meta(self, template, methods: dict[str, str]) -> pd.DataFrame:
        """Nhãn, block, công thức và giả định của từng dòng theo method đang chọn."""
        by_id = template.methods_by_id
        rows = []

        def formula(line_id, block, default):
            m = by_id.get(methods.get(block)) if block else None
            return tuple((m.line_formulas.get(line_id) if m else None) or default)

        for l in self.lines_for(template):
            fml, aids = formula(l.line_id, l.block, l.formula if l.formula[0] or l.statement != "OPS"
                                else ("Không có ở method đang chọn", ()))
            rows.append(dict(line_id=l.line_id, statement=l.statement, label_vn=l.label_vn, block=l.block,
                             formula_vn=fml, assumptions=",".join(aids), source_code=l.source_code, unit=l.unit))
        blocks = set(template.blocks)
        for r in self.bs_lines.itertuples():
            block = r.owner if r.owner in blocks else None
            fml, aids = formula(r.line_id, block, (r.formula_vn, tuple(r.assumptions.split())))
            rows.append(dict(line_id=r.line_id, statement="BS", label_vn=r.label_vn, block=block,
                             formula_vn=fml, assumptions=",".join(aids), source_code=r.codes, unit="VND"))
        return pd.DataFrame(rows).set_index("line_id")

    @abstractmethod
    def engine(self, template) -> "PeriodEngine":
        ...

    def validate_template(self, template) -> list[str]:
        """Kiểm tra template khớp nhóm (kind có class, block khớp, metric default có thật)."""
        return []


class PlannedFamily(StatementFamily):
    """Nhóm đã khai báo nhưng chưa có engine (giai đoạn sau)."""

    implemented = False

    def __init__(self, family_id: str, label_vn: str, reason: str):
        self.family_id, self.label_vn, self.reason = family_id, label_vn, reason

    def engine(self, template):
        raise NotImplementedError(f"Nhóm {self.label_vn} chưa có engine: {self.reason}")


def forecast_periods(origin: str, horizon: int) -> list[str]:
    if horizon < 1:
        raise ValueError("horizon phải >= 1")
    return [shift_label(origin, i) for i in range(1, horizon + 1)]


class PeriodEngine(ABC):
    """Engine theo kỳ của một nhóm BCTC cho một template.

    Phần dùng chung cho mọi nhóm: số dư kỳ gốc theo bs_lines (đối chiếu tổng TS BSA53 / tổng nguồn vốn BSA96), tổng
    tài sản – nguồn vốn, ghi chú chạy theo năm, phát hiện và công bố khoản bất thường."""

    def __init__(self, family: StatementFamily, template, bs_lines: pd.DataFrame | None = None):
        self.family, self.template = family, template
        self.bs_lines = family.bs_lines if bs_lines is None else bs_lines  # cho phép thay để kiểm thử

    @abstractmethod
    def run(self, history, scenario: Scenario, defaults: dict[str, AssumptionValue]) -> ForecastResult:
        ...

    def stock_lines(self) -> set[str]:
        """Dòng số dư (gộp quý -> năm lấy Q4)."""
        return set(self.bs_lines["line_id"]) | {"total_assets", "total_resources", "balance_residual"}

    # ------------------------------------------------------------ số dư kỳ gốc (dùng chung)
    def totals(self, bal: dict[str, float]) -> tuple[float, float]:
        side = self.bs_lines.set_index("line_id")["side"]
        assets = sum(bal[k] for k in side.index if side[k] == "asset")
        resources = sum(bal[k] for k in side.index if side[k] != "asset")
        return assets, resources

    def opening_balances(self, h, origin: str) -> tuple[dict[str, float], list[Issue]]:
        bs_lines = self.bs_lines
        bal = h.balances_by(freq_of(origin))
        if origin not in bal.index:
            return {}, [Issue("error", "origin_missing", None, None, origin, f"Không có BS tại kỳ gốc {origin}")]
        row = bal.loc[origin]
        blocks = set(self.template.blocks)
        issues, out = [], {}
        for line in bs_lines.itertuples():
            vals = [(s, row.get(c)) for s, c in line.terms]
            if all(pd.isna(x) for _, x in vals):
                issues.append(Issue("info", "blank_balance", line.owner if line.owner in blocks else None, line.line_id,
                                    origin, f"{line.label_vn}: ô trống trong nguồn, số dư kỳ gốc coi là 0"))
            out[line.line_id] = sum(s * (0.0 if pd.isna(x) else float(x)) for s, x in vals)
        tol = h.policy.abs_tolerance
        assets, resources = self.totals(out)
        for name, mine, code in (("tài sản", assets, "BSA53"), ("nguồn vốn", resources, "BSA96")):
            reported = row.get(code)
            if pd.isna(reported) or abs(mine - reported) > tol:
                issues.append(Issue("error", "unmapped_balance", None, code, origin,
                                    f"Tổng {name} theo các dòng dự phóng ({mine:,.0f}) khác {code} ({reported:,.0f}): "
                                    "có tài khoản chưa được gán method"))
        return out, issues

    @staticmethod
    def ytd_quarters(h, s: Scenario) -> list[str]:
        """Chạy theo năm: các quý đã công bố của năm dự phóng đầu tiên (kỳ công bố mới nhất)."""
        if s.frequency != FREQ_ANNUAL:
            return []
        y = int(s.origin) + 1
        return [q for q in h.periods_by(FREQ_QUARTER) if fiscal_year(q) == y]

    def frequency_notes(self, h, s: Scenario, plan=None) -> list[Issue]:
        if plan is None:
            return []
        if plan.hybrid:
            qs = plan.actual.get(plan.q_years[0], [])
            return [Issue("info", "annual_ytd", None, None, plan.q_origin,
                          f"Kỳ công bố mới nhất {plan.q_origin}. Năm gốc {s.origin}; chạy theo quý {plan.quarters[0]}–"
                          f"{plan.quarters[-1]} (số dư đầu = BS {plan.q_origin}), rồi theo năm. Năm "
                          + ", ".join(plan.q_years) + " = tổng các quý"
                          + (f" (gồm {len(qs)} quý thực tế của {plan.q_years[0]})" if qs else "") + ".")]
        return [Issue("info", "annual_ytd", None, None, s.origin, plan.note)] if plan.note else []

    @staticmethod
    def one_off_year(s: Scenario) -> int:
        """Năm hiện tại của quét khoản bất thường: năm sau năm gốc (chạy năm) hoặc năm của kỳ gốc (chạy quý)."""
        return int(s.origin) + 1 if s.frequency == FREQ_ANNUAL else fiscal_year(s.origin)

    def one_off_candidates(self, h, s: Scenario) -> list:
        """Khoản bất thường ở các quý đã công bố của năm hiện tại, rồi các năm lịch sử (template one_off.history_years;
        quét tuần tự trên chuỗi đã thay các quý bất thường trước đó bằng trend)."""
        if s.frequency == FREQ_ANNUAL:
            qs = self.ytd_quarters(h, s)
        else:
            qs = [q for q in h.periods_by(FREQ_QUARTER) if fiscal_year(q) == fiscal_year(s.origin) and q <= s.origin]
        hist, cur = oneoff.scan(h, self.template, self.one_off_year(s), qs)
        return cur + hist

    def effective_one_offs(self, h, s: Scenario) -> tuple:
        """Quyết định của analyst + khoản lịch sử tự loại (template one_off.auto_history). Dùng cho default và gốc."""
        auto = oneoff.auto_history(self.one_off_candidates(h, s), s.one_offs, self.one_off_year(s), self.template)
        return tuple(s.one_offs) + tuple(auto)

    def one_off_issues(self, h, s: Scenario, periods: list[str]) -> list[Issue]:
        labels = {l.line_id: l.label_vn for l in self.family.lines}
        block = (self.template.one_off or {}).get("block", "non_operating")
        out = []
        cands = self.one_off_candidates(h, s)
        auto = oneoff.auto_history(cands, s.one_offs, self.one_off_year(s), self.template)
        if auto:
            total = sum(o.amount for o in auto)
            out.append(Issue("info", "ONE-03", block, None, None,
                             f"Đã tự áp {len(auto)} khoản quý bất thường (loại khỏi chuỗi làm default năm và quý; quý đã "
                             "công bố giữ số thực tế trong tổng năm) "
                             f"(tổng {total / 1e9:+,.0f} tỷ): " + "; ".join(
                                 f"{labels.get(o.line, o.line)} {o.quarter} {o.amount / 1e9:+,.0f} tỷ" for o in auto)
                             + ". Số thực tế giữ nguyên; analyst bỏ qua được từng khoản."))
        for c in oneoff.pending(cands, tuple(s.one_offs) + tuple(auto)):
            out.append(Issue("warning", "ONE-01", block, c.line, c.quarter,
                             oneoff.describe(c, labels) + ". Chưa xử lý: xác nhận để loại khỏi chuỗi làm default "
                             "(số thực tế giữ nguyên) hoặc bỏ qua."))
        for o in oneoff.confirmed(tuple(s.one_offs) + tuple(o for o in auto if fiscal_year(o.quarter) > fiscal_year(s.origin))):
            where = "số thực tế giữ nguyên (nằm trong tổng năm); loại khỏi chuỗi làm default"
            out.append(Issue("info", "ONE-02", block, o.line, o.quarter,
                             f"{oneoff.describe(o, labels)}: {where}."
                             + (f" Lý do: {o.reason}" if o.reason else "")))
        return out
