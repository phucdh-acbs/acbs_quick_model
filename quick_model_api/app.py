"""REST API cho mô hình L1: lớp mỏng bọc `quick_model.service`; không giữ trạng thái, không gọi BigQuery.

Chạy:  uvicorn quick_model_api.app:app --host 0.0.0.0 --port 8000   (tài liệu tương tác: /docs)
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from quick_model import service as S

app = FastAPI(title="ACBS Quick Model L1 API", version=S.MODEL_VERSION, description=S.DISCLAIMER)
Family = Literal["nonfinancial", "bank"]


class ForecastRequest(BaseModel):
    ticker: str = Field(..., examples=["HPG"])
    family: Family = "nonfinancial"
    statements: dict[str, list[dict[str, Any]]] = Field(
        ..., description="{tên bảng GBQ: [dòng]} với đúng tên cột GBQ (xem /v1/families/{family}/data-contract)")
    years: int | None = Field(None, ge=1, le=10, description="Số năm dự phóng từ năm gốc (mặc định 5)")
    overrides: dict[str, Any] | None = Field(
        None, description="{assumption_id: số | [số mỗi năm] | {\"2026\": số}}, đơn vị engine")


class ExplainRequest(ForecastRequest):
    line: str = Field(..., examples=["npat_parent"])
    period: str = Field(..., examples=["2026"], description="Năm 'YYYY' hoặc quý 'YYYYQn'")


def _run(req: ForecastRequest) -> S.ForecastOutput:
    try:
        return S.forecast(req.ticker, req.family, frames=req.statements, years=req.years, overrides=req.overrides)
    except S.ServiceError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model_version": S.MODEL_VERSION}


@app.get("/v1/families/{family}/data-contract")
def data_contract(family: Family) -> dict:
    return {"family": family, "tables": S.data_contract(family)}


@app.get("/v1/families/{family}/assumptions")
def assumptions(family: Family) -> dict:
    return {"family": family, "assumptions": S.assumption_catalog(family)}


@app.post("/v1/forecast")
def forecast(req: ForecastRequest) -> dict:
    return _run(req).to_dict()


@app.post("/v1/explain")
def explain(req: ExplainRequest) -> dict:
    out = _run(req)
    try:
        return {"meta": out.meta, "explanation": out.explain(req.line, req.period)}
    except S.ServiceError as e:
        raise HTTPException(422, str(e)) from None
