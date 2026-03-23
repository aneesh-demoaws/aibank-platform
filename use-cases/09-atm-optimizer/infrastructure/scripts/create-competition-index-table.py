#!/usr/bin/env python3
"""
Create pre-aggregated competition_index table in Athena.
Pre-computes Competition Index per NeoBank ATM at 2km radius.
This reduces query_competitor_analysis (all ATMs) from ~1s JOIN to <0.5s single table scan.

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
    table = "competition_index"
    print(f"Creating pre-aggregated {table} table...")

    print(f"  Dropping {table} if exists...")
    run_query(f"DROP TABLE IF EXISTS {DATABASE}.{table}")

    ctas_sql = f"""
        CREATE TABLE {DATABASE}.{table}
        WITH (format = 'PARQUET', parquet_compression = 'SNAPPY')
        AS
        WITH nearby AS (
            SELECT cp.neobank_atm_id,
                   cp.competitor_atm_id,
                   cp.bank_name,
                   cp.distance_km
            FROM {DATABASE}.competitor_proximity cp
            JOIN {DATABASE}.competitor_atm_locations cal
              ON cp.competitor_atm_id = cal.competitor_atm_id
            WHERE cal.status = 'active'
              AND cp.distance_km <= 2.0
              AND cp.distance_km > 0
        ),
        agg AS (
            SELECT neobank_atm_id,
                   COUNT(*) AS competitor_count,
                   SUM(1.0 / distance_km) AS inv_dist_sum,
                   MIN(distance_km) AS nearest_competitor_km,
                   MAX(distance_km) AS farthest_competitor_km
            FROM nearby
            GROUP BY neobank_atm_id
        )
        SELECT a.atm_id,
               a.name,
               a.location_type,
               COALESCE(ag.competitor_count, 0) AS competitor_count_2km,
               COALESCE(LEAST(ag.inv_dist_sum / 5.0, 1.0), 0.0) AS competition_index,
               COALESCE(ag.nearest_competitor_km, 999.0) AS nearest_competitor_km,
               COALESCE(ag.farthest_competitor_km, 0.0) AS farthest_competitor_km
        FROM {DATABASE}.atm_locations a
        LEFT JOIN agg ag ON a.atm_id = ag.neobank_atm_id
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
    exec_info = run_query(f"SELECT * FROM {DATABASE}.{table} ORDER BY competition_index DESC LIMIT 5")
    result = athena.get_query_results(QueryExecutionId=exec_info["QueryExecutionId"])
    cols = [c["Name"] for c in result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
    print(f"\n  Top 5 by competition_index:")
    for row in result["ResultSet"]["Rows"][1:]:
        vals = {cols[i]: row["Data"][i].get("VarCharValue", "") for i in range(len(cols))}
        print(f"    {vals['atm_id']}: CI={vals['competition_index']}, competitors={vals['competitor_count_2km']}, nearest={vals['nearest_competitor_km']}km")

    print(f"\n  {table} table created with {rows} rows.")


if __name__ == "__main__":
    main()
