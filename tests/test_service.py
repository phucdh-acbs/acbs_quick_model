"""Lớp thư viện (quick_model.service) và REST API (quick_model_api) trên dữ liệu mẫu HPG, TCB (BCTC công khai)."""
import json
from pathlib import Path

import pandas as pd
import pytest

from quick_model import service as S

DATA = Path(__file__).resolve().parents[1] / "examples" / "data"
HPG, TCB = DATA / "hpg", DATA / "tcb"
NF_TABLES = {"INCOME_STATEMENT": "is", "BALANCE_SHEET": "bs", "CASH_FLOW": "cf", "STX_FSC_NOTECOMPANY": "note"}


def _frames(folder: Path, suffixes: dict) -> dict:
    return {t: pd.read_csv(folder / f"{folder.name}_{s}.csv") for t, s in suffixes.items()}


def test_folder_and_frames_give_same_forecast():
    a = S.forecast("HPG", "nonfinancial", folder=HPG, years=3)
    b = S.forecast("HPG", "nonfinancial", frames=_frames(HPG, NF_TABLES), years=3)
    assert a.status["ok"] and len(a.meta["forecast_years"]) == 3
    pd.testing.assert_frame_equal(a.annual, b.annual)
    npat = next(r for r in a.to_dict()["annual"]["rows"] if r["line_id"] == "npat_parent")
    assert npat["values"][f"{a.meta['forecast_years'][0]} F"] is not None
    json.dumps(a.to_dict())  # JSON được (NaN -> null)


def test_override_changes_forecast():
    base = S.forecast("HPG", "nonfinancial", folder=HPG, years=2)
    y0 = base.meta["forecast_years"][0]
    o = S.forecast("HPG", "nonfinancial", folder=HPG, years=2, overrides={"gross_margin": {y0: 0.30}})
    assert o.assumptions.set_index("assumption_id").loc["gross_margin", "source"] == "analyst"
    gp = lambda r: r.annual.set_index("line_id").loc["gross_profit", f"{y0} F"]  # noqa: E731
    assert gp(o) > gp(base)


def test_bank_and_explain():
    r = S.forecast("TCB", "bank", folder=TCB, years=2)
    assert r.status["ok"]
    assert r.explain("npat_parent", r.meta["forecast_years"][0])["headline"]


def test_input_errors():
    with pytest.raises(S.ServiceError):
        S.forecast("HPG", "insurance", folder=HPG)
    with pytest.raises(S.ServiceError):
        S.forecast("HPG", "nonfinancial", folder=HPG, overrides={"khong_ton_tai": 1})
    with pytest.raises(S.ServiceError):
        S.forecast("XYZ", "nonfinancial", folder=HPG)  # không có dữ liệu của mã


def test_api():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from quick_model_api.app import app
    c = TestClient(app)
    assert c.get("/health").json()["status"] == "ok"
    assert c.get("/v1/families/bank/data-contract").json()["tables"]["INCOME_STATEMENT"]["required"]
    stm = {t: json.loads(df.to_json(orient="records"))
           for t, df in _frames(TCB, {"INCOME_STATEMENT": "is", "BALANCE_SHEET": "bs"}).items()}
    r = c.post("/v1/forecast", json={"ticker": "TCB", "family": "bank", "years": 2, "statements": stm})
    assert r.status_code == 200, r.text
    assert r.json()["status"]["ok"]
    e = c.post("/v1/explain", json={"ticker": "TCB", "family": "bank", "years": 2, "statements": stm,
                                    "line": "toi", "period": r.json()["meta"]["forecast_years"][0]})
    assert e.status_code == 200, e.text
    assert c.post("/v1/forecast", json={"ticker": "TCB", "family": "bank", "statements": {}}).status_code == 422
