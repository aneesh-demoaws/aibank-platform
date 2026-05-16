-- Create Glue database and table for Neptune graph data access via Athena
-- Run once to set up the Athena → Neptune data path

-- Database
CREATE DATABASE IF NOT EXISTS neptune_c360;

-- Customer peer stats table (reads from S3 export)
CREATE EXTERNAL TABLE IF NOT EXISTS neptune_c360.customer_peer_stats (
    customer_id STRING,
    community_id INT,
    fhs_score INT,
    fhs_band STRING,
    income_band STRING,
    balance DOUBLE,
    peer_count INT,
    peer_pct_home_loan DOUBLE,
    peer_pct_products DOUBLE,
    peer_avg_merchants DOUBLE,
    peer_pct_goals DOUBLE,
    peer_pct_high_balance DOUBLE,
    merchant_count INT,
    eligible_home_loan BOOLEAN
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar'=',', 'quoteChar'='"')
LOCATION 's3://aibank-athena-results-eu-west-1/neptune-exports/'
TBLPROPERTIES ('skip.header.line.count'='1', 'classification'='csv');

-- Example queries for QuickSight / Quick Chat Agent:

-- Peer comparison for a community
-- SELECT customer_id, peer_count, peer_pct_home_loan, peer_avg_merchants
-- FROM neptune_c360.customer_peer_stats
-- WHERE community_id = 13 AND fhs_band = 'good';

-- Customers with high idle balance (FD candidates)
-- SELECT customer_id, balance, peer_pct_high_balance
-- FROM neptune_c360.customer_peer_stats
-- WHERE balance > 10000 AND peer_pct_high_balance > 80;
