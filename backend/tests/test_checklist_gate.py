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


# ---------------------------------------------------------------------------
# 版本撒谎三条路径：留痕版本≠清单绑定版本 / 变更中放行混版 / 停用项后仍凭旧通过放行
# ---------------------------------------------------------------------------


def _latest_release(client: TestClient) -> dict | None:
    rows = client.get("/api/releases").json()
    return rows[0] if rows else None


def _disable_one_item(client: TestClient, index: int = 2) -> dict:
    items = _pass_items()
    items[index]["enabled"] = False
    return _publish(client, items, "停用一项")


def test_pass_trace_version_equals_bound_checklist_version(client):
    """通过留痕的规则版本必须等于所绑清单运行/结果的版本。"""
    pack_id = _build_pack(client)
    version = _current_version(client)
    run = _run(client, pack_id)
    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": version["id"]},
    )
    assert r.status_code == 200, r.text

    trace = _latest_release(client)
    assert trace["decision"] == "pass"
    assert trace["rule_version_id"] == run["rule_version_id"]
    assert trace["checklist_run_id"] == run["run_id"]
    assert trace["checklist_rule_version_id"] == run["rule_version_id"]
    assert trace["rule_version_no"] == trace["checklist_rule_version_no"]
    assert trace["binding_consistent"] is True

    conn = connect()
    try:
        bound = conn.execute(
            "SELECT rule_version_id, rule_version_no FROM checklist_runs WHERE id=?",
            (trace["checklist_run_id"],),
        ).fetchone()
        res_versions = {
            row["rule_version_id"]
            for row in conn.execute(
                "SELECT DISTINCT rule_version_id FROM checklist_results WHERE run_id=?",
                (trace["checklist_run_id"],),
            ).fetchall()
        }
    finally:
        conn.close()
    assert bound["rule_version_id"] == trace["rule_version_id"]
    assert bound["rule_version_no"] == trace["rule_version_no"]
    assert res_versions == {trace["rule_version_id"]}


def test_disabled_item_then_release_must_bind_new_version(client):
    """停用项之后，旧「通过」布尔不得再被放行；留痕须落新版本。"""
    pack_id = _build_pack(client)
    old_run = _run(client, pack_id)
    assert old_run["all_passed"] is True

    new_version = _disable_one_item(client)

    # 尚未复检：旧通过作废，放行被拒且不写留痕。
    r = client.post(
        "/api/releases/decide",
        json={
            "pack_id": pack_id,
            "decision": "pass",
            "expected_rule_version_id": new_version["id"],
        },
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["gate_code"] == "rule_version_drift"
    assert client.get("/api/releases").json() == []
    pack = client.get(f"/api/packs/{pack_id}").json()
    assert pack["status"] == "recheck_required"
    assert pack["status"] != "released"

    # 复检后放行，留痕版本与清单绑定版本一致，且是新版本。
    run = _run(client, pack_id)
    assert run["all_passed"] is True
    assert run["rule_version_id"] == new_version["id"]
    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": new_version["id"]},
    )
    assert r.status_code == 200, r.text
    trace = _latest_release(client)
    assert trace["rule_version_id"] == new_version["id"]
    assert trace["checklist_rule_version_id"] == new_version["id"]
    assert trace["rule_version_no"] == new_version["version_no"]
    assert trace["binding_consistent"] is True


def test_inflight_rule_change_never_writes_mixed_version_pass(client):
    """变更与在飞放行冲突时，不得留下任何混版通过留痕。"""
    pack_id = _build_pack(client)
    run = _run(client, pack_id)
    stale_version_id = run["rule_version_id"]

    # 另一包在换版前已复检通过。
    other_pack = _build_pack(client)
    _run(client, other_pack)

    _disable_one_item(client)

    # 在飞放行仍带旧版本号提交 → 拒绝。
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

    # 即使不带 expected 版本号，最近一次运行仍绑定旧版本 → 仍拒。
    r = client.post("/api/releases/decide", json={"pack_id": other_pack, "decision": "pass"})
    assert r.status_code == 409
    assert r.json()["detail"]["gate_code"] == "rule_version_drift"

    # 全站不存在通过留痕。
    assert client.get("/api/releases").json() == []
    for pid in (pack_id, other_pack):
        assert client.get(f"/api/packs/{pid}").json()["status"] != "released"


def test_stale_green_run_cannot_be_restamped_as_current_version(client):
    """旧绿运行不能被当前版本号重盖后放行。"""
    pack_id = _build_pack(client)
    old_run = _run(client, pack_id)
    old_version_id = old_run["rule_version_id"]

    # 换版到启用项集合不同的新版本，且夹具能过。
    items = _pass_items()
    items[2]["params"]["fields"] = ["business_key", "title", "amount"]
    new_version = _publish(client, items, "换版 v2")
    assert new_version["id"] != old_version_id

    preview = client.get(f"/api/releases/preview/{pack_id}").json()
    assert preview["can_release"] is False
    assert preview["gate_code"] == "rule_version_drift"
    assert preview["checklist_rule_version_id"] == old_version_id
    assert preview["active_rule_version_id"] == new_version["id"]
    assert preview["binding_consistent"] is False

    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": new_version["id"]},
    )
    assert r.status_code == 409
    assert client.get("/api/releases").json() == []


def test_older_green_run_not_used_when_latest_run_failed(client):
    """最近一次运行未过时，不得回退去捡更早的绿运行。"""
    pack_id = _build_pack(client)
    _run(client, pack_id)  # 可通过

    tightened = _pass_items()
    tightened[2]["params"]["fields"].append("settlement_no")
    _publish(client, tightened, "加严")

    run = _run(client, pack_id)
    assert run["all_passed"] is False

    r = client.post("/api/releases/decide", json={"pack_id": pack_id, "decision": "pass"})
    assert r.status_code == 400
    assert r.json()["detail"]["gate_code"] == "checklist_failed"
    assert client.get("/api/releases").json() == []


def test_voided_pass_cleared_only_by_recheck(client):
    """旧通过被作废后，未复检不得再申请放行；复检通过后放行。"""
    pack_id = _build_pack(client)
    _run(client, pack_id)
    new_version = _disable_one_item(client)

    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": new_version["id"]},
    )
    assert r.status_code == 409
    pack = client.get(f"/api/packs/{pack_id}").json()
    assert pack["status"] == "recheck_required"

    # 作废未清时不得写出通过（即使出现其它绿运行的假象）。
    r = client.post("/api/releases/decide", json={"pack_id": pack_id, "decision": "pass"})
    assert r.status_code == 409
    assert r.json()["detail"]["gate_code"] == "rule_version_drift"
    assert client.get("/api/releases").json() == []

    # 复检通过 → 放行。
    run = _run(client, pack_id)
    assert run["all_passed"] is True
    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": new_version["id"]},
    )
    assert r.status_code == 200, r.text
    assert _latest_release(client)["binding_consistent"] is True


def test_legacy_lying_pass_blocks_new_pass_until_recheck(client):
    """历史遗留的混版通过：不得在其上再写通过，须先复检。"""
    pack_id = _build_pack(client)
    run = _run(client, pack_id)
    version = _current_version(client)
    assert run["rule_version_id"] == version["id"]

    # 构造旧实现留下的撒谎留痕：留痕写当前版本，但绑定的是另一版本的运行。
    conn = connect(immediate=True)
    try:
        next_no = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) + 1 AS n FROM rule_versions"
        ).fetchone()["n"]
        stale_version_id = conn.execute(
            """
            INSERT INTO rule_versions(version_no, title, payload_json, active, created_at)
            VALUES (?,?,?,0,?)
            """,
            (next_no, "历史版本", json.dumps({"items": _pass_items()}, ensure_ascii=False),
             "2020-01-01T00:00:00Z"),
        ).lastrowid
        stale_run_id = conn.execute(
            """
            INSERT INTO checklist_runs(
                pack_id, rule_version_id, rule_version_no, status, checked_count, created_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (pack_id, stale_version_id, next_no, "passed", 3, "2020-01-01T00:00:00Z"),
        ).lastrowid
        conn.execute(
            """
            INSERT INTO release_decisions(
                pack_id, decision, reason, rule_version_id, rule_version_no,
                checklist_run_id, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (pack_id, "pass", "历史混版通过", version["id"], version["version_no"],
             stale_run_id, "2020-01-01T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()

    traces = client.get("/api/releases").json()
    assert traces[0]["decision"] == "pass"
    assert traces[0]["binding_consistent"] is False

    # 新的套装 + 复检前，不得再写通过。
    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": version["id"]},
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["gate_code"] == "rule_version_drift"

    # 复检后，旧撒谎留痕被认为已由新运行取代，可放行且绑定一致。
    new_run = _run(client, pack_id)
    assert new_run["all_passed"] is True
    r = client.post(
        "/api/releases/decide",
        json={"pack_id": pack_id, "decision": "pass", "expected_rule_version_id": version["id"]},
    )
    assert r.status_code == 200, r.text
    latest = _latest_release(client)
    assert latest["rule_version_id"] == latest["checklist_rule_version_id"]
    assert latest["binding_consistent"] is True
