"""Shared extension instances: rate limiter, JWT manager, Talisman (security headers)."""
from flask import current_app
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman

# Keyed by client IP only. Keying by a client-supplied header would let an attacker
# get a fresh bucket per request just by rotating the header value.
limiter = Limiter(key_func=get_remote_address)
jwt = JWTManager()
talisman = Talisman()


def login_limit():
    return current_app.config["LOGIN_LIMIT"]


def scan_limit():
    return current_app.config["SCAN_LIMIT"]


CSP = {
    "default-src": "'self'",
    # Tailwind's browser build is loaded from jsDelivr and injects <style> at runtime.
    "script-src": ["'self'", "https://cdn.jsdelivr.net"],
    "style-src": ["'self'", "'unsafe-inline'"],
    "img-src": ["'self'", "data:"],  # data: for the server-generated badge QR
    "connect-src": "'self'",
    "object-src": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
    "frame-ancestors": "'none'",
}


def init_extensions(app):
    # Order matters: the limiter's before_request must be registered BEFORE the auth hook,
    # so floods are cut off before any database lookup happens.
    limiter.init_app(app)
    jwt.init_app(app)
    talisman.init_app(
        app,
        force_https=app.config["FORCE_HTTPS"],
        strict_transport_security=True,
        strict_transport_security_max_age=31536000,
        frame_options="DENY",
        referrer_policy="strict-origin-when-cross-origin",
        content_security_policy=CSP,
        x_content_type_options=True,
        session_cookie_secure=app.config["JWT_COOKIE_SECURE"],
    )
