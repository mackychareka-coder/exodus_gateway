"""Dashboard data, audit logs, terminals, users, employee directory."""
import json

import pymysql
from flask import Blueprint, request

import audit
import db
from auth import generate_api_key, hash_api_key, hash_password, roles_required
from errors import ApiError, ok
from schemas import EmployeeCreate, TerminalCreate, UserCreate, parse_json

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
                   a.check_in_time, a.check_out_time, a.method, a.arrival_type, t.gate_location
            FROM access_logs a
            JOIN employees e ON e.id = a.employee_id
            LEFT JOIN terminals t ON t.id = a.terminal_id
            WHERE a.check_in_time >= CURDATE() OR a.check_out_time >= CURDATE() OR a.check_out_time IS NULL
            ORDER BY a.id DESC LIMIT 500""")
        logs = cur.fetchall()
        cur.execute("""
            SELECT vt.id, v.full_name, v.national_id, vt.purpose, vt.arrival_type, vt.plate, vt.vehicle_model,
                   vt.vehicle_color, vt.status, vt.entry_time, vt.exit_time, h.full_name AS host_name
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
        cur.execute("SELECT id, username, role, employee_id, is_active, last_login_at FROM users ORDER BY id")
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


@bp.post("/admin/users/<int:uid>/deactivate")
@roles_required()
def deactivate_user(uid):
    with db.get_cursor() as cur:
        cur.execute("UPDATE users SET is_active=0 WHERE id=%s", (uid,))
        if cur.rowcount == 0:
            raise ApiError(404, "USER_NOT_FOUND", "User not found.")
    audit.log_event("admin.user_deactivate", target_type="user", target_id=uid)
    return ok(message="User deactivated.")
