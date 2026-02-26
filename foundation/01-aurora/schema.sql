-- AI Bank Core Banking Schema — Aurora MySQL (me-south-1)
-- Based on NeoBank corebanking schema with multi-country support
-- Run against: aibank-core-banking cluster, database: corebanking

CREATE DATABASE IF NOT EXISTS corebanking;
USE corebanking;

-- ═══════════════════════════════════════════════════════════
-- CORE TABLES (from NeoBank, with country support)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE customers (
  customer_id       VARCHAR(12) NOT NULL,
  cognito_user_id   VARCHAR(128) NOT NULL,
  email             VARCHAR(255) NOT NULL,
  phone_number      VARCHAR(20) NOT NULL,
  first_name        VARCHAR(100) NOT NULL,
  last_name         VARCHAR(100) NOT NULL,
  date_of_birth     DATE NOT NULL,
  national_id       VARCHAR(20) DEFAULT NULL,
  nationality       VARCHAR(50) DEFAULT 'Bahraini',
  country           VARCHAR(2) NOT NULL DEFAULT 'BH',
  address_line1     VARCHAR(255) DEFAULT NULL,
  city              VARCHAR(100) DEFAULT NULL,
  kyc_status        ENUM('PENDING','PROCESSING','VERIFIED','REJECTED','EXPIRED') DEFAULT 'PENDING',
  credit_score      INT DEFAULT 0,
  risk_category     ENUM('LOW','MEDIUM','HIGH') DEFAULT 'MEDIUM',
  status            ENUM('ACTIVE','INACTIVE','SUSPENDED','CLOSED') DEFAULT 'ACTIVE',
  employment_info   JSON DEFAULT NULL,
  phone_verified    TINYINT(1) DEFAULT 0,
  email_verified    TINYINT(1) DEFAULT 0,
  last_login        TIMESTAMP NULL DEFAULT NULL,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (customer_id),
  UNIQUE KEY uq_cognito (cognito_user_id),
  UNIQUE KEY uq_email (email),
  UNIQUE KEY uq_phone (phone_number),
  KEY idx_country (country),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE accounts (
  account_id        VARCHAR(20) NOT NULL,
  customer_id       VARCHAR(12) NOT NULL,
  account_type      ENUM('savings','current','premium','business') NOT NULL DEFAULT 'savings',
  account_number    VARCHAR(16) NOT NULL,
  balance           DECIMAL(15,3) NOT NULL DEFAULT 0.000,
  currency          VARCHAR(3) NOT NULL DEFAULT 'BHD',
  status            ENUM('ACTIVE','INACTIVE','SUSPENDED','CLOSED') DEFAULT 'ACTIVE',
  opening_date      DATE NOT NULL,
  last_transaction_date TIMESTAMP NULL DEFAULT NULL,
  minimum_balance   DECIMAL(10,3) DEFAULT 100.000,
  overdraft_limit   DECIMAL(10,3) DEFAULT 0.000,
  interest_rate     DECIMAL(5,4) DEFAULT 0.0250,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (account_id),
  UNIQUE KEY uq_account_number (account_number),
  KEY idx_customer_id (customer_id),
  KEY idx_status (status),
  CONSTRAINT fk_accounts_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE transactions (
  transaction_id    VARCHAR(30) NOT NULL,
  account_id        VARCHAR(20) NOT NULL,
  transaction_type  ENUM('credit','debit') NOT NULL,
  amount            DECIMAL(12,3) NOT NULL,
  currency          VARCHAR(3) NOT NULL DEFAULT 'BHD',
  description       VARCHAR(255) NOT NULL,
  reference_number  VARCHAR(50) DEFAULT NULL,
  balance_after     DECIMAL(15,3) NOT NULL,
  transaction_date  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  value_date        DATE NOT NULL,
  status            ENUM('pending','completed','failed','cancelled') DEFAULT 'completed',
  channel           ENUM('online','mobile','atm','branch','system') DEFAULT 'system',
  merchant_name     VARCHAR(255) DEFAULT NULL,
  mcc_code          VARCHAR(4) DEFAULT NULL,
  category_id       VARCHAR(10) DEFAULT NULL,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (transaction_id),
  KEY idx_account_id (account_id),
  KEY idx_transaction_date (transaction_date),
  KEY idx_status (status),
  CONSTRAINT fk_txn_account FOREIGN KEY (account_id) REFERENCES accounts(account_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════
-- LOAN TABLES
-- ═══════════════════════════════════════════════════════════

CREATE TABLE loan_applications (
  application_id    VARCHAR(50) NOT NULL,
  customer_id       VARCHAR(12) DEFAULT NULL,
  country           VARCHAR(2) NOT NULL DEFAULT 'BH',
  currency          VARCHAR(3) NOT NULL DEFAULT 'BHD',
  loan_type         ENUM('instant_money','personal','housing','vehicle','education') NOT NULL,
  amount            DECIMAL(12,3) NOT NULL,
  duration          INT DEFAULT NULL COMMENT 'Months',
  interest          DECIMAL(5,2) DEFAULT NULL,
  monthly_payment   DECIMAL(10,3) DEFAULT NULL,
  emi_schedule      JSON DEFAULT NULL,
  status            ENUM('draft','submitted','processing','underwriting','manual_review','approved','rejected','disbursed','cancelled') DEFAULT 'submitted',
  risk_assessment   JSON DEFAULT NULL,
  customer_segment  VARCHAR(50) DEFAULT NULL,
  underwriting_score DECIMAL(5,2) DEFAULT NULL,
  decision_type     ENUM('auto_approve','manual_approve','manual_reject','auto_decline') DEFAULT NULL,
  decision_at       TIMESTAMP NULL DEFAULT NULL,
  reviewer_id       VARCHAR(128) DEFAULT NULL,
  review_notes      TEXT DEFAULT NULL,
  application_source VARCHAR(50) DEFAULT 'web',
  ip_address        VARCHAR(45) DEFAULT NULL,
  device_info       JSON DEFAULT NULL,
  sfn_execution_arn VARCHAR(256) DEFAULT NULL,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (application_id),
  KEY idx_customer (customer_id),
  KEY idx_status (status),
  KEY idx_country_status (country, status),
  KEY idx_created (created_at),
  CONSTRAINT fk_loan_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE loan_workflow_steps (
  step_id           VARCHAR(50) NOT NULL,
  application_id    VARCHAR(50) NOT NULL,
  step_name         VARCHAR(100) NOT NULL,
  step_order        INT NOT NULL DEFAULT 0,
  step_status       ENUM('pending','in_progress','completed','failed','skipped') DEFAULT 'pending',
  started_at        TIMESTAMP NULL DEFAULT NULL,
  completed_at      TIMESTAMP NULL DEFAULT NULL,
  duration_ms       INT DEFAULT NULL,
  error_message     TEXT DEFAULT NULL,
  step_data         JSON DEFAULT NULL,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (step_id),
  KEY idx_application (application_id),
  KEY idx_status (step_status),
  CONSTRAINT fk_step_application FOREIGN KEY (application_id) REFERENCES loan_applications(application_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE loan_documents (
  document_id       VARCHAR(50) NOT NULL,
  application_id    VARCHAR(50) NOT NULL,
  document_type     ENUM('salary_certificate','bank_statement','id_document','proof_of_address','employment_letter','other') NOT NULL,
  s3_key            VARCHAR(512) NOT NULL,
  s3_bucket         VARCHAR(128) NOT NULL,
  file_name         VARCHAR(255) NOT NULL,
  file_size         INT DEFAULT NULL,
  mime_type         VARCHAR(100) DEFAULT NULL,
  extraction_status ENUM('pending','processing','completed','failed') DEFAULT 'pending',
  extracted_data    JSON DEFAULT NULL COMMENT 'BDA extraction results',
  validation_status ENUM('pending','valid','invalid','needs_review') DEFAULT 'pending',
  validation_notes  TEXT DEFAULT NULL,
  uploaded_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  processed_at      TIMESTAMP NULL DEFAULT NULL,
  PRIMARY KEY (document_id),
  KEY idx_application (application_id),
  KEY idx_type (document_type),
  CONSTRAINT fk_doc_application FOREIGN KEY (application_id) REFERENCES loan_applications(application_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE loan_decisions (
  decision_id       VARCHAR(50) NOT NULL,
  application_id    VARCHAR(50) NOT NULL,
  decision_type     ENUM('auto_approve','auto_decline','manual_approve','manual_reject','override') NOT NULL,
  decision_score    DECIMAL(5,2) DEFAULT NULL,
  decision_reasons  JSON DEFAULT NULL COMMENT 'Array of reason strings',
  underwriting_summary TEXT DEFAULT NULL COMMENT 'AI-generated underwriting narrative',
  five_cs_scores    JSON DEFAULT NULL COMMENT '{character, capacity, capital, collateral, conditions}',
  approved_amount   DECIMAL(12,3) DEFAULT NULL,
  approved_rate     DECIMAL(5,2) DEFAULT NULL,
  approved_duration INT DEFAULT NULL,
  reviewer_id       VARCHAR(128) DEFAULT NULL,
  reviewer_name     VARCHAR(100) DEFAULT NULL,
  review_notes      TEXT DEFAULT NULL,
  decided_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (decision_id),
  KEY idx_application (application_id),
  KEY idx_type (decision_type),
  CONSTRAINT fk_decision_application FOREIGN KEY (application_id) REFERENCES loan_applications(application_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE loan_contracts (
  contract_id       VARCHAR(50) NOT NULL,
  application_id    VARCHAR(50) NOT NULL,
  customer_id       VARCHAR(12) NOT NULL,
  country           VARCHAR(2) NOT NULL,
  currency          VARCHAR(3) NOT NULL,
  principal_amount  DECIMAL(12,3) NOT NULL,
  interest_rate     DECIMAL(5,2) NOT NULL,
  duration_months   INT NOT NULL,
  monthly_payment   DECIMAL(10,3) NOT NULL,
  total_repayment   DECIMAL(12,3) NOT NULL,
  processing_fee    DECIMAL(10,3) DEFAULT 0,
  insurance_fee     DECIMAL(10,3) DEFAULT 0,
  disbursement_account VARCHAR(20) DEFAULT NULL,
  first_payment_date DATE NOT NULL,
  maturity_date     DATE NOT NULL,
  contract_s3_key   VARCHAR(512) DEFAULT NULL,
  status            ENUM('generated','signed','active','completed','defaulted','written_off') DEFAULT 'generated',
  disbursed_at      TIMESTAMP NULL DEFAULT NULL,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (contract_id),
  KEY idx_application (application_id),
  KEY idx_customer (customer_id),
  KEY idx_status (status),
  CONSTRAINT fk_contract_application FOREIGN KEY (application_id) REFERENCES loan_applications(application_id),
  CONSTRAINT fk_contract_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE loan_segment_configs (
  id                INT AUTO_INCREMENT PRIMARY KEY,
  country           VARCHAR(2) NOT NULL,
  segment           VARCHAR(50) NOT NULL,
  description       VARCHAR(255) DEFAULT NULL,
  min_salary        DECIMAL(12,3) NOT NULL,
  income_multiplier INT NOT NULL,
  dti_threshold     DECIMAL(5,2) NOT NULL,
  min_credit_score  INT NOT NULL,
  min_employment_months INT NOT NULL DEFAULT 6,
  min_age           INT NOT NULL DEFAULT 21,
  max_age_at_maturity INT NOT NULL DEFAULT 65,
  base_interest_rate DECIMAL(5,2) NOT NULL,
  risk_adj_min      DECIMAL(5,2) DEFAULT 0,
  risk_adj_max      DECIMAL(5,2) DEFAULT 3.0,
  processing_fee_pct DECIMAL(5,2) DEFAULT 1.5,
  auto_approve_threshold INT NOT NULL DEFAULT 80,
  manual_review_threshold INT NOT NULL DEFAULT 65,
  auto_decline_threshold INT NOT NULL DEFAULT 40,
  scoring_weights   JSON NOT NULL COMMENT '{credit_score, dti_ratio, banking_relationship, employment_stability, financial_behavior}',
  max_loan_amount   DECIMAL(12,3) DEFAULT NULL,
  min_loan_amount   DECIMAL(12,3) DEFAULT 1000,
  status            ENUM('active','inactive') DEFAULT 'active',
  effective_date    DATE NOT NULL,
  ssm_parameter     VARCHAR(256) DEFAULT NULL,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_country_segment (country, segment),
  KEY idx_country (country),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════
-- NBA / CUSTOMER 360 TABLES (from NeoBank)
-- ═══════════════════════════════════════════════════════════

CREATE TABLE next_best_offers (
  offer_id          VARCHAR(50) NOT NULL DEFAULT (CONCAT('OFFER_', UNIX_TIMESTAMP(), '_', SUBSTR(MD5(RAND()),1,8))),
  customer_id       VARCHAR(12) NOT NULL,
  product_type      ENUM('home_loan','personal_loan','credit_card','investment','insurance','savings_account','current_account','premium_account') NOT NULL,
  offer_title       VARCHAR(200) NOT NULL,
  offer_subtitle    VARCHAR(300) DEFAULT NULL,
  offer_description TEXT DEFAULT NULL,
  confidence_score  DECIMAL(5,2) NOT NULL,
  priority_rank     INT NOT NULL DEFAULT 1,
  offer_amount      DECIMAL(12,3) DEFAULT NULL,
  interest_rate     DECIMAL(5,4) DEFAULT NULL,
  tenure_months     INT DEFAULT NULL,
  monthly_payment   DECIMAL(10,3) DEFAULT NULL,
  benefits          JSON DEFAULT NULL,
  call_to_action    VARCHAR(100) DEFAULT 'Learn More',
  valid_from        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  valid_until       TIMESTAMP NOT NULL,
  status            ENUM('active','presented','accepted','declined','expired') DEFAULT 'active',
  presentation_count INT DEFAULT 0,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (offer_id),
  KEY idx_customer_offers (customer_id, status),
  KEY idx_priority (customer_id, priority_rank),
  CONSTRAINT fk_nbo_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE customer_360_metrics (
  metric_id         VARCHAR(50) NOT NULL DEFAULT (CONCAT('METRIC_', UNIX_TIMESTAMP(), '_', SUBSTR(MD5(RAND()),1,8))),
  customer_id       VARCHAR(12) NOT NULL,
  financial_health_score INT DEFAULT 0,
  monthly_income    DECIMAL(12,3) DEFAULT 0,
  monthly_expenses  DECIMAL(12,3) DEFAULT 0,
  savings_rate      DECIMAL(5,2) DEFAULT 0,
  debt_to_income_ratio DECIMAL(5,2) DEFAULT 0,
  engagement_score  INT DEFAULT 0,
  transaction_frequency DECIMAL(5,2) DEFAULT 0,
  last_calculated   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (metric_id),
  KEY idx_customer (customer_id),
  KEY idx_health (financial_health_score DESC),
  CONSTRAINT fk_metrics_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE customer_insights (
  insight_id        VARCHAR(50) NOT NULL DEFAULT (CONCAT('INSIGHT_', UNIX_TIMESTAMP(), '_', SUBSTR(MD5(RAND()),1,8))),
  customer_id       VARCHAR(12) NOT NULL,
  insight_type      ENUM('spending_pattern','saving_opportunity','risk_alert','goal_progress','product_usage','budget_alert','investment_opportunity') NOT NULL,
  insight_title     VARCHAR(200) NOT NULL,
  insight_description TEXT DEFAULT NULL,
  insight_data      JSON DEFAULT NULL,
  severity          ENUM('low','medium','high','critical') DEFAULT 'medium',
  action_required   TINYINT(1) DEFAULT 0,
  is_read           TINYINT(1) DEFAULT 0,
  expires_at        TIMESTAMP NULL DEFAULT NULL,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (insight_id),
  KEY idx_customer (customer_id, is_read),
  KEY idx_type (insight_type, severity),
  CONSTRAINT fk_insights_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE customer_goals (
  goal_id           VARCHAR(50) NOT NULL DEFAULT (CONCAT('GOAL_', UNIX_TIMESTAMP(), '_', SUBSTR(MD5(RAND()),1,8))),
  customer_id       VARCHAR(12) NOT NULL,
  goal_type         ENUM('savings','investment','debt_payoff','emergency_fund','home_purchase','education','vacation','retirement') NOT NULL,
  goal_title        VARCHAR(200) NOT NULL,
  target_amount     DECIMAL(12,3) NOT NULL,
  current_amount    DECIMAL(12,3) DEFAULT 0,
  target_date       DATE NOT NULL,
  monthly_contribution DECIMAL(10,3) DEFAULT 0,
  auto_save         TINYINT(1) DEFAULT 0,
  linked_account_id VARCHAR(20) DEFAULT NULL,
  status            ENUM('active','completed','paused','cancelled') DEFAULT 'active',
  progress_percentage DECIMAL(5,2) GENERATED ALWAYS AS (CASE WHEN target_amount > 0 THEN (current_amount / target_amount * 100) ELSE 0 END) STORED,
  created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (goal_id),
  KEY idx_customer (customer_id, status),
  CONSTRAINT fk_goals_customer FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE,
  CONSTRAINT fk_goals_account FOREIGN KEY (linked_account_id) REFERENCES accounts(account_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ═══════════════════════════════════════════════════════════
-- VIEW: Customer 360 Summary (from NeoBank)
-- ═══════════════════════════════════════════════════════════

CREATE OR REPLACE VIEW customer_360_summary AS
SELECT
  c.customer_id,
  CONCAT(c.first_name, ' ', c.last_name) AS full_name,
  c.email, c.phone_number, c.country,
  c.credit_score, c.kyc_status, c.risk_category,
  c.created_at AS member_since,
  COALESCE(a.total_accounts, 0) AS total_accounts,
  COALESCE(a.total_balance, 0) AS total_balance,
  COALESCE(t.transaction_count_90d, 0) AS transaction_count_90d,
  COALESCE(t.total_spending_90d, 0) AS total_spending_90d,
  COALESCE(t.total_income_90d, 0) AS total_income_90d,
  t.last_transaction_date,
  CASE
    WHEN c.credit_score >= 750 THEN 'EXCELLENT'
    WHEN c.credit_score >= 650 THEN 'GOOD'
    WHEN c.credit_score >= 550 THEN 'FAIR'
    ELSE 'POOR'
  END AS credit_rating,
  CASE
    WHEN COALESCE(a.total_balance, 0) >= 50000 THEN 'HIGH_VALUE'
    WHEN COALESCE(a.total_balance, 0) >= 10000 THEN 'MEDIUM_VALUE'
    ELSE 'STANDARD'
  END AS value_segment
FROM customers c
LEFT JOIN (
  SELECT customer_id, COUNT(*) AS total_accounts, SUM(balance) AS total_balance
  FROM accounts WHERE status = 'ACTIVE' GROUP BY customer_id
) a ON c.customer_id = a.customer_id
LEFT JOIN (
  SELECT ac.customer_id,
    COUNT(tx.transaction_id) AS transaction_count_90d,
    SUM(CASE WHEN tx.transaction_type = 'debit' THEN tx.amount ELSE 0 END) AS total_spending_90d,
    SUM(CASE WHEN tx.transaction_type = 'credit' THEN tx.amount ELSE 0 END) AS total_income_90d,
    MAX(tx.transaction_date) AS last_transaction_date
  FROM transactions tx JOIN accounts ac ON tx.account_id = ac.account_id
  WHERE tx.transaction_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY) AND tx.status = 'completed'
  GROUP BY ac.customer_id
) t ON c.customer_id = t.customer_id
WHERE c.status = 'ACTIVE';
