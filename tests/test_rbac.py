import pytest

from conftest import bearer

SCAN = {"employee_code": "EXODUS-1"}


@pytest.mark.parametrize("role,method,path,body", [
    ("EMPLOYEE", "post", "/api/v1/access/employee/check-in", SCAN),
    ("EMPLOYEE", "post", "/api/v1/access/employee/check-out", SCAN),
    ("EMPLOYEE", "get", "/api/v1/admin/dashboard", None),
    ("EMPLOYEE", "get", "/api/v1/admin/audit-logs", None),
    ("GATE_OPERATOR", "get", "/api/v1/admin/audit-logs", None),
    ("SECURITY_GUARD", "get", "/api/v1/admin/audit-logs", None),
    ("SECURITY_GUARD", "post", "/api/v1/admin/terminals", {"name": "T1", "gate_location": "Main"}),
    ("SECURITY_GUARD", "get", "/api/v1/admin/users", None),
    ("GATE_OPERATOR", "post", "/api/v1/employees", {"employee_code": "X-1", "full_name": "Test User"}),
    ("SECURITY_GUARD", "post", "/api/v1/visitors/pre-register", {}),
    ("EMPLOYEE", "post", "/api/v1/visitors/walk-in", {}),
    ("EMPLOYEE", "get", "/api/v1/visitors/on-site", None),
])
def test_role_denied(client, token_for, fake_db, role, method, path, body):
    r = getattr(client, method)(path, headers=bearer(token_for(role)), **({"json": body} if body is not None else {}))
    assert r.status_code == 403 and r.json["code"] == "FORBIDDEN"
    denied = [p for sql, p in fake_db.ran("INSERT INTO audit_logs")]
    assert any("rbac.denied" in (p or ()) for p in denied)  # every denial lands in the audit log


def test_super_admin_allowed(client, token_for, fake_db):
    fake_db.on("FROM audit_logs", [])
    r = client.get("/api/v1/admin/audit-logs", headers=bearer(token_for("SUPER_ADMIN")))
    assert r.status_code == 200 and r.json["data"]["logs"] == []


def test_hardware_client_cannot_reach_admin_or_preregister(client, fake_db):
    import auth
    key = "gk_test"
    fake_db.on("FROM terminals WHERE api_key_hash",
               [{"id": 1, "gate_location": "Main", "api_key_hash": auth.hash_api_key(key)}])
    h = {"X-Gate-API-Key": key}
    assert client.get("/api/v1/admin/audit-logs", headers=h).status_code == 403
    assert client.get("/api/v1/admin/dashboard", headers=h).status_code == 403
    assert client.post("/api/v1/visitors/pre-register", headers=h, json={}).status_code == 403


def test_hardware_cannot_record_manual_entry(client, fake_db):
    import auth
    fake_db.on("FROM terminals WHERE api_key_hash",
               [{"id": 1, "gate_location": "Main", "api_key_hash": auth.hash_api_key("gk_t")}])
    r = client.post("/api/v1/access/employee/check-in", headers={"X-Gate-API-Key": "gk_t"},
                    json={"employee_code": "EXODUS-1", "method": "MANUAL"})
    assert r.status_code == 403 and r.json["code"] == "MANUAL_NOT_ALLOWED"
