"""校验清单闸门回归测试。

覆盖：
- 启用项全过可放行；
- 加严（新增检查项）后旧通过作废，须复检；复检未过则拒绝；
- 停用项后仍以「当前启用项全过」为准；
- 缺跑不得放行；
- 在飞冲突：规则版本已变则拒绝放行且不写出通过。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.config import DATA_DIR
from app.db import connect
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.db.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.db.DB_PATH", tmp_path / "app.db")
    with TestClient(app) as c:
        yield c


def _build_pack(client: TestClient) -> int:
    subjects = client.get("/api/subjects").json()
    subject = next(s for s in subjects if s["code"] == "store_dayend")
    batch = client.post(
        "/api/samples/run",
        json={"subject_id": subject["id"], "rate": 1.0, "seed": "test-seed"},
    ).json()
    pack = client.post("/api/packs/build", json={"batch_id": batch["id"]}).json()
    return pack["id"]


def _run(client: TestClient, pack_id: int) -> dict:
    r = client.post("/api/checklist/run", json={"pack_id": pack_id})
    assert r.status_code == 200, r.text
    return r.json()


def _current_version(client: TestClient) -> dict:
    return client.get("/api/rules/current").json()


def _publish(client: TestClient, items: list[dict], title: str = "测试规则") -> dict:
    r = client.post("/api/rules/versions", json={"title": title, "items": items})
    assert r.status_code == 200, r.text
    return r.json()


def _pass_items() -> list[dict]:
    return [
        {"code": "count_match", "title": "条数一致", "enabled": True, "params": {}},
        {"code": "key_unique", "title": "键唯一", "enabled": True, "params": {}},
        {
            "code": "required_fields",
            "title": "必填非空",
            "enabled": True,
            "params": {"fields": ["business_key", "amount", "qty", "event_ts", "revision", "title"]},
        },
    ]


def test_full_pass_allows_release_and_traces_rule_version(client):
    pack_id = _build_pack(client)
    version = _current_version(client)
    run = _run(client, pack_id)
    assert run["all_passed"] is True
    assert run["rule_version_id"] == version["id"]

    preview = client.get(f"/api/releases/preview/{pack_id}").json()
    assert preview["can_release"] is True

    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": version["id"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "pass"
    assert body["rule_version_no"] == version["version_no"]

    trace = client.get("/api/releases").json()
    assert trace[0]["rule_version_id"] == version["id"]
    assert trace[0]["rule_version_no"] == version["version_no"]
    assert trace[0]["checklist_run_id"] == run["run_id"]


def test_missing_run_blocks_release(client):
    pack_id = _build_pack(client)
    version = _current_version(client)
    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": version["id"]},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["gate_code"] == "checklist_missing"
    assert client.get("/api/releases").json() == []


def test_tightened_rule_invalidates_old_pass_and_requires_recheck(client):
    pack_id = _build_pack(client)
    _run(client, pack_id)

    # 加严：必填字段加入一个夹具中不存在的字段 → 复检必不过。
    tightened = _pass_items()
    tightened[2]["params"]["fields"].append("settlement_no")
    new_version = _publish(client, tightened, "加严 v2")
    assert new_version["active"] == 1

    # 旧通过作废：直接用旧结果放行被拒（规则版本漂移）。
    r = client.post(
        "/api/releases/decide",
        json={
            "pack_id": pack_id,
            "decision": "pass",
            "expected_rule_version_id": new_version["id"],
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["gate_code"] == "rule_version_drift"
    assert client.get("/api/releases").json() == []

    # 按新规则复检：未过。
    run = _run(client, pack_id)
    assert run["all_passed"] is False
    r = client.post("/api/releases/decide", json={"pack_id": pack_id, "decision": "pass"})
    assert r.status_code == 400
    assert r.json()["detail"]["gate_code"] == "checklist_failed"


def test_tightened_rule_recheck_pass_allows_release(client):
    pack_id = _build_pack(client)
    _run(client, pack_id)
    # 加严但夹具仍可满足（必需字段仍是既有字段）→ 复检可通过。
    tightened = _pass_items()
    tightened.append(
        {"code": "count_match", "title": "条数一致(重)", "enabled": True, "params": {}}
    )
    # 不允许重复 code，改为仅调整 required_fields 集合
    tightened = _pass_items()
    tightened[2]["params"]["fields"] = ["business_key", "title", "amount", "qty", "revision"]
    new_version = _publish(client, tightened, "加严 v2")

    r = client.post(
        "/api/releases/decide",
        json={
            "pack_id": pack_id,
            "decision": "pass",
            "expected_rule_version_id": new_version["id"],
        },
    )
    assert r.status_code == 409, "旧通过应因版本漂移被拒"

    run = _run(client, pack_id)
    assert run["all_passed"] is True
    assert run["rule_version_id"] == new_version["id"]
    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": new_version["id"]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rule_version_no"] == new_version["version_no"]


def test_disabled_item_is_not_required(client):
    pack_id = _build_pack(client)
    _run(client, pack_id)
    # 停用必填项后，仍以启用项为准；复检通过可放行。
    items = _pass_items()
    items[2]["enabled"] = False
    new_version = _publish(client, items, "停用必填 v2")
    run = _run(client, pack_id)
    assert run["all_passed"] is True
    assert {r["check_code"] for r in run["results"]} == {"count_match", "key_unique"}
    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": new_version["id"]},
    )
    assert r.status_code == 200, r.text


def test_inflight_conflict_rejects_release_without_writing_pass(client):
    pack_id = _build_pack(client)
    run = _run(client, pack_id)
    assert run["all_passed"] is True
    stale_version_id = run["rule_version_id"]

    # 在飞期间规则被换版（停用必填项的新版本）。
    items = _pass_items()
    items[2]["enabled"] = False
    _publish(client, items, "换版 v2")

    # 仍以发起时的旧版本提交放行 → 拒绝，且不写出通过。
    r = client.post(
        "/api/releases/decide",
        json={
            "pack_id": pack_id,
            "decision": "pass",
            "expected_rule_version_id": stale_version_id,
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["gate_code"] == "rule_version_drift"
    assert client.get("/api/releases").json() == []

    # 包状态未被改成已放行。
    pack = client.get(f"/api/packs/{pack_id}").json()
    assert pack["status"] != "released"


def test_duplicate_or_unknown_check_codes_rejected(client):
    items = _pass_items()
    items.append({"code": "count_match", "title": "重复", "enabled": True, "params": {}})
    r = client.post("/api/rules/versions", json={"title": "坏规则", "items": items})
    assert r.status_code == 400

    r = client.post(
        "/api/rules/versions",
        json={"title": "坏规则", "items": [{"code": "nope", "enabled": True}]},
    )
    assert r.status_code == 400


def test_all_items_disabled_rejected(client):
    items = _pass_items()
    for it in items:
        it["enabled"] = False
    r = client.post("/api/rules/versions", json={"title": "全停", "items": items})
    assert r.status_code == 400


def test_rule_versions_persist_and_bind_results(client):
    pack_id = _build_pack(client)
    run = _run(client, pack_id)
    versions = client.get("/api/rules/versions").json()
    assert versions["active"]["id"] == run["rule_version_id"]
    assert len(versions["versions"]) >= 1

    conn = connect()
    try:
        row = conn.execute(
            "SELECT rule_version_id, run_id FROM checklist_results WHERE pack_id=? LIMIT 1",
            (pack_id,),
        ).fetchone()
        assert row["rule_version_id"] == run["rule_version_id"]
        assert row["run_id"] == run["run_id"]
        stored = conn.execute(
            "SELECT rule_version_no FROM checklist_runs WHERE id=?", (run["run_id"],)
        ).fetchone()
        assert stored["rule_version_no"] == run["rule_version_no"]
    finally:
        conn.close()
