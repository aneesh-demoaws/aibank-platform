#!/usr/bin/env python3
"""
Setup Athena database and table schemas for the ATM Profitability Optimizer.

Creates the atm_optimizer database and all 6 tables:
  - atm_transactions
  - atm_locations
  - branch_locations
  - atm_proximity
  - maintenance_costs
  - cash_levels

All tables are backed by CSV data in S3 and target the me-south-1 (Bahrain) region.

Usage:
    python setup-athena-tables.py [--drop-existing]
"""

import argparse
import sys
import time
import os

# Allow imports from the project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import boto3
from botocore.exceptions import ClientError

from agent.config import (
    ATHENA_DATABASE,
    ATHENA_OUTPUT_LOCATION,
    DATA_REGION,
    S3_DATA_BUCKET,
    S3_PREFIXES,
)

# ---------------------------------------------------------------------------
# Athena DDL statements
# ---------------------------------------------------------------------------

CREATE_DATABASE = f"CREATE DATABASE IF NOT EXISTS {ATHENA_DATABASE}"

DROP_DATABASE = f"DROP DATABASE IF EXISTS {ATHENA_DATABASE} CASCADE"

TABLE_SCHEMAS = {
    "atm_transactions": f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.atm_transactions (
            transaction_id STRING,
            atm_id         STRING,
            `timestamp`    TIMESTAMP,
            transaction_type STRING,
            amount         DECIMAL(10,3),
            fee            DECIMAL(10,3)
        )
        ROW FORMAT DELIMITED
        FIELDS TERMINATED BY ','
        STORED AS TEXTFILE
        LOCATION 's3://{S3_DATA_BUCKET}/{S3_PREFIXES["transactions"]}'
        TBLPROPERTIES ('skip.header.line.count'='1')
    """,

    "atm_locations": f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.atm_locations (
            atm_id         STRING,
            name           STRING,
            latitude       DOUBLE,
            longitude      DOUBLE,
            location_type  STRING,
            branch_id      STRING,
            daily_capacity INT,
            status         STRING
        )
        ROW FORMAT DELIMITED
        FIELDS TERMINATED BY ','
        STORED AS TEXTFILE
        LOCATION 's3://{S3_DATA_BUCKET}/{S3_PREFIXES["atm_locations"]}'
        TBLPROPERTIES ('skip.header.line.count'='1')
    """,

    "branch_locations": f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.branch_locations (
            branch_id          STRING,
            name               STRING,
            latitude           DOUBLE,
            longitude          DOUBLE,
            atm_count          INT,
            avg_daily_footfall INT
        )
        ROW FORMAT DELIMITED
        FIELDS TERMINATED BY ','
        STORED AS TEXTFILE
        LOCATION 's3://{S3_DATA_BUCKET}/{S3_PREFIXES["branch_locations"]}'
        TBLPROPERTIES ('skip.header.line.count'='1')
    """,

    "atm_proximity": f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.atm_proximity (
            source_atm_id STRING,
            target_atm_id STRING,
            distance_km   DOUBLE,
            is_same_branch BOOLEAN
        )
        ROW FORMAT DELIMITED
        FIELDS TERMINATED BY ','
        STORED AS TEXTFILE
        LOCATION 's3://{S3_DATA_BUCKET}/{S3_PREFIXES["proximity"]}'
        TBLPROPERTIES ('skip.header.line.count'='1')
    """,

    "maintenance_costs": f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.maintenance_costs (
            atm_id           STRING,
            `date`           DATE,
            maintenance_type STRING,
            cost             DECIMAL(10,3),
            downtime_hours   DOUBLE
        )
        ROW FORMAT DELIMITED
        FIELDS TERMINATED BY ','
        STORED AS TEXTFILE
        LOCATION 's3://{S3_DATA_BUCKET}/{S3_PREFIXES["maintenance"]}'
        TBLPROPERTIES ('skip.header.line.count'='1')
    """,

    "cash_levels": f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.cash_levels (
            atm_id               STRING,
            `date`               DATE,
            opening_balance      DECIMAL(12,3),
            closing_balance      DECIMAL(12,3),
            total_withdrawals    DECIMAL(12,3),
            replenishment_amount DECIMAL(12,3),
            replenishment_cost   DECIMAL(10,3)
        )
        ROW FORMAT DELIMITED
        FIELDS TERMINATED BY ','
        STORED AS TEXTFILE
        LOCATION 's3://{S3_DATA_BUCKET}/{S3_PREFIXES["cash_levels"]}'
        TBLPROPERTIES ('skip.header.line.count'='1')
    """,

    "competitor_atm_locations": f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.competitor_atm_locations (
            competitor_atm_id STRING,
            bank_name         STRING,
            name              STRING,
            latitude          DOUBLE,
            longitude         DOUBLE,
            location_type     STRING,
            area              STRING,
            status            STRING
        )
        STORED AS PARQUET
        LOCATION 's3://{S3_DATA_BUCKET}/competitor_atm_locations/'
        TBLPROPERTIES ('classification'='parquet')
    """,

    "competitor_proximity": f"""
        CREATE EXTERNAL TABLE IF NOT EXISTS {ATHENA_DATABASE}.competitor_proximity (
            neobank_atm_id    STRING,
            competitor_atm_id STRING,
            bank_name         STRING,
            distance_km       DOUBLE
        )
        STORED AS PARQUET
        LOCATION 's3://{S3_DATA_BUCKET}/competitor_proximity/'
        TBLPROPERTIES ('classification'='parquet')
    """,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_athena_client():
    """Create an Athena client targeting me-south-1."""
    return boto3.client("athena", region_name=DATA_REGION)


def run_query(client, query: str, description: str) -> None:
    """Execute an Athena DDL query and wait for completion."""
    print(f"  Running: {description} …")
    response = client.start_query_execution(
        QueryString=query,
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT_LOCATION},
    )
    query_id = response["QueryExecutionId"]

    # Poll until the query finishes
    while True:
        result = client.get_query_execution(QueryExecutionId=query_id)
        state = result["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)

    if state != "SUCCEEDED":
        reason = result["QueryExecution"]["Status"].get("StateChangeReason", "unknown")
        print(f"    ✗ FAILED: {reason}")
        raise RuntimeError(f"Query failed: {description} — {reason}")

    print(f"    ✓ {description}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def setup_tables(drop_existing: bool = False) -> None:
    """Create the Athena database and all table schemas."""
    client = get_athena_client()

    if drop_existing:
        print("\nDropping existing database …")
        run_query(client, DROP_DATABASE, f"DROP DATABASE {ATHENA_DATABASE}")

    print(f"\nCreating database '{ATHENA_DATABASE}' …")
    run_query(client, CREATE_DATABASE, f"CREATE DATABASE {ATHENA_DATABASE}")

    print(f"\nCreating {len(TABLE_SCHEMAS)} tables:")
    for table_name, ddl in TABLE_SCHEMAS.items():
        run_query(client, ddl, f"CREATE TABLE {table_name}")

    print(f"\n✓ All {len(TABLE_SCHEMAS)} tables created in {ATHENA_DATABASE}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Setup Athena tables for ATM Profitability Optimizer"
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop existing database before recreating (destructive!)",
    )
    args = parser.parse_args()

    print(f"ATM Profitability Optimizer — Athena Table Setup")
    print(f"  Region:   {DATA_REGION}")
    print(f"  Database: {ATHENA_DATABASE}")
    print(f"  S3:       s3://{S3_DATA_BUCKET}/")
    print(f"  Output:   {ATHENA_OUTPUT_LOCATION}")

    try:
        setup_tables(drop_existing=args.drop_existing)
    except ClientError as exc:
        print(f"\nAWS Error: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        print(f"\nError: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
