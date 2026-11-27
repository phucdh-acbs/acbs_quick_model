"""Công cụ định giá: máy đo và tính, AI chọn. Từ một lượt dự phóng đã lưu (`forecast_tool`):
  không có `choice`   bảng đo: hồ sơ dự phóng, phương pháp nào dữ liệu này chở được (và vì sao phương pháp khác không), các mốc bội số đo được kèm
                      giá trị của mỗi mốc, hôm nay ở đâu trong 5 năm, trần tái định giá. Không có giá nào, không có mặc định nào.
  có `choice`         phương pháp, tỷ trọng, bội số (hoặc beta, g) và lý do do AI chọn; công cụ kiểm rồi tính.
Luật trần: bội số trên max(hôm nay, P10 5 năm) phải có lý do dẫn mã chất xúc tác người gọi đưa vào, hoặc EPS 12 tháng tới tăng đủ ngưỡng. rf và
ERP do người gọi đưa vào (AI không đặt). Lượt không qua cổng thì không có bảng và không có giá trị. Luật định giá là luật tạm thời, chưa phải công
thức cuối cùng Phòng Phân tích chốt."""
from __future__ import annotations

import csv
import datetime as dt
import decimal
import pathlib
import re
import statistics
from decimal import Decimal

from . import forecast_tool, settings
from .common import CONTEXT, D, Refuse, canonical, dstr, need, pctile, sha_file, sha_hex, vi

MULTIPLE = {"pe": "eps_forward", "pb": "bvps_forward"}
INTRINSIC = ("pb_roe", "residual_income")
ONE, ZERO = D(1), D(0)


def window(day: dt.date) -> tuple[int, int]:
    """(năm, quý) của quý trọn cuối cùng kết thúc trước hoặc đúng ngày định giá + 365 ngày."""
    end = day + dt.timedelta(days=365)
    q = (end.month - 1) // 3
    return (end.year, q) if q > 0 else (end.year - 1, 4)


def history(path, day: str, flags: list) -> tuple[dict, dict]:
    """{pe|pb: {mốc, phân vị hôm nay, trần, n}} trong cửa sổ 5 năm kết thúc ngày định giá; CSV có cột date, pe, pb."""
    c = settings.VALUATION
    p = pathlib.Path(path)
    if not p.is_file():
        raise Refuse("INPUT_MISSING", [need("multiples_csv", "không có tệp lịch sử bội số (date, pe, pb)")])
    d1 = dt.date.fromisoformat(day)
    d0 = d1.replace(year=d1.year - c["window_years"], day=min(d1.day, 28)).isoformat()
    cols: dict[str, list] = {"pe": [], "pb": []}
    with open(p, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if d0 <= (r.get("date") or "") <= day:
                for k in cols:
                    if (r.get(k) or "").strip() and D(r[k]) > 0:
                        cols[k].append((r["date"], D(r[k])))
    out = {}
    for k, xs in cols.items():
        if len(xs) < c["min_days"]:
            continue
        xs.sort()
        vals, cur = [x for _, x in xs], xs[-1][1]
        a = {n: dstr(pctile(vals, D(q)), 4) for n, q in c["stats"]}
        out[k] = {"today": dstr(cur, 4), "today_percentile": dstr(D(sum(1 for v in vals if v <= cur)) / D(len(vals)), 2), "anchors": a,
                  "min": dstr(min(vals), 4), "max": dstr(max(vals), 4), "cap": dstr(max(D(a["p10"]), cur), 4), "n": len(xs), "first": xs[0][0], "last": xs[-1][0]}
        if (d1 - dt.date.fromisoformat(xs[-1][0])).days > c["stale_days"]:
            flags.append({"code": "MULTIPLES_STALE", "text": f"bội số {k} chỉ đến {xs[-1][0]}, ngày định giá {day}"})
    return out, {"name": p.name, "sha256": sha_file(p)}


class Case:
    """Mọi thứ đo được từ một lượt và một tệp bội số, một lần."""

    def __init__(self, doc: dict, inp: dict):
        self.c, self.doc, self.inp, self.day = settings.VALUATION, doc, inp, inp["valuation_date"]
        self.vd, self.fam, self.flags = dt.date.fromisoformat(self.day), doc["key"]["family"], []
        miss = [need(f, "bắt buộc") for f in ("shares_million", "shares_source") if inp.get(f) in (None, "")]
        if miss:
            raise Refuse("INPUT_MISSING", miss)
        self.n = D(str(inp["shares_million"]))
        if self.n <= 0:
            raise Refuse("INVALID_INPUT", [("shares_million", "phải lớn hơn 0")])
        self.mult, self.source = history(inp["multiples_csv"], self.day, self.flags)
        self.catalysts = [x for x in (inp.get("catalysts") or []) if x.get("code") and x.get("text")]
        self.market = {k: D(str(inp["market"][k])) for k in ("rf", "erp")} if inp.get("market") else None
        self.g = g = forecast_tool.Grid(doc, "year")
        self.fc = [x[:4] for x in g.forecast()]
        ded, self.dps = D(str(inp.get("eps_deduction", 0))), D(str(inp.get("dps", 0)))
        if not ded:
            self._flag("EPS_BEFORE_WELFARE_FUND", "EPS chưa trừ trích quỹ khen thưởng, phúc lợi (eps_deduction = 0)")
        if not self.dps:
            self._flag("DIVIDEND_ZERO", "mô hình đặt cổ tức bằng 0: BVPS các năm sau cao hơn thực tế nếu có chia")
        rebased = [r for r in doc["flags"] if r["code"] == "REBASED"]
        for r in doc["flags"]:                                     # the forecast's own flags travel with the value
            if r["code"] not in ("NOT_PUBLISHABLE", "LATEST_QUARTER_NOT_USED"):
                self._flag(r["code"], r["text"])
        self.off = ZERO if inp.get("keep_one_offs") or rebased or self.fam == "bank" else self._one_off()
        if self.off:
            self._flag("ONE_OFF_REMOVED", f"đã bỏ {vi(float(self.off))} tỷ khoản một lần (sau thuế) khỏi LNST mẹ {self.fc[0]} khi tính EPS")
        self.npat = {x[:4]: D(g.v("npat_parent", x)) for x in (*g.actual(), *g.forecast()) if g.v("npat_parent", x) is not None}
        self.eq = {x[:4]: D(g.v("equity_parent", x)) for x in (*g.actual(), *g.forecast()) if g.v("equity_parent", x) is not None}
        self.roe = {y: self.npat[y] * 2 / (self.eq[y] + self.eq[str(int(y) - 1)]) for y in sorted(self.npat)
                    if str(int(y) - 1) in self.eq and y in self.eq and self.eq[y] + self.eq[str(int(y) - 1)] > 0}
        self.eps, self.bvps, paid = {}, {}, ZERO
        for y in self.fc:
            paid += self.dps
            self.eps[y] = D(dstr((self.npat[y] - (self.off if y == self.fc[0] else ZERO)) * (ONE - ded) * 1000 / self.n, 2))
            self.bvps[y] = D(dstr(self.eq[y] * 1000 / self.n - paid, 2))
        q = doc["quarterly"]
        vals = next(r["values"] for r in q["rows"] if r["line_id"] == "npat_parent")
        last = [vals[k] for k, col in enumerate(q["columns"]) if col.endswith(" A")][-4:]
        self.eps_ttm = None if len(last) < 4 or any(x is None for x in last) else sum(D(x) for x in last) * 1000 / self.n

    def _flag(self, code, text):
        self.flags.append({"code": code, "text": text})

    def _one_off(self) -> Decimal:
        y = self.fc[0]
        v = lambda ln: D(self.g.v(ln, f"{y} F"))  # noqa: E731
        tax = min(ONE, max(ZERO, -v("income_tax") / v("pbt"))) if v("pbt") > 0 else ZERO
        mi = min(ONE, max(ZERO, v("npat_minority") / v("npat"))) if v("npat") > 0 else ZERO
        total = ZERO
        for o in self.doc["one_offs"]:
            if str(o["quarter"]).startswith(y):
                total += D(o["amount"]) * (ONE if o["line"] == "jv_associates" else ONE - tax)
        return total * (ONE - mi)

    def forward(self, per_share: dict) -> Decimal:
        """Giá trị 12 tháng tới = (1 − q/4) × năm n−1 + q/4 × năm n, với (n, q) của cửa sổ."""
        y, q = window(self.vd)
        out = ZERO
        for yy, w in ((str(y - 1), ONE - D(q) / 4), (str(y), D(q) / 4)):
            if w > 0:
                if yy not in per_share:
                    raise Refuse("HORIZON_TOO_SHORT", [("valuation_date", f"dự phóng không có năm {yy} cho giá trị 12 tháng tới")])
                out += w * per_share[yy]
        return out

    # ---- chiết khấu theo ngày thật / 365, dòng tiền đặt cuối năm, năm đầu chỉ tính phần sau ngày định giá
    def _per(self, y: int) -> Decimal:
        return max(ZERO, D((dt.date(y, 12, 31) - self.vd).days) / D(365))

    def _df(self, rate: Decimal, per: Decimal) -> Decimal:
        return (-per * (ONE + rate).ln()).exp()

    def ke(self, beta) -> Decimal:
        if self.market is None:
            raise Refuse("INPUT_MISSING", [need("market", "cần rf và erp để dựng chi phí vốn chủ")])
        return self.market["rf"] + D(str(beta)) * self.market["erp"]

    def pe(self, m: Decimal) -> tuple[Decimal, dict]:
        e = self.forward(self.eps)
        if e <= 0:
            raise Refuse("METHOD_NOT_APPLICABLE", [("pe", "EPS 12 tháng tới ≤ 0")])
        return m * e, {"eps_forward": e}

    def pb(self, m: Decimal) -> tuple[Decimal, dict]:
        b = self.forward(self.bvps)
        if b <= 0:
            raise Refuse("METHOD_NOT_APPLICABLE", [("pb", "BVPS 12 tháng tới ≤ 0")])
        return m * b, {"bvps_forward": b}

    def pb_roe(self, beta, g) -> tuple[Decimal, dict]:
        ke, g = self.ke(beta), D(str(g))
        ys = self.fc[:self.c["pb_roe_years"]]
        if any(y not in self.roe for y in ys) or len(ys) < self.c["pb_roe_years"]:
            raise Refuse("METHOD_NOT_APPLICABLE", [("pb_roe", "thiếu ROE dự phóng")])
        roe = sum(D(dstr(self.roe[y], 10)) for y in ys) / len(ys)
        if ke <= g:
            raise Refuse("METHOD_NOT_APPLICABLE", [("pb_roe", "ke ≤ g")])
        pbj = (roe - g) / (ke - g)
        b = self.forward(self.bvps)
        if pbj <= 0 or b <= 0:
            raise Refuse("METHOD_NOT_APPLICABLE", [("pb_roe", "P/B hợp lý hoặc BVPS ≤ 0")])
        return pbj * b, {"ke": ke, "bvps_forward": b, "roe": roe, "pb_justified": pbj}

    def residual_income(self, beta, g) -> tuple[Decimal, dict]:
        ke, g = self.ke(beta), D(str(g))
        if ke <= g:
            raise Refuse("METHOD_NOT_APPLICABLE", [("residual_income", "ke ≤ g")])
        live = [int(y) for y in self.fc if self._per(int(y)) > 0]
        if not live:
            raise Refuse("HORIZON_TOO_SHORT", [("valuation_date", "ngày định giá sau kỳ dự phóng cuối")])
        first, stub = live[0], min(ONE, self._per(live[0]))
        prev = self.eq[str(first - 1)] + self._div(str(first - 1))
        cur = self.eq[str(first)] + self._div(str(first))
        bv0 = prev + (ONE - stub) * (cur - prev)
        if bv0 <= 0:
            raise Refuse("METHOD_NOT_APPLICABLE", [("residual_income", "vốn chủ mẹ ≤ 0")])
        pv, tv_cf = ZERO, ZERO
        for y in live:
            eq0 = self.eq[str(y - 1)] + self._div(str(y - 1))
            ri = self.npat[str(y)] - ke * eq0
            pv += ri * min(ONE, self._per(y)) * self._df(ke, self._per(y))
            tv_cf = ri
        tv = tv_cf * (ONE + g) / (ke - g) * self._df(ke, self._per(live[-1]))
        return (bv0 + pv + tv) * 1000 / self.n, {"ke": ke}

    def _div(self, year: str) -> Decimal:
        """Vốn chủ thuộc mô hình chưa trừ cổ tức: khi người gọi nêu dps thì trừ cộng dồn."""
        if not self.dps or year not in self.fc:
            return ZERO
        return -self.dps * self.n / 1000 * (self.fc.index(year) + 1)

    def run(self, m: str, *, multiple=None, beta=None, g=None) -> dict:
        try:
            with decimal.localcontext(CONTEXT):
                v, inp = getattr(self, m)(D(dstr(multiple, 4))) if m in MULTIPLE else getattr(self, m)(beta, g)
            return {"per_share": v, "inputs": inp}
        except Refuse as e:
            if e.code in ("METHOD_NOT_APPLICABLE", "INPUT_MISSING", "HORIZON_TOO_SHORT"):
                return {"refused": e.missing[0][1]}
            raise


def _profile(k: Case) -> dict:
    """Dự phóng thành các con số để chọn (tỷ đồng; tỷ lệ là phân số)."""
    ys = sorted(k.npat)
    yoy = {y: k.npat[y] / k.npat[str(int(y) - 1)] - 1 for y in ys if str(int(y) - 1) in k.npat and k.npat[str(int(y) - 1)] > 0}
    past = [float(v) for y, v in yoy.items() if y not in k.fc]
    show = [y for y in ys if y not in k.fc][-3:] + k.fc[:3]
    num = lambda d, n: {y + ("F" if y in k.fc else "A"): dstr(d[y], n) for y in show if y in d}  # noqa: E731
    r = k.run("pe", multiple="1")
    eb = None if "refused" in r else r["inputs"]["eps_forward"]
    share = forecast_tool.thesis(k.doc)["associates_share_of_pbt"]
    return {"npat_parent": num(k.npat, 0), "npat_yoy": num(yoy, 4), "roe": num(k.roe, 4), "eps_ttm": None if k.eps_ttm is None else dstr(k.eps_ttm, 0), "eps_forward": None if eb is None else dstr(eb, 0),
            "eps_forward_growth": None if eb is None or not k.eps_ttm or k.eps_ttm <= 0 else dstr(eb / k.eps_ttm - 1, 4), "past_npat_yoy_std": None if len(past) < 3 else dstr(statistics.stdev(past), 4),
            "associates_share_of_pbt": share, "one_off_removed_bn": dstr(k.off, 1), "typical_npat_error": k.c["typical_npat_error"][k.fam], "warnings": sorted({i["code"] for i in k.doc["issues"]})[:8]}


def menu(k: Case) -> dict:
    rows = []
    for m, base_key in MULTIPLE.items():
        if m not in k.mult:
            rows.append({"id": m, "available": False, "why": f"không đủ {k.c['min_days']} phiên để đo bội số"})
            continue
        r = k.run(m, multiple=k.mult[m]["today"])
        if "refused" in r:
            rows.append({"id": m, "available": False, "why": r["refused"]})
            continue
        b, x = r["inputs"][base_key], k.mult[m]
        rows.append({"id": m, "available": True, "base": {base_key: dstr(b, 2)}, "today": x["today"], "today_percentile": x["today_percentile"], "cap": x["cap"], "window": f"{x['first']}..{x['last']} ({x['n']} phiên)",
                     "anchors": [[a, v, dstr(D(v) * b, 0)] for a, v in (*x["anchors"].items(), ("today", x["today"]))]})
    for m in INTRINSIC:
        got = [(b, g, k.run(m, beta=b, g=g)) for b in k.c["beta_trial"] for g in k.c["g_trial"]]
        if all("refused" in r for *_, r in got):
            rows.append({"id": m, "available": False, "why": got[0][2]["refused"]})
            continue
        rows.append({"id": m, "available": True, "needs": ["beta", "g"], "basis": "giá trị hôm nay" if m == "residual_income" else "12 tháng tới",
                     "trial": [[b, g, None if "refused" in r else dstr(r["per_share"], 0)] for b, g, r in got], "trial_cols": ["beta", "g", "giá trị/cp"]})
    rows += [{"id": m, "available": False, "why": w} for m, w in k.c["unavailable"][k.fam].items()]
    return {"status": "need_choice", "unit": "đồng/cp", "shares_million": dstr(k.n, 6), "forecast": _profile(k), "methods": rows,
            "params": {"market": None if k.market is None else {x: str(v) for x, v in k.market.items()}, "ke": "rf + beta × erp"},
            "rerating": {"rule": "bội số trên cap cần lý do dẫn mã chất xúc tác hoặc EPS tới tăng ít nhất eps_growth_min", "eps_growth_min": k.c["eps_growth_min"], "catalysts": k.catalysts}, "flags": k.flags, "rule_status": settings.VALUATION["rule_status"],
            "choice_form": {"decided_by": "vai", "methods": {"<id>": {"weight": "tổng 1", "multiple": "pe|pb", "beta, g": "pb_roe|residual_income", "reason": "vì sao hợp với dự phóng này, ít nhất 30 ký tự"}},
                            "rules": "phương pháp có trong bảng và available; tổng tỷ trọng 1; bội số không quá cap trừ khi rerating cho phép; giá trị của từng phương pháp phải > 0"}}


def decide(k: Case, choice: dict) -> dict:
    ms, bad, rows, over = choice["methods"], [], [], []
    total = sum(D(str(x["weight"])) for x in ms.values())
    if not 1 <= len(ms) <= 3:
        bad.append(("choice/methods", "1 đến 3 phương pháp"))
    if abs(total - 1) > D("0.000001"):
        bad.append(("choice/methods", f"tổng tỷ trọng {total} khác 1"))
    avail = {r["id"]: r for r in menu(k)["methods"]}
    fw = k.run("pe", multiple="1")
    growth = None if "refused" in fw or not k.eps_ttm or k.eps_ttm <= 0 else fw["inputs"]["eps_forward"] / k.eps_ttm - 1
    for m, x in ms.items():
        if len(str(x.get("reason", ""))) < 30:
            bad.append((f"choice/methods/{m}/reason", "cần lý do ít nhất 30 ký tự"))
        if m not in avail or not avail[m]["available"]:
            bad.append((f"choice/methods/{m}", f"phương pháp không dùng được: {(avail.get(m) or {}).get('why', 'không có trong bảng')}"))
            continue
        if m in MULTIPLE:
            if x.get("multiple") is None:
                bad.append((f"choice/methods/{m}/multiple", "cần bội số"))
                continue
            t, cap = D(str(x["multiple"])), D(k.mult[m]["cap"])
            if t > cap:
                cited = [z["code"] for z in k.catalysts if re.search(rf"(?<![0-9A-Za-z]){re.escape(z['code'])}(?![0-9])", str(x["reason"]))]
                if not cited and not (growth is not None and growth >= D(k.c["eps_growth_min"])):
                    bad.append((f"choice/methods/{m}/multiple", f"RERATING_UNSUPPORTED: {t} vượt trần {cap} = max(hôm nay {k.mult[m]['today']}, P10 {k.mult[m]['anchors']['p10']}): nêu mã chất xúc tác hoặc hạ về trần"))
                    continue
                over.append(f"{m}: {', '.join(cited) or 'EPS tới tăng đủ ngưỡng'}")
            if not D(k.mult[m]["min"]) <= t <= D(k.mult[m]["max"]):
                k._flag("OUTSIDE_5Y_RANGE", f"{m}: bội số {t} ngoài khoảng 5 năm {k.mult[m]['min']}–{k.mult[m]['max']}")
            r = k.run(m, multiple=t)
        else:
            lo, hi, gl, gh = (D(v) for v in (*k.c["beta_range"], *k.c["g_range"]))
            if x.get("beta") is None or x.get("g") is None:
                bad.append((f"choice/methods/{m}", "cần beta và g"))
                continue
            if not lo <= D(str(x["beta"])) <= hi or not gl <= D(str(x["g"])) <= gh:
                bad.append((f"choice/methods/{m}", f"beta trong {lo}–{hi}, g trong {gl}–{gh}"))
                continue
            r = k.run(m, beta=x["beta"], g=x["g"])
        if "refused" in r:
            bad.append((f"choice/methods/{m}", r["refused"]))
            continue
        if r["per_share"] <= 0:
            bad.append((f"choice/methods/{m}", f"giá trị {dstr(r['per_share'], 0)} đồng/cp ≤ 0: không dùng được làm giá; bỏ phương pháp này hoặc đổi tham số"))
            continue
        row = {"id": m, "weight": dstr(x["weight"], 4), "value": dstr(r["per_share"], 2), "reason": x["reason"]}
        if m in MULTIPLE:
            b = r["inputs"][MULTIPLE[m]]
            row.update(multiple=dstr(t, 4), base=dstr(b, 2), at_p10=dstr(D(k.mult[m]["anchors"]["p10"]) * b, 2), at_today=dstr(D(k.mult[m]["today"]) * b, 2))
        else:
            row.update(beta=str(x["beta"]), g=str(x["g"]), ke=dstr(r["inputs"]["ke"], 4))
        rows.append((row, r))
    if bad:
        raise Refuse("RERATING_UNSUPPORTED" if any("RERATING_UNSUPPORTED" in w for _, w in bad) else "CHOICE_INVALID", bad)
    fa = sum(D(str(r["per_share"])) * D(str(ms[row["id"]]["weight"])) / total for row, r in rows)
    span = {}
    if all(row["id"] in MULTIPLE for row, _ in rows):
        for name, key in (("at_p10_multiple", "at_p10"), ("at_today_multiple", "at_today")):
            span[name] = dstr(sum(D(row[key]) * D(row["weight"]) for row, _ in rows), 2)
        now = D(span["at_today_multiple"])
        if now and abs(fa / now - 1) > D(k.c["rerating_flag"]):
            k._flag("RERATING_IN_VALUE", f"giá ở bội số đã chọn lệch {vi(float((fa / now - 1) * 100), 1)}% so với giá ở bội số hôm nay: phần này là giả định tái định giá")
    if "residual_income" in ms and len(ms) > 1:
        k._flag("MIXED_HORIZON", "trộn lợi nhuận thặng dư (giá trị hôm nay) với phương pháp 12 tháng tới")
    sp = forecast_tool.thesis(k.doc)["associates_share_of_pbt"]
    if sp is not None and sp > k.c["associates_flag"]:
        k._flag("ASSOCIATES_SHARE_HIGH", f"lãi công ty liên kết chiếm {vi(sp * 100, 1)}% LNTT quý: một bội số cho cả lợi nhuận là giản lược, nên tách phần")
    err = D(k.c["typical_npat_error"][k.fam])
    return {"status": "ok", "value_per_share": dstr(fa, 2), "unit": "đồng/cp", **span, "methods": [r for r, _ in rows], "over_cap": over, "decided_by": choice.get("decided_by"),
            "choice_sha256": sha_hex(canonical(choice)), "shares_million": dstr(k.n, 6), "shares_source": k.inp["shares_source"], "multiples_source": k.source,
            "eps": {y: dstr(k.eps[y], 0) for y in k.fc[:3]}, "bvps": {y: dstr(k.bvps[y], 0) for y in k.fc[:3]},
            "range": {"low": dstr(fa * (1 - err), 0), "high": dstr(fa * (1 + err), 0), "basis": k.c["typical_npat_error"]["basis"]},
            "flags": k.flags, "rule_status": settings.VALUATION["rule_status"]}


def value(doc: dict, inp: dict) -> dict:
    head = {"ticker": doc["key"]["ticker"], "run_id": doc["run_id"], "valuation_date": inp["valuation_date"]}
    if not doc["key"].get("org") or "usable" not in doc:
        return {**head, "status": "refused", "reasons": ["lượt chạy tạo bằng bản công cụ cũ: gọi op run lại (com_type_code, icb_code)"]}
    if not doc["usable"] or doc["annual"] is None:
        return {**head, "status": "refused", "reasons": [r["text"] for r in doc["reasons"]] or ["mô hình không có số"]}
    k = Case(doc, inp)
    return {**head, **(decide(k, inp["choice"]) if inp.get("choice") else menu(k))}


def run(inp: dict) -> dict:
    """Điểm vào: một đối tượng JSON vào, một đối tượng JSON ra."""
    try:
        miss = [need(n, "bắt buộc") for n in ("run_id", "store", "valuation_date", "multiples_csv", "shares_million", "shares_source") if inp.get(n) in (None, "")]
        if miss:
            raise Refuse("INPUT_MISSING", miss)
        dt.date.fromisoformat(inp["valuation_date"])
        res = value(forecast_tool.read(inp["store"], inp["run_id"]), inp)
        return {"status": "ok" if res["status"] != "refused" else "refused", "result": res} if res["status"] != "refused" else {"status": "refused", "code": "FORECAST_NOT_OK", "result": res}
    except Refuse as e:
        return {"status": "refused", "code": e.code, "missing": [{"field": a, "why": w} for a, w in e.missing]}
    except ValueError as e:
        return {"status": "refused", "code": "INVALID_INPUT", "missing": [{"field": "valuation_date", "why": str(e)[:120]}]}
