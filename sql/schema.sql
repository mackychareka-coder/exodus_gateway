-- Exodus Gateway schema v2 (MySQL 8.0+, InnoDB, utf8mb4).
-- Presence is DERIVED from open access_logs rows (check_out_time IS NULL); there is no is_logged_in flag.
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS employees (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  employee_code VARCHAR(32)  NOT NULL,
  full_name     VARCHAR(100) NOT NULL,
  phone         VARCHAR(32)  NULL,
  email         VARCHAR(254) NULL,
  is_active     TINYINT(1)   NOT NULL DEFAULT 1,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_employees_code (employee_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS users (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(64)  NOT NULL,
  password_hash VARCHAR(255) NOT NULL,                       -- Argon2id
  role          ENUM('SUPER_ADMIN','SECURITY_GUARD','GATE_OPERATOR','EMPLOYEE') NOT NULL,
  employee_id   INT UNSIGNED NULL,                           -- links EMPLOYEE users to their profile (host)
  is_active     TINYINT(1)   NOT NULL DEFAULT 1,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at DATETIME     NULL,
  UNIQUE KEY uq_users_username (username),
  CONSTRAINT fk_users_employee FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS vehicles (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  employee_id INT UNSIGNED NOT NULL,
  plate       VARCHAR(16)  NOT NULL,
  model       VARCHAR(50)  NULL,
  color       VARCHAR(30)  NULL,
  KEY idx_vehicles_employee (employee_id),
  KEY idx_vehicles_plate (plate),
  CONSTRAINT fk_vehicles_employee FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS terminals (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  name          VARCHAR(64)  NOT NULL,
  gate_location VARCHAR(64)  NOT NULL,
  api_key_hash  CHAR(64)     NOT NULL,                       -- SHA-256 hex of the API key
  is_active     TINYINT(1)   NOT NULL DEFAULT 1,
  created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_terminals_key (api_key_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS access_logs (
  id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  employee_id           INT UNSIGNED NOT NULL,
  check_in_time         DATETIME NOT NULL,
  check_out_time        DATETIME NULL,
  terminal_id           INT UNSIGNED NULL,
  check_out_terminal_id INT UNSIGNED NULL,
  method                ENUM('QR','NFC','MANUAL') NOT NULL,
  check_out_method      ENUM('QR','NFC','MANUAL','AUTO') NULL,
  arrival_type          ENUM('vehicle','walk_in') NOT NULL DEFAULT 'walk_in',
  vehicle_id            INT UNSIGNED NULL,                   -- the car used for a 'vehicle' arrival
  recorded_by_user_id   INT UNSIGNED NULL,
  -- DB-level anti-passback backstop: at most one open row per employee (NULLs never collide).
  open_flag             TINYINT GENERATED ALWAYS AS (IF(check_out_time IS NULL, 1, NULL)) STORED,
  KEY idx_access_check_in  (check_in_time),
  KEY idx_access_check_out (check_out_time),
  KEY idx_access_emp_out   (employee_id, check_out_time),    -- "who is on site"
  KEY idx_access_emp_latest (employee_id, check_in_time),
  UNIQUE KEY uq_access_one_open (employee_id, open_flag),
  CONSTRAINT fk_access_employee FOREIGN KEY (employee_id) REFERENCES employees(id),
  CONSTRAINT fk_access_term_in  FOREIGN KEY (terminal_id) REFERENCES terminals(id) ON DELETE SET NULL,
  CONSTRAINT fk_access_term_out FOREIGN KEY (check_out_terminal_id) REFERENCES terminals(id) ON DELETE SET NULL,
  CONSTRAINT fk_access_user     FOREIGN KEY (recorded_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_access_vehicle  FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS visitors (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  full_name   VARCHAR(100) NOT NULL,
  phone       VARCHAR(32)  NOT NULL,
  national_id VARCHAR(32)  NOT NULL,
  created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_visitors_national_id (national_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS visits (
  id                    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  visitor_id            INT UNSIGNED NOT NULL,
  host_id               INT UNSIGNED NULL,                   -- NULL only for migrated legacy rows
  purpose               VARCHAR(255) NULL,
  status                ENUM('pre-registered','approved','on-site','departed','denied') NOT NULL,
  source                ENUM('PRE_REG','WALK_IN') NOT NULL,
  badge_code            CHAR(12)     NOT NULL,
  arrival_type          ENUM('vehicle','walk_in') NOT NULL DEFAULT 'walk_in',
  plate                 VARCHAR(16)  NULL,
  vehicle_model         VARCHAR(50)  NULL,
  vehicle_color         VARCHAR(30)  NULL,
  expected_date         DATE         NULL,
  entry_time            DATETIME     NULL,
  exit_time             DATETIME     NULL,
  auto_closed           TINYINT(1)   NOT NULL DEFAULT 0,   -- 1 = closed by the automatic end-of-day job
  entry_terminal_id     INT UNSIGNED NULL,
  exit_terminal_id      INT UNSIGNED NULL,
  registered_by_user_id INT UNSIGNED NULL,
  approved_by_user_id   INT UNSIGNED NULL,
  created_at            DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_visits_badge (badge_code),
  KEY idx_visits_status (status),
  KEY idx_visits_host (host_id, status),
  KEY idx_visits_entry (entry_time),
  KEY idx_visits_exit (exit_time),
  KEY idx_visits_created (created_at),
  CONSTRAINT fk_visits_visitor FOREIGN KEY (visitor_id) REFERENCES visitors(id),
  CONSTRAINT fk_visits_host    FOREIGN KEY (host_id) REFERENCES employees(id) ON DELETE SET NULL,
  CONSTRAINT fk_visits_term_in  FOREIGN KEY (entry_terminal_id) REFERENCES terminals(id) ON DELETE SET NULL,
  CONSTRAINT fk_visits_term_out FOREIGN KEY (exit_terminal_id) REFERENCES terminals(id) ON DELETE SET NULL,
  CONSTRAINT fk_visits_reg_by   FOREIGN KEY (registered_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_visits_appr_by  FOREIGN KEY (approved_by_user_id) REFERENCES users(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_logs (
  id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  actor_user_id INT UNSIGNED NULL,
  actor_role    VARCHAR(32)  NULL,
  terminal_id   INT UNSIGNED NULL,
  action        VARCHAR(64)  NOT NULL,
  outcome       ENUM('success','denied','error') NOT NULL,
  target_type   VARCHAR(32)  NULL,
  target_id     VARCHAR(64)  NULL,
  ip_address    VARCHAR(45)  NULL,
  user_agent    VARCHAR(255) NULL,
  detail        JSON         NULL,
  KEY idx_audit_created (created_at),
  KEY idx_audit_action (action, created_at),
  KEY idx_audit_actor (actor_user_id),
  CONSTRAINT fk_audit_user FOREIGN KEY (actor_user_id) REFERENCES users(id) ON DELETE SET NULL,
  CONSTRAINT fk_audit_term FOREIGN KEY (terminal_id) REFERENCES terminals(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Revoked refresh tokens (populated on logout).
CREATE TABLE IF NOT EXISTS revoked_tokens (
  jti        VARCHAR(64) NOT NULL PRIMARY KEY,
  expires_at DATETIME    NOT NULL,
  KEY idx_revoked_exp (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
