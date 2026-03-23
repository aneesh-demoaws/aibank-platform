import boto3, time

athena = boto3.client("athena", region_name="me-south-1")
glue = boto3.client("glue", region_name="me-south-1")
DATABASE = "atm_optimizer"
WORKGROUP = "atm-optimizer"

def run_query(sql, desc=""):
    if desc:
        print(f"  {desc}...")
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DATABASE},
        WorkGroup=WORKGROUP,
    )
    qid = resp["QueryExecutionId"]
    while True:
        result = athena.get_query_execution(QueryExecutionId=qid)
        state = result["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            stats = result["QueryExecution"].get("Statistics", {})
            exec_ms = stats.get("EngineExecutionTimeInMillis", 0)
            if desc:
                print(f"    OK ({exec_ms}ms)")
            return qid
        if state in ("FAILED", "CANCELLED"):
            reason = result["QueryExecution"]["Status"].get("StateChangeReason", "")
            print(f"    FAILED: {reason}")
            raise RuntimeError(f"Query failed: {reason}")
        time.sleep(0.5)

SEP = "=" * 70
print(SEP)
print("  STEP 2: Create pre-aggregated competition_index table")
print(SEP)

run_query(f"DROP TABLE IF EXISTS {DATABASE}.competition_index", "Drop existing competition_index")

start = time.time()
run_query("""
    CREATE TABLE {db}.competition_index
    WITH (format = 'PARQUET', parquet_compression = 'SNAPPY')
    AS
    WITH nearby AS (
        SELECT cp.neobank_atm_id,
               cp.competitor_atm_id,
               cp.bank_name,
               cp.distance_km
        FROM {db}.competitor_proximity cp
        JOIN {db}.competitor_atm_locations cal
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
    FROM {db}.atm_locations a
    LEFT JOIN agg ag ON a.atm_id = ag.neobank_atm_id
""".format(db=DATABASE), "CTAS competition_index")
elapsed = time.time() - start

qid = run_query(f"SELECT COUNT(*) FROM {DATABASE}.competition_index")
result = athena.get_query_results(QueryExecutionId=qid)
rows = int(result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])
print(f"  Rows: {rows}")

qid = run_query(f"SELECT * FROM {DATABASE}.competition_index ORDER BY competition_index DESC LIMIT 5")
result = athena.get_query_results(QueryExecutionId=qid)
cols = [c["Name"] for c in result["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
print(f"\n  Top 5 by competition_index:")
for row in result["ResultSet"]["Rows"][1:]:
    vals = {cols[i]: row["Data"][i].get("VarCharValue", "") for i in range(len(cols))}
    print(f"    {vals['atm_id']}: CI={vals['competition_index']}, competitors={vals['competitor_count_2km']}, nearest={vals['nearest_competitor_km']}km")

print(f"\n  competition_index table created: {rows} rows in {elapsed:.1f}s")
print(SEP)
