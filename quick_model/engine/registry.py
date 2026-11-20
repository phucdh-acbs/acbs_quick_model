"""Truy cập template theo kịch bản; giữ tên cũ (BLOCKS, METHODS, SPECS…) trỏ tới template mặc định.

Nội dung method/assumption nằm trong config: config/templates/<id>/template.json (có kế thừa) và
config/methods/library.json. Mỗi kịch bản mang template_id; module nghiệp vụ lấy template bằng
template_for(scenario) thay vì dùng tên toàn cục.
"""

from __future__ import annotations

from quick_model.engine.template import MethodSpec, MethodUnavailable, Template, available_templates, load_template

DEFAULT_TEMPLATE_ID = "manufacturing"


def get_template(template_id: str | None = None) -> Template:
    return load_template(template_id or DEFAULT_TEMPLATE_ID)


def template_for(obj) -> Template:
    """Template của một Scenario hoặc ForecastResult (hoặc template_id)."""
    if isinstance(obj, str) or obj is None:
        return get_template(obj)
    tid = getattr(obj, "template_id", None) or getattr(getattr(obj, "scenario", None), "template_id", None)
    return get_template(tid)


TEMPLATE: Template = get_template()
TEMPLATE_ID = TEMPLATE.template_id
TEMPLATE_VERSION = TEMPLATE.version
BLOCKS = TEMPLATE.blocks
BLOCK_LABELS = TEMPLATE.block_labels
METHODS = list(TEMPLATE.methods)
METHODS_BY_ID = TEMPLATE.methods_by_id
DEFAULT_METHODS = TEMPLATE.default_methods
ASSUMPTIONS = list(TEMPLATE.assumptions)
SPECS = TEMPLATE.specs
REQUIRES_WHEN_FILLED = TEMPLATE.requires_when_filled

method_specs = TEMPLATE.method_specs
check_method_selection = TEMPLATE.check_method_selection
kind_of = TEMPLATE.kind_of

__all__ = ["DEFAULT_TEMPLATE_ID", "get_template", "template_for", "available_templates", "TEMPLATE", "TEMPLATE_ID",
           "TEMPLATE_VERSION", "BLOCKS", "BLOCK_LABELS", "METHODS", "METHODS_BY_ID", "DEFAULT_METHODS", "ASSUMPTIONS",
           "SPECS", "REQUIRES_WHEN_FILLED", "MethodSpec", "MethodUnavailable", "method_specs",
           "check_method_selection", "kind_of"]
