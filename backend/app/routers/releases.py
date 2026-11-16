from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import release_service as svc

router = APIRouter(tags=["releases"])


class ReleaseBody(BaseModel):
    pack_id: int
    decision: str
    reason: str | None = None
    # 发起放行时依据的规则版本；用于识别在飞期间规则已变的冲突。
    expected_rule_version_id: int | None = None


@router.get("/releases")
def list_releases():
    return svc.list_releases()


@router.get("/releases/preview/{pack_id}")
def preview_release(pack_id: int) -> dict[str, Any]:
    return svc.preview_release(pack_id)


@router.post("/releases/decide")
def decide(body: ReleaseBody) -> dict[str, Any]:
    return svc.decide(
        body.pack_id,
        body.decision,
        body.reason,
        expected_rule_version_id=body.expected_rule_version_id,
    )
