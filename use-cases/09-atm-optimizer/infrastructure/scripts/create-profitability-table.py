#!/usr/bin/env python3
"""
Create pre-aggregated atm_profitability table in Athena.
Combines revenue, maintenance, and cash handling costs per ATM in one table.
This reduces profitability_ranking from ~11s (4 Athena queries) to <1s (1 query on tiny table).

Region: me-south-1
"""

import boto3
import time

REGION = "me-south-1"
DATABASE = "atm_optimizer"
WORKGROUP = "atm-optimizer"

athena = boto3.client("athena", region_name=REGION)


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


def main():
    table = "atm_profitability"
    print(f"Creating pre-aggregated {table} table...")

    print(f"  Dropping {table} if exists...")
    run_query(f"DROP TABLE IF EXISTS {DATABASE}.{table}")

    ctas_sql = f"""
        CREATE TABLE {DATABASE}.{table}
        WITH (format = 'PARQUET', parquet_compression = 'SNAPPY')
        AS
        SELECT
            a.atm_id,
            a.name,
            a.location_type,
            COALESCE(r.total_revenue, 0.0) AS total_revenue,
            COALESCE(r.txn_count, 0) AS txn_count,
            COALESCE(m.total_maintenance_cost, 0.0) AS total_maintenance_cost,
            COALESCE(m.total_downtime, 0.0) AS total_downtime,
            COALESCE(c.total_cash_cost, 0.0) AS total_cash_cost,
            COALESCE(r.total_revenue, 0.0) - COALESCE(m.total_maintenance_cost, 0.0) - COALESCE(c.total_cash_cost, 0.0) AS net_revenue
        FROM {DATABASE}.atm_locations a
        LEFT JOIN (
            SELECT atm_id, SUM(fee) AS total_revenue, COUNT(*) AS txn_count
            FROM {DATABASE}.atm_transactions
            GROUP BY atm_id
        ) r ON a.atm_id = r.atm_id
        LEFT JOIN (
            SELECT atm_id, SUM(CAST(cost AS DOUBLE)) AS total_maintenance_cost, SUM(CAST(downtime_hours AS DOUBLE)) AS total_downtime
            FROM {DATABASE}.maintenance_costs
            GROUP BY atm_id
        ) m ON a.atm_id = m.atm_id
        LEFT JOIN (
            SELECT atm_id, SUM(CAST(replenishment_cost AS DOUBLE)) AS total_cash_cost
            FROM {DATABASE}.cash_levels
            GROUP BY atm_id
        ) c ON a.atm_id = c.atm_id
    """
    print("  Running CTAS...")
    start = time.time()
    exec_info = run_query(ctas_sql)
    elapsed = time.time() - start
    scanned = exec_info.get("Statistics", {}).get("DataScannedInBytes", 0)
    print(f"  Done in {elapsed:.1f}s (scanned {scanned / 1024 / 1024:.1f} MB)")

    # Count rows
    exec_info = run_query(f"SELECT COUNT(*) AS cnt FROM {DATABASE}.{table}")
    result = athena.get_query_results(QueryExecutionId=exec_info["QueryExecutionId"])
    rows = int(result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])
    print(f"  Rows: {rows}")

    # Verify sample
    exec_info = run_query(f"SELECT * FROM {DATABASE}.{table} ORDER BY net_revenue DESC LIMIT 3")
    result = athena.get_query_results(QueryExecutionId=exec_info["QueryExecutionId"])
    cols = [c["Name"] for c in result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
    print(f"\n  Top 3 by net_revenue:")
    for row in result["ResultSet"]["Rows"][1:]:
        vals = {cols[i]: row["Data"][i].get("VarCharValue", "") for i in range(len(cols))}
        print(f"    {vals['atm_id']}: revenue={vals['total_revenue']}, maint={vals['total_maintenance_cost']}, cash={vals['total_cash_cost']}, net={vals['net_revenue']}")

    print(f"\n  atm_profitability table created with {rows} rows.")


if __name__ == "__main__":
    main()
