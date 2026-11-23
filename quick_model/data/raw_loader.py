"""Nạp CSV thô xuất từ BigQuery console (sinh bởi sql/<ticker>/*.sql).

Chỉ đọc file local; không gọi BigQuery. Áp lại quy tắc chọn bản ghi
(STATUS = 1, UPDATE_DATE mới nhất mỗi kỳ) để an toàn nếu file chưa được lọc.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from quick_model.periods import EXPECTED_MONTHS, LENGTH_TO_BASIS


class RawDataError(ValueError):
    pass


# statement -> (hậu tố file, bảng BQ, cột ID, đổi tên cột metadata -> tên chuẩn)
_STATEMENT_META = {
    "ORGAN_CODE": "organ_code",
    "TICKER": "ticker",
    "YEAR_REPORT": "fiscal_year",
    "LENGTH_REPORT": "length_report",
    "LENGTH_SERIES": "length_series",
    "START_DATE": "start_date",
    "END_DATE": "end_date",
    "PUBLIC_DATE": "public_date",
    "IS_AUDIT": "is_audit",
    "REPORT_FORM_TYPE_CODE": "report_form_type",
    "SOURCE_NAME": "source_name",
    "STATUS": "status",
    "CREATE_DATE": "create_date",
    "UPDATE_DATE": "update_date",
}
_NOTE_META = {
    "OrganCode": "organ_code",
    "Ticker": "ticker",
    "YearReport": "fiscal_year",
    "LengthReport": "length_report",
    "LengthSeries": "length_series",
    "StartDate": "start_date",
    "EndDate": "end_date",
    "PublicDate": "public_date",
    "IsAudit": "is_audit",
    "ReportFormTypeCode": "report_form_type",
    "SourceName": "source_name",
    "Status": "status",
    "CreateDate": "create_date",
    "UpdateDate": "update_date",
}

STATEMENTS = {
    "IS": ("is", "INCOME_STATEMENT", "INCOME_STATEMENT_ID", _STATEMENT_META),
    "BS": ("bs", "BALANCE_SHEET", "BALANCE_SHEET_ID", _STATEMENT_META),
    "CF": ("cf", "CASH_FLOW", "CASH_FLOW_ID", _STATEMENT_META),
    "NOTE": ("note", "STX_FSC_NOTECOMPANY", "NoteCompanyId", _NOTE_META),
    "NOTE_BANK": ("notes", "STX_FSC_NOTEBANK", "NoteBankId", _NOTE_META),  # thuyết minh nhóm ngân hàng
}
TABLE_TO_STATEMENT = {v[1]: k for k, v in STATEMENTS.items()}

META_COLUMNS = ["record_id", *_STATEMENT_META.values()]
_DATE_COLUMNS = ["start_date", "end_date", "public_date", "create_date", "update_date"]


@dataclass
class RawStatement:
    statement: str
    bq_table: str
    source_file: Path
    frame: pd.DataFrame  # metadata chuẩn hóa + các cột mã khoản mục
    codes: list[str]
    dropped: pd.DataFrame = field(default_factory=pd.DataFrame)  # bản ghi bị loại bởi quy tắc chọn


def read_manifest(manifest_path: Path) -> dict[str, list[str]]:
    """bq_table -> danh sách mã khoản mục kỳ vọng, theo thứ tự trong SQL."""
    m = pd.read_csv(manifest_path, dtype=str, encoding="utf-8-sig")
    return {t: g["code"].tolist() for t, g in m.groupby("bq_table", sort=False)}


def load_raw_statement(path: Path, statement: str, required_codes: list[str],
                       ticker: str | None = None, drop_incomplete: bool = False) -> RawStatement:
    """drop_incomplete=True (file nhiều mã của backtest): bỏ dòng thiếu khóa kỳ (vd dòng cuối của file xuất bị cắt)
    và ghi vào `dropped` với lý do `incomplete_row`, thay vì dừng."""
    if statement not in STATEMENTS:
        raise RawDataError(f"Loại báo cáo không hợp lệ: {statement!r}")
    path = Path(path)
    if not path.exists():
        raise RawDataError(f"Không tìm thấy file: {path}")
    return raw_statement_from_frame(pd.read_csv(path), statement, required_codes, path, ticker, drop_incomplete)


def raw_statement_from_frame(df: pd.DataFrame, statement: str, required_codes: list[str], source: Path | str,
                             ticker: str | None = None, drop_incomplete: bool = False) -> RawStatement:
    """Như load_raw_statement nhưng nhận DataFrame có đúng tên cột bảng GBQ (vd kết quả truy vấn BigQuery của
    phía gọi). `source` chỉ dùng để ghi nguồn và báo lỗi."""
    if statement not in STATEMENTS:
        raise RawDataError(f"Loại báo cáo không hợp lệ: {statement!r}")
    _, bq_table, id_col, meta_map = STATEMENTS[statement]
    path = Path(str(source))
    df = df.copy()
    missing = [c for c in [id_col, *meta_map, *required_codes] if c not in df.columns]
    if missing:
        raise RawDataError(f"{path.name}: thiếu cột {missing}")

    df = df.rename(columns={id_col: "record_id", **meta_map})
    df = df[[*META_COLUMNS, *required_codes]].copy()
    incomplete = pd.DataFrame()
    if drop_incomplete:
        # dữ liệu cũ (2012–2015) để trống LENGTH_SERIES: số tháng suy ra từ LENGTH_REPORT (quý 3, năm 12, 6T, 9T)
        df["length_series"] = df["length_series"].fillna(pd.to_numeric(df["length_report"], errors="coerce")
                                                         .map(EXPECTED_MONTHS))
        bad = df[["fiscal_year", "length_report", "length_series", "status", "end_date"]].isna().any(axis=1)
        incomplete, df = df[bad].assign(drop_reason="incomplete_row"), df[~bad].copy()

    for c in _DATE_COLUMNS:
        df[c] = pd.to_datetime(df[c], utc=True, errors="raise", format="mixed")  # có/không phần giây lẻ
    df["is_audit"] = df["is_audit"].map(_to_bool)
    for c in ("fiscal_year", "length_report", "length_series", "status"):
        if df[c].isna().any():
            raise RawDataError(f"{path.name}: cột {c} có giá trị rỗng")
        df[c] = df[c].astype(int)

    if drop_incomplete:  # file nhiều mã: bỏ dòng kỳ không hợp lệ, ghi lý do
        bad = ~df["length_report"].isin(list(LENGTH_TO_BASIS)) | (
            df["length_report"].map(EXPECTED_MONTHS) != df["length_series"])
        if bad.any():
            extra = df[bad].assign(drop_reason="invalid_length")
            incomplete = extra if incomplete.empty else pd.concat([incomplete, extra], ignore_index=True)
        df = df[~bad].copy()
    bad_len = sorted(set(df["length_report"]) - set(LENGTH_TO_BASIS))
    if bad_len:
        raise RawDataError(f"{path.name}: LENGTH_REPORT không hỗ trợ {bad_len}")
    expected = df["length_report"].map(EXPECTED_MONTHS)
    mismatch = df[expected != df["length_series"]]
    if not mismatch.empty:
        rows = mismatch[["fiscal_year", "length_report", "length_series"]].values.tolist()
        raise RawDataError(f"{path.name}: LENGTH_SERIES không khớp LENGTH_REPORT {rows}")

    if ticker is not None:
        other = sorted(set(df["ticker"]) - {ticker})
        if other:
            raise RawDataError(f"{path.name}: có mã khác {ticker}: {other}")

    numeric = {}
    for c in required_codes:
        try:
            numeric[c] = pd.to_numeric(df[c], errors="raise").astype("float64")
        except (ValueError, TypeError):
            raise RawDataError(f"{path.name}: cột {c} có giá trị không phải số") from None
    if numeric:  # ghép một lần (không gán từng cột: hàng trăm mã làm DataFrame phân mảnh, PerformanceWarning)
        meta = df.drop(columns=list(numeric))
        df = pd.concat([meta, pd.DataFrame(numeric, index=df.index)], axis=1)[list(df.columns)]

    kept, dropped = _apply_selection_rule(df)
    if len(incomplete):
        dropped = pd.concat([dropped, incomplete], ignore_index=True)
    return RawStatement(statement, bq_table, path, kept, list(required_codes), dropped)


def load_raw_bundle(raw_dir: Path, ticker: str, manifest_path: Path,
                    statements: tuple[str, ...] | None = None) -> dict[str, RawStatement]:
    """statements = None: nạp đúng các bảng có trong manifest (ngân hàng: IS, BS, thuyết minh NH; không có CF)."""
    manifest = read_manifest(manifest_path)
    if statements is None:
        unknown = [t for t in manifest if t not in TABLE_TO_STATEMENT]
        if unknown:
            raise RawDataError(f"Manifest có bảng chưa hỗ trợ: {unknown}")
        statements = tuple(TABLE_TO_STATEMENT[t] for t in manifest)
    out = {}
    for st in statements:
        suffix, bq_table, *_ = STATEMENTS[st]
        if bq_table not in manifest:
            raise RawDataError(f"Manifest không có bảng {bq_table}")
        path = Path(raw_dir) / f"{ticker.lower()}_{suffix}.csv"
        out[st] = load_raw_statement(path, st, manifest[bq_table], ticker=ticker)
    return out


def _apply_selection_rule(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """STATUS = 1; mỗi (ticker, năm, LENGTH_REPORT) giữ UPDATE_DATE mới nhất, hòa thì record_id lớn hơn."""
    active = df["status"] == 1
    ranked = df[active].sort_values(
        ["update_date", "record_id"], ascending=[False, False], na_position="last")
    dup = ranked.duplicated(["ticker", "fiscal_year", "length_report"], keep="first")
    kept = ranked[~dup].sort_values(["fiscal_year", "length_report"]).reset_index(drop=True)
    dropped = pd.concat([
        df[~active].assign(drop_reason="status_not_1"),
        ranked[dup].assign(drop_reason="superseded_by_newer_update"),
    ], ignore_index=True)
    return kept, dropped


def _to_bool(v) -> bool | None:
    if pd.isna(v):
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1"):
            return True
        if s in ("false", "0"):
            return False
        raise RawDataError(f"IS_AUDIT không hợp lệ: {v!r}")
    return bool(int(v))
