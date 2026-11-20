"""Phát hiện và xử lý khoản bất thường (one-off) đã ghi nhận trong các quý đã công bố của năm hiện tại.

Thuật toán (đề xuất của owner, 2026-11-16, đã chỉnh sau phản biện):
1. Mỗi dòng trong phạm vi (template `one_off.lines`, mặc định chỉ dòng ngoài hoạt động lõi: DT tài chính, chi phí
   tài chính khác, liên doanh liên kết, lợi nhuận khác) lấy chuỗi quý riêng lẻ.
2. Trend tuyến tính (OLS) trên `window` quý TRƯỚC năm đang xét (không gồm các quý của năm đó, để chính khoản bất
   thường không làm phình độ lệch chuẩn), cần ít nhất `min_obs` quý hợp lệ.
3. Quý của năm đang xét bị cờ khi |thực tế − trend| > `threshold` × độ lệch chuẩn phần dư (ddof = 2) VÀ
   |k| ≥ `materiality_to_revenue` × doanh thu quý đó. Xét cả hai chiều (lãi và lỗ bất thường).
4. k = thực tế − trend là ĐỀ XUẤT. Máy không tự áp: analyst xác nhận (hoặc bỏ qua) từng khoản, kèm lý do.

Khoản đã xác nhận:
- Chạy theo năm: cộng k vào dòng tương ứng của năm dự phóng chứa quý đó, đi qua thuế, cổ đông không kiểm soát,
  LCTT (giản lược: coi là đã thu/chi bằng tiền) và tiền cuối kỳ; giả định năm vẫn áp như thường cho phần còn lại.
- Chạy theo quý: quý thực tế giữ nguyên (đã gồm khoản này); không cộng gì vào quý dự phóng.
- Default lịch sử: loại k khỏi dòng nguồn ở kỳ chứa khoản đó trước khi tính chỉ số (ví dụ suất sinh lời), ghi rõ
  trong basis của default.

Quy tắc theo dòng (template `one_off.lines.<dòng>.rule`, L1 v2.5.1):
- `trend_sigma` (mặc định): như trên.
- `isolated_spike` (DT tài chính): m = trung vị `base` quý trước quý xét. Quý ≥ `spike_ratio` × m và quý ngay sau đã
  quay về ≤ `revert_ratio` × m là khoản một lần (mức thay thế m); chiều giảm đối xứng. Quý công bố mới nhất chưa có
  quý sau: chặn trần `latest_cap` × m (sàn m / `latest_cap`). Quý cao mà quý sau vẫn cao là mức mới, giữ nguyên. Lý do:
  trend ± 3σ không tách được khoản một lần với mức mới, dòng lệch dương nên bị cắt một phía (bias âm).

Giới hạn: trend tuyến tính không tách được thay đổi chu kỳ/công suất (vì vậy không áp cho doanh thu, giá vốn,
chi phí lõi); không biết bản chất khoản (thoái vốn, tỷ giá, hoàn nhập) vì database không có thuyết minh; phần không
bằng tiền (lãi đánh giá lại) chưa tách khỏi LCTT.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quick_model.data.history import History
from quick_model.periods import FREQ_ANNUAL, FREQ_QUARTER, fiscal_year

STATUSES = ("confirmed", "dismissed")


@dataclass(frozen=True)
class OneOffPolicy:
    window: int = 12
    min_obs: int = 8
    threshold: float = 3.0
    materiality_to_revenue: float = 0.005
    materiality_base: str = "ISA3"  # mã IS làm mẫu số trọng yếu (phi tài chính: doanh thu; ngân hàng: TOI ISB38)
    history_years: int = 0  # quét thêm n năm lịch sử trước năm hiện tại (0 = chỉ năm hiện tại)
    # True: khoản phát hiện ở năm LỊCH SỬ mà analyst chưa quyết định được tự loại khỏi chuỗi làm default (công bố,
    # analyst bỏ qua được). Khoản ở quý đã công bố của năm dự phóng vẫn cần analyst xác nhận.
    auto_history: bool = False
    # True: khoản ở quý đã công bố của năm dự phóng cũng tự áp (k cộng lại vào số cả năm dự phóng); analyst bỏ qua được.
    auto_current: bool = False
    # Mức thay thế quý bất thường (k = thực tế − mức này): "trend" (giá trị trend tại quý đó) hoặc "mean" (trung bình
    # các quý trong cửa sổ ước lượng). Phát hiện luôn dùng trend ± ngưỡng × σ.
    replace_with: str = "trend"
    # True: quét tuần tự, quý bị cờ được thay trong chuỗi dùng để quét năm sau (ngân hàng). False: mọi năm quét trên chuỗi
    # gốc — tránh "khóa trend" khi dòng tăng cấu trúc (phi tài chính, owner 2026-11-18: HPG thu nhập tài chính).
    sequential: bool = True

    @classmethod
    def from_template(cls, tpl) -> "OneOffPolicy":
        cfg = getattr(tpl, "one_off", {}) or {}
        return cls(**{k: cfg[k] for k in ("window", "min_obs", "threshold", "materiality_to_revenue",
                                          "materiality_base", "history_years", "auto_history", "auto_current",
                                          "replace_with", "sequential") if k in cfg})


@dataclass(frozen=True)
class OneOffCandidate:
    line: str
    quarter: str
    actual: float
    expected: float  # giá trị theo trend
    sigma: float  # độ lệch chuẩn phần dư của trend
    z: float
    fit_periods: tuple[str, ...]
    slope: float
    intercept_at_first: float  # giá trị trend tại quý đầu của cửa sổ (đột biến cô lập: trung vị m)
    rule: str = "trend_sigma"  # trend_sigma | isolated_spike | latest_cap (quý mới nhất bị chặn trần)

    @property
    def amount(self) -> float:
        return self.actual - self.expected


@dataclass(frozen=True)
class OneOff:
    """Quyết định của analyst cho một khoản phát hiện (bằng chứng phát hiện đi kèm để kiểm toán)."""
    line: str
    quarter: str
    amount: float
    status: str  # confirmed | dismissed
    actual: float
    expected: float
    sigma: float
    z: float
    fit_periods: tuple[str, ...] = ()
    reason: str = ""
    decided_at: str | None = None
    method: str = "linear_trend_sigma"
    basis: dict = field(default_factory=dict, compare=False, hash=False)

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"status phải thuộc {STATUSES}")

    @classmethod
    def from_candidate(cls, c: OneOffCandidate, status: str, reason: str = "", decided_at: str | None = None,
                       policy: OneOffPolicy | None = None) -> "OneOff":
        basis = {"rule": c.rule} if policy is None else {
            "window": policy.window, "threshold": policy.threshold, "min_obs": policy.min_obs, "rule": c.rule,
            "materiality_to_revenue": policy.materiality_to_revenue}
        return cls(c.line, c.quarter, c.amount, status, c.actual, c.expected, c.sigma, c.z, c.fit_periods, reason,
                   decided_at, basis=basis)

    def to_dict(self) -> dict:
        return {"line": self.line, "quarter": self.quarter, "amount": self.amount, "status": self.status,
                "actual": self.actual, "expected": self.expected, "sigma": self.sigma, "z": self.z,
                "fit_periods": list(self.fit_periods), "reason": self.reason, "decided_at": self.decided_at,
                "method": self.method, "basis": dict(self.basis)}

    @classmethod
    def from_dict(cls, d: dict) -> "OneOff":
        return cls(d["line"], d["quarter"], float(d["amount"]), d["status"], float(d["actual"]), float(d["expected"]),
                   float(d["sigma"]), float(d["z"]), tuple(d.get("fit_periods", ())), d.get("reason", ""),
                   d.get("decided_at"), d.get("method", "linear_trend_sigma"), dict(d.get("basis", {})))


# ---------------------------------------------------------------- chuỗi quý và phát hiện


def line_specs(tpl) -> dict[str, dict]:
    return dict((getattr(tpl, "one_off", {}) or {}).get("lines", {}))


def _signed_terms(codes) -> list[tuple[int, str]]:
    return [(-1 if c.startswith("-") else 1, c.lstrip("+-")) for c in codes]


def quarter_series(h: History, codes) -> pd.Series:
    """Giá trị quý riêng lẻ của dòng = tổng có dấu các mã IS; quý thiếu mọi mã -> NaN."""
    flows = h.flows("IS")
    terms = _signed_terms(codes)
    parts = [sg * flows[c] for sg, c in terms if c in flows.columns]
    if not parts:
        return pd.Series(float("nan"), index=flows.index)
    df = pd.concat(parts, axis=1)
    return df.sum(axis=1, min_count=1)


def _detect_line(line: str, s: pd.Series, all_q: list[str], cur: list[str], policy: OneOffPolicy,
                 revenue: pd.Series) -> list[OneOffCandidate]:
    """Một dòng: trend OLS trên `window` quý trước quý đầu của `cur`, cờ quý lệch > ngưỡng × σ và trọng yếu."""
    pos = {q: i for i, q in enumerate(all_q)}
    before = s.loc[[q for q in all_q if q < cur[0]]].dropna().iloc[-policy.window:]
    if len(before) < policy.min_obs:
        return []
    x = np.array([pos[q] for q in before.index], dtype=float)
    y = before.to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (intercept + slope * x)
    sigma = float(resid.std(ddof=2)) if len(y) > 2 else float("nan")
    window_mean = float(y.mean())
    out = []
    for q in cur:
        v = s.get(q)
        if v is None or pd.isna(v):
            continue
        trend = float(intercept + slope * pos[q])
        dev = float(v) - trend
        z = dev / sigma if sigma and sigma > 0 else (float("inf") if dev else 0.0)
        expected = window_mean if policy.replace_with == "mean" else trend  # mức thay thế
        k = float(v) - expected
        rev = revenue.get(q)
        material = rev is not None and pd.notna(rev) and abs(k) >= policy.materiality_to_revenue * abs(float(rev))
        if abs(z) > policy.threshold and material:
            out.append(OneOffCandidate(line, q, float(v), expected, sigma, float(z), tuple(before.index),
                                       float(slope), float(intercept + slope * x[0])))
    return out


ISOLATED_DEFAULTS = {"base": 8, "spike_ratio": 5.0, "revert_ratio": 2.0, "latest_cap": 5.0}


def isolated_params(spec: dict) -> dict:
    return {**ISOLATED_DEFAULTS, **{k: spec[k] for k in ISOLATED_DEFAULTS if k in spec}}


def _detect_isolated(line: str, s: pd.Series, all_q: list[str], cur: list[str], policy: OneOffPolicy,
                     revenue: pd.Series, spec: dict) -> list[OneOffCandidate]:
    """Đột biến cô lập (v2.5.1): so với trung vị `base` quý trước; loại khi quý sau đã quay về; quý mới nhất chặn trần.
    z = thực tế / trung vị (số lần), sigma = 0 (quy tắc không dùng độ lệch chuẩn)."""
    p = isolated_params(spec)
    base, ks, kr, kc = int(p["base"]), float(p["spike_ratio"]), float(p["revert_ratio"]), float(p["latest_cap"])
    pos = {q: i for i, q in enumerate(all_q)}
    valid = s.dropna()
    last = valid.index[-1] if len(valid) else None
    out = []
    for q in cur:
        v = s.get(q)
        if v is None or pd.isna(v):
            continue
        before = s.iloc[max(0, pos[q] - base):pos[q]].dropna()
        if len(before) < base:
            continue
        m = float(before.median())
        if m <= 0:
            continue
        v = float(v)
        nxt = s.iloc[pos[q] + 1] if pos[q] + 1 < len(all_q) else float("nan")
        expected, rule = None, "isolated_spike"
        if q == last or pd.isna(nxt):  # chưa có quý sau để xác nhận: chặn trần / sàn
            if v > kc * m:
                expected, rule = kc * m, "latest_cap"
            elif v < m / kc:
                expected, rule = m / kc, "latest_cap"
        elif v >= ks * m and float(nxt) <= kr * m:
            expected = m
        elif v <= m / ks and float(nxt) >= m / kr:
            expected = m
        if expected is None:
            continue
        rev = revenue.get(q)
        material = (rev is not None and pd.notna(rev)
                    and abs(v - expected) >= policy.materiality_to_revenue * abs(float(rev)))
        if material:
            out.append(OneOffCandidate(line, q, v, expected, 0.0, v / m, tuple(before.index), 0.0, m, rule))
    return out


def _detect(line: str, spec: dict, s: pd.Series, all_q: list[str], cur: list[str], policy: OneOffPolicy,
            revenue: pd.Series) -> list[OneOffCandidate]:
    if spec.get("rule", "trend_sigma") == "isolated_spike":
        return _detect_isolated(line, s, all_q, cur, policy, revenue, spec)
    return _detect_line(line, s, all_q, cur, policy, revenue)


def _revenue(h: History, policy: OneOffPolicy) -> pd.Series:
    base = policy.materiality_base
    return h.flows("IS")[base] if base in h.flows("IS").columns else pd.Series(dtype=float)


def detect(h: History, tpl, year: int | None = None, policy: OneOffPolicy | None = None,
           quarters: list[str] | None = None, cleaned: dict[str, pd.Series] | None = None) -> list[OneOffCandidate]:
    """Các khoản bất thường trong các quý đã công bố của `year` (mặc định: năm của quý công bố mới nhất).

    cleaned: {dòng: chuỗi quý đã thay các quý bất thường trước đó bằng trend} (quét lịch sử tuần tự); mặc định chuỗi gốc."""
    policy = policy or OneOffPolicy.from_template(tpl)
    all_q = list(h.periods_by(FREQ_QUARTER))
    if not all_q:
        return []
    year = fiscal_year(all_q[-1]) if year is None else int(year)
    cur = quarters or [q for q in all_q if fiscal_year(q) == year]
    if not cur:
        return []
    revenue = _revenue(h, policy)
    out = []
    for line, spec in line_specs(tpl).items():
        s = (cleaned or {}).get(line)
        s = quarter_series(h, spec.get("codes", ())).reindex(all_q) if s is None else s
        out += _detect(line, spec, s, all_q, cur, policy, revenue)
    return out


def scan(h: History, tpl, current_year: int, current_quarters: list[str]) -> tuple[list, list]:
    """Quét tuần tự: các năm lịch sử (history_years năm trước current_year, từ cũ đến mới) rồi các quý đã công bố
    của năm hiện tại. Mỗi quý bị cờ được thay bằng trend trong chuỗi dùng để quét các năm sau, để một khoản bất
    thường không làm phình σ và che các khoản sau. Trả về (khoản ở năm lịch sử, khoản ở năm hiện tại).
    history_years = 0: chỉ quét năm hiện tại trên chuỗi gốc (như trước)."""
    policy = OneOffPolicy.from_template(tpl)
    all_q = list(h.periods_by(FREQ_QUARTER))
    if not all_q:
        return [], []
    revenue = _revenue(h, policy)
    specs = line_specs(tpl)
    cleaned = {line: quarter_series(h, spec.get("codes", ())).reindex(all_q).copy() for line, spec in specs.items()}
    hist: list[OneOffCandidate] = []
    for y in range(current_year - policy.history_years, current_year):
        qs = [q for q in all_q if fiscal_year(q) == y]
        if not qs:
            continue
        for line, s in cleaned.items():
            found = _detect(line, specs[line], s, all_q, qs, policy, revenue)
            if policy.sequential:
                for c in found:
                    s[c.quarter] = c.expected
            hist += found
    cur = []
    if current_quarters:
        for line, s in cleaned.items():
            cur += _detect(line, specs[line], s, all_q, current_quarters, policy, revenue)
    return hist, cur


def trend_frame(h: History, tpl, c: OneOffCandidate, extra: int = 0) -> pd.DataFrame:
    """Dữ liệu vẽ: thực tế từng quý (cửa sổ + năm đang xét), trend và dải ± ngưỡng × σ."""
    policy = OneOffPolicy.from_template(tpl)
    all_q = list(h.periods_by(FREQ_QUARTER))
    spec = line_specs(tpl)[c.line]
    s = quarter_series(h, spec.get("codes", ())).reindex(all_q)
    start = all_q.index(c.fit_periods[0])
    qs = [q for q in all_q[start:] if fiscal_year(q) <= fiscal_year(c.quarter)]
    rows = []
    if c.rule != "trend_sigma":  # đột biến cô lập: đường trung vị và ngưỡng theo bội số, kèm quý sau
        p = isolated_params(spec)
        k = p["latest_cap"] if c.rule == "latest_cap" else p["spike_ratio"]
        m = c.intercept_at_first
        nxt = all_q.index(c.quarter) + 1
        qs = qs + ([all_q[nxt]] if nxt < len(all_q) and all_q[nxt] not in qs else [])
        return pd.DataFrame([{"quý": q, "thực tế": s.get(q), "trend": m, "trên": k * m, "dưới": m / k,
                              "trong cửa sổ": q in c.fit_periods} for q in qs])
    for i, q in enumerate(qs):
        t = c.intercept_at_first + c.slope * (all_q.index(q) - start)
        rows.append({"quý": q, "thực tế": s.get(q), "trend": t, "trên": t + policy.threshold * c.sigma,
                     "dưới": t - policy.threshold * c.sigma, "trong cửa sổ": q in c.fit_periods})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- dùng trong engine và default


def confirmed(one_offs) -> list[OneOff]:
    return [o for o in (one_offs or ()) if o.status == "confirmed"]


def forecast_amounts(one_offs, periods: list[str], frequency: str) -> dict[str, dict[str, list[OneOff]]]:
    """{kỳ dự phóng: {dòng: [khoản]}}. Chỉ chạy theo năm: khoản ở quý của năm dự phóng được cộng vào năm đó."""
    if frequency != FREQ_ANNUAL:
        return {}
    out: dict[str, dict[str, list[OneOff]]] = {}
    for o in confirmed(one_offs):
        y = str(fiscal_year(o.quarter))
        if y in periods:
            out.setdefault(y, {}).setdefault(o.line, []).append(o)
    return out


def history_adjustments(one_offs, tpl, annual: bool) -> dict[tuple[str, str], float]:
    """{(mã IS, kỳ lịch sử): số cần trừ} để loại khoản bất thường đã xác nhận khỏi chỉ số làm default.

    Khoản của dòng có nhiều mã được trừ ở mã đầu tiên mang dấu + (ví dụ chi phí tài chính khác -> ISA7)."""
    specs = line_specs(tpl)
    out: dict[tuple[str, str], float] = {}
    for o in confirmed(one_offs):
        codes = specs.get(o.line, {}).get("codes", ())
        terms = [c for sg, c in _signed_terms(codes) if sg > 0]
        if not terms:
            continue
        period = str(fiscal_year(o.quarter)) if annual else o.quarter
        out[(terms[0], period)] = out.get((terms[0], period), 0.0) + o.amount
    return out


def affected_metrics(one_offs, tpl) -> dict[str, list[OneOff]]:
    specs = line_specs(tpl)
    out: dict[str, list[OneOff]] = {}
    for o in confirmed(one_offs):
        for m in specs.get(o.line, {}).get("metrics", ()):
            out.setdefault(m, []).append(o)
    return out


def rule_of(o) -> str:
    return getattr(o, "rule", None) or (getattr(o, "basis", {}) or {}).get("rule", "trend_sigma")


def describe(o, labels: dict | None = None) -> str:
    name = (labels or {}).get(o.line, o.line)
    rule = rule_of(o)
    if rule in ("isolated_spike", "latest_cap"):
        what = ("đột biến cô lập (quý sau đã quay về), thay bằng trung vị" if rule == "isolated_spike"
                else "quý mới nhất chưa có quý sau xác nhận, chặn ở")
        return (f"{name} {o.quarter}: thực tế {o.actual / 1e9:,.0f} tỷ = {o.z:,.1f} lần trung vị 8 quý trước; {what} "
                f"{o.expected / 1e9:,.0f} tỷ → khoản bất thường k = {o.amount / 1e9:+,.0f} tỷ")
    return (f"{name} {o.quarter}: thực tế {o.actual / 1e9:,.0f} tỷ, mức thay thế {o.expected / 1e9:,.0f} tỷ, "
            f"σ {o.sigma / 1e9:,.0f} tỷ, z = {o.z:+.1f} → khoản bất thường k = {o.amount / 1e9:+,.0f} tỷ")


def auto_history(candidates: list[OneOffCandidate], one_offs, current_year: int, tpl) -> list[OneOff]:
    """Khoản analyst chưa quyết định được tự áp: năm lịch sử (< current_year) khi bật one_off.auto_history (loại khỏi
    chuỗi làm default); năm hiện tại khi bật one_off.auto_current (thêm: k cộng lại vào số cả năm dự phóng).
    Ghi method = 'auto_history' để phân biệt với khoản analyst xác nhận."""
    policy = OneOffPolicy.from_template(tpl)
    if not (policy.auto_history or policy.auto_current):
        return []
    decided = {(o.line, o.quarter) for o in (one_offs or ())}
    basis = {"window": policy.window, "threshold": policy.threshold, "min_obs": policy.min_obs,
             "materiality_to_revenue": policy.materiality_to_revenue, "auto": True}
    return [OneOff(c.line, c.quarter, c.amount, "confirmed", c.actual, c.expected, c.sigma, c.z, c.fit_periods,
                   "Tự áp: loại khỏi chuỗi làm default; năm dự phóng thì cộng lại vào số cả năm", None,
                   "auto_history", {**basis, "rule": c.rule})
            for c in candidates if (c.line, c.quarter) not in decided
            and ((policy.auto_history and fiscal_year(c.quarter) < current_year)
                 or (policy.auto_current and fiscal_year(c.quarter) >= current_year))]


def is_auto(o) -> bool:
    return getattr(o, "method", "") == "auto_history"


def pending(candidates: list[OneOffCandidate], one_offs) -> list[OneOffCandidate]:
    """Khoản phát hiện chưa được analyst quyết định."""
    decided = {(o.line, o.quarter) for o in (one_offs or ())}
    return [c for c in candidates if (c.line, c.quarter) not in decided]
