"""Thư viện kiểu method (kind) dùng chung mọi ngành, đọc từ config/methods/library.json."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from quick_model.methods import primitives

from quick_model.paths import CONFIG_DIR  # noqa: E402

DEFAULT_LIBRARY = CONFIG_DIR / "methods" / "library.json"


class LibraryError(ValueError):
    pass


@dataclass(frozen=True)
class MethodKind:
    kind: str
    label_vn: str
    formula_vn: str
    primitives: tuple[str, ...]
    implemented: bool
    seen_in: tuple[str, ...] = ()


@dataclass(frozen=True)
class MethodLibrary:
    kinds: dict[str, MethodKind]
    primitives: dict[str, str]

    def get(self, kind: str) -> MethodKind:
        if kind not in self.kinds:
            raise LibraryError(f"Kind không có trong thư viện: {kind!r}")
        return self.kinds[kind]


def parse_library(data: dict) -> MethodLibrary:
    problems = []
    prims = dict(data.get("primitives", {}))
    for name in prims:
        if not callable(getattr(primitives, name, None)):
            problems.append(f"primitive {name!r} khai báo nhưng không có hàm trong quick_model/methods/primitives.py")
    kinds: dict[str, MethodKind] = {}
    for k in data.get("kinds", []):
        kid = k.get("kind")
        if not kid:
            problems.append(f"kind thiếu id: {k}")
            continue
        if kid in kinds:
            problems.append(f"kind trùng: {kid}")
        for p in k.get("primitives", []):
            if p not in prims:
                problems.append(f"kind {kid}: primitive không khai báo {p!r}")
        kinds[kid] = MethodKind(kid, k.get("label_vn", ""), k.get("formula_vn", ""), tuple(k.get("primitives", [])),
                                bool(k.get("implemented", False)), tuple(k.get("seen_in", [])))
    if problems:
        raise LibraryError("; ".join(problems))
    return MethodLibrary(kinds, prims)


@lru_cache(maxsize=None)
def load_library(path: Path = DEFAULT_LIBRARY) -> MethodLibrary:
    return parse_library(json.loads(Path(path).read_text(encoding="utf-8")))
