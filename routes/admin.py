"""Dashboard data, audit logs, terminals, users, employee directory."""
import csv
import html
import io
import json
from datetime import date, datetime, timedelta

import pymysql
from flask import Blueprint, Response, current_app, g, request
from pydantic import TypeAdapter

import audit
import db
from auth import generate_api_key, hash_api_key, hash_password, roles_required
from errors import ApiError, ok
from extensions import limiter
from schemas import EmployeeCreate, EmployeeCode, EmployeeUpdate, PasswordReset, RoleChange, TerminalCreate, UserCreate, parse_json

bp = Blueprint("admin", __name__, url_prefix="/api/v1")
GATE_STAFF = ("SECURITY_GUARD", "GATE_OPERATOR")


def _dup(e):
    return isinstance(e, pymysql.err.IntegrityError) and e.args and e.args[0] == 1062


# ---- employees ------------------------------------------------------------------------------
@bp.get("/employees")
@roles_required(*GATE_STAFF)
def list_employees():
    with db.get_cursor() as cur:
        cur.execute("SELECT employee_code, full_name FROM employees WHERE is_active=1 ORDER BY full_name")
        return ok({"employees": cur.fetchall()})


@bp.post("/employees")
@roles_required("SECURITY_GUARD")
def create_employee():
    b = parse_json(EmployeeCreate)
    try:
        with db.get_cursor() as cur:
            cur.execute("INSERT INTO employees (employee_code, full_name, phone, email) VALUES (%s,%s,%s,%s)",
                        (b.employee_code, b.full_name, b.phone, b.email))
            emp_id = cur.lastrowid
            if b.vehicle:
                cur.execute("INSERT INTO vehicles (employee_id, plate, model, color) VALUES (%s,%s,%s,%s)",
                            (emp_id, b.vehicle.plate, b.vehicle.model, b.vehicle.color))
    except pymysql.err.IntegrityError as e:
        if _dup(e):
            raise ApiError(409, "EMPLOYEE_EXISTS", "An employee with this ID already exists.")
        raise
    audit.log_event("admin.employee_create", target_type="employee", target_id=b.employee_code)
    return ok({"employee_code": b.employee_code}, "Employee profile created.", 201)


@bp.patch("/employees/<code>")
@roles_required("SECURITY_GUARD")
def update_employee(code):
    code = TypeAdapter(EmployeeCode).validate_python(code)
    b = parse_json(EmployeeUpdate)
    with db.get_cursor() as cur:
        cur.execute("SELECT id FROM employees WHERE employee_code=%s", (code,))
        if not cur.fetchone():
            raise ApiError(404, "EMPLOYEE_NOT_FOUND", "Employee ID not recognized.")
        cur.execute("UPDATE employees SET email=COALESCE(%s, email), phone=COALESCE(%s, phone) "
                    "WHERE employee_code=%s", (b.email, b.phone, code))
    audit.log_event("admin.employee_update", target_type="employee", target_id=code,
                    detail={"email_set": b.email is not None, "phone_set": b.phone is not None})
    return ok(message="Employee updated.")


# ---- dashboard ------------------------------------------------------------------------------
@bp.get("/admin/dashboard")
@roles_required("SECURITY_GUARD")
def dashboard():
    with db.get_cursor() as cur:
        # Plates come from a correlated subquery, so multiple vehicles can no longer duplicate log rows.
        cur.execute("""
            SELECT a.id, e.employee_code, e.full_name,
                   (SELECT GROUP_CONCAT(v.plate ORDER BY v.id SEPARATOR ', ')
                      FROM vehicles v WHERE v.employee_id = e.id) AS plates,
                   a.check_in_time, a.check_out_time, a.method, a.check_out_method, a.arrival_type, t.gate_location
            FROM access_logs a
            JOIN employees e ON e.id = a.employee_id
            LEFT JOIN terminals t ON t.id = a.terminal_id
            WHERE a.check_in_time >= CURDATE() OR a.check_out_time >= CURDATE() OR a.check_out_time IS NULL
            ORDER BY a.id DESC LIMIT 500""")
        logs = cur.fetchall()
        cur.execute("""
            SELECT vt.id, v.full_name, v.national_id, vt.purpose, vt.arrival_type, vt.plate, vt.vehicle_model,
                   vt.vehicle_color, vt.status, vt.entry_time, vt.exit_time, vt.auto_closed, h.full_name AS host_name
            FROM visits vt JOIN visitors v ON v.id = vt.visitor_id LEFT JOIN employees h ON h.id = vt.host_id
            WHERE vt.created_at >= CURDATE() OR vt.status = 'on-site' OR vt.expected_date = CURDATE()
            ORDER BY vt.id DESC LIMIT 500""")
        visitors = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM access_logs WHERE check_out_time IS NULL")
        staff_on_site = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM visits WHERE status = 'on-site'")
        visitors_on_site = cur.fetchone()["n"]
        cur.execute("""
            SELECT e.full_name, v.plate, v.model, v.color,
                   EXISTS(SELECT 1 FROM access_logs a WHERE a.employee_id = e.id
                          AND a.check_out_time IS NULL AND a.arrival_type = 'vehicle') AS parked
            FROM vehicles v JOIN employees e ON e.id = v.employee_id ORDER BY e.full_name""")
        vehicles = cur.fetchall()
    return ok({"counts": {"staff_on_site": staff_on_site, "visitors_on_site": visitors_on_site},
               "access_logs": logs, "visitors": visitors, "vehicles": vehicles})


# ---- audit logs (SUPER_ADMIN only) ----------------------------------------------------------
@bp.get("/admin/audit-logs")
@roles_required()  # empty tuple: only SUPER_ADMIN passes
def audit_logs():
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
        before = int(request.args["before_id"]) if "before_id" in request.args else None
    except ValueError:
        raise ApiError(422, "VALIDATION_ERROR", "limit and before_id must be integers.")
    where, params = ["1=1"], []
    for col in ("action", "outcome"):
        if request.args.get(col):
            where.append(f"{col} = %s")
            params.append(request.args[col][:64])
    if before:
        where.append("id < %s")
        params.append(before)
    with db.get_cursor() as cur:
        cur.execute("SELECT id, created_at, actor_user_id, actor_role, terminal_id, action, outcome, target_type, "
                    "target_id, ip_address, detail FROM audit_logs WHERE " + " AND ".join(where) +
                    " ORDER BY id DESC LIMIT %s", (*params, limit))
        rows = cur.fetchall()
    for r in rows:
        if isinstance(r["detail"], (str, bytes)):
            r["detail"] = json.loads(r["detail"])
    return ok({"logs": rows, "next_before_id": rows[-1]["id"] if len(rows) == limit else None})


# ---- terminals ------------------------------------------------------------------------------
@bp.get("/admin/terminals")
@roles_required()
def list_terminals():
    with db.get_cursor() as cur:
        cur.execute("SELECT id, name, gate_location, is_active, created_at FROM terminals ORDER BY id")
        return ok({"terminals": cur.fetchall()})


@bp.post("/admin/terminals")
@roles_required()
def create_terminal():
    b = parse_json(TerminalCreate)
    key = generate_api_key()
    with db.get_cursor() as cur:
        cur.execute("INSERT INTO terminals (name, gate_location, api_key_hash) VALUES (%s,%s,%s)",
                    (b.name, b.gate_location, hash_api_key(key)))
        tid = cur.lastrowid
    audit.log_event("admin.terminal_create", target_type="terminal", target_id=tid)
    return ok({"id": tid, "name": b.name, "gate_location": b.gate_location, "api_key": key},
              "Store this key now; it is shown only once.", 201)


@bp.post("/admin/terminals/<int:tid>/rotate-key")
@roles_required()
def rotate_terminal_key(tid):
    key = generate_api_key()
    with db.get_cursor() as cur:
        cur.execute("UPDATE terminals SET api_key_hash=%s WHERE id=%s", (hash_api_key(key), tid))
        if cur.rowcount == 0:
            raise ApiError(404, "TERMINAL_NOT_FOUND", "Terminal not found.")
    audit.log_event("admin.terminal_rotate", target_type="terminal", target_id=tid)
    return ok({"id": tid, "api_key": key}, "Store this key now; it is shown only once.")


@bp.post("/admin/terminals/<int:tid>/deactivate")
@roles_required()
def deactivate_terminal(tid):
    with db.get_cursor() as cur:
        cur.execute("UPDATE terminals SET is_active=0 WHERE id=%s", (tid,))
        if cur.rowcount == 0:
            raise ApiError(404, "TERMINAL_NOT_FOUND", "Terminal not found.")
    audit.log_event("admin.terminal_deactivate", target_type="terminal", target_id=tid)
    return ok(message="Terminal deactivated.")


# ---- users ----------------------------------------------------------------------------------
@bp.get("/admin/users")
@roles_required()
def list_users():
    with db.get_cursor() as cur:
        cur.execute("""SELECT u.id, u.username, u.role, u.is_active, u.last_login_at,
                              e.employee_code, e.full_name AS employee_name
                       FROM users u LEFT JOIN employees e ON e.id = u.employee_id ORDER BY u.id""")
        return ok({"users": cur.fetchall()})


@bp.post("/admin/users")
@roles_required()
def create_user():
    b = parse_json(UserCreate)
    try:
        with db.get_cursor() as cur:
            emp_id = None
            if b.employee_code:
                cur.execute("SELECT id FROM employees WHERE employee_code=%s", (b.employee_code,))
                row = cur.fetchone()
                if not row:
                    raise ApiError(404, "EMPLOYEE_NOT_FOUND", "Employee ID not recognized.")
                emp_id = row["id"]
            cur.execute("INSERT INTO users (username, password_hash, role, employee_id) VALUES (%s,%s,%s,%s)",
                        (b.username, hash_password(b.password), b.role, emp_id))
            uid = cur.lastrowid
    except pymysql.err.IntegrityError as e:
        if _dup(e):
            raise ApiError(409, "USER_EXISTS", "Username already taken.")
        raise
    audit.log_event("admin.user_create", target_type="user", target_id=uid, detail={"role": b.role})
    return ok({"id": uid, "username": b.username, "role": b.role}, "User created.", 201)


def _no_self(uid, what):
    if uid == g.user_id:
        raise ApiError(409, "SELF_CHANGE", f"You cannot {what} your own account.")


def _user_exists(cur, uid):
    cur.execute("SELECT id FROM users WHERE id=%s", (uid,))
    if not cur.fetchone():
        raise ApiError(404, "USER_NOT_FOUND", "User not found.")


@bp.post("/admin/users/<int:uid>/role")
@roles_required()
def change_role(uid):
    b = parse_json(RoleChange)
    _no_self(uid, "change the role of")
    with db.get_cursor() as cur:
        _user_exists(cur, uid)
        emp_id = None
        if b.employee_code:
            cur.execute("SELECT id FROM employees WHERE employee_code=%s", (b.employee_code,))
            row = cur.fetchone()
            if not row:
                raise ApiError(404, "EMPLOYEE_NOT_FOUND", "Employee ID not recognized.")
            emp_id = row["id"]
        cur.execute("UPDATE users SET role=%s, employee_id=COALESCE(%s, employee_id) WHERE id=%s",
                    (b.role, emp_id, uid))
    audit.log_event("admin.user_role_change", target_type="user", target_id=uid, detail={"role": b.role})
    return ok(message="Role updated. It takes effect within 15 minutes or at the user's next sign-in.")


@bp.post("/admin/users/<int:uid>/reset-password")
@roles_required()
def reset_password(uid):
    b = parse_json(PasswordReset)
    with db.get_cursor() as cur:
        _user_exists(cur, uid)
        cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(b.password), uid))
    audit.log_event("admin.user_password_reset", target_type="user", target_id=uid)
    return ok(message="Password reset.")


@bp.post("/admin/users/<int:uid>/deactivate")
@roles_required()
def deactivate_user(uid):
    _no_self(uid, "deactivate")
    with db.get_cursor() as cur:
        _user_exists(cur, uid)
        cur.execute("UPDATE users SET is_active=0 WHERE id=%s", (uid,))
    audit.log_event("admin.user_deactivate", target_type="user", target_id=uid)
    return ok(message="User deactivated.")


@bp.post("/admin/users/<int:uid>/activate")
@roles_required()
def activate_user(uid):
    with db.get_cursor() as cur:
        _user_exists(cur, uid)
        cur.execute("UPDATE users SET is_active=1 WHERE id=%s", (uid,))
    audit.log_event("admin.user_activate", target_type="user", target_id=uid)
    return ok(message="User activated.")


# ---- CSV export (SUPER_ADMIN only) ----------------------------------------------------------
_ACCESS_SQL = """
    SELECT a.id, e.employee_code, e.full_name, a.check_in_time, a.check_out_time,
           TIMESTAMPDIFF(MINUTE, a.check_in_time, a.check_out_time) AS duration_minutes,
           a.method AS check_in_method, a.check_out_method, a.arrival_type,
           ti.gate_location AS gate_in, tc.gate_location AS gate_out, u.username AS recorded_by
    FROM access_logs a
    JOIN employees e ON e.id = a.employee_id
    LEFT JOIN terminals ti ON ti.id = a.terminal_id
    LEFT JOIN terminals tc ON tc.id = a.check_out_terminal_id
    LEFT JOIN users u ON u.id = a.recorded_by_user_id
    WHERE a.check_in_time >= %s AND a.check_in_time < %s
    ORDER BY a.check_in_time, a.id LIMIT %s"""

_VISITS_SQL = """
    SELECT vt.id, v.full_name AS visitor_name, v.phone, v.national_id,
           h.full_name AS host_name, h.employee_code AS host_code, vt.purpose, vt.status, vt.source,
           vt.badge_code, vt.arrival_type, vt.plate, vt.vehicle_model, vt.vehicle_color, vt.expected_date,
           vt.entry_time, vt.exit_time, TIMESTAMPDIFF(MINUTE, vt.entry_time, vt.exit_time) AS duration_minutes,
           vt.auto_closed, vt.created_at
    FROM visits vt JOIN visitors v ON v.id = vt.visitor_id LEFT JOIN employees h ON h.id = vt.host_id
    WHERE COALESCE(vt.entry_time, vt.created_at) >= %s AND COALESCE(vt.entry_time, vt.created_at) < %s
    ORDER BY COALESCE(vt.entry_time, vt.created_at), vt.id LIMIT %s"""


def _parse_day(text, name):
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ApiError(422, "VALIDATION_ERROR", f"{name} must be a date like 2026-11-30.")


def export_range():
    """?period=day|week|month&date=YYYY-MM-DD  (week = Mon-Sun)  or  ?start=YYYY-MM-DD&end=YYYY-MM-DD"""
    period = request.args.get("period")
    if period:
        anchor = _parse_day(request.args["date"], "date") if request.args.get("date") else date.today()
        if period == "day":
            start = end = anchor
        elif period == "week":
            start = anchor - timedelta(days=anchor.weekday())
            end = start + timedelta(days=6)
        elif period == "month":
            start = anchor.replace(day=1)
            end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        else:
            raise ApiError(422, "VALIDATION_ERROR", "period must be day, week or month.")
    else:
        start, end = _parse_day(request.args.get("start"), "start"), _parse_day(request.args.get("end"), "end")
    if end < start:
        raise ApiError(422, "VALIDATION_ERROR", "end must not be before start.")
    if (end - start).days + 1 > current_app.config["EXPORT_MAX_DAYS"]:
        raise ApiError(422, "RANGE_TOO_LARGE", f"Choose at most {current_app.config['EXPORT_MAX_DAYS']} days.")
    return start, end


def csv_cell(v):
    """Unescape stored HTML entities and neutralise spreadsheet formulas (=, +, -, @) in text."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, str):
        v = html.unescape(v)
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
    return str(v)


@bp.get("/admin/export/<kind>.csv")
@limiter.limit("10 per minute")
@roles_required()
def export_csv(kind):
    sql = {"access-logs": _ACCESS_SQL, "visits": _VISITS_SQL}.get(kind)
    if sql is None:
        raise ApiError(404, "NOT_FOUND", "Unknown export type.")
    start, end = export_range()
    cap = current_app.config["EXPORT_MAX_ROWS"]
    with db.get_cursor() as cur:
        cur.execute(sql, (start, end + timedelta(days=1), cap + 1))
        rows = cur.fetchall()
    if len(rows) > cap:
        raise ApiError(422, "RANGE_TOO_LARGE", f"More than {cap} rows; choose a shorter range.")
    out = io.StringIO()
    out.write("\ufeff")  # BOM so Excel opens UTF-8 names correctly
    w = csv.writer(out)
    if rows:
        w.writerow(rows[0].keys())
    else:
        w.writerow(["no rows in this range"])
    for r in rows:
        w.writerow([csv_cell(v) for v in r.values()])
    audit.log_event("admin.export", target_type=kind, detail={"start": str(start), "end": str(end), "rows": len(rows)})
    name = f"{kind.replace('-', '_')}_{start}_to_{end}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
