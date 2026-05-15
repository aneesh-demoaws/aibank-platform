-- NBA Platform Database Migrations
-- Run in order against Aurora MySQL (corebanking database)

-- V001: NBA Templates
CREATE TABLE IF NOT EXISTS nba_templates (
    template_id VARCHAR(64) PRIMARY KEY,
    template_name VARCHAR(200) NOT NULL,
    category ENUM('opportunity','wellness','security','profile','loyalty','servicing','retention') NOT NULL,
    description TEXT,
    eligibility_rules JSON,
    default_priority TINYINT UNSIGNED NOT NULL,
    default_confidence DECIMAL(3,2) NOT NULL DEFAULT 0.80,
    reasoning_prompt TEXT,
    cta_template JSON NOT NULL,
    channels_allowed JSON NOT NULL DEFAULT '["app","web"]',
    status ENUM('active','paused','retired','draft') NOT NULL DEFAULT 'draft',
    version INT UNSIGNED NOT NULL DEFAULT 1,
    compliance_approved TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- V002: Next Best Actions
CREATE TABLE IF NOT EXISTS next_best_actions (
    action_id VARCHAR(64) PRIMARY KEY,
    customer_id VARCHAR(12) NOT NULL,
    template_id VARCHAR(64) NOT NULL,
    category ENUM('opportunity','wellness','security','engagement') NOT NULL,
    priority TINYINT UNSIGNED NOT NULL DEFAULT 50,
    confidence DECIMAL(3,2) NOT NULL DEFAULT 0.80,
    title VARCHAR(200) NOT NULL,
    reasoning TEXT,
    metrics JSON,
    cta_primary JSON,
    source ENUM('rule','agent','ml','manual') NOT NULL,
    product_type VARCHAR(64),
    status ENUM('active','dismissed','converted','expired') NOT NULL DEFAULT 'active',
    model_version VARCHAR(64),
    generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NULL,
    view_count INT UNSIGNED DEFAULT 0,
    UNIQUE INDEX idx_nba_dedup_active (customer_id, template_id, status),
    INDEX idx_nba_customer_active (customer_id, status, priority)
);

-- V003: Customer Life Events
CREATE TABLE IF NOT EXISTS customer_life_events (
    event_id VARCHAR(64) PRIMARY KEY,
    customer_id VARCHAR(12) NOT NULL,
    event_type ENUM('travel','new_baby','job_change','income_change','marriage','relocation') NOT NULL,
    detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    detection_source ENUM('app_chat','transaction_pattern','external_signal','manual') NOT NULL,
    confidence DECIMAL(3,2) NOT NULL,
    attributes JSON,
    status ENUM('active','expired','acted_upon') NOT NULL DEFAULT 'active',
    INDEX idx_life_events_customer (customer_id, status)
);

-- V004: Customer Products
CREATE TABLE IF NOT EXISTS customer_products (
    product_id VARCHAR(64) PRIMARY KEY,
    customer_id VARCHAR(12) NOT NULL,
    product_type VARCHAR(64) NOT NULL,
    product_name VARCHAR(200),
    amount_bhd DECIMAL(12,3) DEFAULT 0,
    status ENUM('active','expired','cancelled') NOT NULL DEFAULT 'active',
    details JSON,
    purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_nba_id VARCHAR(64),
    receipt_id VARCHAR(64),
    INDEX idx_products_customer (customer_id, status)
);

-- V005: Product Catalog
CREATE TABLE IF NOT EXISTS product_catalog (
    product_type VARCHAR(64) PRIMARY KEY,
    product_name VARCHAR(200) NOT NULL,
    category VARCHAR(64),
    price_bhd DECIMAL(12,3) NOT NULL DEFAULT 0,
    price_type ENUM('one_time','monthly','annual') NOT NULL DEFAULT 'one_time',
    description TEXT,
    status ENUM('active','inactive') NOT NULL DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- V006: NBA Suppressions
CREATE TABLE IF NOT EXISTS nba_suppressions (
    suppression_id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    customer_id VARCHAR(12) NOT NULL,
    scope_type ENUM('category','template','all') NOT NULL,
    scope_value VARCHAR(64) NOT NULL,
    status ENUM('active','lifted') NOT NULL DEFAULT 'active',
    suppressed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NULL,
    INDEX idx_supp_customer (customer_id, status)
);

-- V007: Customer Signals
CREATE TABLE IF NOT EXISTS customer_signals (
    signal_id VARCHAR(64) PRIMARY KEY,
    customer_id VARCHAR(12) NOT NULL,
    signal_type VARCHAR(64) NOT NULL,
    confidence DECIMAL(3,2) NOT NULL DEFAULT 0.70,
    attributes JSON,
    detected_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    consumed_at TIMESTAMP NULL,
    expires_at TIMESTAMP NULL,
    INDEX idx_signals_customer (customer_id, consumed_at)
);

-- V008: Seed Product Catalog
INSERT IGNORE INTO product_catalog (product_type, product_name, category, price_bhd, price_type, status) VALUES
('travel_insurance_regional', 'Travel Insurance (Regional)', 'opportunity', 8.000, 'one_time', 'active'),
('travel_insurance_international', 'Travel Insurance (International)', 'opportunity', 12.000, 'one_time', 'active'),
('goal_saver', 'Goal Saver Account', 'opportunity', 0.000, 'one_time', 'active'),
('salary_allocation', 'Smart Salary Allocation', 'wellness', 0.000, 'one_time', 'active'),
('fixed_deposit', 'Fixed Deposit', 'opportunity', 500.000, 'one_time', 'active'),
('credit_card_classic', 'Classic Credit Card', 'opportunity', 0.000, 'one_time', 'active'),
('credit_card_gold', 'Gold Credit Card', 'opportunity', 25.000, 'annual', 'active'),
('life_insurance_basic', 'Life Insurance (Basic)', 'security', 15.000, 'monthly', 'active'),
('bnpl_split_pay', 'BNPL Split Pay', 'wellness', 0.000, 'one_time', 'active');
