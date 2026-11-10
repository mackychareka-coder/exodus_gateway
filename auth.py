"""Authentication: Argon2 passwords, JWT users, hashed terminal API keys, RBAC."""
import hashlib
import hmac
import secrets
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import g, request
from flask_jwt_extended import get_jwt, verify_jwt_in_request

import audit
import db
from errors import ApiError, error_response
from extensions import jwt

USER_ROLES = ("SUPER_ADMIN", "SECURITY_GUARD", "GATE_OPERATOR", "EMPLOYEE")
HARDWARE_ROLE = "GATE_HARDWARE_CLIENT"
ALL_ROLES = USER_ROLES + (HARDWARE_ROLE,)

# Endpoints reachable without credentials. Page routes (blueprint "pages") only serve static
# HTML shells; all data is fetched through the authenticated /api/v1 endpoints.
# /auth/refresh does its own refresh-cookie verification via @jwt_required(refresh=True).
PUBLIC_ENDPOINTS = {"health", "static", "auth.login", "auth.refresh", "auth.logout"}

ph = PasswordHasher()
_DUMMY_HASH = ph.hash("timing-equaliser-not-a-real-password")


def hash_password(password):
    return ph.hash(password)


def verify_password(stored_hash, password):
    try:
        return ph.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def dummy_verify(password):
    """Burn the same CPU as a real check so unknown usernames are not distinguishable by timing."""
    verify_password(_DUMMY_HASH, password)


def generate_api_key():
    return "gk_" + secrets.token_urlsafe(32)


def hash_api_key(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


# ---- JWT error responses (consistent JSON format) -------------------------------------------
@jwt.unauthorized_loader
def _missing(reason):
    return error_response(401, "AUTH_REQUIRED", "Authentication required.")


@jwt.invalid_token_loader
def _invalid(reason):
    return error_response(401, "INVALID_TOKEN", "Invalid authentication token.")


@jwt.expired_token_loader
def _expired(header, payload):
    return error_response(401, "TOKEN_EXPIRED", "Token expired.")


@jwt.revoked_token_loader
def _revoked(header, payload):
    return error_response(401, "TOKEN_REVOKED", "Token has been revoked.")


@jwt.token_in_blocklist_loader
def _is_revoked(header, payload):
    if payload.get("type") != "refresh":  # access tokens are short-lived; skip the DB hit
        return False
    with db.get_cursor() as cur:
        cur.execute("SELECT 1 AS hit FROM revoked_tokens WHERE jti=%s", (payload["jti"],))
        return cur.fetchone() is not None


# ---- before_request -------------------------------------------------------------------------
def authenticate():
    g.user_id = g.user_role = g.terminal_id = g.gate_location = g.employee_id = None
    ep = request.endpoint
    if ep is None or ep in PUBLIC_ENDPOINTS or ep.startswith("pages."):
        return
    api_key = request.headers.get("X-Gate-API-Key")
    if api_key:
        return _auth_terminal(api_key)
    verify_jwt_in_request(locations=["headers"])  # access tokens only; raises -> JSON 401
    claims = get_jwt()
    role = claims.get("role")
    if role not in USER_ROLES:
        raise ApiError(401, "INVALID_TOKEN", "Invalid authentication token.")
    g.user_id, g.user_role, g.employee_id = int(claims["sub"]), role, claims.get("employee_id")


def _auth_terminal(api_key):
    digest = hash_api_key(api_key) if len(api_key) <= 128 else None
    row = None
    if digest:
        with db.get_cursor() as cur:
            cur.execute(
                "SELECT id, gate_location, api_key_hash FROM terminals WHERE api_key_hash=%s AND is_active=1",
                (digest,),
            )
            row = cur.fetchone()
    if not row or not hmac.compare_digest(row["api_key_hash"], digest):
        audit.log_event("terminal.invalid_key", "denied")  # the key itself is never logged
        raise ApiError(401, "INVALID_API_KEY", "Invalid or inactive terminal key.")
    g.user_role, g.terminal_id, g.gate_location = HARDWARE_ROLE, row["id"], row["gate_location"]


def roles_required(*roles):
    """SUPER_ADMIN passes every user-role check; terminals only pass if explicitly listed."""
    allowed = set(roles)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            role = g.get("user_role")
            if role is None:
                raise ApiError(401, "AUTH_REQUIRED", "Authentication required.")
            if role not in allowed and role != "SUPER_ADMIN":
                audit.log_event("rbac.denied", "denied", detail={"endpoint": request.endpoint, "role": role})
                raise ApiError(403, "FORBIDDEN", "Your role is not permitted to perform this action.")
            return fn(*args, **kwargs)
        return wrapper
    return decorator
