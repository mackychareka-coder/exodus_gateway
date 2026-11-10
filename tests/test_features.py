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
