"""Auto check-out, CSV export, and email notification."""
from datetime import datetime

import pytest

import notifier
import scheduler
from conftest import bearer

CUT = "23:00:00"


# ---- automatic check-out ------------------------------------------------------------------
def test_auto_checkout_closes_staff_and_visitors_and_audits(app, fake_db):
    fake_db.rowcount = 3
    with app.app_context():
        assert scheduler.run_auto_checkout(CUT) == (3, 3)
    sql = [s for s, _ in fake_db.executed]
    assert any("UPDATE access_logs SET check_out_time" in s and "'AUTO'" in s for s in sql)
    assert any("UPDATE visits SET status = 'departed'" in s and "auto_closed = 1" in s for s in sql)
    assert any("system.auto_checkout" in (p or ()) for _, p in fake_db.ran("INSERT INTO audit_logs"))


def test_auto_checkout_is_silent_when_nothing_to_close(app, fake_db):
    fake_db.rowcount = 0
    with app.app_context():
        assert scheduler.run_auto_checkout(CUT) == (0, 0)
    assert not fake_db.ran("INSERT INTO audit_logs")  # runs every minute: no audit noise


def test_cutoff_is_passed_to_sql_for_every_placeholder(app, fake_db):
    with app.app_context():
        scheduler.run_auto_checkout("22:30:00")
    for sql, params in fake_db.ran("UPDATE"):
        assert params == ("22:30:00",) * 6 and sql.count("%s") == 6


def test_scheduler_disabled_in_tests(app):
    assert scheduler.start_scheduler(app) is None


# ---- CSV export ---------------------------------------------------------------------------
ROW = {"id": 1, "employee_code": "EXODUS-1", "full_name": "=HYPERLINK(\"http://x\")", "purpose": "R&amp;D",
       "check_in_time": datetime(2026, 11, 10, 8, 30), "check_out_time": None, "duration_minutes": None}


def _admin(token_for):
    return bearer(token_for("SUPER_ADMIN"))


def test_export_is_super_admin_only(client, token_for):
    for role in ("SECURITY_GUARD", "GATE_OPERATOR", "EMPLOYEE"):
        r = client.get("/api/v1/admin/export/access-logs.csv?period=day", headers=bearer(token_for(role)))
        assert r.status_code == 403


def test_export_csv_content_and_safety(client, token_for, fake_db):
    fake_db.on("FROM access_logs a", [ROW])
    r = client.get("/api/v1/admin/export/access-logs.csv?period=day&date=2026-11-10", headers=_admin(token_for))
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert 'filename="access_logs_2026-11-10_to_2026-11-10.csv"' in r.headers["Content-Disposition"]
    text = r.get_data(as_text=True)
    assert text.startswith("\ufeff") and "employee_code" in text
    assert "'=HYPERLINK" in text            # formula neutralised
    assert "R&D" in text and "&amp;" not in text  # stored escaping undone for the file
    assert any("admin.export" in (p or ()) for _, p in fake_db.ran("INSERT INTO audit_logs"))


@pytest.mark.parametrize("qs,start,end", [
    ("period=day&date=2026-11-10", "2026-11-10", "2026-11-11"),
    ("period=week&date=2026-11-10", "2026-11-09", "2026-11-16"),     # Tue -> Mon..Sun (end exclusive)
    ("period=month&date=2026-11-10", "2026-11-01", "2026-12-01"),
    ("period=month&date=2026-12-31", "2026-12-01", "2027-01-01"),
    ("start=2026-10-05&end=2026-10-07", "2026-10-05", "2026-10-08"),
])
def test_export_ranges(client, token_for, fake_db, qs, start, end):
    fake_db.on("FROM access_logs a", [])
    r = client.get(f"/api/v1/admin/export/access-logs.csv?{qs}", headers=_admin(token_for))
    assert r.status_code == 200
    params = fake_db.ran("FROM access_logs a")[-1][1]
    assert str(params[0]) == start and str(params[1]) == end


@pytest.mark.parametrize("qs", ["period=year", "start=2026-11-10&end=2026-11-01", "start=nope&end=2026-11-01",
                                "start=2025-01-01&end=2026-12-31", ""])
def test_export_rejects_bad_ranges(client, token_for, qs):
    r = client.get(f"/api/v1/admin/export/access-logs.csv?{qs}", headers=_admin(token_for))
    assert r.status_code == 422 and r.json["status"] == "error"


def test_export_visits_and_unknown_kind(client, token_for, fake_db):
    fake_db.on("FROM visits vt", [])
    assert client.get("/api/v1/admin/export/visits.csv?period=week", headers=_admin(token_for)).status_code == 200
    assert client.get("/api/v1/admin/export/secrets.csv?period=week", headers=_admin(token_for)).status_code == 404


# ---- email notification -------------------------------------------------------------------
VISIT = {"id": 9, "visitor_name": "John Moyo", "purpose": "Meeting about R&amp;D budget", "badge_code": "ABCD23XYZ9",
         "status": "on-site", "host_name": "Jane Doe", "host_email": "jane@example.com", "host_code": "EXODUS-5"}


def test_email_names_visitor_and_purpose():
    subject, body = notifier.render("visitor.arrived", VISIT)
    assert "John Moyo" in subject
    assert "John Moyo" in body and "Meeting about R&D budget" in body and "Hello Jane Doe" in body


def test_arrival_triggers_email_to_host(make_app, fake_db, monkeypatch):
    sent = []

    class Capture:
        def __init__(self, cfg): pass
        def send(self, event, ctx): sent.append((event, ctx))
    monkeypatch.setitem(notifier.BACKENDS, "smtp", Capture)
    app = make_app(NOTIFIER_BACKEND="smtp")
    with app.app_context():
        notifier.notify("visitor.arrived", VISIT)
    assert sent and sent[0][1]["host_email"] == "jane@example.com" and sent[0][1]["visitor_name"] == "John Moyo"
    assert any("notify.sent" in (p or ()) for _, p in fake_db.ran("INSERT INTO audit_logs"))


def test_host_without_email_is_skipped_and_audited(make_app, fake_db, monkeypatch):
    sent = []

    class Capture:
        def __init__(self, cfg): pass
        def send(self, event, ctx): sent.append(ctx)
    monkeypatch.setitem(notifier.BACKENDS, "smtp", Capture)
    app = make_app(NOTIFIER_BACKEND="smtp")
    with app.app_context():
        notifier.notify("visitor.arrived", {**VISIT, "host_email": None})
    assert not sent
    assert any("notify.failed" in (p or ()) for _, p in fake_db.ran("INSERT INTO audit_logs"))


def test_smtp_failure_never_breaks_the_request(make_app, fake_db, monkeypatch):
    class Boom:
        def __init__(self, cfg): pass
        def send(self, event, ctx): raise OSError("mail server down")
    monkeypatch.setitem(notifier.BACKENDS, "smtp", Boom)
    app = make_app(NOTIFIER_BACKEND="smtp")
    with app.app_context():
        notifier.notify("visitor.arrived", VISIT)  # must not raise
    assert any("notify.failed" in (p or ()) for _, p in fake_db.ran("INSERT INTO audit_logs"))


def test_update_employee_email_guard_only(client, token_for, fake_db):
    fake_db.on("FROM employees WHERE employee_code", [{"id": 5}])
    ok = client.patch("/api/v1/employees/exodus-5", json={"email": "Jane@Example.com"},
                      headers=bearer(token_for("SECURITY_GUARD")))
    assert ok.status_code == 200 and fake_db.ran("UPDATE employees SET email")
    assert fake_db.ran("UPDATE employees SET email")[-1][1][0] == "jane@example.com"
    denied = client.patch("/api/v1/employees/EXODUS-5", json={"email": "a@b.co"}, headers=bearer(token_for("GATE_OPERATOR")))
    assert denied.status_code == 403
    bad = client.patch("/api/v1/employees/EXODUS-5", json={"email": "not-an-email"}, headers=bearer(token_for("SECURITY_GUARD")))
    assert bad.status_code == 422


# ---- employee management, search, arrival mode ---------------------------------------------
EMP = {"id": 5, "full_name": "Jane Doe", "is_active": 1}


def test_employee_admin_endpoints_are_super_admin_only(client, token_for):
    for role in ("SECURITY_GUARD", "GATE_OPERATOR", "EMPLOYEE"):
        h = bearer(token_for(role))
        assert client.get("/api/v1/admin/employees", headers=h).status_code == 403
        assert client.patch("/api/v1/admin/employees/EXODUS-5", json={"full_name": "X Y"}, headers=h).status_code == 403
        assert client.delete("/api/v1/admin/employees/EXODUS-5", headers=h).status_code == 403
        assert client.post("/api/v1/admin/employees/EXODUS-5/vehicles", json={"plate": "ABC 123"}, headers=h).status_code == 403
        assert client.delete("/api/v1/admin/vehicles/1", headers=h).status_code == 403


def test_search_by_employee_id_or_name(client, token_for, fake_db):
    fake_db.on("FROM employees e WHERE", [{"employee_code": "EXODUS-5", "full_name": "Jane Doe", "has_vehicle": 1, "plates": "ACX 1"}])
    r = client.get("/api/v1/employees?q=exodus-5", headers=bearer(token_for("GATE_OPERATOR")))
    assert r.status_code == 200 and r.json["data"]["employees"][0]["has_vehicle"] is True
    sql, params = fake_db.ran("FROM employees e WHERE")[-1]
    assert "e.employee_code LIKE" in sql and "e.full_name LIKE" in sql and params == ("%exodus-5%", "%exodus-5%")


def test_admin_search_escapes_wildcards(client, token_for, fake_db):
    fake_db.on("FROM employees e WHERE", [])
    client.get("/api/v1/admin/employees?q=100%25_x&status=active", headers=bearer(token_for("SUPER_ADMIN")))
    sql, params = fake_db.ran("FROM employees e WHERE")[-1]
    assert "e.is_active = 1" in sql and params[0] == "%100\\%\\_x%"


def test_walk_in_arrival_is_recorded_even_when_employee_has_a_car(client, token_for, fake_db):
    fake_db.on("FROM employees WHERE employee_code", [EMP])
    fake_db.on("FROM access_logs WHERE employee_id", [])
    fake_db.on("FROM vehicles WHERE employee_id", [{"hit": 1}])   # has a registered car
    r = client.post("/api/v1/access/employee/check-in", headers=bearer(token_for("GATE_OPERATOR")),
                    json={"employee_code": "EXODUS-5", "arrival_type": "walk_in"})
    assert r.status_code == 201 and r.json["data"]["arrival_type"] == "walk_in"
    assert fake_db.ran("INSERT INTO access_logs")[-1][1][3] == "walk_in"


def test_unspecified_arrival_still_infers_vehicle(client, token_for, fake_db):
    fake_db.on("FROM employees WHERE employee_code", [EMP])
    fake_db.on("FROM access_logs WHERE employee_id", [])
    fake_db.on("FROM vehicles WHERE employee_id", [{"hit": 1}])
    r = client.post("/api/v1/access/employee/check-in", headers=bearer(token_for("GATE_OPERATOR")),
                    json={"employee_code": "EXODUS-5"})
    assert r.json["data"]["arrival_type"] == "vehicle"


def test_inactive_employee_cannot_check_in_but_can_check_out(client, token_for, fake_db):
    h = bearer(token_for("GATE_OPERATOR"))
    fake_db.on("FROM employees WHERE employee_code", [{**EMP, "is_active": 0}])
    fake_db.on("FROM access_logs WHERE employee_id", [{"id": 9, "check_out_time": None, "age": 600}])
    assert client.post("/api/v1/access/employee/check-in", headers=h, json={"employee_code": "EXODUS-5"}).json["code"] == "EMPLOYEE_INACTIVE"
    assert client.post("/api/v1/access/employee/check-out", headers=h, json={"employee_code": "EXODUS-5"}).status_code == 200


def test_delete_refuses_when_history_exists(client, token_for, fake_db):
    fake_db.on("FROM employees WHERE employee_code", [{"id": 5}])
    fake_db.on("COUNT(*) AS n FROM access_logs", [{"n": 12}])
    r = client.delete("/api/v1/admin/employees/EXODUS-5", headers=bearer(token_for("SUPER_ADMIN")))
    assert r.status_code == 409 and r.json["code"] == "HAS_HISTORY"
    assert not fake_db.ran("DELETE FROM employees")


def test_purge_requires_matching_confirmation(client, token_for, fake_db):
    h = bearer(token_for("SUPER_ADMIN"))
    fake_db.on("FROM employees WHERE employee_code", [{"id": 5}])
    fake_db.on("COUNT(*) AS n FROM access_logs", [{"n": 12}])
    bad = client.delete("/api/v1/admin/employees/EXODUS-5", headers=h, json={"purge": True, "confirm_code": "EXODUS-6"})
    assert bad.status_code == 422 and bad.json["code"] == "CONFIRM_REQUIRED" and not fake_db.ran("DELETE FROM employees")
    good = client.delete("/api/v1/admin/employees/EXODUS-5", headers=h, json={"purge": True, "confirm_code": "exodus-5"})
    assert good.status_code == 200
    assert fake_db.ran("DELETE FROM access_logs") and fake_db.ran("DELETE FROM employees")
    assert any("admin.employee_delete" in (p or ()) for _, p in fake_db.ran("INSERT INTO audit_logs"))


def test_delete_without_history_just_deletes(client, token_for, fake_db):
    fake_db.on("FROM employees WHERE employee_code", [{"id": 5}])
    fake_db.on("COUNT(*) AS n FROM access_logs", [{"n": 0}])
    assert client.delete("/api/v1/admin/employees/EXODUS-5", headers=bearer(token_for("SUPER_ADMIN"))).status_code == 200
    assert fake_db.ran("DELETE FROM employees") and not fake_db.ran("DELETE FROM access_logs")


def test_admin_edit_changes_only_sent_fields_and_can_clear_email(client, token_for, fake_db):
    fake_db.on("FROM employees WHERE employee_code", [{"id": 5}])
    r = client.patch("/api/v1/admin/employees/EXODUS-5", headers=bearer(token_for("SUPER_ADMIN")),
                     json={"full_name": "Jane Q Doe", "email": None})
    assert r.status_code == 200
    sql, params = fake_db.ran("UPDATE employees SET")[-1]
    assert "full_name=%s" in sql and "email=%s" in sql and "phone" not in sql and "employee_code=%s" in sql.split("WHERE")[1]
    assert params == ("Jane Q Doe", None, "EXODUS-5")


@pytest.mark.parametrize("body", [{}, {"full_name": None}, {"employee_code": "bad code"}, {"is_admin": True}])
def test_admin_edit_validation(client, token_for, body):
    r = client.patch("/api/v1/admin/employees/EXODUS-5", headers=bearer(token_for("SUPER_ADMIN")), json=body)
    assert r.status_code == 422


def test_admin_can_add_and_remove_vehicle(client, token_for, fake_db):
    h = bearer(token_for("SUPER_ADMIN"))
    fake_db.on("FROM employees WHERE employee_code", [{"id": 5}])
    assert client.post("/api/v1/admin/employees/EXODUS-5/vehicles", headers=h, json={"plate": "acx 1234"}).status_code == 201
    assert fake_db.ran("INSERT INTO vehicles")[-1][1][1] == "ACX 1234"
    fake_db.on("FROM vehicles WHERE id", [{"plate": "ACX 1234"}])
    assert client.delete("/api/v1/admin/vehicles/3", headers=h).status_code == 200
