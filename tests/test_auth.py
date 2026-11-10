from flask_jwt_extended import create_refresh_token

import auth
from conftest import bearer

PW = "correct horse battery staple"


def _user(**kw):
    u = {"id": 7, "username": "guard1", "password_hash": auth.hash_password(PW), "role": "SECURITY_GUARD",
         "employee_id": None, "is_active": 1}
    u.update(kw)
    return u


def test_login_success_sets_hardened_refresh_cookie(make_app, fake_db):
    app = make_app(JWT_COOKIE_SECURE=True)
    fake_db.on("FROM users WHERE username", [_user()])
    r = app.test_client().post("/api/v1/auth/login", json={"username": "guard1", "password": PW})
    assert r.status_code == 200
    assert r.json["data"]["access_token"] and r.json["data"]["role"] == "SECURITY_GUARD"
    cookie = next(h for h in r.headers.getlist("Set-Cookie") if h.startswith("refresh_token_cookie"))
    for flag in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/api/v1/auth/refresh"):
        assert flag in cookie
    assert "refresh_token" not in r.get_data(as_text=True)  # refresh token never in the JSON body


def test_login_failures_are_indistinguishable(client, fake_db):
    fake_db.on("FROM users WHERE username", [_user()])
    bad_pw = client.post("/api/v1/auth/login", json={"username": "guard1", "password": "nope-nope-nope"})
    fake_db.on("FROM users WHERE username", [])
    no_user = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "nope-nope-nope"})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json == no_user.json and bad_pw.json["code"] == "INVALID_CREDENTIALS"
    assert fake_db.ran("INSERT INTO audit_logs")  # denials are audited


def test_inactive_user_cannot_log_in(client, fake_db):
    fake_db.on("FROM users WHERE username", [_user(is_active=0)])
    r = client.post("/api/v1/auth/login", json={"username": "guard1", "password": PW})
    assert r.status_code == 401


def test_protected_route_requires_token(client):
    r = client.get("/api/v1/employees")
    assert r.status_code == 401 and r.json["status"] == "error" and r.json["code"] == "AUTH_REQUIRED"


def test_garbage_token_rejected(client):
    assert client.get("/api/v1/employees", headers=bearer("not.a.jwt")).status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(app, client):
    with app.app_context():
        refresh = create_refresh_token(identity="7")
    assert client.get("/api/v1/employees", headers=bearer(refresh)).status_code == 401


def test_refresh_endpoint_issues_new_access_token(client, fake_db):
    fake_db.on("FROM users WHERE username", [_user()])
    fake_db.on("FROM users WHERE id", [_user()])
    client.post("/api/v1/auth/login", json={"username": "guard1", "password": PW})
    r = client.post("/api/v1/auth/refresh")  # cookie jar sends the path-scoped cookie
    assert r.status_code == 200 and r.json["data"]["access_token"]


def test_refresh_without_cookie_is_401(client):
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_terminal_api_key_valid_and_invalid(client, fake_db):
    key = auth.generate_api_key()
    fake_db.on("FROM terminals WHERE api_key_hash",
               lambda p: [{"id": 3, "gate_location": "Main Gate", "api_key_hash": auth.hash_api_key(key)}]
               if p[0] == auth.hash_api_key(key) else [])
    ok = client.post("/api/v1/access/employee/check-in", headers={"X-Gate-API-Key": key}, json={"employee_code": "EXODUS-1"})
    assert ok.status_code != 401
    bad = client.post("/api/v1/access/employee/check-in", headers={"X-Gate-API-Key": "gk_wrong"}, json={"employee_code": "EXODUS-1"})
    assert bad.status_code == 401 and bad.json["code"] == "INVALID_API_KEY"


def test_argon2_hash_and_verify():
    h = auth.hash_password(PW)
    assert h.startswith("$argon2") and auth.verify_password(h, PW) and not auth.verify_password(h, "x")


def test_health_is_public(client):
    assert client.get("/health").status_code == 200


def test_pages_serve_without_data(client):
    assert client.get("/login").status_code == 200
    assert client.get("/dashboard").status_code == 200  # shell only; data needs a token
    assert client.get("/nope").status_code == 404
