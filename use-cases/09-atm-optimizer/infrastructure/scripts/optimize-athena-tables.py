#!/usr/bin/env python3
"""
Optimize Athena tables for performance:
1. Re-create atm_transactions with proper typed columns (TIMESTAMP, DOUBLE)
2. Create pre-aggregated daily_atm_stats table for anomaly detection

Region: me-south-1
"""

import boto3
import time

REGION = "me-south-1"
DATABASE = "atm_optimizer"
WORKGROUP = "atm-optimizer"

athena = boto3.client("athena", region_name=REGION)
glue = boto3.client("glue", region_name=REGION)


def wait_query(qid: str) -> dict:
    while True:
        resp = athena.get_query_execution(QueryExecutionId=qid)
        state = resp["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            return resp["QueryExecution"]
        if state in ("FAILED", "CANCELLED"):
            reason = resp["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Query {state}: {reason}")
        time.sleep(1)


def run_query(sql: str) -> dict:
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )
    return wait_query(resp["QueryExecutionId"])


def count_rows(table: str) -> int:
    exec_info = run_query(f"SELECT COUNT(*) AS cnt FROM {DATABASE}.{table}")
    qid = exec_info["QueryExecutionId"]
    result = athena.get_query_results(QueryExecutionId=qid)
    return int(result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])


def get_table_location(table: str) -> str:
    resp = glue.get_table(DatabaseName=DATABASE, Name=table)
    return resp["Table"]["StorageDescriptor"]["Location"]


def get_table_info(table: str) -> dict:
    return glue.get_table(DatabaseName=DATABASE, Name=table)["Table"]


def recreate_typed_table(table_name, ctas_select):
    """Drop existing table, CTAS with proper types, rename back."""
    temp = f"{table_name}_typed"
    print(f"\n--- Re-creating {table_name} with proper types ---")

    # Drop temp if exists
    print(f"  Dropping {temp} if exists...")
    run_query(f"DROP TABLE IF EXISTS {DATABASE}.{temp}")

    # CTAS with typed columns
    ctas_sql = f"""
        CREATE TABLE {DATABASE}.{temp}
        WITH (format = 'PARQUET', parquet_compression = 'SNAPPY')
        AS {ctas_select}
    """
    print(f"  Running typed CTAS...")
    start = time.time()
    exec_info = run_query(ctas_sql)
    elapsed = time.time() - start
    scanned = exec_info.get("Statistics", {}).get("DataScannedInBytes", 0)
    print(f"  CTAS done in {elapsed:.1f}s (scanned {scanned / 1024 / 1024:.1f} MB)")

    rows = count_rows(temp)
    print(f"  Rows: {rows:,}")

    pq_location = get_table_location(temp)
    pq_info = get_table_info(temp)
    pq_sd = pq_info["StorageDescriptor"]

    # Drop original
    print(f"  Dropping original '{table_name}'...")
    run_query(f"DROP TABLE IF EXISTS {DATABASE}.{table_name}")

    # Create new table pointing to typed Parquet data
    print(f"  Creating '{table_name}' with typed Parquet...")
    glue.create_table(
        DatabaseName=DATABASE,
        TableInput={
            "Name": table_name,
            "TableType": "EXTERNAL_TABLE",
            "Parameters": {"classification": "parquet", "EXTERNAL": "TRUE"},
            "StorageDescriptor": {
                "Location": pq_location,
                "InputFormat": pq_sd["InputFormat"],
                "OutputFormat": pq_sd["OutputFormat"],
                "SerdeInfo": pq_sd["SerdeInfo"],
                "Columns": pq_sd["Columns"],
            },
        },
    )

    # Drop temp
    run_query(f"DROP TABLE IF EXISTS {DATABASE}.{temp}")

    verify = count_rows(table_name)
    print(f"  Verified: {table_name} has {verify:,} rows")
    return {"rows": verify, "location": pq_location, "time": elapsed}


def create_daily_stats_table():
    """Create pre-aggregated daily_atm_stats table."""
    table = "daily_atm_stats"
    print(f"\n--- Creating {table} (pre-aggregated) ---")

    print(f"  Dropping {table} if exists...")
    run_query(f"DROP TABLE IF EXISTS {DATABASE}.{table}")

    ctas_sql = f"""
        CREATE TABLE {DATABASE}.{table}
        WITH (format = 'PARQUET', parquet_compression = 'SNAPPY')
        AS
        SELECT atm_id,
               CAST(SUBSTR(CAST(txn_timestamp AS VARCHAR), 1, 10) AS DATE) AS txn_date,
               COUNT(*) AS txn_count,
               SUM(amount) AS total_amount,
               SUM(fee) AS total_fee,
               AVG(amount) AS avg_amount,
               MIN(amount) AS min_amount,
               MAX(amount) AS max_amount
        FROM {DATABASE}.atm_transactions
        GROUP BY atm_id, CAST(SUBSTR(CAST(txn_timestamp AS VARCHAR), 1, 10) AS DATE)
    """
    print(f"  Running CTAS...")
    start = time.time()
    exec_info = run_query(ctas_sql)
    elapsed = time.time() - start
    scanned = exec_info.get("Statistics", {}).get("DataScannedInBytes", 0)
    print(f"  CTAS done in {elapsed:.1f}s (scanned {scanned / 1024 / 1024:.1f} MB)")

    rows = count_rows(table)
    print(f"  Rows: {rows:,}")
    return {"rows": rows, "time": elapsed}


def main():
    print("=" * 60)
    print("  Athena Table Optimization")
    print(f"  Region: {REGION}")
    print(f"  Database: {DATABASE}")
    print("=" * 60)

    # Step 1: Re-create atm_transactions with proper types
    txn_result = recreate_typed_table(
        "atm_transactions",
        f"""
        SELECT transaction_id,
               atm_id,
               DATE_PARSE(timestamp, '%Y-%m-%dT%H:%i:%s') AS txn_timestamp,
               transaction_type,
               CAST(amount AS DOUBLE) AS amount,
               CAST(fee AS DOUBLE) AS fee
        FROM {DATABASE}.atm_transactions
        """
    )

    # Step 2: Create daily_atm_stats
    stats_result = create_daily_stats_table()

    # Step 3: Verify schema
    print("\n--- Verifying atm_transactions schema ---")
    cols = glue.get_table(DatabaseName=DATABASE, Name="atm_transactions")["Table"]["StorageDescriptor"]["Columns"]
    for c in cols:
        print(f"  {c['Name']}: {c['Type']}")

    print("\n--- Verifying daily_atm_stats schema ---")
    cols = glue.get_table(DatabaseName=DATABASE, Name="daily_atm_stats")["Table"]["StorageDescriptor"]["Columns"]
    for c in cols:
        print(f"  {c['Name']}: {c['Type']}")

    print(f"\n{'='*60}")
    print("  OPTIMIZATION COMPLETE")
    print(f"{'='*60}")
    print(f"  atm_transactions: {txn_result['rows']:,} rows, typed Parquet ({txn_result['time']:.1f}s)")
    print(f"  daily_atm_stats:  {stats_result['rows']:,} rows, pre-aggregated ({stats_result['time']:.1f}s)")
    print()


if __name__ == "__main__":
    main()
