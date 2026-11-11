"""Employee access with anti-passback enforced inside a locking transaction."""
import pymysql
from flask import Blueprint, current_app, g

import audit
import db
from auth import roles_required
from errors import ApiError, ok
from extensions import limiter, scan_limit
from schemas import EmployeeScan, parse_json

bp = Blueprint("checkin", __name__, url_prefix="/api/v1/access")
SCAN_ROLES = ("GATE_HARDWARE_CLIENT", "GATE_OPERATOR", "SECURITY_GUARD")


def _method(body):
    """Hardware can only push QR/NFC scans; humans are always recorded as MANUAL."""
    if g.user_role == "GATE_HARDWARE_CLIENT":
        if body.method == "MANUAL":
            raise ApiError(403, "MANUAL_NOT_ALLOWED", "Hardware clients cannot record manual entries.")
        return body.method or "QR"
    return "MANUAL"


def _lock_employee(cur, code, allow_inactive=False):
    # Row lock on the employee serialises concurrent scans for the same person.
    cur.execute("SELECT id, full_name, is_active FROM employees WHERE employee_code=%s FOR UPDATE", (code,))
    emp = cur.fetchone()
    if not emp:
        raise ApiError(404, "EMPLOYEE_NOT_FOUND", "Employee ID not recognized.")
    if not emp["is_active"] and not allow_inactive:  # a deactivated employee on site must still be able to leave
        raise ApiError(403, "EMPLOYEE_INACTIVE", "Employee account is inactive.")
    return emp


def _last_log(cur, emp_id):
    cur.execute(
        """SELECT id, check_out_time,
                  TIMESTAMPDIFF(SECOND, COALESCE(check_out_time, check_in_time), NOW()) AS age
           FROM access_logs WHERE employee_id=%s
           ORDER BY check_in_time DESC, id DESC LIMIT 1 FOR UPDATE""", (emp_id,))
    last = cur.fetchone()
    if last and last["age"] is not None and last["age"] < current_app.config["RESUBMIT_WINDOW_SECONDS"]:
        raise ApiError(409, "DUPLICATE_SCAN", "Duplicate scan ignored; please wait a few seconds.")
    return last


def _now(cur):
    cur.execute("SELECT NOW() AS now")
    return cur.fetchone()["now"]


def _check_in(body, method):
    try:
        with db.get_cursor() as cur:
            emp = _lock_employee(cur, body.employee_code)
            last = _last_log(cur, emp["id"])
            if last and last["check_out_time"] is None:
                raise ApiError(409, "ALREADY_CHECKED_IN", "Already checked in. Check out before entering again.")
            arrival = body.arrival_type
            if arrival is None:
                cur.execute("SELECT 1 AS hit FROM vehicles WHERE employee_id=%s LIMIT 1", (emp["id"],))
                arrival = "vehicle" if cur.fetchone() else "walk_in"
            cur.execute(
                """INSERT INTO access_logs (employee_id, check_in_time, terminal_id, method, arrival_type,
                                            recorded_by_user_id)
                   VALUES (%s, NOW(), %s, %s, %s, %s)""",
                (emp["id"], g.terminal_id, method, arrival, g.user_id))
            return {"employee_code": body.employee_code, "name": emp["full_name"], "action": "check_in",
                    "method": method, "arrival_type": arrival, "time": _now(cur)}
    except pymysql.err.IntegrityError:  # DB-level one-open-row backstop fired
        raise ApiError(409, "ALREADY_CHECKED_IN", "Already checked in. Check out before entering again.")


def _check_out(body, method):
    with db.get_cursor() as cur:
        emp = _lock_employee(cur, body.employee_code, allow_inactive=True)
        last = _last_log(cur, emp["id"])
        if not last or last["check_out_time"] is not None:
            raise ApiError(409, "NOT_CHECKED_IN", "No open check-in found for this employee.")
        cur.execute(
            """UPDATE access_logs SET check_out_time=NOW(), check_out_terminal_id=%s, check_out_method=%s
               WHERE id=%s AND check_out_time IS NULL""", (g.terminal_id, method, last["id"]))
        return {"employee_code": body.employee_code, "name": emp["full_name"], "action": "check_out",
                "method": method, "time": _now(cur)}


def _handle(action, fn, message):
    body = parse_json(EmployeeScan)
    method = _method(body)
    try:
        result = fn(body, method)
    except ApiError as e:
        audit.log_event(action, "denied", target_type="employee", target_id=body.employee_code,
                        detail={"code": e.code, "method": method})
        raise
    audit.log_event(action, "success", target_type="employee", target_id=body.employee_code,
                    detail={"method": method, "gate": g.gate_location})
    return ok(result, message, 201 if action == "access.check_in" else 200)


@bp.post("/employee/check-in")
@limiter.limit(scan_limit)
@roles_required(*SCAN_ROLES)
def check_in():
    return _handle("access.check_in", _check_in, "Checked in.")


@bp.post("/employee/check-out")
@limiter.limit(scan_limit)
@roles_required(*SCAN_ROLES)
def check_out():
    return _handle("access.check_out", _check_out, "Checked out.")
