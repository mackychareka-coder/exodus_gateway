import os
from contextlib import contextmanager
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-0123456789abcdef0123456789")
os.environ.setdefault("DB_PASSWORD", "unused")
os.environ["APP_ENV"] = "testing"

import pytest  # noqa: E402
from flask_jwt_extended import create_access_token  # noqa: E402

import db as dbmod  # noqa: E402
from app import create_app  # noqa: E402
from extensions import limiter  # noqa: E402


class FakeCursor:
    def __init__(self, fake):
        self.fake, self._rows, self.rowcount, self.lastrowid = fake, [], 1, 1

    def execute(self, sql, params=None):
        self.fake.executed.append((" ".join(sql.split()), params))
        self._rows = []
        for needle, rows in self.fake.handlers:
            if needle in sql:
                self._rows = list(rows(params) if callable(rows) else rows)
                break

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeDB:
    """Scripted stand-in for db.get_cursor: first handler whose needle appears in the SQL wins."""
    def __init__(self):
        self.handlers = [("SELECT NOW()", [{"now": datetime(2026, 1, 1, 9, 0)}])]
        self.executed = []

    def on(self, needle, rows):
        self.handlers.insert(0, (needle, rows))

    @contextmanager
    def cursor(self):
        yield FakeCursor(self)

    def ran(self, needle):
        return [e for e in self.executed if needle in e[0]]


@pytest.fixture
def fake_db(monkeypatch):
    f = FakeDB()
    monkeypatch.setattr(dbmod, "get_cursor", f.cursor)
    return f


@pytest.fixture
def make_app(fake_db):
    def factory(**overrides):
        cfg = {"TESTING": True, "RATELIMIT_ENABLED": False, "FORCE_HTTPS": False,
               "JWT_COOKIE_SECURE": False, "RESUBMIT_WINDOW_SECONDS": 5}
        cfg.update(overrides)
        app = create_app(cfg)
        limiter.reset()
        return app
    return factory


@pytest.fixture
def app(make_app):
    return make_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def token_for(app):
    def make(role, user_id=1, employee_id=None):
        with app.app_context():
            return create_access_token(identity=str(user_id),
                                       additional_claims={"role": role, "employee_id": employee_id})
    return make


def bearer(token):
    return {"Authorization": f"Bearer {token}"}
