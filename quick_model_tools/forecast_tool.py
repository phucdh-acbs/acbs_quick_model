"""Công cụ dự phóng: gói mỏng quanh `quick_model.service`, không sửa gì của mô hình.

op run          (ticker, com_type_code, icb_code, dataset_dir) -> nhóm báo cáo tài chính -> dự phóng -> cổng kiểm -> câu trả lời ngắn; lượt chạy lưu theo nội dung đầu vào
op view         hình dạng khác của một lượt đã lưu, không tính lại
op explain      một ô: công thức và các phần đóng góp lớn nhất
op assumptions  giả định được phép nhập, theo nhóm
Cùng dữ liệu, cùng phiên bản mô hình và công cụ, cùng giả định: cùng `run_id` và trả lại bản đã lưu. `model_ok` là false thì không dùng số."""
from __future__ import annotations

import csv
import functools
import json
import math
import os
import pathlib
import shutil
import statistics
from decimal import Decimal

from quick_model import service
from quick_model.paths import CONFIG_DIR

from . import settings
from .common import BILLION, Fail, Refuse, bn, canonical, clean, edge, need, sha_file, sha_hex, vi

SCHEMA = "quick_model_tools.run@1"
SUFFIX = {"INCOME_STATEMENT": "is", "BALANCE_SHEET": "bs", "CASH_FLOW": "cf", "STX_FSC_NOTECOMPANY": "note", "STX_FSC_NOTEBANK": "notes"}
MONTHS = {1: 3, 2: 3, 3: 3, 4: 3, 5: 12, 6: 6, 9: 9}
VIEWS = ("brief", "thesis", "table", "assumptions", "issues", "one_offs", "ratios")


# ------------------------------------------------------------------------------------------------------------ định danh
@functools.lru_cache(maxsize=1)
def engine() -> dict:
    """Phiên bản mô hình và mã băm của mã nguồn, cấu hình mô hình và công cụ: đổi một dòng là `run_id` đổi."""
    here = pathlib.Path(__file__).resolve().parent
    model = CONFIG_DIR.parent
    files = [p for p in sorted(model.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]
    tools = [p for p in sorted(here.glob("*.py"))]
    return {"model_version": service.MODEL_VERSION, "model_sha256": sha_hex(canonical({p.relative_to(model).as_posix(): sha_file(p) for p in files})),
            "tool_sha256": sha_hex(canonical({p.name: sha_file(p) for p in tools}))}


def dataset_files(folder: pathlib.Path, ticker: str) -> dict[str, pathlib.Path]:
    return {t: p for t, s in SUFFIX.items() if (p := folder / f"{ticker.lower()}_{s}.csv").is_file()}


def dataset_id(folder: pathlib.Path, ticker: str) -> dict:
    ps = list(dataset_files(folder, ticker).values())
    if not ps:
        raise Refuse("INPUT_MISSING", [need("dataset_dir", f"không có tệp {ticker.lower()}_is.csv … trong thư mục dữ liệu")])
    got = sorted(({"path": p.name, "sha256": sha_file(p)} for p in ps), key=lambda f: f["path"])
    return {"id": "data:" + sha_hex(canonical(got)), "files": got}


def audit(folder: pathlib.Path, ticker: str) -> dict:
    """Quý mới nhất có ở cả kết quả kinh doanh và cân đối, dù mô hình có dùng hay không (để báo quý bị bỏ lặng lẽ)."""
    latest = {}
    for table, p in dataset_files(folder, ticker).items():
        qs = set()
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                g = {k.replace("_", "").upper(): v for k, v in r.items() if k}
                if (g.get("TICKER") or "").upper() != ticker:
                    continue
                try:
                    y, ln = int(float(g.get("YEARREPORT"))), int(float(g.get("LENGTHREPORT")))
                except (TypeError, ValueError):
                    continue
                if ln in (1, 2, 3, 4):
                    qs.add(f"{y}Q{ln}")
        latest[table] = qs
    both = latest.get("INCOME_STATEMENT", set()) & latest.get("BALANCE_SHEET", set())
    return {"latest_quarter": max(both) if both else None}


def route(ticker: str, com_type_code, icb_code, family: str | None = None) -> str:
    """Nhóm của mã theo dòng ORGANIZATION. Chứng khoán, bảo hiểm, bất động sản và loại lạ bị từ chối: mô hình vẫn ra số cho các mã này nhưng là số sai
    (một mã chứng khoán chạy như phi tài chính không có cảnh báo nào)."""
    r = settings.ROUTING
    ctype, icb = str(com_type_code).strip().upper(), str(icb_code).strip()
    icb = icb.zfill(4) if icb.isdigit() else icb
    if ctype in r["unsupported_com_type"]:
        raise Refuse("FAMILY_UNSUPPORTED", [("com_type_code", f"{ticker}: {r['unsupported_com_type'][ctype]} — {r['waiting']}")])
    for prefix, name in r["unsupported_icb_prefix"].items():
        if icb.startswith(prefix):
            raise Refuse("FAMILY_UNSUPPORTED", [("icb_code", f"{ticker}: {name} — {r['waiting']}")])
    fam = r["family_by_com_type"].get(ctype)
    if fam is None or fam not in service.FAMILIES:
        raise Refuse("FAMILY_UNSUPPORTED", [("com_type_code", f"{ticker}: COM_TYPE_CODE {ctype!r} chưa có trong cấu hình")])
    if not (icb.isdigit() and len(icb) == 4):
        raise Refuse("INVALID_INPUT", [("icb_code", f"{icb_code!r} không phải mã ICB 4 chữ số")])
    if family and family != fam:
        raise Refuse("INVALID_INPUT", [("family", f"{family!r} khác nhóm {fam!r} suy từ COM_TYPE_CODE {ctype}")])
    return fam


# ------------------------------------------------------------------------------------------------------------ cổng kiểm
def loss_in_path(annual, quarterly) -> str | None:
    """Nơi LNST cổ đông công ty mẹ không dương: quý báo cáo mới nhất, hoặc một trong ba năm dự phóng đầu (nhân bội số sẽ vô nghĩa)."""
    a, q = annual.set_index("line_id").loc["npat_parent"], quarterly.set_index("line_id").loc["npat_parent"]
    last = [c for c in quarterly.columns if c.endswith(" A")][-1]
    bad = [last[:-2]] if q[last] == q[last] and q[last] <= 0 else []
    bad += [c[:-2] for c in [c for c in annual.columns if c.endswith(" F")][:3] if a[c] == a[c] and a[c] <= 0]
    return ", ".join(bad) or None


def scope_break(quarterly) -> tuple[str, str] | None:
    """(quý đầu của mặt bằng mới, cái gì đổi): tỷ trọng lợi ích cổ đông không kiểm soát, hoặc lãi công ty liên kết trên LNTT, nhảy sang một mức
    khác và ở lại đó. Chỉ xét phi tài chính (ngân hàng không có các dòng này)."""
    c = settings.GATE["scope"]
    df = quarterly.set_index("line_id")
    cols = [x for x in df.columns if x.endswith(" A")]
    if not {"npat", "npat_minority", "pbt", "jv_associates"} <= set(df.index):
        return None

    def share(a, b):
        return [None if df.loc[b, x] != df.loc[b, x] or df.loc[b, x] <= 0 or df.loc[a, x] != df.loc[a, x] else float(df.loc[a, x] / df.loc[b, x]) for x in cols]
    mi, jv = share("npat_minority", "npat"), share("jv_associates", "pbt")
    for k in range(max(c["min_before"], len(cols) - c["max_after"]), len(cols)):
        for name, v, test in (("lợi ích cổ đông không kiểm soát", mi, lambda m, x: m >= c["minority_min_share"] and x <= m * c["minority_ratio"]),
                              ("lãi công ty liên kết", jv, lambda m, x: x >= c["jv_min_share"] and x >= max(m, 0.01) * c["jv_ratio"])):
            before, after = v[:k], v[k:]
            if None in after or sum(x is not None for x in before) < c["min_before"]:
                continue
            m = statistics.median(x for x in before if x is not None)
            if all(test(m, x) for x in after):
                return cols[k][:-2], f"{name} {m * 100:.0f}% → {statistics.median(after) * 100:.0f}%"
    return None


def gate(out, aud: dict, overrides: dict, rebased: dict | None = None) -> list[dict]:
    g, st, reasons = settings.GATE, out.status, []

    def add(code, **kw):
        reasons.append({"code": code, "text": g["texts"][code].format(**kw), **({"quarter": kw["q"]} if "q" in kw else {})})
    if out.annual is None:
        add("NO_RESULT")
    elif (st.get("funding_gap_max") or 0) > 0 and st.get("first_gap_period"):
        add("FUNDING_GAP", gap=f"{st['funding_gap_max'] / 1e9:,.0f}".replace(",", "."), period=st["first_gap_period"])
    elif not st.get("publishable"):
        add("UPSTREAM_NOT_PUBLISHABLE")
    raw, used = aud["latest_quarter"], out.meta.get("last_reported_quarter")
    if raw and used and raw > used:
        add("LATEST_QUARTER_NOT_USED", raw=raw, used=used)
    if out.quarterly is not None and (sb := scope_break(out.quarterly)):
        add("REBASED" if rebased else "SCOPE_SUSPECT", q=sb[0], what=sb[1], y=out.meta["forecast_years"][0])
    if out.annual is not None and (why := loss_in_path(out.annual, out.quarterly)):
        add("LOSS_IN_PATH", what=why)
    iss = out.issues
    hit = sorted({r["target"] for _, r in iss.iterrows() if r["code"] in g["warn_codes_block"] and r["target"] in overrides and r["target"] not in (rebased or {})}) if len(iss) else []
    if hit:
        add("OVERRIDE_OUTSIDE_HISTORY", ids=", ".join(hit))
    return reasons


# ------------------------------------------------------------------------------------------------------------ lượt chạy
def check_overrides(overrides: dict | None, family: str, years: int | None) -> dict:
    """Giả định nhập tay theo đúng dạng mô hình nhận; mọi lỗi được nêu một lần (bản thân mô hình dừng ở lỗi đầu)."""
    cat = {a["assumption_id"]: a for a in service.assumption_catalog(family)}
    out, bad = {}, []
    for aid, v in sorted((overrides or {}).items()):
        if aid not in cat:
            bad.append((f"overrides/{aid}", "không có giả định này"))
            continue
        vals = list(v.values()) if isinstance(v, dict) else list(v) if isinstance(v, list) else [v]
        if not vals or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vals):
            bad.append((f"overrides/{aid}", "phải là số, danh sách số hoặc {năm: số}"))
            continue
        lo, hi = cat[aid]["valid_min"], cat[aid]["valid_max"]
        if any((lo is not None and x < lo) or (hi is not None and x > hi) for x in vals):
            bad.append((f"overrides/{aid}", f"ngoài miền hợp lệ [{lo}, {hi}]"))
            continue
        out[aid] = v
    if bad:
        raise Refuse("OVERRIDE_INVALID", bad)
    return out


def _compute(ticker, family, folder, years, overrides):
    try:
        return service.forecast(ticker, family, folder=folder, years=years, overrides=overrides or None)
    except service.ServiceError as e:
        raise Refuse("DATA_INVALID", [("dataset_dir", str(e))]) from None
    except Exception as e:                                    # mô hình không có lớp lỗi riêng cho các trường hợp còn lại
        raise Fail("MODEL_FAILED", f"{type(e).__name__}: {str(e)[:200]}") from None


def _grid(df) -> dict:
    head = ("line_id", "label", "statement", "unit")
    cols = [c for c in df.columns if c not in head]
    return {"columns": cols, "rows": [{**{h: r[h] for h in head}, "values": [edge(r[c], r["unit"]) for c in cols]} for _, r in df.iterrows()]}


def _keep(root: pathlib.Path, folder: pathlib.Path, ticker: str, ds: dict) -> pathlib.Path:
    """Bản sao dữ liệu trong kho, đặt tên theo mã nội dung: lần giải thích sau (tiến trình khác, dịch vụ) vẫn tìm thấy."""
    dst = root / "data" / ds["id"].split(":")[1]
    if not dst.is_dir():
        tmp = dst.with_name(f"{dst.name}.{os.getpid()}.tmp")
        tmp.mkdir(parents=True)
        for f in ds["files"]:
            shutil.copyfile(folder / f["path"], tmp / f["path"])
        try:
            os.replace(tmp, dst)
        except OSError:                                        # lượt khác vừa ghi cùng nội dung
            shutil.rmtree(tmp, ignore_errors=True)
    if dataset_id(dst, ticker)["id"] != ds["id"]:
        raise Refuse("RUN_TAMPERED", [("store", "dữ liệu đã lưu không còn khớp mã của nó")])
    return dst


def rebase_overrides(doc: dict, first: str) -> dict:
    """Giả định đọc từ các quý từ `first` (mặt bằng hợp nhất mới), tính bằng mã: biên, tỷ lệ chi phí, thuế, phần cổ đông không kiểm soát, lãi
    liên kết và thu nhập tài chính quy ra năm. Áp từ năm dự phóng thứ hai; năm đang chạy giữ đúng giá trị của lượt gốc. Tăng trưởng doanh
    thu vẫn của mô hình."""
    q, y = doc["quarterly"], doc["annual"]
    qs = [i for i, c in enumerate(q["columns"]) if c.endswith(" A") and c[:-2] >= first]
    fc = [c for c in y["columns"] if c.endswith(" F")]
    now = {r["line_id"]: sum(Decimal(r["values"][i]) for i in qs) for r in q["rows"] if all(r["values"][i] is not None for i in qs)}
    run = {r["line_id"]: Decimal(r["values"][y["columns"].index(fc[0])]) for r in y["rows"] if r["values"][y["columns"].index(fc[0])] is not None}

    def shape(v: dict, yearly) -> dict:
        rev = v["net_revenue"]
        return {"gross_margin": v["gross_profit"] / rev, "selling_expense_ratio": -v["selling_expense"] / rev, "selling_fixed_cost": Decimal(0),
                "admin_expense_ratio": -v["admin_expense"] / rev, "admin_fixed_cost": Decimal(0), "other_financial_expense_ratio": -v["other_financial_expense"] / rev,
                "minority_share": max(Decimal(0), v["npat_minority"] / v["npat"]), "effective_tax_rate": -v["income_tax"] / v["pbt"],
                "jv_associates_run_rate": v["jv_associates"] * yearly * BILLION, "financial_income_run_rate": v["financial_income"] * yearly * BILLION}
    a, b = shape(run, Decimal(1)), shape(now, Decimal(4) / len(qs))
    return {k: {c[:4]: round(float(a[k] if i == 0 else b[k]), 10) for i, c in enumerate(fc)} for k in a}


def execute(store, ticker: str, org: dict, folder, *, family: str | None = None, years: int | None = None, overrides: dict | None = None, rebase: bool = False) -> dict:
    """Bản ghi lượt chạy (tính mới, hoặc đọc lại khi đã có cùng đầu vào; `_cached` cho biết)."""
    family = route(ticker, org["com_type_code"], org["icb_code"], family)
    root, folder = pathlib.Path(store).resolve(), pathlib.Path(folder).resolve()
    if not folder.is_dir():
        raise Refuse("INPUT_MISSING", [need("dataset_dir", "thư mục dữ liệu không tồn tại")])
    ds = dataset_id(folder, ticker)
    ov = check_overrides(overrides, family, years)
    key = {"dataset": ds["id"], "engine": engine(), "ticker": ticker, "family": family, "org": {k: str(org[k]) for k in ("com_type_code", "icb_code")}, "years": years or 5, "overrides": ov, **({"rebase": True} if rebase else {})}
    run_id = "qmt:" + sha_hex(canonical(key))
    path = root / run_id[4:] / "run.json"
    if path.is_file():
        return {**read(root, run_id), "_cached": True}
    kept = _keep(root, folder, ticker, ds)
    rb = None
    if rebase:
        base = execute(store, ticker, org, kept, family=family, years=years, overrides=overrides)
        sc = next((r for r in base["reasons"] if r["code"] == "SCOPE_SUSPECT"), None)
        if sc is None:
            raise Refuse("REBASE_NOT_NEEDED", [("rebase", "lượt chạy không có dấu hiệu đổi phạm vi hợp nhất: chạy không kèm rebase")])
        rb = {"first_quarter": sc["quarter"], "base_run": base["run_id"], "overrides": rebase_overrides(base, sc["quarter"])}
        ov = {**rb["overrides"], **ov}
    out = _compute(ticker, family, kept, years, ov)
    reasons = gate(out, audit(kept, ticker), ov, rb["overrides"] if rb else None)
    doc = {"schema": SCHEMA, "run_id": run_id, "key": key, "dataset": ds, "meta": clean(out.meta), "rebase": rb, "model_ok": not reasons, "reasons": reasons,
           "annual": _grid(out.annual) if out.annual is not None else None, "quarterly": _grid(out.quarterly) if out.quarterly is not None else None,
           "ratios": {k: {"columns": list(v.columns), "rows": [{"ratio": i, "values": [edge(x) for x in r]} for i, r in zip(v.index, v.to_numpy().tolist())]}
                      for k, v in out.ratios.items()},
           "assumptions": [{**{k: r[k] for k in ("assumption_id", "label", "unit", "source", "note")}, **{k: edge(v, r["unit"]) for k, v in r.items() if k[:1].isdigit()}}
                           for r in out.assumptions.to_dict("records")] if out.assumptions is not None else [],
           "one_offs": clean(out.one_offs.to_dict("records")) if out.one_offs is not None else [], "issues": clean(out.issues.to_dict("records"))}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"run.json.{os.getpid()}.tmp")
    tmp.write_bytes(canonical(doc))
    os.replace(tmp, path)
    return {**json.loads(canonical(doc)), "_cached": False}


def read(store, run_id: str) -> dict:
    p = pathlib.Path(store).resolve() / run_id[4:] / "run.json"
    if not run_id.startswith("qmt:") or len(run_id) != 68 or not p.is_file():
        raise Refuse("RUN_NOT_FOUND", [("run_id", "không có lượt này trong kho: gọi op run trước")])
    doc = json.loads(p.read_text(encoding="utf-8"))
    if doc.get("run_id") != run_id or "qmt:" + sha_hex(canonical(doc["key"])) != run_id:
        raise Refuse("RUN_TAMPERED", [("run_id", "bản ghi không còn khớp mã của nó")])
    return doc


def explain(store, run_id: str, line: str, period: str) -> dict:
    doc = read(store, run_id)
    if doc["annual"] is None:
        raise Refuse("NO_NUMBERS", [("run_id", "mô hình dừng vì lỗi: lượt này không có số")])
    if line not in {r["line_id"] for r in doc["annual"]["rows"]}:
        raise Refuse("LINE_UNKNOWN", [("line", f"{line!r} không phải dòng của lượt này (view=table liệt kê)")])
    if period not in {c.split(" ")[0] for g in ("annual", "quarterly") for c in doc[g]["columns"] if "T" not in c.split(" ")[0]}:
        raise Refuse("PERIOD_UNKNOWN", [("period", f"{period!r} không phải kỳ của lượt này")])
    k = doc["key"]
    folder = pathlib.Path(store).resolve() / "data" / k["dataset"].split(":")[1]
    if not folder.is_dir() or dataset_id(folder, k["ticker"])["id"] != k["dataset"]:
        raise Refuse("DATASET_CHANGED", [("run_id", "dữ liệu của lượt này đã mất hoặc đổi: gọi op run lại")])
    e = _compute(k["ticker"], k["family"], folder, k["years"], {**(doc["rebase"]["overrides"] if doc["rebase"] else {}), **k["overrides"]}).explain(line, period)
    money = e.get("unit") == "VND"
    return {**{n: clean(v, "VND" if money and n in ("value", "prior_value", "change") else None) for n, v in e.items() if n != "bridge"},
            "bridge": [{"label": b["label"], "kind": b["kind"], "amount": edge(b["amount"], "VND" if money else None)} for b in e["bridge"]]}


# ------------------------------------------------------------------------------------------------------------ các dạng câu trả lời
class Grid:
    """Bảng năm hoặc quý của một lượt, đọc theo mã dòng."""

    def __init__(self, doc: dict, freq: str):
        if doc["annual"] is None:
            raise Refuse("NO_NUMBERS", [("run_id", "mô hình dừng vì lỗi: lượt này không có số")])
        g = doc["annual" if freq == "year" else "quarterly"]
        self.cols, self.rows = g["columns"], {x["line_id"]: x for x in g["rows"]}

    def actual(self) -> list[str]:
        return [c for c in self.cols if c.endswith(" A") and "T" not in c]

    def forecast(self) -> list[str]:
        return [c for c in self.cols if c.endswith(" F")]

    def v(self, line: str, col: str):
        return self.rows[line]["values"][self.cols.index(col)] if line in self.rows and col in self.cols else None


def brief(doc: dict) -> dict:
    k = doc["key"]
    out = {"ticker": k["ticker"], "run_id": doc["run_id"], "family": k["family"], "model": doc["meta"]["model_version"], "data_to": doc["meta"]["last_reported_quarter"],
           "model_ok": doc["model_ok"], "reasons": [r["text"] for r in doc["reasons"]], "disclaimer": doc["meta"]["disclaimer"]}
    if doc["annual"] is None:
        out["errors"] = [i["message"][:160] for i in doc["issues"] if i["severity"] == "error"][:3]
        return out
    g, q = Grid(doc, "year"), Grid(doc, "quarter")
    act, fc = g.actual()[-1], g.forecast()[:settings.BRIEF_YEARS]
    use = [act, *fc]
    out["unit"], out["cols"] = "tỷ đồng", [x.replace(" ", "") for x in use]
    out["rows"] = {ln: [bn(g.v(ln, x)) for x in use] for ln in settings.KEY_LINES[k["family"]] if ln in g.rows}
    h = settings.HEADLINE
    if q.forecast():
        out["next_quarter"] = {"period": q.forecast()[0].replace(" ", ""), h: bn(q.v(h, q.forecast()[0]))}
    f, prev = g.v(h, fc[0]), g.v(h, act)
    if f is not None:
        gr = float(f) / float(prev) - 1 if prev is not None and float(prev) > 0 else None
        out["text"] = f"{g.rows[h]['label']} {fc[0][:4]} dự phóng {vi(float(f))} tỷ đồng" + (f" ({'+' if gr >= 0 else '−'}{vi(abs(gr) * 100, 1)}% so với {act[:4]})" if gr is not None else "")
    return out


def thesis(doc: dict) -> dict:
    """Số làm bằng chứng cho luận điểm: quý gần nhất, so cùng kỳ, dự phóng hai năm, tỷ trọng lãi liên kết trên LNTT. Không viết lời."""
    g, q = Grid(doc, "year"), Grid(doc, "quarter")
    h = settings.HEADLINE
    act_q = q.actual()[-1]
    same = next((c for c in q.actual() if c == f"{int(act_q[:4]) - 1}{act_q[4:]}"), None)
    yoy = float(q.v(h, act_q)) / float(q.v(h, same)) - 1 if same and q.v(h, same) and float(q.v(h, same)) > 0 else None
    jv, pbt = q.v("jv_associates", act_q), q.v("pbt", act_q)
    share = float(jv) / float(pbt) if jv is not None and pbt is not None and float(pbt) > 0 else None
    return {"ticker": doc["key"]["ticker"], "run_id": doc["run_id"], "model_ok": doc["model_ok"], "reasons": [r["text"] for r in doc["reasons"]],
            "latest_quarter": {"period": act_q[:-2], h: bn(q.v(h, act_q)), "yoy": None if yoy is None else round(yoy, 4)},
            "forecast": {c[:4]: {h: bn(g.v(h, c)), **{ln: bn(g.v(ln, c)) for ln in settings.KEY_LINES[doc["key"]["family"]][:2]}} for c in g.forecast()[:2]},
            "associates_share_of_pbt": None if share is None else round(share, 4), "one_offs": len(doc["one_offs"])}


def table(doc: dict, freq: str = "year") -> dict:
    g = Grid(doc, freq)
    return {"run_id": doc["run_id"], "unit": "tỷ đồng", "cols": [c.replace(" ", "") for c in g.cols], "rows": [{"id": i, "label": r["label"], "v": r["values"]} for i, r in g.rows.items()]}


def view(doc: dict, name: str, freq: str = "year") -> dict:
    if name == "brief":
        return brief(doc)
    if name == "thesis":
        return thesis(doc)
    if name == "table":
        return table(doc, freq)
    if name == "issues":
        return {"run_id": doc["run_id"], "issues": doc["issues"]}
    if name in ("assumptions", "one_offs", "ratios"):
        return {"run_id": doc["run_id"], name: doc[name]}
    raise Refuse("INVALID_INPUT", [("view", f"{name!r}: chọn một trong {', '.join(VIEWS)}")])


def run(inp: dict) -> dict:
    """Điểm vào: một đối tượng JSON vào, một đối tượng JSON ra. Lỗi đầu vào -> {"status": "refused", "code", "missing"}."""
    try:
        op = inp.get("op", "run")
        if op == "assumptions":
            return {"status": "ok", "result": {"family": inp["family"], "cols": ["id", "label", "unit", "min", "max"], "rows": [[a["assumption_id"], a["label"], a["unit"], a["valid_min"], a["valid_max"]] for a in service.assumption_catalog(inp["family"])]}}
        miss = [need(n, f"op {op} cần trường này") for n in {"run": ("ticker", "com_type_code", "icb_code", "dataset_dir", "store"), "view": ("run_id", "store"), "explain": ("run_id", "store", "line", "period")}.get(op, ()) if inp.get(n) in (None, "")]
        if miss:
            raise Refuse("INPUT_MISSING", miss)
        if op == "run":
            d = execute(inp["store"], inp["ticker"].upper(), inp, inp["dataset_dir"], family=inp.get("family"), years=inp.get("years"), overrides=inp.get("overrides"), rebase=bool(inp.get("rebase")))
            return {"status": "ok", "cached": d["_cached"], "result": view(d, inp.get("view", "brief"), inp.get("freq", "year"))}
        if op == "view":
            return {"status": "ok", "result": view(read(inp["store"], inp["run_id"]), inp.get("view", "brief"), inp.get("freq", "year"))}
        if op == "explain":
            return {"status": "ok", "result": explain(inp["store"], inp["run_id"], inp["line"], inp["period"])}
        raise Refuse("INVALID_INPUT", [("op", f"{op!r}: run | view | explain | assumptions")])
    except Refuse as e:
        return {"status": "refused", "code": e.code, "missing": [{"field": a, "why": w} for a, w in e.missing]}
    except Fail as e:
        return {"status": "error", "code": e.code, "detail": e.detail}
    except KeyError as e:
        return {"status": "refused", "code": "INPUT_MISSING", "missing": [{"field": str(e.args[0]), "why": "thiếu trường"}]}
