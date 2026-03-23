import boto3, time

athena = boto3.client("athena", region_name="me-south-1")
DATABASE = "atm_optimizer"
WORKGROUP = "atm-optimizer"

def run_and_time(sql, desc):
    start = time.time()
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
            elapsed = time.time() - start
            stats = result["QueryExecution"]["Statistics"]
            scanned = stats.get("DataScannedInBytes", 0)
            exec_ms = stats.get("EngineExecutionTimeInMillis", 0)
            qr = athena.get_query_results(QueryExecutionId=qid, MaxResults=2)
            row_count = "N/A"
            if len(qr["ResultSet"]["Rows"]) > 1:
                row_count = qr["ResultSet"]["Rows"][1]["Data"][0].get("VarCharValue", "N/A")
            print(f"{desc}")
            print(f"  Wall: {elapsed:.2f}s | Engine: {exec_ms}ms | Scanned: {scanned/1024:.1f} KB | Result: {row_count}")
            return elapsed, exec_ms, scanned
        if state in ("FAILED", "CANCELLED"):
            reason = result["QueryExecution"]["Status"].get("StateChangeReason", "")
            print(f"{desc}: FAILED - {reason}")
            return -1, -1, -1
        time.sleep(0.5)

SEP = "=" * 70
print(SEP)
print("  POST-OPTIMIZATION BENCHMARK (Parquet tables)")
print(SEP)

# Q1: Full scan competitor_atm_locations (Parquet)
run_and_time(
    f"SELECT COUNT(*) FROM {DATABASE}.competitor_atm_locations",
    "Q1: COUNT(*) competitor_atm_locations (Parquet)"
)

# Q2: Full scan competitor_proximity (Parquet)
run_and_time(
    f"SELECT COUNT(*) FROM {DATABASE}.competitor_proximity",
    "Q2: COUNT(*) competitor_proximity (Parquet)"
)

# Q3: WHERE filter on Parquet (should work now!)
run_and_time(
    f"SELECT COUNT(*) FROM {DATABASE}.competitor_atm_locations WHERE status = 'active'",
    "Q3: WHERE status=active (Parquet - should work now)"
)

# Q4: Single ATM proximity (Parquet)
run_and_time(
    f"SELECT COUNT(*) FROM {DATABASE}.competitor_proximity WHERE neobank_atm_id = 'ATM_SEEF_01'",
    "Q4: WHERE neobank_atm_id=ATM_SEEF_01 (Parquet)"
)

# Q5: Competition Index JOIN (Parquet)
run_and_time(
    """SELECT cp.neobank_atm_id,
           COUNT(*) as competitor_count,
           SUM(1.0 / cp.distance_km) as inv_dist_sum
    FROM {db}.competitor_proximity cp
    JOIN {db}.competitor_atm_locations cal
      ON cp.competitor_atm_id = cal.competitor_atm_id
    WHERE cal.status = 'active'
      AND cp.distance_km <= 2.0
      AND cp.distance_km > 0
    GROUP BY cp.neobank_atm_id""".format(db=DATABASE),
    "Q5: Competition Index JOIN+GROUP BY (Parquet)"
)

# Q6: Pre-aggregated competition_index table (instant!)
run_and_time(
    f"SELECT * FROM {DATABASE}.competition_index ORDER BY competition_index DESC",
    "Q6: Pre-aggregated competition_index (single table scan)"
)

# Q7: Single ATM from pre-aggregated table
run_and_time(
    f"SELECT * FROM {DATABASE}.competition_index WHERE atm_id = 'ATM_SEEF_01'",
    "Q7: Single ATM from competition_index"
)

# Verify all tables
print("\n--- Table Format Verification ---")
glue = boto3.client("glue", region_name="me-south-1")
for table in ["competitor_atm_locations", "competitor_proximity", "competition_index"]:
    try:
        resp = glue.get_table(DatabaseName=DATABASE, Name=table)
        sd = resp["Table"]["StorageDescriptor"]
        serde = sd["SerdeInfo"]["SerializationLibrary"]
        fmt = "PARQUET" if "parquet" in serde.lower() else "CSV"
        print(f"  {table:<30} {fmt}")
    except Exception as e:
        print(f"  {table:<30} ERROR: {e}")

print(f"\n{SEP}")
print("  BENCHMARK COMPLETE")
print(SEP)
