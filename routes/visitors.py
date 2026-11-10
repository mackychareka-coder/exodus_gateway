"""Visitor workflow: pre-registration, walk-ins, badges (QR), check-in/out, approval, search.

Status machine:  pre-registered --approve--> approved --check-in--> on-site --check-out--> departed
                 pre-registered --deny--> denied
Host pre-registrations start as `approved` (the authenticated host vouches). Walk-ins start as
`pre-registered` only when WALKIN_REQUIRES_APPROVAL is on; otherwise they are checked in immediately.
"""
import base64
import io
import secrets

import pymysql
import qrcode
import qrcode.image.svg
from flask import Blueprint, current_app, g, request

import audit
import db
import notifier
from auth import roles_required
from errors import ApiError, ok
from extensions import limiter, scan_limit
from schemas import PreRegister, VisitRef, WalkIn, parse_json

bp = Blueprint("visitors", __name__, url_prefix="/api/v1/visitors")
GATE_STAFF = ("SECURITY_GUARD", "GATE_OPERATOR")
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L

_VISIT_SQL = """
SELECT vt.id, vt.badge_code, vt.status, vt.source, vt.purpose, vt.arrival_type, vt.plate,
       vt.vehicle_model, vt.vehicle_color, vt.expected_date, vt.entry_time, vt.exit_time, vt.created_at,
       vt.host_id, (vt.expected_date IS NULL OR vt.expected_date = CURDATE()) AS date_ok,
       v.full_name AS visitor_name, v.phone, v.national_id,
       h.full_name AS host_name, h.employee_code AS host_code, h.email AS host_email
FROM visits vt JOIN visitors v ON v.id = vt.visitor_id LEFT JOIN employees h ON h.id = vt.host_id"""


def _badge():
    return "".join(secrets.choice(_ALPHABET) for _ in range(10))


def qr_data_uri(text):
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def _public(row, strip_pii=False):
    row = dict(row)
    for k in ("host_email", "date_ok", "host_id"):
        row.pop(k, None)
    if strip_pii:
        row.pop("national_id", None)
        row.pop("phone", None)
    return row


def _with_badge(row):
    out = _public(row)
    out["badge_qr"] = qr_data_uri(row["badge_code"])
    return out


def _visit_by(cur, *, badge=None, visit_id=None, lock=False):
    sql = _VISIT_SQL + (" WHERE vt.badge_code=%s" if badge else " WHERE vt.id=%s")
    if lock:
        sql += " FOR UPDATE OF vt"  # lock the visit only, not the host's employee row (MySQL 8)
    cur.execute(sql, (badge or visit_id,))
    return cur.fetchone()


def _load(cur, ref, lock=False):
    row = _visit_by(cur, badge=ref.badge_code, visit_id=ref.visit_id, lock=lock)
    if not row:
        raise ApiError(404, "VISIT_NOT_FOUND", "Visit not found.")
    return row


def _resolve_host(cur, code):
    if g.user_role == "EMPLOYEE":
        if not g.employee_id:
            raise ApiError(403, "NO_EMPLOYEE_PROFILE", "Your account is not linked to an employee profile.")
        cur.execute("SELECT id, employee_code, full_name, email FROM employees WHERE id=%s AND is_active=1",
                    (g.employee_id,))
    else:
        if not code:
            raise ApiError(422, "HOST_REQUIRED", "host_employee_code is required.")
        cur.execute("SELECT id, employee_code, full_name, email FROM employees "
                    "WHERE employee_code=%s AND is_active=1", (code,))
    host = cur.fetchone()
    if not host:
        raise ApiError(404, "HOST_NOT_FOUND", "Host employee not found.")
    if g.user_role == "EMPLOYEE" and code and code != host["employee_code"]:
        raise ApiError(403, "FORBIDDEN", "Employees can only pre-register their own visitors.")
    return host


def _create_visit(cur, d, host, source, status):
    cur.execute("SELECT id FROM visitors WHERE national_id=%s ORDER BY id DESC LIMIT 1", (d.national_id,))
    row = cur.fetchone()
    if row:
        visitor_id = row["id"]
        cur.execute("UPDATE visitors SET full_name=%s, phone=%s WHERE id=%s", (d.full_name, d.phone, visitor_id))
    else:
        cur.execute("INSERT INTO visitors (full_name, phone, national_id) VALUES (%s,%s,%s)",
                    (d.full_name, d.phone, d.national_id))
        visitor_id = cur.lastrowid
    for _ in range(5):
        try:
            cur.execute(
                """INSERT INTO visits (visitor_id, host_id, purpose, status, source, badge_code, arrival_type,
                       plate, vehicle_model, vehicle_color, expected_date, registered_by_user_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (visitor_id, host["id"], d.purpose, status, source, _badge(), d.arrival_type, d.plate,
                 d.vehicle_model, d.vehicle_color, getattr(d, "expected_date", None), g.user_id))
            break
        except pymysql.err.IntegrityError:  # badge collision: retry with a new code
            continue
    else:
        raise ApiError(500, "BADGE_GENERATION_FAILED", "Could not generate a unique badge code.")
    return _visit_by(cur, visit_id=cur.lastrowid)


def _mark_on_site(cur, visit_id):
    cur.execute("UPDATE visits SET status='on-site', entry_time=NOW(), entry_terminal_id=%s WHERE id=%s",
                (g.terminal_id, visit_id))


def _transition(action, ref, mutate):
    """Lock the visit row, apply `mutate`, return the fresh row; audit denials outside the rolled-back txn."""
    try:
        with db.get_cursor() as cur:
            visit = _load(cur, ref, lock=True)
            mutate(cur, visit)
            return _visit_by(cur, visit_id=visit["id"])
    except ApiError as e:
        audit.log_event(action, "denied", target_type="visit", target_id=ref.badge_code or ref.visit_id,
                        detail={"code": e.code})
        raise


# ---- creation -------------------------------------------------------------------------------
@bp.post("/pre-register")
@roles_required("EMPLOYEE")
def pre_register():
    body = parse_json(PreRegister)
    with db.get_cursor() as cur:
        host = _resolve_host(cur, body.host_employee_code)
        visit = _create_visit(cur, body, host, "PRE_REG", "approved")
    audit.log_event("visitor.pre_register", target_type="visit", target_id=visit["id"],
                    detail={"host": host["employee_code"]})
    return ok(_with_badge(visit), "Visitor pre-registered.", 201)


@bp.post("/walk-in")
@roles_required(*GATE_STAFF)
def walk_in():
    body = parse_json(WalkIn)
    needs_approval = current_app.config["WALKIN_REQUIRES_APPROVAL"]
    with db.get_cursor() as cur:
        host = _resolve_host(cur, body.host_employee_code)
        visit = _create_visit(cur, body, host, "WALK_IN", "pre-registered" if needs_approval else "approved")
        if not needs_approval:
            _mark_on_site(cur, visit["id"])
            visit = _visit_by(cur, visit_id=visit["id"])
    audit.log_event("visitor.walk_in", target_type="visit", target_id=visit["id"],
                    detail={"host": host["employee_code"], "status": visit["status"]})
    notifier.notify("visitor.approval_requested" if needs_approval else "visitor.arrived", visit)
    msg = "Walk-in registered; awaiting host approval." if needs_approval else "Walk-in checked in."
    return ok(_with_badge(visit), msg, 201)


# ---- gate transitions -----------------------------------------------------------------------
def _do_check_in(cur, v):
    st = v["status"]
    if st == "on-site":
        raise ApiError(409, "ALREADY_ON_SITE", "Visitor is already on site.")
    if st == "departed":
        raise ApiError(409, "VISIT_CLOSED", "This visit has already ended.")
    if st == "denied":
        raise ApiError(403, "VISIT_DENIED", "This visit was denied.")
    if st == "pre-registered":
        raise ApiError(409, "APPROVAL_PENDING", "Waiting for host approval.")
    if not v["date_ok"]:
        raise ApiError(409, "WRONG_DATE", "This visit is not expected today.")
    _mark_on_site(cur, v["id"])


def _do_check_out(cur, v):
    if v["status"] != "on-site":
        raise ApiError(409, "NOT_ON_SITE", "Visitor is not currently on site.")
    cur.execute("UPDATE visits SET status='departed', exit_time=NOW(), exit_terminal_id=%s "
                "WHERE id=%s AND status='on-site'", (g.terminal_id, v["id"]))


@bp.post("/check-in")
@limiter.limit(scan_limit)
@roles_required("GATE_HARDWARE_CLIENT", *GATE_STAFF)
def check_in():
    ref = parse_json(VisitRef)
    visit = _transition("visitor.check_in", ref, _do_check_in)
    audit.log_event("visitor.check_in", target_type="visit", target_id=visit["id"])
    notifier.notify("visitor.arrived", visit)
    return ok(_public(visit), "Visitor checked in.")


@bp.post("/check-out")
@limiter.limit(scan_limit)
@roles_required("GATE_HARDWARE_CLIENT", *GATE_STAFF)
def check_out():
    ref = parse_json(VisitRef)
    visit = _transition("visitor.check_out", ref, _do_check_out)
    audit.log_event("visitor.check_out", target_type="visit", target_id=visit["id"])
    return ok(_public(visit), "Visitor checked out.")


def _decide(visit_id, new_status, action):
    def mutate(cur, v):
        if g.user_role == "EMPLOYEE" and v["host_id"] != g.employee_id:
            raise ApiError(403, "FORBIDDEN", "Only the host can decide on this visit.")
        if v["status"] != "pre-registered":
            raise ApiError(409, "NOT_PENDING", "This visit is not awaiting approval.")
        cur.execute("UPDATE visits SET status=%s, approved_by_user_id=%s WHERE id=%s",
                    (new_status, g.user_id, v["id"]))
    visit = _transition(action, VisitRef(visit_id=visit_id), mutate)
    audit.log_event(action, target_type="visit", target_id=visit["id"])
    return ok(_public(visit), f"Visit {new_status}.")


@bp.post("/<int:visit_id>/approve")
@roles_required("EMPLOYEE", "SECURITY_GUARD")
def approve(visit_id):
    return _decide(visit_id, "approved", "visitor.approve")


@bp.post("/<int:visit_id>/deny")
@roles_required("EMPLOYEE", "SECURITY_GUARD")
def deny(visit_id):
    return _decide(visit_id, "denied", "visitor.deny")


# ---- queries --------------------------------------------------------------------------------
def _like(q):
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _list(where, params, strip_pii=False):
    with db.get_cursor() as cur:
        cur.execute(_VISIT_SQL + " WHERE " + where + " ORDER BY vt.id DESC LIMIT 200", params)
        return [_public(r, strip_pii) for r in cur.fetchall()]


def _search(base_where):
    q = (request.args.get("q") or "").strip()[:64]
    where, params = base_where, []
    if q:
        where += " AND (v.full_name LIKE %s OR vt.badge_code = %s OR vt.plate LIKE %s)"
        params = [_like(q), q.upper(), _like(q)]
    return _list(where, tuple(params))


@bp.get("/on-site")
@roles_required(*GATE_STAFF)
def on_site():
    return ok({"visitors": _search("vt.status='on-site'")})


@bp.get("/pending")
@roles_required(*GATE_STAFF)
def pending():
    return ok({"visitors": _search("vt.status IN ('pre-registered','approved') "
                                   "AND (vt.expected_date IS NULL OR vt.expected_date = CURDATE())")})


@bp.get("/mine")
@roles_required("EMPLOYEE")
def mine():
    if not g.employee_id:
        return ok({"visitors": []})
    rows = _list("vt.host_id=%s AND vt.created_at >= NOW() - INTERVAL 7 DAY", (g.employee_id,), strip_pii=True)
    return ok({"visitors": rows})


@bp.get("/<int:visit_id>/badge")
@roles_required("EMPLOYEE", *GATE_STAFF)
def badge(visit_id):
    with db.get_cursor() as cur:
        v = _visit_by(cur, visit_id=visit_id)
    if not v:
        raise ApiError(404, "VISIT_NOT_FOUND", "Visit not found.")
    if g.user_role == "EMPLOYEE" and v["host_id"] != g.employee_id:
        raise ApiError(403, "FORBIDDEN", "Not your visitor.")
    return ok({"badge_code": v["badge_code"], "badge_qr": qr_data_uri(v["badge_code"])})
