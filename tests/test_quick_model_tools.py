"""Hai công cụ AI trên mô hình L1: tất định, không đổi số của mô hình, không có giá mặc định, kiểm lựa chọn của AI."""
import datetime as dt
import json
import pathlib
import subprocess
import sys

import pytest

from quick_model import service
from quick_model_tools import forecast_tool as F
from quick_model_tools import tool_definitions
from quick_model_tools import valuation_tool as V
from quick_model_tools.common import edge

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "examples" / "data"
DAY = "2026-11-23"
WHY = "Thu nhập dự phóng ổn định và dương nên bội số lợi nhuận là thước đo hợp lý"


def forecast(tmp_path, ticker, family, **kw):
    return F.run({"op": "run", "ticker": ticker, "family": family, "dataset_dir": str(DATA / ticker.lower()), "store": str(tmp_path / "store"), **kw})


@pytest.fixture()
def tcb(tmp_path):
    r = forecast(tmp_path, "TCB", "bank")
    assert r["status"] == "ok"
    return r["result"]["run_id"], tmp_path


def csv_file(folder, pe=8.0, pb=1.3, days=600, last="2026-11-20"):
    d1 = dt.date.fromisoformat(last)
    p = folder / "multiples.csv"
    p.write_text("date,pe,pb\n" + "\n".join(f"{(d1 - dt.timedelta(days=i)).isoformat()},{pe * (1 + 0.5 * i / days):.4f},{pb * (1 + 0.5 * i / days):.4f}" for i in range(days)) + "\n")
    return str(p)


def value(tcb, **kw):
    run_id, tmp = tcb
    return V.run({"run_id": run_id, "store": str(tmp / "store"), "valuation_date": DAY, "multiples_csv": csv_file(tmp), "shares_million": 7000, "shares_source": "test", "market": {"rf": 0.04, "erp": 0.08}, **kw})


def pick(**methods):
    return {"decided_by": "test", "methods": {m: {"reason": WHY, **x} for m, x in methods.items()}}


def test_numbers_are_the_models(tmp_path):
    r = forecast(tmp_path, "TCB", "bank", view="table")
    out = service.forecast("TCB", "bank", folder=DATA / "tcb")
    cols = [c for c in out.annual.columns if c not in ("line_id", "label", "statement", "unit")]
    rows = {x["id"]: x["v"] for x in r["result"]["rows"]}
    for _, x in out.annual.iterrows():
        assert rows[x["line_id"]] == [edge(x[c], x["unit"]) for c in cols]


def test_same_input_same_run_and_stored_once(tmp_path):
    a, b = forecast(tmp_path, "TCB", "bank"), forecast(tmp_path, "TCB", "bank")
    assert a["result"]["run_id"] == b["result"]["run_id"] and (a["cached"], b["cached"]) == (False, True)
    assert forecast(tmp_path, "TCB", "bank", years=4)["result"]["run_id"] != a["result"]["run_id"]


def test_gate_stops_a_run_that_cannot_be_used(tmp_path):
    r = forecast(tmp_path, "HPG", "nonfinancial")["result"]
    assert r["model_ok"] is False and r["reasons"]
    v = V.run({"run_id": r["run_id"], "store": str(tmp_path / "store"), "valuation_date": DAY, "multiples_csv": csv_file(tmp_path), "shares_million": 7000, "shares_source": "t"})
    assert v["status"] == "refused" and v["code"] == "FORECAST_NOT_OK" and "value_per_share" not in json.dumps(v)


def test_every_wrong_override_is_named(tmp_path):
    r = forecast(tmp_path, "TCB", "bank", overrides={"khong_co": 1, "cir": "x"})
    assert r["status"] == "refused" and r["code"] == "OVERRIDE_INVALID" and len(r["missing"]) == 2


def test_missing_inputs_are_all_named(tmp_path):
    r = F.run({"op": "run", "ticker": "TCB"})
    assert r["code"] == "INPUT_MISSING" and {m["field"] for m in r["missing"]} == {"family", "dataset_dir", "store"}
    assert F.run({"op": "view", "run_id": "qmt:" + "0" * 64, "store": str(tmp_path)})["code"] == "RUN_NOT_FOUND"


def test_explain_and_views_read_the_stored_run(tcb):
    run_id, tmp = tcb
    for v in F.VIEWS:
        assert F.run({"op": "view", "run_id": run_id, "store": str(tmp / "store"), "view": v})["status"] == "ok"
    e = F.run({"op": "explain", "run_id": run_id, "store": str(tmp / "store"), "line": "npat_parent", "period": "2026"})
    assert e["status"] == "ok" and e["result"]["bridge"]


def test_without_a_choice_there_is_no_price(tcb):
    r = value(tcb)
    s, m = json.dumps(r), {x["id"]: x for x in r["result"]["methods"]}
    assert r["result"]["status"] == "need_choice" and "value_per_share" not in s and "fa_price" not in s
    assert all(m[k]["available"] for k in ("pe", "pb", "pb_roe", "residual_income")) and not m["dcf_fcff"]["available"] and m["dcf_fcff"]["why"]
    a = {n: float(x) for n, x, _ in m["pe"]["anchors"]}
    assert a["p10"] < a["p50"] < a["p90"] and float(m["pe"]["cap"]) == max(a["p10"], a["today"])


def test_the_choice_is_computed_as_chosen(tcb):
    cap = {x["id"]: x for x in value(tcb)["result"]["methods"]}["pe"]["cap"]
    r = value(tcb, choice=pick(pe={"weight": 0.6, "multiple": float(cap)}, residual_income={"weight": 0.4, "beta": 1.0, "g": 0.03}))["result"]
    m = {x["id"]: x for x in r["methods"]}
    assert abs(float(m["pe"]["multiple"]) * float(m["pe"]["base"]) - float(m["pe"]["value"])) < 0.1
    assert abs(float(r["value_per_share"]) - (0.6 * float(m["pe"]["value"]) + 0.4 * float(m["residual_income"]["value"]))) < 0.1
    assert m["residual_income"]["ke"] == "0.1200" and "MIXED_HORIZON" in {f["code"] for f in r["flags"]}
    assert value(tcb, choice=pick(pe={"weight": 0.6, "multiple": float(cap)}, residual_income={"weight": 0.4, "beta": 1.0, "g": 0.03}))["result"] == r


def test_a_bad_choice_is_refused_with_every_reason(tcb):
    r = value(tcb, choice=pick(pe={"weight": 0.5, "multiple": 8.0}))
    assert r["status"] == "refused" and "tổng tỷ trọng" in r["missing"][0]["why"]
    assert value(tcb, choice={"methods": {"pe": {"weight": 1, "multiple": 8.0, "reason": "ngắn"}}})["code"] == "CHOICE_INVALID"
    assert value(tcb, choice=pick(dcf_fcff={"weight": 1}))["code"] == "CHOICE_INVALID"
    assert value(tcb, market=None, choice=pick(residual_income={"weight": 1, "beta": 1.0, "g": 0.03}))["code"] == "CHOICE_INVALID"


def test_above_the_cap_needs_a_catalyst_or_growth(tcb):
    x = {m["id"]: m for m in value(tcb)["result"]["methods"]}["pe"]
    grow = float(value(tcb)["result"]["forecast"]["eps_forward_growth"])
    r = value(tcb, choice=pick(pe={"weight": 1, "multiple": float(x["cap"]) * 1.2}))
    assert (r["status"] == "ok") == (grow >= 0.2)
    if r["status"] != "ok":
        assert r["code"] == "RERATING_UNSUPPORTED"
        c = pick(pe={"weight": 1, "multiple": float(x["cap"]) * 1.2})
        c["methods"]["pe"]["reason"] += " theo SK1"
        ok = value(tcb, catalysts=[{"code": "SK1", "text": "sự kiện"}], choice=c)
        assert ok["status"] == "ok" and "SK1" in ok["result"]["over_cap"][0]


def test_bad_inputs_are_refused(tcb):
    assert value(tcb, shares_million=None)["code"] == "INPUT_MISSING"
    assert value(tcb, multiples_csv="/khong/co.csv")["code"] == "INPUT_MISSING"
    assert value(tcb, valuation_date="23/11/2026")["code"] == "INVALID_INPUT"


def test_tool_definitions_and_command_line(tmp_path):
    defs = {d["name"]: d for d in tool_definitions()}
    assert set(defs) == {"quick_model_forecast", "quick_model_valuation"} and all(d["input_schema"]["type"] == "object" for d in defs.values())
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"op": "run", "ticker": "TCB", "family": "bank", "dataset_dir": str(DATA / "tcb"), "store": str(tmp_path / "s")}))
    p = subprocess.run([sys.executable, "-m", "quick_model_tools", "forecast", "--in", str(inp)], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and json.loads(p.stdout)["result"]["ticker"] == "TCB"
