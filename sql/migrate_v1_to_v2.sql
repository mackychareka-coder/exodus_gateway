-- Migrate the v1 schema (employees, vehicles, attendance_logs, visitor_logs) to v2, keeping all data.
--
-- 1. BACK UP FIRST:   mysqldump company_attendance > backup_v1.sql
-- 2. Run from the project root (SOURCE paths are relative to the current directory):
--        mysql -u root -p company_attendance < sql/migrate_v1_to_v2.sql
-- 3. Old tables are kept as legacy_* for verification. Drop them yourself once satisfied.
-- Needs MySQL 8.0+ (window functions). v1 column names were inferred from the old app.py queries.
-- DDL statements auto-commit in MySQL, so this script is not atomic: restore the backup if it fails midway.

RENAME TABLE employees TO legacy_employees, vehicles TO legacy_vehicles,
             attendance_logs TO legacy_attendance_logs, visitor_logs TO legacy_visitor_logs;

SOURCE sql/schema.sql;

-- Employees (codes are upper-cased to match the new validation rules) ---------------------------
INSERT INTO employees (employee_code, full_name, phone)
SELECT UPPER(TRIM(le.employee_id)), LEFT(le.name, 100), LEFT(le.contact_details, 32)
FROM legacy_employees le;

-- Vehicles --------------------------------------------------------------------------------------
INSERT INTO vehicles (employee_id, plate, model, color)
SELECT e.id, UPPER(LEFT(TRIM(lv.reg_number), 16)), LEFT(lv.vehicle_model, 50), LEFT(lv.vehicle_color, 30)
FROM legacy_vehicles lv
JOIN employees e ON e.employee_code = UPPER(TRIM(CONVERT(lv.employee_id USING utf8mb4))) COLLATE utf8mb4_unicode_ci
WHERE lv.reg_number IS NOT NULL AND TRIM(lv.reg_number) <> '';

-- Attendance: pair each 'Clocked In' with the event that follows it ------------------------------
--  * next event exists            -> check_out_time = that event's time (an In followed by another In,
--                                    the old stale-state bug, is closed implicitly)
--  * last event, from today       -> stays open (employee is on site)
--  * last event, from before today-> closed at 23:59:59 of that day (no stale "on site" rows)
INSERT INTO access_logs (employee_id, check_in_time, check_out_time, method, check_out_method, arrival_type)
SELECT e.id, s.ts,
       CASE WHEN s.next_ts IS NOT NULL THEN s.next_ts
            WHEN s.ts >= CURDATE() THEN NULL
            ELSE TIMESTAMP(DATE(s.ts), '23:59:59') END,
       'MANUAL',
       CASE WHEN s.next_ts IS NOT NULL OR s.ts < CURDATE() THEN 'MANUAL' ELSE NULL END,
       IF(s.arrival_type = 'vehicle', 'vehicle', 'walk_in')
FROM (
  SELECT employee_id, action, arrival_type, `timestamp` AS ts,
         LEAD(`timestamp`) OVER w AS next_ts
  FROM legacy_attendance_logs
  WINDOW w AS (PARTITION BY employee_id ORDER BY id)
) s
JOIN employees e ON e.employee_code = UPPER(TRIM(CONVERT(s.employee_id USING utf8mb4))) COLLATE utf8mb4_unicode_ci
WHERE s.action = 'Clocked In';

-- Visitors + visits (legacy rows have no host and get a synthetic LEG badge code) -----------------
ALTER TABLE visitors ADD COLUMN legacy_id BIGINT NULL, ADD UNIQUE KEY uq_visitors_legacy (legacy_id);

INSERT INTO visitors (full_name, phone, national_id, legacy_id)
SELECT LEFT(visitor_name, 100), LEFT(COALESCE(contact_number, ''), 32), LEFT(COALESCE(national_id, ''), 32), id
FROM legacy_visitor_logs;

INSERT INTO visits (visitor_id, host_id, purpose, status, source, badge_code, arrival_type,
                    plate, vehicle_model, vehicle_color, entry_time, exit_time, created_at)
SELECT v.id, NULL, LEFT(l.purpose_of_visit, 255),
       CASE WHEN l.is_checked_out = 1 THEN 'departed'
            WHEN l.`timestamp` >= CURDATE() THEN 'on-site'
            ELSE 'departed' END,
       'WALK_IN', CONCAT('LEG', LPAD(l.id, 7, '0')), IF(l.arrival_type = 'vehicle', 'vehicle', 'walk_in'),
       NULLIF(UPPER(LEFT(TRIM(l.reg_number), 16)), ''), LEFT(l.vehicle_model, 50), LEFT(l.vehicle_color, 30),
       l.`timestamp`,
       CASE WHEN l.is_checked_out = 1 THEN COALESCE(l.checkout_time, l.`timestamp`)
            WHEN l.`timestamp` >= CURDATE() THEN NULL
            ELSE TIMESTAMP(DATE(l.`timestamp`), '23:59:59') END,
       l.`timestamp`
FROM legacy_visitor_logs l JOIN visitors v ON v.legacy_id = l.id;

ALTER TABLE visitors DROP INDEX uq_visitors_legacy, DROP COLUMN legacy_id;

-- Sanity checks (run manually; counts should line up) ---------------------------------------------
-- SELECT (SELECT COUNT(*) FROM legacy_employees) a, (SELECT COUNT(*) FROM employees) b;
-- SELECT (SELECT COUNT(*) FROM legacy_attendance_logs WHERE action='Clocked In') a, (SELECT COUNT(*) FROM access_logs) b;
-- SELECT (SELECT COUNT(*) FROM legacy_visitor_logs) a, (SELECT COUNT(*) FROM visits) b;
-- Staff and terminals are NOT migrated: create them with `flask --app "app:create_app()" create-user` (see README).
