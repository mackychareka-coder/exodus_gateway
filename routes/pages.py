"""Server-rendered page shells. They contain no data: each page's JS obtains an access token via the
refresh cookie and calls /api/v1/... Unauthenticated visitors are bounced to /login client-side,
and every data endpoint is independently protected server-side."""
from flask import Blueprint, redirect, render_template, url_for

bp = Blueprint("pages", __name__)


@bp.get("/")
def index():
    return redirect(url_for("pages.login_page"))


@bp.get("/login")
def login_page():
    return render_template("login.html")


@bp.get("/dashboard")
def dashboard():
    return render_template("index.html")


@bp.get("/scanner")
def scanner():
    return render_template("scanner.html")


@bp.get("/scanner/staff-checkin")
def staff_checkin():
    return render_template("kiosk_staff.html")


@bp.get("/scanner/visitor-register")
def visitor_register():
    return render_template("kiosk_visitor_reg.html")


@bp.get("/scanner/visitor-checkout")
def visitor_checkout():
    return render_template("kiosk_visitor_out.html")


@bp.get("/scanner/add-employee")
def add_employee():
    return render_template("kiosk_add_employee.html")


@bp.get("/admin/users")
def admin_users():
    return render_template("admin_users.html")


@bp.get("/admin/exports")
def admin_exports():
    return render_template("admin_export.html")
