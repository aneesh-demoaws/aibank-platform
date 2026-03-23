#!/usr/bin/env python3
"""
Convert CSV tables to Parquet using CTAS via the enforced workgroup.

Strategy:
1. CTAS creates a new _parquet table (Athena writes Parquet to its results location)
2. Get the S3 location of the new table from Glue
3. Drop the original CSV table
4. Rename the Parquet table to the original name via Glue update

Region: me-south-1
"""

import boto3
import time
import json

REGION = "me-south-1"
BUCKET = os.environ.get("ATM_S3_DATA_BUCKET", "atm-optimizer-data-me-south-1")
DATABASE = "atm_optimizer"
WORKGROUP = "atm-optimizer"
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "CHANGE_ME")

athena = boto3.client("athena", region_name=REGION)
glue = boto3.client("glue", region_name=REGION)


def wait_query(qid: str) -> dict:
    """Wait for Athena query and return execution details."""
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
    """Run Athena query via enforced workgroup and wait for completion."""
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )
    return wait_query(resp["QueryExecutionId"])


def count_rows(table: str) -> int:
    """Count rows in a table."""
    exec_info = run_query(f"SELECT COUNT(*) AS cnt FROM {DATABASE}.{table}")
    qid = exec_info["QueryExecutionId"]
    result = athena.get_query_results(QueryExecutionId=qid)
    return int(result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])


def get_table_location(table: str) -> str:
    """Get S3 location of a Glue table."""
    resp = glue.get_table(DatabaseName=DATABASE, Name=table)
    return resp["Table"]["StorageDescriptor"]["Location"]


def get_table_info(table: str) -> dict:
    """Get full Glue table info."""
    return glue.get_table(DatabaseName=DATABASE, Name=table)["Table"]


TABLES = [
    "atm_transactions",
    "atm_locations",
    "branch_locations",
    "maintenance_costs",
    "cash_levels",
    "atm_proximity",
]


def main():
    print("=" * 60)
    print("  CSV → Parquet Conversion (via enforced workgroup CTAS)")
    print(f"  Region: {REGION}")
    print(f"  Database: {DATABASE}")
    print(f"  Workgroup: {WORKGROUP}")
    print("=" * 60)

    results = {}

    for table in TABLES:
        parquet_table = f"{table}_pq"
        print(f"\n--- {table} ---")

        # Step 1: Drop temp parquet table if exists
        print(f"  Dropping {parquet_table} if exists...")
        try:
            run_query(f"DROP TABLE IF EXISTS {DATABASE}.{parquet_table}")
        except Exception as e:
            print(f"  Warning dropping: {e}")

        # Step 2: CTAS — no external_location, workgroup picks the path
        ctas_sql = f"""
            CREATE TABLE {DATABASE}.{parquet_table}
            WITH (format = 'PARQUET', parquet_compression = 'SNAPPY')
            AS SELECT * FROM {DATABASE}.{table}
        """
        print(f"  Running CTAS...")
        start = time.time()
        exec_info = run_query(ctas_sql)
        elapsed = time.time() - start
        scanned = exec_info.get("Statistics", {}).get("DataScannedInBytes", 0)
        print(f"  CTAS done in {elapsed:.1f}s (scanned {scanned / 1024 / 1024:.1f} MB)")

        # Step 3: Verify row count
        rows = count_rows(parquet_table)
        print(f"  Rows: {rows:,}")

        # Step 4: Get the Parquet table's S3 location
        pq_location = get_table_location(parquet_table)
        print(f"  Parquet location: {pq_location}")

        # Step 5: Get the Parquet table's full schema from Glue
        pq_info = get_table_info(parquet_table)
        pq_sd = pq_info["StorageDescriptor"]

        # Step 6: Drop the original CSV table
        print(f"  Dropping original CSV table '{table}'...")
        run_query(f"DROP TABLE IF EXISTS {DATABASE}.{table}")

        # Step 7: Rename parquet table to original name by creating new table
        # pointing to the same Parquet data
        print(f"  Creating '{table}' pointing to Parquet data...")
        glue.create_table(
            DatabaseName=DATABASE,
            TableInput={
                "Name": table,
                "TableType": "EXTERNAL_TABLE",
                "Parameters": {
                    "classification": "parquet",
                    "EXTERNAL": "TRUE",
                },
                "StorageDescriptor": {
                    "Location": pq_location,
                    "InputFormat": pq_sd["InputFormat"],
                    "OutputFormat": pq_sd["OutputFormat"],
                    "SerdeInfo": pq_sd["SerdeInfo"],
                    "Columns": pq_sd["Columns"],
                },
            },
        )

        # Step 8: Drop the temp _pq table (data stays in S3)
        print(f"  Dropping temp table '{parquet_table}'...")
        run_query(f"DROP TABLE IF EXISTS {DATABASE}.{parquet_table}")

        # Step 9: Verify the new table works
        verify_rows = count_rows(table)
        print(f"  Verified: {table} has {verify_rows:,} rows (Parquet)")

        results[table] = {"rows": verify_rows, "location": pq_location, "time": elapsed}

    print(f"\n{'='*60}")
    print("  CONVERSION COMPLETE")
    print(f"{'='*60}")
    print(f"{'Table':<25} {'Rows':>10} {'Time(s)':>8} Location")
    print(f"{'-'*25} {'-'*10} {'-'*8} {'-'*40}")
    for t, r in results.items():
        print(f"{t:<25} {r['rows']:>10,} {r['time']:>8.1f} {r['location']}")
    print()
    print("All tables now use Parquet with Snappy compression.")


if __name__ == "__main__":
    main()
