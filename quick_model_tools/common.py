"""Lỗi, mã băm, số ở mép."""
from __future__ import annotations

import decimal
import hashlib
import json
import math
from decimal import ROUND_HALF_EVEN, Decimal

CONTEXT = decimal.Context(prec=34, rounding=ROUND_HALF_EVEN)
D = Decimal
MONEY = ("VND", "VND_per_year")
BILLION = Decimal(10) ** 9


class Refuse(Exception):
    """Người gọi phải sửa đầu vào. `missing` = [(trường, lý do)], nêu hết một lần."""

    def __init__(self, code: str, missing: list[tuple[str, str]]):
        super().__init__(f"{code}: {'; '.join(w for _, w in missing)}")
        self.code, self.missing = code, missing


class Fail(Exception):
    """Lượt gọi không thực hiện được (mô hình lỗi)."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def need(field: str, why: str) -> tuple[str, str]:
    return field, f"THIẾU ĐẦU VÀO: {field} — {why}"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def dstr(v, places: int) -> str:
    x = D(str(v)).quantize(D(1).scaleb(-places), rounding=ROUND_HALF_EVEN, context=CONTEXT)
    return format(abs(x) if x.is_zero() else x, "f")


def edge(v, unit: str | None = None):
    """Một số thực ở mép, thành chuỗi thập phân: tiền (VND) -> tỷ đồng 6 số lẻ (1.000 đồng, dưới mức nhiễu giữa các loại CPU); số khác 8 số lẻ."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int) and unit not in MONEY:
        return v
    x = float(v)
    if math.isnan(x) or math.isinf(x):
        return None
    if unit in MONEY:
        d = (Decimal(repr(x)) / BILLION).quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
    else:
        d = Decimal(repr(x)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN)
    return format(abs(d) if d.is_zero() else d, "f")


MONEY_KEYS = ("balance_max_residual", "funding_gap_max", "auto_funding_max", "amount")


def clean(x, unit: str | None = None):
    if isinstance(x, dict):
        return {str(k): clean(v, "VND" if k in MONEY_KEYS else unit) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v, unit) for v in x]
    return edge(x, unit)


def vi(x: float, places: int = 0) -> str:
    return f"{x:,.{places}f}".replace(",", "_").replace(".", ",").replace("_", ".")


def bn(v) -> float | None:
    """Một số lẻ dưới 1.000 tỷ, số nguyên trên mức đó."""
    if v is None:
        return None
    x = float(v)
    r = round(x, 1) if abs(x) < 1000 else round(x)
    return int(r) if r == int(r) else r


def pctile(vals: list[Decimal], q: Decimal) -> Decimal:
    """Phân vị nội suy tuyến tính (mặc định của numpy), tính bằng Decimal."""
    s = sorted(vals)
    pos = q * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)
