"""Chuẩn hóa dữ liệu lịch sử thành bảng dài (canonical) và các chuỗi theo quý.

Chính sách đã được chủ dự án duyệt (2026-10-28):
- IS/CF quý: dùng đúng số quý riêng lẻ đã công bố (LENGTH_REPORT 1..4), không tự điều chỉnh
  về số cả năm đã kiểm toán; chênh lệch được báo trong các kiểm tra.
- BS ngày 31/12: lấy dòng cả năm (LENGTH_REPORT = 5, đã kiểm toán) thay cho dòng Q4.
- Dòng lũy kế 6M/9M không vào chuỗi quý, chỉ dùng để đối chiếu.
- Giữ nguyên dấu như database: chi phí âm, tài khoản trừ âm, LCTT theo chiều tiền.
- Ô trống giữ là NaN (trống khác 0).
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

from quick_model.data.raw_loader import RawStatement, load_raw_bundle
from quick_model.periods import FREQ_ANNUAL, Basis, LENGTH_TO_BASIS, period_label

from quick_model.paths import CONFIG_DIR  # noqa: E402

DEFAULT_ITEMS = CONFIG_DIR / "families" / "nonfinancial" / "items.csv"  # nhóm phi tài chính (raw chung)

DEFAULT_KIND = {"IS": "flow", "CF": "flow", "BS": "stock", "NOTE": "note", "NOTE_BANK": "note"}


@dataclass(frozen=True)
class HistoryPolicy:
    unit: str = "VND"
    scope: str = "consolidated"
    year_end_balance_source: str = "FY"  # "FY" (đã duyệt) hoặc "Q4"
    abs_tolerance: float = 1e6  # VND, ngưỡng làm tròn cho kiểm tra

    def __post_init__(self):
        if self.year_end_balance_source not in ("FY", "Q4"):
            raise ValueError("year_end_balance_source phải là 'FY' hoặc 'Q4'")
        if self.abs_tolerance < 0:
            raise ValueError("abs_tolerance phải >= 0")


def load_items(path: Path = DEFAULT_ITEMS) -> pd.DataFrame:
    items = pd.read_csv(path, dtype=str, encoding="utf-8")
    dup = items[items.duplicated("source_code") | items.duplicated("item_id")]
    if not dup.empty:
        raise ValueError(f"items.csv có mã hoặc item_id trùng: {dup[['item_id', 'source_code']].values.tolist()}")
    bad = set(items["kind"]) - {"flow", "stock", "per_share"}
    if bad:
        raise ValueError(f"items.csv có kind không hợp lệ: {bad}")
    return items.set_index("source_code")


@dataclass
class History:
    ticker: str
    policy: HistoryPolicy
    long: pd.DataFrame
    items: pd.DataFrame
    raw: dict[str, RawStatement]
    provenance: dict = field(default_factory=dict)

    # ---- chuỗi theo quý ----
    def quarter_periods(self) -> pd.DataFrame:
        q = self.long[(self.long["basis"] == Basis.QUARTER.value) & (self.long["statement"] == "IS")]
        cols = ["period_label", "fiscal_year", "length_report", "period_start", "period_end", "period_days"]
        return (q[cols].drop_duplicates("period_label")
                .sort_values(["fiscal_year", "length_report"]).reset_index(drop=True))

    def flows(self, statement: str, basis: Basis = Basis.QUARTER) -> pd.DataFrame:
        """Bảng rộng: dòng = kỳ (theo thứ tự thời gian), cột = mã khoản mục."""
        if statement not in ("IS", "CF"):
            raise ValueError("flows() chỉ dùng cho IS hoặc CF")
        sub = self.long[(self.long["statement"] == statement) & (self.long["basis"] == basis.value)]
        if sub.empty:  # nhóm không có báo cáo này (ngân hàng không có LCTT): bảng rỗng, không lỗi
            return pd.DataFrame(index=self.flows("IS", basis).index if statement != "IS" else None)
        return self._wide(sub)

    @property
    def statements(self) -> set[str]:
        return set(self.long["statement"].unique())

    def balances(self) -> pd.DataFrame:
        """Số dư BS cuối mỗi quý; 31/12 lấy theo policy.year_end_balance_source."""
        sub = self.long[(self.long["statement"] == "BS") & self.long["in_balance_series"]]
        return self._wide(sub, label_col="balance_label")

    # ---- chuỗi theo tần suất dự phóng (nhãn "YYYY" cho năm, "YYYYQn" cho quý) ----
    def annual_periods(self) -> list[str]:
        """Năm có báo cáo KQKD cả năm (LENGTH_REPORT 5)."""
        return [s[2:] for s in self.flows("IS", Basis.FULL_YEAR).index]

    def periods_by(self, freq: str) -> list[str]:
        return self.annual_periods() if freq == FREQ_ANNUAL else list(self.quarter_periods()["period_label"])

    def flows_by(self, statement: str, freq: str) -> pd.DataFrame:
        """Năm: số cả năm đã công bố (FY), không cộng quý; quý: quý riêng lẻ."""
        if freq == FREQ_ANNUAL:
            return self.flows(statement, Basis.FULL_YEAR).rename(index=lambda s: s[2:])
        return self.flows(statement)

    def balances_by(self, freq: str) -> pd.DataFrame:
        """Năm: số dư 31/12 (nguồn theo policy.year_end_balance_source); quý: số dư cuối quý."""
        bal = self.balances()
        if freq == FREQ_ANNUAL:
            return bal[bal.index.str.endswith("Q4")].rename(index=lambda s: s[:4])
        return bal

    def item(self, item_id: str, **kw) -> pd.Series:
        row = self.items[self.items["item_id"] == item_id]
        if row.empty:
            raise KeyError(f"Không có item_id {item_id!r}")
        code, st = row.index[0], row.iloc[0]["statement"]
        frame = self.balances() if st == "BS" else self.flows(st, **kw)
        return frame[code].rename(item_id)

    def _wide(self, sub: pd.DataFrame, label_col: str = "period_label") -> pd.DataFrame:
        order = (sub[[label_col, "period_end"]].drop_duplicates(label_col)
                 .sort_values("period_end")[label_col].tolist())
        wide = sub.pivot(index=label_col, columns="source_code", values="value")
        codes = [c for c in dict.fromkeys(sub["source_code"])]
        return wide.reindex(index=order, columns=codes)


def build_history(raw: dict[str, RawStatement], ticker: str,
                  policy: HistoryPolicy = HistoryPolicy(),
                  items: pd.DataFrame | None = None,
                  manifest: pd.DataFrame | None = None) -> History:
    items = load_items() if items is None else items
    frames = [_to_long(r, ticker, policy, items, manifest) for r in raw.values()]
    long = pd.concat(frames, ignore_index=True)
    long = _mark_balance_series(long, policy)
    prov = {
        "ticker": ticker,
        "policy": asdict(policy),
        "files": {st: {"path": str(r.source_file), "sha256": _sha256(r.source_file),
                       "rows_kept": len(r.frame), "rows_dropped": len(r.dropped)}
                  for st, r in raw.items()},
        "point_in_time": False,
        "point_in_time_note": "Mỗi kỳ chỉ có một phiên bản (bản cập nhật cuối); không tái tạo được số liệu tại thời điểm công bố.",
    }
    return History(ticker, policy, long, items, raw, prov)


def load_history(raw_dir: Path, ticker: str, manifest_path: Path,
                 policy: HistoryPolicy = HistoryPolicy(),
                 items_path: Path = DEFAULT_ITEMS) -> History:
    raw = load_raw_bundle(raw_dir, ticker, manifest_path)
    manifest = pd.read_csv(manifest_path, dtype=str, encoding="utf-8-sig")
    return build_history(raw, ticker, policy, load_items(items_path), manifest)


def _to_long(r: RawStatement, ticker: str, policy: HistoryPolicy,
             items: pd.DataFrame, manifest: pd.DataFrame | None) -> pd.DataFrame:
    meta = ["record_id", "fiscal_year", "length_report", "start_date", "end_date",
            "public_date", "update_date", "is_audit"]
    long = r.frame[meta + r.codes].melt(id_vars=meta, var_name="source_code", value_name="value")
    long.insert(0, "entity", ticker)
    long.insert(1, "statement", r.statement)
    long["basis"] = long["length_report"].map(lambda x: LENGTH_TO_BASIS[x].value)
    long["period_label"] = [period_label(y, l) for y, l in zip(long["fiscal_year"], long["length_report"])]
    long["period_start"] = long.pop("start_date")
    long["period_end"] = long.pop("end_date")
    long["period_days"] = (long["period_end"] - long["period_start"]).dt.days + 1

    cat = items.reindex(long["source_code"])
    long["item_id"] = cat["item_id"].to_numpy()
    long["kind"] = cat["kind"].fillna(DEFAULT_KIND[r.statement]).to_numpy()
    long["sign"] = cat["sign"].fillna("as_reported").to_numpy()
    long["unit"] = cat["unit"].fillna(policy.unit).to_numpy()
    long["scope"] = policy.scope
    long["source_table"] = r.bq_table
    long["source_file"] = r.source_file.name
    if manifest is not None:
        rows = manifest[manifest["bq_table"] == r.bq_table].set_index("code")["mapping_row"]
        long["mapping_row"] = long["source_code"].map(rows)
    return long


def _mark_balance_series(long: pd.DataFrame, policy: HistoryPolicy) -> pd.DataFrame:
    """Đánh dấu dòng BS nào là số dư cuối quý chính thức."""
    long["in_balance_series"] = False
    long["balance_label"] = pd.NA
    bs = long["statement"] == "BS"
    lr = long["length_report"]
    year_end_len = 5 if policy.year_end_balance_source == "FY" else 4
    use = bs & (lr.isin([1, 2, 3]) | (lr == year_end_len))
    long.loc[use, "in_balance_series"] = True
    q = lr.where(lr <= 4, 4)
    long.loc[use, "balance_label"] = (long.loc[use, "fiscal_year"].astype(str) + "Q"
                                      + q[use].astype(str))
    return long


def _sha256(path: Path) -> str | None:
    p = Path(path)
    if not p.is_file():  # dữ liệu truyền vào dạng DataFrame (thư viện/API): không có file để băm
        return None
    return _sha256_cached(str(p), p.stat().st_mtime_ns)  # backtest dựng lịch sử nhiều lần từ cùng một file lớn


@lru_cache(maxsize=64)
def _sha256_cached(path: str, _mtime_ns: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
