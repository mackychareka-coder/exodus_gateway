-- Upgrade to v2.2: remember which vehicle an employee arrived in.
-- Run once:  mysql -u root -p company_attendance < sql/upgrade_v2_2.sql
ALTER TABLE access_logs ADD COLUMN vehicle_id INT UNSIGNED NULL AFTER arrival_type;
ALTER TABLE access_logs ADD CONSTRAINT fk_access_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE SET NULL;
