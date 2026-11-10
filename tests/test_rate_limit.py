def test_login_limited_to_5_per_minute(make_app, fake_db):
    app = make_app(RATELIMIT_ENABLED=True)
    c = app.test_client()
    codes = [c.post("/api/v1/auth/login", json={"username": "ghost", "password": "wrong-pass-123"}).status_code
             for _ in range(7)]
    assert codes[:5] == [401] * 5
    assert codes[5:] == [429, 429]


def test_429_has_consistent_json_body(make_app, fake_db):
    app = make_app(RATELIMIT_ENABLED=True, LOGIN_LIMIT="1 per minute")
    c = app.test_client()
    c.post("/api/v1/auth/login", json={"username": "ghost", "password": "wrong-pass-123"})
    r = c.post("/api/v1/auth/login", json={"username": "ghost", "password": "wrong-pass-123"})
    assert r.status_code == 429 and r.json["status"] == "error"


def test_scan_endpoint_has_its_own_tight_limit(make_app, fake_db, token_for):
    from conftest import bearer
    app = make_app(RATELIMIT_ENABLED=True, SCAN_LIMIT="3 per minute")
    c = app.test_client()
    tok = token_for("SECURITY_GUARD")
    fake_db.on("FROM employees WHERE employee_code", [])
    codes = [c.post("/api/v1/access/employee/check-in", json={"employee_code": "EXODUS-1"}, headers=bearer(tok)).status_code
             for _ in range(5)]
    assert codes == [404, 404, 404, 429, 429]


def test_health_is_exempt_from_limits(make_app):
    app = make_app(RATELIMIT_ENABLED=True, RATELIMIT_DEFAULT="2 per minute")
    c = app.test_client()
    assert all(c.get("/health").status_code == 200 for _ in range(10))


def test_security_headers_present(client):
    h = client.get("/login").headers
    assert h["X-Frame-Options"] == "DENY" and h["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert "Referrer-Policy" in h
