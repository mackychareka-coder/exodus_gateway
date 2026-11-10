-- Upgrade an existing v2 database to v2.1 (automatic check-out support).
-- Run once:  mysql -u root -p company_attendance < sql/upgrade_v2_1.sql
ALTER TABLE access_logs MODIFY check_out_method ENUM('QR','NFC','MANUAL','AUTO') NULL;
ALTER TABLE visits ADD COLUMN auto_closed TINYINT(1) NOT NULL DEFAULT 0 AFTER exit_time;
