"""Nhóm phi tài chính: một bộ BCTC, một L1 chung cho mọi ngành; template ngành chỉ thêm L2/L3."""

from __future__ import annotations

from quick_model.families.base import StatementFamily
from quick_model.families.nonfinancial import methods as _methods  # noqa: F401  (đăng ký class theo kind)
from quick_model.families.nonfinancial.engine import SUBTOTALS, NonFinancialEngine
from quick_model.families.nonfinancial.metrics import NonFinancialMetrics
from quick_model.methods.base import registered_kinds


class NonFinancialFamily(StatementFamily):
    family_id = "nonfinancial"
    label_vn = "Phi tài chính"
    subtotals = SUBTOTALS
    profit_lines = ("operating_profit", "pbt", "npat", "npat_parent")
    margin_lines = {"gross_profit": ("net_revenue", "gross_margin", "Biên lợi nhuận gộp", "cogs")}

    def engine(self, template, bs_lines=None) -> NonFinancialEngine:
        problems = self.validate_template(template)
        if problems:
            raise ValueError(f"Template {template.template_id} không chạy được với nhóm {self.label_vn}: "
                             + "; ".join(problems))
        return NonFinancialEngine(self, template, bs_lines)

    def metrics(self, history, origin: str, annual: bool, adjustments: dict | None = None) -> NonFinancialMetrics:
        return NonFinancialMetrics(history, origin, annual, adjustments)

    def validate_template(self, template) -> list[str]:
        problems = []
        if template.family != self.family_id:
            problems.append(f"template thuộc nhóm {template.family}")
        kinds = registered_kinds()
        for m in template.methods:
            if not m.implemented:
                continue
            cls = kinds.get(m.kind)
            if cls is None:
                problems.append(f"{m.method_id}: chưa có class cho kind {m.kind}")
            elif cls.block != m.block:
                problems.append(f"{m.method_id}: kind {m.kind} thuộc block {cls.block}")
        metric_names = {n[2:] for n in dir(NonFinancialMetrics) if n.startswith("m_")}
        for aid, d in template.default_specs.items():
            if d.get("type") == "history" and d.get("metric") not in metric_names:
                problems.append(f"{aid}: chỉ số lịch sử không có {d.get('metric')!r}")
        return problems
