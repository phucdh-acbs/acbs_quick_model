"""Bốn nhóm BCTC. Có engine: phi tài chính, ngân hàng (L1, theo năm). Chứng khoán, bảo hiểm: khai báo, giai đoạn sau."""

from __future__ import annotations

from quick_model.families.base import ForecastResult, LineDef, PeriodEngine, PlannedFamily, StatementFamily

_FAMILIES: dict[str, StatementFamily] = {}


def _register(f: StatementFamily) -> None:
    _FAMILIES[f.family_id] = f


def get_family(family_id: str) -> StatementFamily:
    if not _FAMILIES:
        _load()
    if family_id not in _FAMILIES:
        raise KeyError(f"Nhóm BCTC không có: {family_id!r}")
    return _FAMILIES[family_id]


def all_families() -> list[StatementFamily]:
    if not _FAMILIES:
        _load()
    return list(_FAMILIES.values())


def _load() -> None:
    from quick_model.families.bank import BankFamily
    from quick_model.families.nonfinancial import NonFinancialFamily
    _register(NonFinancialFamily())
    _register(BankFamily())
    _register(PlannedFamily("securities", "Chứng khoán", "BCTC công ty chứng khoán (FVTPL, margin, môi giới) – giai đoạn sau"))
    _register(PlannedFamily("insurance", "Bảo hiểm", "BCTC bảo hiểm (phí, bồi thường, dự phòng nghiệp vụ) – giai đoạn sau"))


__all__ = ["ForecastResult", "LineDef", "PeriodEngine", "PlannedFamily", "StatementFamily", "get_family", "all_families"]
