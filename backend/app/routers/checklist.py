from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import checklist_service as svc

router = APIRouter(tags=["checklist"])


class ChecklistRunBody(BaseModel):
    pack_id: int


@router.get("/checklist")
def list_checklist(
    pack_id: int | None = Query(default=None),
    run_id: int | None = Query(default=None),
    all_runs: bool = Query(default=False),
) -> dict[str, Any]:
    return svc.list_results(pack_id=pack_id, run_id=run_id, all_runs=all_runs)


@router.get("/checklist/runs")
def list_checklist_runs(pack_id: int = Query(...)) -> list[dict[str, Any]]:
    return svc.list_runs(pack_id=pack_id)


@router.post("/checklist/run")
def run_checklist(body: ChecklistRunBody) -> dict[str, Any]:
    return svc.run_checklist(body.pack_id)
