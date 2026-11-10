import os
import threading

import pytest

from conftest import bearer

EMP = {"id": 5, "full_name": "Jane Doe", "is_active": 1}
IN, OUT = "/api/v1/access/employee/check-in", "/api/v1/access/employee/check-out"
BODY = {"employee_code": "exodus-5"}  # lower-case on purpose: normalised to EXODUS-5


@pytest.fixture
def guard(token_for):
    return bearer(token_for("SECURITY_GUARD", user_id=2))


def _script(fake_db, last):
    fake_db.on("FROM employees WHERE employee_code", [EMP])
    fake_db.on("FROM access_logs WHERE employee_id", [last] if last else [])


def test_first_check_in_succeeds_and_locks_rows(client, fake_db, guard):
    _script(fake_db, None)
    r = client.post(IN, json=BODY, headers=guard)
    assert r.status_code == 201 and r.json["data"]["method"] == "MANUAL"  # guards are flagged MANUAL
    assert "FOR UPDATE" in fake_db.executed[0][0]          # employee row locked first
    assert fake_db.ran("INSERT INTO access_logs")


def test_double_check_in_rejected_409(client, fake_db, guard):
    _script(fake_db, {"id": 9, "check_out_time": None, "age": 600})
    r = client.post(IN, json=BODY, headers=guard)
    assert r.status_code == 409 and r.json["code"] == "ALREADY_CHECKED_IN"
    assert not fake_db.ran("INSERT INTO access_logs")


def test_resubmission_window_blocks_replay(client, fake_db, guard):
    _script(fake_db, {"id": 9, "check_out_time": None, "age": 1})
    r = client.post(IN, json=BODY, headers=guard)
    assert r.status_code == 409 and r.json["code"] == "DUPLICATE_SCAN"


def test_check_out_without_open_row_409(client, fake_db, guard):
    _script(fake_db, {"id": 9, "check_out_time": "2026-01-01 08:00:00", "age": 600})
    r = client.post(OUT, json=BODY, headers=guard)
    assert r.status_code == 409 and r.json["code"] == "NOT_CHECKED_IN"


def test_check_out_closes_open_row(client, fake_db, guard):
    _script(fake_db, {"id": 9, "check_out_time": None, "age": 600})
    r = client.post(OUT, json=BODY, headers=guard)
    assert r.status_code == 200 and fake_db.ran("UPDATE access_logs SET check_out_time")


def test_unknown_employee_404(client, fake_db, guard):
    fake_db.on("FROM employees WHERE employee_code", [])
    assert client.post(IN, json=BODY, headers=guard).json["code"] == "EMPLOYEE_NOT_FOUND"


def test_denials_and_successes_are_audited(client, fake_db, guard):
    _script(fake_db, {"id": 9, "check_out_time": None, "age": 600})
    client.post(IN, json=BODY, headers=guard)
    assert any("access.check_in" in (p or ()) and "denied" in (p or ()) for _, p in fake_db.ran("INSERT INTO audit_logs"))


@pytest.mark.db
@pytest.mark.skipif(not os.environ.get("TEST_DB_NAME"), reason="needs a real MySQL test database")
def test_concurrent_scans_only_one_check_in(monkeypatch):
    """Real MySQL: 8 simultaneous check-ins for one employee -> exactly one 201, the rest 409."""
    for k in ("HOST", "USER", "PASSWORD", "NAME"):
        monkeypatch.setenv(f"DB_{k}", os.environ[f"TEST_DB_{k}"])
    import db as dbmod
    from app import create_app
    app = create_app({"TESTING": True, "RATELIMIT_ENABLED": False, "FORCE_HTTPS": False,
                      "RESUBMIT_WINDOW_SECONDS": 0})
    with dbmod.get_cursor() as cur:
        cur.execute("DELETE FROM access_logs")
        cur.execute("INSERT IGNORE INTO employees (employee_code, full_name) VALUES ('RACE-1','Race Test')")
    from flask_jwt_extended import create_access_token
    with app.app_context():
        tok = create_access_token(identity="1", additional_claims={"role": "SUPER_ADMIN", "employee_id": None})
    results = []

    def scan():
        r = app.test_client().post(IN, json={"employee_code": "RACE-1"}, headers=bearer(tok))
        results.append(r.status_code)
    threads = [threading.Thread(target=scan) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == [201] + [409] * 7
