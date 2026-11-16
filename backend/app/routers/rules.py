from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.policy import policy_snapshot
from app.services import rule_service as svc

router = APIRouter(tags=["rules"])


class RuleVersionBody(BaseModel):
    title: str | None = None
    items: list[dict[str, Any]]


@router.get("/rules/policy")
def get_policy() -> dict[str, str]:
    return policy_snapshot()


@router.get("/rules/supported")
def supported_checks() -> list[dict[str, str]]:
    return svc.supported_checks()


@router.get("/rules/current")
def current_version() -> dict[str, Any]:
    return svc.get_current()


@router.get("/rules/versions")
def list_versions(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return svc.list_versions(limit=limit)


@router.get("/rules/versions/{version_id}")
def get_version(version_id: int) -> dict[str, Any]:
    return svc.get_version(version_id)


@router.post("/rules/versions")
def create_version(body: RuleVersionBody) -> dict[str, Any]:
    return svc.create_version(body.title, body.items)
