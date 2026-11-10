"""login / refresh / logout. Refresh token lives in an HttpOnly cookie scoped to /api/v1/auth/refresh."""
from flask import Blueprint, current_app
from flask_jwt_extended import (create_access_token, create_refresh_token, get_jwt, get_jwt_identity,
                                jwt_required, set_refresh_cookies, unset_refresh_cookies)

import audit
import db
from auth import dummy_verify, hash_password, ph, verify_password
from errors import ApiError, ok
from extensions import limiter, login_limit
from flask_limiter.util import get_remote_address
from schemas import LoginIn, parse_json

bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


def _session_payload(user, access):
    return {"access_token": access, "token_type": "Bearer", "expires_in": 900,
            "username": user["username"], "role": user["role"]}


def _access_for(user):
    return create_access_token(identity=str(user["id"]),
                               additional_claims={"role": user["role"], "employee_id": user["employee_id"]})


@bp.post("/login")
@limiter.limit(login_limit, key_func=get_remote_address)
def login():
    body = parse_json(LoginIn)
    with db.get_cursor() as cur:
        cur.execute("SELECT id, username, password_hash, role, employee_id, is_active "
                    "FROM users WHERE username=%s", (body.username,))
        user = cur.fetchone()
    if user:
        valid = verify_password(user["password_hash"], body.password) and bool(user["is_active"])
    else:
        dummy_verify(body.password)
        valid = False
    if not valid:
        audit.log_event("auth.login", "denied", detail={"username": body.username[:64]})
        raise ApiError(401, "INVALID_CREDENTIALS", "Invalid username or password.")

    with db.get_cursor() as cur:
        cur.execute("UPDATE users SET last_login_at=NOW() WHERE id=%s", (user["id"],))
        if ph.check_needs_rehash(user["password_hash"]):
            cur.execute("UPDATE users SET password_hash=%s WHERE id=%s", (hash_password(body.password), user["id"]))
    resp = ok(_session_payload(user, _access_for(user)), "Login successful.")
    set_refresh_cookies(resp, create_refresh_token(identity=str(user["id"])),
                        max_age=int(current_app.config["JWT_REFRESH_TOKEN_EXPIRES"].total_seconds()))
    audit.log_event("auth.login", "success", user_id=user["id"], target_type="user", target_id=user["id"])
    return resp


@bp.post("/refresh")
@jwt_required(refresh=True, locations=["cookies"])
def refresh():
    uid = int(get_jwt_identity())
    with db.get_cursor() as cur:
        cur.execute("SELECT id, username, role, employee_id, is_active FROM users WHERE id=%s", (uid,))
        user = cur.fetchone()
    if not user or not user["is_active"]:
        raise ApiError(401, "INVALID_TOKEN", "Account is no longer active.")
    return ok(_session_payload(user, _access_for(user)), "Token refreshed.")


@bp.delete("/refresh")
@jwt_required(refresh=True, locations=["cookies"], optional=True)
def logout():
    """Logout shares the refresh path so the path-scoped cookie is actually sent; revokes its jti."""
    claims = get_jwt()
    uid = int(claims["sub"]) if claims.get("sub") else None
    if claims.get("jti"):
        with db.get_cursor() as cur:
            cur.execute("INSERT IGNORE INTO revoked_tokens (jti, expires_at) VALUES (%s, FROM_UNIXTIME(%s))",
                        (claims["jti"], claims["exp"]))
            cur.execute("DELETE FROM revoked_tokens WHERE expires_at < NOW()")
    resp = ok(message="Logged out.")
    unset_refresh_cookies(resp)
    audit.log_event("auth.logout", "success", user_id=uid)
    return resp
