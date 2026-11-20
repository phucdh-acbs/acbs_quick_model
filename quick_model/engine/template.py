"""Template ngành dạng config, có kế thừa: config/templates/<template_id>/template.json.

Cây kế thừa: nhóm BCTC (family) -> template chung của nhóm (vd. `nonfinancial`: mọi L1 + L2 chung) -> template
ngành (`extends`, vd. `manufacturing`, `retail`): chỉ thêm/ghi đè method L2/L3, assumption, dòng vận hành
riêng. Template con kế thừa block, method, assumption, dòng, file cấu hình của cha; ghi đè theo ID.

Mỗi method gắn một kind của thư viện; engine dispatch theo kind. Mỗi assumption khai báo cách lấy default
(`default`) và, nếu được phép, cách cân nguồn tự động điều chỉnh (`auto_balance`).
Template lỗi thì báo đủ mọi vấn đề một lần và không nạp (không tự sửa, không bỏ qua).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path

from quick_model.engine.assumptions import AssumptionSpec
from quick_model.methods.library import MethodLibrary, load_library
from quick_model.units import KNOWN_UNITS

from quick_model.paths import CONFIG_DIR  # noqa: E402

TEMPLATES_DIR = CONFIG_DIR / "templates"
LEVELS = ("L1", "L2", "L3")
DEFAULT_TYPES = ("history", "explicit", "needs_input", "blank", "copy", "cost_structure")
HISTORY_STATS = ("median", "mean", "min", "max", "latest", "same_period_latest", "converge", "ema", "ema_rolling", "ema_decay")
CONVERGE_STARTS = ("latest", "same_period_latest")


class TemplateError(ValueError):
    pass


class MethodUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class MethodSpec:
    method_id: str
    block: str
    level: str
    label_vn: str
    version: str
    implemented: bool
    required_items: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    unavailable_reason: str | None = None
    notes_vn: str = ""
    provides: tuple[
        str, ...
    ] = ()  # driver vận hành cung cấp cho block khác (vd. sales_volume)
    requires: tuple[str, ...] = ()  # driver bắt buộc phải có từ block khác
    kind: str = ""  # kiểu method trong thư viện; engine dispatch theo kind
    roles: dict = field(
        default_factory=dict, compare=False, hash=False
    )  # vai trò -> assumption ID
    line_formulas: dict = field(
        default_factory=dict, compare=False, hash=False
    )  # line_id -> [công thức, [aid]]
    defined_in: str = (
        ""  # template khai báo method (để biết là L chung của nhóm hay riêng ngành)
    )


@dataclass(frozen=True)
class Template:
    template_id: str
    version: str
    label_vn: str
    family: str
    lineage: tuple[str, ...]  # (template này, cha, ông…)
    directory: Path
    frequencies: dict
    blocks: tuple[str, ...]
    block_labels: dict[str, str]
    methods: tuple[MethodSpec, ...]
    assumptions: tuple[AssumptionSpec, ...]
    default_specs: dict[str, dict]
    auto_balance_specs: dict[str, dict]
    extra_lines: tuple[dict, ...]
    requires_when_filled: dict[str, tuple[str, ...]]
    files: dict[str, Path]
    defined_in: dict[str, str] = field(
        default_factory=dict, compare=False, hash=False
    )  # assumption -> template
    one_off: dict = field(
        default_factory=dict, compare=False, hash=False
    )  # phát hiện khoản bất thường: dòng trong phạm vi, cửa sổ, ngưỡng (quick_model/oneoff.py)
    default_paths: dict = field(
        default_factory=dict, compare=False, hash=False
    )  # đường default L1: số năm hội tụ, cửa sổ neo cả chu kỳ, mùa vụ quý, cửa sổ tách cố định/biến đổi, dải kịch bản
    quarterly_engine: dict = field(
        default_factory=dict, compare=False, hash=False
    )  # engine quý + năm (quick_model/hybrid.py): λ, span EMA quý, giả định chỉ theo năm, mục tiêu override năm

    @cached_property
    def methods_by_id(self) -> dict[str, MethodSpec]:
        return {m.method_id: m for m in self.methods}

    @cached_property
    def specs(self) -> dict[str, AssumptionSpec]:
        return {s.assumption_id: s for s in self.assumptions}

    @cached_property
    def default_methods(self) -> dict[str, str]:
        """Kịch bản mới: mỗi block chạy method L1 đã triển khai đầu tiên."""
        return {
            b: next(
                m.method_id
                for m in self.methods
                if m.block == b and m.level == "L1" and m.implemented
            )
            for b in self.blocks
        }

    def file(self, name: str) -> Path:
        return self.files[name]

    def kind_of(self, method_id: str | None) -> str | None:
        m = self.methods_by_id.get(method_id) if method_id else None
        return m.kind if m else None

    def level_of(self, method_id: str | None) -> str | None:
        m = self.methods_by_id.get(method_id) if method_id else None
        return m.level if m else None

    def role(self, method_id: str, role: str):
        roles = self.methods_by_id[method_id].roles
        if role not in roles:
            raise TemplateError(f"Method {method_id} không khai báo vai trò {role!r}")
        return roles[role]

    def has_role(self, method_id: str, role: str) -> bool:
        m = self.methods_by_id.get(method_id)
        return m is not None and role in m.roles

    def method_specs(self, method_id: str) -> list[AssumptionSpec]:
        """Assumption hiển thị cho một method: riêng của method + dùng chung của block."""
        block = method_id.split(".")[0]
        return [
            s
            for s in self.assumptions
            if s.method_id == method_id or (s.shared and s.block == block)
        ]

    def check_method_selection(self, methods: dict[str, str]) -> None:
        """Mỗi block đúng một method, method tồn tại và đã triển khai."""
        problems = []
        by_id = self.methods_by_id
        for b in self.blocks:
            if b not in methods:
                problems.append(f"Block {b}: chưa chọn method")
        for b, mid in methods.items():
            m = by_id.get(mid)
            if m is None:
                problems.append(
                    f"Block {b}: method không tồn tại trong template {self.template_id} {mid!r}"
                )
            elif m.block != b:
                problems.append(f"Block {b}: method {mid} thuộc block {m.block}")
            elif not m.implemented:
                problems.append(
                    f"Block {b}: {m.label_vn} ({m.level}) chưa dùng được — {m.unavailable_reason}"
                )
        if problems:
            raise MethodUnavailable("; ".join(problems))


def _tuple(x) -> tuple:
    return tuple(x or ())


# ---------------------------------------------------------------- kế thừa


def _read(template_id: str, templates_dir: Path) -> dict:
    path = Path(templates_dir) / template_id / "template.json"
    if not path.exists():
        raise TemplateError(f"Không tìm thấy template: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_by_id(
    parent: list[dict], child: list[dict], key: str, tid: str
) -> list[dict]:
    out = [dict(x) for x in parent]
    index = {x[key]: i for i, x in enumerate(out)}
    for x in child:
        x = {**x, "_defined_in": tid}
        if x[key] in index:
            out[index[x[key]]] = x
        else:
            index[x[key]] = len(out)
            out.append(x)
    return out


def resolve_raw(
    template_id: str, templates_dir: Path = TEMPLATES_DIR, _seen: tuple = ()
) -> dict:
    """Template đã gộp với mọi cha (dict thô), kèm lineage và đường dẫn file đã phân giải."""
    if template_id in _seen:
        raise TemplateError(f"Kế thừa vòng: {' -> '.join(_seen + (template_id,))}")
    data = _read(template_id, templates_dir)
    directory = Path(templates_dir) / template_id
    own_files = {k: directory / v for k, v in data.get("files", {}).items()}
    if not data.get("extends"):
        out = copy.deepcopy(data)
        for key in ("methods", "assumptions", "lines"):
            out[key] = [{**x, "_defined_in": template_id} for x in out.get(key, [])]
        out["_lineage"] = (template_id,)
        out["_files"] = own_files
        out["_directory"] = directory
        return out
    parent = resolve_raw(data["extends"], templates_dir, _seen + (template_id,))
    out = copy.deepcopy(parent)
    for k in ("template_id", "version", "label_vn", "description_vn", "schema_version"):
        if k in data:
            out[k] = data[k]
    if "blocks" in data and [b["block_id"] for b in data["blocks"]] != [
        b["block_id"] for b in parent["blocks"]
    ]:
        raise TemplateError(
            f"Template {template_id}: không được đổi danh sách block của {data['extends']}"
        )
    if "family" in data and data["family"] != parent.get("family"):
        raise TemplateError(f"Template {template_id}: family khác template cha")
    out["methods"] = _merge_by_id(
        parent["methods"], data.get("methods", []), "method_id", template_id
    )
    for patch in data.get("method_patches", []):
        m = next(
            (x for x in out["methods"] if x["method_id"] == patch["method_id"]), None
        )
        if m is None:
            raise TemplateError(
                f"Template {template_id}: method_patches trỏ tới method không có {patch['method_id']}"
            )
        for k, v in patch.items():
            if k == "line_formulas":
                m["line_formulas"] = {**m.get("line_formulas", {}), **v}
            elif k != "method_id":
                m[k] = v
    out["assumptions"] = _merge_by_id(
        parent["assumptions"], data.get("assumptions", []), "assumption_id", template_id
    )
    out["lines"] = _merge_by_id(
        parent.get("lines", []), data.get("lines", []), "line_id", template_id
    )
    out["requires_when_filled"] = {
        **parent.get("requires_when_filled", {}),
        **data.get("requires_when_filled", {}),
    }
    if "one_off" in data:  # ngành chỉnh phạm vi/ngưỡng; khóa nào không khai báo thì kế thừa
        out["one_off"] = {**parent.get("one_off", {}), **data["one_off"]}
    if "default_paths" in data:  # ví dụ ngành chu kỳ dài hơn: cửa sổ neo 12 năm
        out["default_paths"] = {**parent.get("default_paths", {}), **data["default_paths"]}
    if "quarterly_engine" in data:  # ngành thêm giả định số dư / mục tiêu override của method L2/L3
        out["quarterly_engine"] = {**parent.get("quarterly_engine", {}), **data["quarterly_engine"]}
    out["_lineage"] = (template_id,) + parent["_lineage"]
    out["_files"] = {**parent["_files"], **own_files}
    out["_directory"] = directory
    out.pop("extends", None)
    return out


# ---------------------------------------------------------------- kiểm tra và dựng


def parse_template(
    data: dict,
    directory: Path,
    library: MethodLibrary,
    lineage: tuple[str, ...] | None = None,
    files: dict[str, Path] | None = None,
) -> Template:
    problems: list[str] = []
    tid = data.get("template_id")
    lineage = lineage or data.get("_lineage") or (tid,)
    files = (
        files
        if files is not None
        else data.get("_files")
        or {k: directory / v for k, v in data.get("files", {}).items()}
    )
    if not tid or not data.get("version"):
        problems.append("thiếu template_id hoặc version")
    family = data.get("family")
    if not family:
        problems.append("thiếu family (nhóm BCTC)")

    blocks, labels = [], {}
    for b in data.get("blocks", []):
        bid = b.get("block_id")
        if not bid or bid in labels:
            problems.append(f"block thiếu id hoặc trùng: {bid!r}")
            continue
        blocks.append(bid)
        labels[bid] = b.get("label_vn", bid)

    methods: list[MethodSpec] = []
    seen = set()
    for m in data.get("methods", []):
        mid, block, level, kind = (
            m.get("method_id"),
            m.get("block"),
            m.get("level"),
            m.get("kind"),
        )
        where = f"method {mid}"
        if not mid or mid in seen:
            problems.append(f"method thiếu id hoặc trùng: {mid!r}")
            continue
        seen.add(mid)
        if block not in labels:
            problems.append(f"{where}: block không khai báo {block!r}")
        if not str(mid).startswith(f"{block}."):
            problems.append(f"{where}: method_id phải bắt đầu bằng '<block>.'")
        if level not in LEVELS:
            problems.append(f"{where}: level không hợp lệ {level!r}")
        implemented = bool(m.get("implemented", False))
        if kind not in library.kinds:
            problems.append(f"{where}: kind không có trong thư viện {kind!r}")
        elif implemented and not library.kinds[kind].implemented:
            problems.append(
                f"{where}: đánh dấu implemented nhưng kind {kind} chưa triển khai trong thư viện"
            )
        if not implemented and not m.get("unavailable_reason"):
            problems.append(
                f"{where}: method chưa triển khai phải có unavailable_reason"
            )
        methods.append(
            MethodSpec(
                mid,
                block,
                level,
                m.get("label_vn", ""),
                str(m.get("version", "")),
                implemented,
                _tuple(m.get("required_items")),
                _tuple(m.get("outputs")),
                m.get("unavailable_reason"),
                m.get("notes_vn", ""),
                _tuple(m.get("provides")),
                _tuple(m.get("requires")),
                kind or "",
                dict(m.get("roles", {})),
                dict(m.get("line_formulas", {})),
                m.get("_defined_in", tid or ""),
            )
        )

    for b in blocks:
        if not any(m.block == b and m.level == "L1" and m.implemented for m in methods):
            problems.append(
                f"block {b}: không có method L1 đã triển khai (cần làm mặc định)"
            )
    provided = {d for m in methods for d in m.provides}
    for m in methods:
        for d in m.requires:
            if d not in provided:
                problems.append(
                    f"method {m.method_id}: cần driver {d!r} nhưng không method nào cung cấp"
                )

    by_id = {m.method_id: m for m in methods}
    specs: list[AssumptionSpec] = []
    defaults, autos, defined = {}, {}, {}
    seen_a = set()
    for a in data.get("assumptions", []):
        aid, mid = a.get("assumption_id"), a.get("method_id", "")
        where = f"assumption {aid}"
        if not aid or aid in seen_a:
            problems.append(f"assumption thiếu id hoặc trùng: {aid!r}")
            continue
        seen_a.add(aid)
        block = mid.split(".")[0]
        shared = bool(a.get("shared", False))
        if shared:
            if mid != f"{block}.*" or block not in labels:
                problems.append(
                    f"{where}: assumption dùng chung phải có method_id '<block>.*' với block hợp lệ"
                )
        elif mid not in by_id:
            problems.append(f"{where}: method không tồn tại {mid!r}")
        unit = a.get("unit")
        if unit not in KNOWN_UNITS:
            problems.append(f"{where}: đơn vị không hợp lệ {unit!r}")
        rng = a.get("valid_range", [None, None])
        if len(rng) != 2:
            problems.append(f"{where}: valid_range phải có 2 phần tử")
            rng = [None, None]
        lo, hi = rng
        if lo is not None and hi is not None and lo > hi:
            problems.append(f"{where}: valid_range min > max")
        d = a.get("default")
        if not isinstance(d, dict) or d.get("type") not in DEFAULT_TYPES:
            problems.append(
                f"{where}: thiếu hoặc sai cách lấy default (type thuộc {DEFAULT_TYPES})"
            )
        elif d["type"] == "history" and (
            not d.get("metric") or d.get("stat") not in HISTORY_STATS
        ):
            problems.append(
                f"{where}: default lịch sử cần metric và stat thuộc {HISTORY_STATS}"
            )
        elif d["type"] == "history" and d["stat"] == "converge" and (
            d.get("start") not in CONVERGE_STARTS or d.get("favourable") not in ("high", "low")
            or not isinstance(d.get("anchor", {}), dict)
            or not isinstance(d.get("fade_years", 1), (int, float))
        ):
            problems.append(
                f"{where}: default hội tụ cần start thuộc {CONVERGE_STARTS}, favourable high|low, "
                "anchor (nếu có) dạng {{assumption: id}}, fade_years là số"
            )
        elif d["type"] == "cost_structure" and (
            not d.get("expense_metric") or not d.get("ratio_metric") or d.get("part") not in ("fixed", "variable")
        ):
            problems.append(f"{where}: default cost_structure cần expense_metric, ratio_metric và part fixed|variable")
        elif d["type"] == "explicit" and not isinstance(d.get("value"), (int, float)):
            problems.append(f"{where}: default chính sách cần value là số")
        defaults[aid] = d or {}
        ab = a.get("auto_balance")
        if ab is not None:
            if ab.get("direction") not in ("increase", "decrease") or ab.get(
                "bound"
            ) not in ("min", "max"):
                problems.append(
                    f"{where}: auto_balance cần direction increase|decrease và bound min|max"
                )
            autos[aid] = ab
        defined[aid] = a.get("_defined_in", tid or "")
        specs.append(
            AssumptionSpec(
                aid,
                block,
                mid,
                a.get("label_vn", ""),
                a.get("description_vn", ""),
                unit,
                (lo, hi),
                a.get("default_policy", ""),
                per_period=bool(a.get("per_period", True)),
                editable=bool(a.get("editable", True)),
                override_reason_required=bool(a.get("override_reason_required", False)),
                optional=bool(a.get("optional", False)),
                shared=shared,
            )
        )

    spec_by_id = {s.assumption_id: s for s in specs}
    for aid, d in defaults.items():
        if d.get("type") == "copy" and d.get("from") not in spec_by_id:
            problems.append(
                f"assumption {aid}: default copy từ assumption không có {d.get('from')!r}"
            )
    for m in methods:
        for role, ids in m.roles.items():
            for aid in [ids] if isinstance(ids, str) else ids:
                s = spec_by_id.get(aid)
                if s is None or not (
                    s.method_id == m.method_id or (s.shared and s.block == m.block)
                ):
                    problems.append(
                        f"method {m.method_id}: vai trò {role} trỏ tới assumption {aid!r} "
                        "không thuộc method/block"
                    )
        for line, f in m.line_formulas.items():
            for aid in f[1]:
                if aid not in spec_by_id:
                    problems.append(
                        f"method {m.method_id}: công thức dòng {line} trỏ tới assumption không có {aid!r}"
                    )

    rwf = {}
    for aid, drivers in data.get("requires_when_filled", {}).items():
        if aid not in spec_by_id:
            problems.append(f"requires_when_filled: assumption không tồn tại {aid!r}")
        for d in drivers:
            if d not in provided:
                problems.append(
                    f"requires_when_filled {aid}: driver {d!r} không method nào cung cấp"
                )
        rwf[aid] = tuple(drivers)

    lines = []
    for ln in data.get("lines", []):
        if (
            not ln.get("line_id")
            or ln.get("statement") != "OPS"
            or ln.get("unit") not in KNOWN_UNITS
        ):
            problems.append(
                f"dòng vận hành không hợp lệ (cần line_id, statement OPS, unit hợp lệ): {ln}"
            )
        lines.append({k: v for k, v in ln.items() if not k.startswith("_")})

    for name, path in files.items():
        if not Path(path).exists():
            problems.append(f"file {name} không tồn tại: {path}")

    if problems:
        raise TemplateError(f"Template {tid!r} không hợp lệ: " + "; ".join(problems))
    return Template(
        tid,
        str(data["version"]),
        data.get("label_vn", tid),
        family,
        tuple(lineage),
        directory,
        dict(data.get("frequencies", {})),
        tuple(blocks),
        labels,
        tuple(methods),
        tuple(specs),
        defaults,
        autos,
        tuple(lines),
        rwf,
        dict(files),
        defined,
        dict(data.get("one_off", {})),
        dict(data.get("default_paths", {})),
        dict(data.get("quarterly_engine", {})),
    )


@lru_cache(maxsize=None)
def load_template(template_id: str, templates_dir: Path = TEMPLATES_DIR) -> Template:
    raw = resolve_raw(template_id, templates_dir)
    return parse_template(raw, raw["_directory"], load_library())


def available_templates(templates_dir: Path = TEMPLATES_DIR) -> list[str]:
    return sorted(p.parent.name for p in Path(templates_dir).glob("*/template.json"))
