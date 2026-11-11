# Exodus Gateway

Employee access (with anti-passback) and visitor management for EXODUS & CO. Flask + MySQL, JWT for people,
hashed API keys for gate hardware, RBAC, full audit trail.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # fill in SECRET_KEY, JWT_SECRET_KEY, DB_*; for local http set COOKIE_SECURE=false FORCE_HTTPS=false

# new database
mysql -u root -p -e "CREATE DATABASE company_attendance CHARACTER SET utf8mb4"
mysql -u root -p company_attendance < sql/schema.sql
# ...or migrate the existing v1 data (back up first, run from this directory):
mysqldump -u root -p company_attendance > backup_v1.sql
mysql -u root -p company_attendance < sql/migrate_v1_to_v2.sql

# least-privilege DB user (the app must not run as root)
mysql -u root -p -e "CREATE USER 'exodus_app'@'localhost' IDENTIFIED BY '...';
  GRANT SELECT,INSERT,UPDATE,DELETE ON company_attendance.* TO 'exodus_app'@'localhost';"
# optional tamper-resistance: REVOKE UPDATE,DELETE ON company_attendance.audit_logs FROM 'exodus_app'@'localhost';

# first accounts (the old hard-coded PINs are gone)
flask --app "app:create_app()" create-user admin --role SUPER_ADMIN
flask --app "app:create_app()" create-user reception --role SECURITY_GUARD
flask --app "app:create_app()" create-terminal "Main gate scanner" "Main Gate"   # prints the API key once

python app.py                                         # dev server, debug OFF
gunicorn -c gunicorn.conf.py "app:create_app()"       # production (behind nginx/TLS; set TRUSTED_PROXIES=1)
```

Mapping of old logins: `admin123` -> SUPER_ADMIN, `reception123` -> SECURITY_GUARD, `exodus123` (kiosk) -> GATE_OPERATOR.

## Architecture

```
 Browser (kiosk / dashboard)               Gate hardware (QR/NFC reader)
   |  HTML shells (no data)                  |  X-Gate-API-Key
   |  Authorization: Bearer <15-min JWT>     |
   |  refresh cookie (HttpOnly, Strict,      |
   |  Path=/api/v1/auth/refresh)             |
   v                                         v
 +------------------------------------------------------------+
 | Talisman (CSP, HSTS, X-Frame-Options)                      |
 | Flask-Limiter (120/min global, 5/min login, scans tight)   |
 | before_request: resolve JWT or API key -> g.user_id,       |
 |   g.user_role, g.terminal_id, g.gate_location              |
 | @roles_required -> pydantic schemas -> route handler       |
 +---------+-----------------------+--------------------------+
           |                       |
     db.py (PyMySQL + DBUtils pool,        audit.py -> audit_logs
      get_cursor commit/rollback)          notifier.py -> log | SMTP | webhook
           |
        MySQL 8 (InnoDB, FKs)
```

Layout: `app.py` factory, `config.py` env config, `extensions.py`, `db.py`, `auth.py`, `schemas.py`, `audit.py`,
`notifier.py`, `errors.py` (JSON error format + `errors.html` fallback), `cli.py`, `routes/{auth_routes,checkin,visitors,admin,pages}.py`.

## Roles

| Role | Can |
|---|---|
| SUPER_ADMIN | everything (passes every role check), incl. audit logs, users, terminals |
| SECURITY_GUARD | employee scans (MANUAL), walk-ins, visitor check-in/out/approve, dashboard, add employees |
| GATE_OPERATOR | kiosk: employee scans (MANUAL), walk-ins, visitor check-in/out/search |
| EMPLOYEE | pre-register own visitors, approve/deny own pending visitors, list own visitors |
| GATE_HARDWARE_CLIENT (API key) | push employee scans (QR/NFC) and visitor badge scans only |

## Endpoint reference

All under `/api/v1`. Success: `{"status":"success","message":...,"data":...}`. Error:
`{"status":"error","code":"...","message":"...","details":[...]}`.

| Method & path | Roles | Notes |
|---|---|---|
| POST `/auth/login` | public (5/min/IP) | `{username,password}` -> access token in body, refresh token in cookie |
| POST `/auth/refresh` | refresh cookie | new 15-min access token |
| DELETE `/auth/refresh` | refresh cookie | logout: revokes refresh token, clears cookie (shares the refresh path because the cookie is path-scoped) |
| POST `/access/employee/check-in` | hardware, operator, guard | `{employee_code, arrival_type?, method?}`; 409 `ALREADY_CHECKED_IN` / `DUPLICATE_SCAN` |
| POST `/access/employee/check-out` | hardware, operator, guard | 409 `NOT_CHECKED_IN` |
| POST `/visitors/pre-register` | EMPLOYEE | host = caller; returns `badge_code` + `badge_qr` (SVG data URI) |
| POST `/visitors/walk-in` | guard, operator | needs `host_employee_code`; on-site immediately unless `WALKIN_REQUIRES_APPROVAL=true` |
| POST `/visitors/check-in` / `check-out` | hardware, guard, operator | `{badge_code}` or `{visit_id}` |
| POST `/visitors/<id>/approve` / `deny` | host EMPLOYEE (own), guard | only from `pre-registered` |
| GET `/visitors/on-site?q=` | guard, operator | search name / badge / plate |
| GET `/visitors/pending` | guard, operator | approved or awaiting approval, expected today |
| GET `/visitors/mine` | EMPLOYEE | last 7 days, PII stripped |
| GET `/visitors/<id>/badge` | host, guard, operator | reprint QR |
| GET `/employees` / POST `/employees` | guard+operator / guard | directory for kiosks / create (+ optional vehicle) |
| GET `/admin/dashboard` | guard | counts, today's logs, visitors, parking |
| GET `/admin/audit-logs?action=&outcome=&limit=&before_id=` | SUPER_ADMIN | |
| GET/POST `/admin/terminals`, POST `/admin/terminals/<id>/rotate-key`, `/deactivate` | SUPER_ADMIN | key shown once |
| GET/POST `/admin/users`, POST `/admin/users/<id>/deactivate` | SUPER_ADMIN | |
| PATCH `/employees/<code>` | guard | set an employee's `email` / `phone` (needed for arrival emails) |
| GET `/admin/export/access-logs.csv`, `/admin/export/visits.csv` | SUPER_ADMIN | `?period=day\|week\|month&date=YYYY-MM-DD` (week = Mon-Sun) or `?start=&end=` (inclusive, max 366 days) |
| POST `/admin/users/<id>/role`, `/reset-password`, `/activate`, `/deactivate` | SUPER_ADMIN | cannot target yourself for role/deactivate |
| GET `/employees?q=` | guard, operator | kiosk directory; `q` matches Employee ID or name; includes `has_vehicle`, `plates` |
| GET `/admin/employees?q=&status=`, GET/PATCH/DELETE `/admin/employees/<code>` | SUPER_ADMIN | search, edit (incl. changing the ID, clearing phone/email, activate/deactivate), delete |
| POST `/admin/employees/<code>/vehicles`, DELETE `/admin/vehicles/<id>` | SUPER_ADMIN | manage an employee's vehicles |
| GET `/health` | public | |

Visit status: `pre-registered` -> (approve) `approved` -> (check-in) `on-site` -> (check-out) `departed`; or `denied`.
Host pre-registrations start as `approved` (the authenticated host vouches for them).

## Data model notes

* Presence = open `access_logs` row (`check_out_time IS NULL`). No `is_logged_in` flag.
* Anti-passback: `SELECT ... FOR UPDATE` on the employee row serialises concurrent scans; a unique key on a
  generated `open_flag` column is a database-level backstop (one open row per employee).
* Migration closes orphan/stale v1 sessions: an old open session from before today is closed at 23:59:59 of its day.
  Legacy visitors get no host and a synthetic `LEG…` badge code. Old tables remain as `legacy_*`.
* Timestamps use the database server's `NOW()` (server local time).

## Tests

```bash
pytest                       # auth, RBAC denials, anti-passback, validation, rate limiting, headers
# real-MySQL concurrency test (optional):
TEST_DB_HOST=localhost TEST_DB_USER=... TEST_DB_PASSWORD=... TEST_DB_NAME=exodus_test pytest -m db
```
Most tests use a scripted fake cursor (`tests/conftest.py`), so they need no database.

## Threat model

| Threat | Control |
|---|---|
| Guessing/brute-forcing logins | Argon2id hashes, 5/min/IP limit, identical error + dummy hash verify for unknown users (no enumeration/timing), all attempts audited |
| Stolen/forged API calls | 15-min access JWT in memory only; refresh token HttpOnly + Secure + SameSite=Strict + path-scoped, revoked on logout; role claim re-checked on every request |
| CSRF | No cookie-authenticated state-changing endpoint except refresh/logout; SameSite=Strict + path scoping; everything else needs a Bearer header a cross-site page cannot attach |
| Stolen terminal key | Only a SHA-256 hash is stored; constant-time compare; keys rotate/deactivate instantly; terminal role can only push scans |
| Privilege escalation | Central `@roles_required`; denials return 403 and are audited |
| Tailgating / double badge / replay | Anti-passback under row locks (409), re-submission window, DB unique-open-row backstop |
| Fake "manual" entries from hardware | Hardware cannot submit `MANUAL`; human entries are always flagged MANUAL and attributed |
| Stored XSS | Pydantic charset regexes, `markupsafe.escape` on free text, DOM `textContent` rendering (no `innerHTML` with data), CSP without inline script |
| SQL injection | Parameterised queries everywhere; LIKE wildcards escaped |
| Clickjacking / sniffing / downgrade | X-Frame-Options DENY + `frame-ancestors 'none'`, nosniff, HSTS, Referrer-Policy |
| Resource abuse | 5 MB body cap, global 120/min limit, scan limits, pooled DB connections |
| Repudiation | `audit_logs` for logins, scans, denials, admin actions; readable by SUPER_ADMIN only |
| Secrets in code | All secrets via `.env`; the app refuses to start without them |

## Known gaps / decisions to revisit

* **Employee (host) web UI is not built**: pre-registration and approval exist as API endpoints only.
* Roles are embedded in the access token, so a role change or deactivation takes up to 15 minutes to bite
  (refresh does re-check `is_active`).
* Rate limiting is per-IP; gate hardware behind one NAT shares a bucket (tune `SCAN_LIMIT`). Use Redis storage with several workers.
* Tailwind is loaded from jsDelivr (allowed in the CSP). For an air-gapped or stricter deployment, vendor it and drop the CDN from the CSP.
* Audit writes fail open (logged, not raised) so an audit outage cannot lock the gate; monitor the app log.
* The old third-party QR image on the dashboard (it sent a fixed token to api.qrserver.com) was removed.

## Automatic check-out, CSV export, email alerts

**Auto check-out (23:00 by default).** `scheduler.py` runs inside the app every 60 s and closes every open
employee session and every `on-site` visit whose cutoff has passed. Rule: checked in at/before the cutoff time
of day -> closed at that day's cutoff (23:00); checked in after it -> closed at the next day's cutoff. It is
idempotent and catches up after downtime (an app that was off overnight fixes yesterday's rows on next start,
stamping them 23:00). Closed rows are marked (`check_out_method='AUTO'`, `visits.auto_closed=1`), shown as
"Auto-closed" on the dashboard, included in exports, and summarised in the audit log (`system.auto_checkout`).
Configure with `AUTO_CHECKOUT_ENABLED` / `AUTO_CHECKOUT_TIME`. Run once manually: `flask --app "app:create_app()" auto-checkout`.
Existing v2 databases need `sql/upgrade_v2_1.sql` once. The app must be running for the job to run.

**CSV export.** SUPER_ADMIN only, page at `/admin/exports`. Staff rows are selected by check-in date; visitor rows by
entry date (registration date if they never arrived). Text cells starting with `= + - @` are prefixed with `'`
to prevent spreadsheet formula injection, HTML escaping is undone for readability, and a UTF-8 BOM is added for Excel.
Every export is audited (`admin.export`). Visitor exports contain phone and ID numbers.

**Email to the host on arrival.** Set `NOTIFIER_BACKEND=smtp` plus the `SMTP_*` values. When a visitor is checked
in (walk-in, or a pre-registered badge scan) the host gets an email with the visitor's name and purpose; with
`WALKIN_REQUIRES_APPROVAL=true` they get an approval-needed email instead. Hosts need an email address:
set it when adding the employee or via "Update Employee Email" on the Add Employee page. Emails are sent in a
background thread; successes and failures are audited (`notify.sent` / `notify.failed`, e.g. "host has no email
address on file"). Check your setup with `flask --app "app:create_app()" send-test-email you@example.com`.

## Employee records, search, and arrival mode

SUPER_ADMIN manages employees at `/admin/employees`: search by Employee ID or name, add, edit (name, phone, email,
even the ID), manage vehicles, deactivate/activate, and delete.

* **Deactivate** blocks check-in and hides the person from kiosk lists but keeps all history. A deactivated
  employee who is still on site can still be checked out.
* **Delete** is refused if the employee has access history (`HAS_HISTORY`). SUPER_ADMIN can still delete permanently
  together with that history by repeating the Employee ID (`purge` + `confirm_code`); this is audited with the number
  of records removed. Visits they hosted are kept (host becomes empty); linked user accounts stay but lose the link.
* **On foot vs vehicle.** Each check-in records `arrival_type` (`walk_in` or `vehicle`). The staff kiosk has an
  "Arrived by" switch, pre-set to "By vehicle" only when the employee has a registered car. Parking status is derived
  from the open session: an employee who walks in never marks their car as parked, so it stays "Away". API/hardware
  callers should send `arrival_type`; if omitted it is inferred (vehicle when the employee has one).
