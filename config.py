"""Environment-driven configuration. No secrets live in code."""
import os
from datetime import timedelta

from dotenv import load_dotenv

load_dotenv()

REQUIRED = ("SECRET_KEY", "JWT_SECRET_KEY", "DB_PASSWORD")


def _hhmm(value):
    import re
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise RuntimeError("AUTO_CHECKOUT_TIME must look like 23:00 (24-hour HH:MM)")
    return value + ":00"


def _bool(name, default):
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in ("1", "true", "yes", "on")


def load_config():
    missing = [k for k in REQUIRED if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing) + " (see .env.example)"
        )
    e = os.environ.get
    production = e("APP_ENV", "production") == "production"
    return {
        "APP_ENV": e("APP_ENV", "production"),
        "SECRET_KEY": e("SECRET_KEY"),
        # --- database
        "DB_HOST": e("DB_HOST", "localhost"),
        "DB_PORT": int(e("DB_PORT", "3306")),
        "DB_USER": e("DB_USER", "exodus_app"),
        "DB_PASSWORD": e("DB_PASSWORD"),
        "DB_NAME": e("DB_NAME", "company_attendance"),
        "DB_POOL_MAX": int(e("DB_POOL_MAX", "10")),
        # --- JWT: 15 min access token, refresh token in an HttpOnly cookie scoped to /refresh
        "JWT_SECRET_KEY": e("JWT_SECRET_KEY"),
        "JWT_ACCESS_TOKEN_EXPIRES": timedelta(minutes=15),
        "JWT_REFRESH_TOKEN_EXPIRES": timedelta(hours=int(e("REFRESH_TOKEN_HOURS", "12"))),
        "JWT_TOKEN_LOCATION": ["headers", "cookies"],
        "JWT_COOKIE_SECURE": _bool("COOKIE_SECURE", production),
        "JWT_COOKIE_SAMESITE": "Strict",
        "JWT_REFRESH_COOKIE_PATH": "/api/v1/auth/refresh",
        "JWT_COOKIE_CSRF_PROTECT": False,  # SameSite=Strict + path scoping; see README threat model
        # --- request limits
        "MAX_CONTENT_LENGTH": 5 * 1024 * 1024,
        # --- rate limiting (use redis:// storage when running several workers)
        "RATELIMIT_ENABLED": True,
        "RATELIMIT_DEFAULT": e("RATE_LIMIT_DEFAULT", "120 per minute"),
        "RATELIMIT_STORAGE_URI": e("RATELIMIT_STORAGE_URI", "memory://"),
        "RATELIMIT_HEADERS_ENABLED": True,
        "LOGIN_LIMIT": e("LOGIN_LIMIT", "5 per minute"),
        "SCAN_LIMIT": e("SCAN_LIMIT", "30 per minute"),
        # --- transport / proxy
        "FORCE_HTTPS": _bool("FORCE_HTTPS", production),
        "TRUSTED_PROXIES": int(e("TRUSTED_PROXIES", "0")),
        # --- business rules
        "RESUBMIT_WINDOW_SECONDS": int(e("RESUBMIT_WINDOW_SECONDS", "5")),
        "WALKIN_REQUIRES_APPROVAL": _bool("WALKIN_REQUIRES_APPROVAL", False),
        # --- automatic check-out of anyone still on site (employees + visitors)
        "AUTO_CHECKOUT_ENABLED": _bool("AUTO_CHECKOUT_ENABLED", True),
        "AUTO_CHECKOUT_TIME": _hhmm(e("AUTO_CHECKOUT_TIME", "23:00")),
        "SCHEDULER_INTERVAL_SECONDS": int(e("SCHEDULER_INTERVAL_SECONDS", "60")),
        # --- CSV export limits
        "EXPORT_MAX_ROWS": 100000,
        "EXPORT_MAX_DAYS": 366,
        # --- notifier: log | smtp | webhook
        "NOTIFIER_BACKEND": e("NOTIFIER_BACKEND", "log"),
        "SMTP_HOST": e("SMTP_HOST", ""),
        "SMTP_PORT": int(e("SMTP_PORT", "587")),
        "SMTP_USER": e("SMTP_USER", ""),
        "SMTP_PASSWORD": e("SMTP_PASSWORD", ""),
        "SMTP_STARTTLS": _bool("SMTP_STARTTLS", True),
        "SMTP_SSL": _bool("SMTP_SSL", False),          # true for port 465
        "NOTIFY_ASYNC": _bool("NOTIFY_ASYNC", True),   # send emails in the background
        "SMTP_FROM": e("SMTP_FROM", "reception@example.com"),
        "WEBHOOK_URL": e("WEBHOOK_URL", ""),
        "WEBHOOK_SECRET": e("WEBHOOK_SECRET", ""),
    }
