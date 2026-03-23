import boto3, time

athena = boto3.client("athena", region_name="me-south-1")
glue = boto3.client("glue", region_name="me-south-1")
BUCKET = os.environ.get("ATM_S3_DATA_BUCKET", "atm-optimizer-data-me-south-1")
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
            scanned = stats.get("DataScannedInBytes", 0)
            exec_ms = stats.get("EngineExecutionTimeInMillis", 0)
            if desc:
                print(f"    OK ({exec_ms}ms, {scanned/1024:.1f} KB scanned)")
            return qid
        if state in ("FAILED", "CANCELLED"):
            reason = result["QueryExecution"]["Status"].get("StateChangeReason", "")
            print(f"    FAILED: {reason}")
            raise RuntimeError(f"Query failed: {reason}")
        time.sleep(0.5)

def count_rows(table):
    qid = run_query(f"SELECT COUNT(*) FROM {DATABASE}.{table}")
    result = athena.get_query_results(QueryExecutionId=qid)
    return int(result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"])

SEP = "=" * 70
print(SEP)
print("  STEP 1: Fix CSV tables with OpenCSVSerde, then convert to Parquet")
print(SEP)

# --- Fix competitor_atm_locations ---
print("\n--- competitor_atm_locations ---")

run_query(f"DROP TABLE IF EXISTS {DATABASE}.competitor_atm_locations", "Drop existing table")

glue.create_table(
    DatabaseName=DATABASE,
    TableInput={
        "Name": "competitor_atm_locations",
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {"skip.header.line.count": "1", "classification": "csv"},
        "StorageDescriptor": {
            "Location": f"s3://{BUCKET}/competitor_atm_locations/",
            "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
            "SerdeInfo": {
                "SerializationLibrary": "org.apache.hadoop.hive.serde2.OpenCSVSerde",
                "Parameters": {"separatorChar": ",", "quoteChar": '"'},
            },
            "Columns": [
                {"Name": "competitor_atm_id", "Type": "string"},
                {"Name": "bank_name", "Type": "string"},
                {"Name": "name", "Type": "string"},
                {"Name": "latitude", "Type": "string"},
                {"Name": "longitude", "Type": "string"},
                {"Name": "location_type", "Type": "string"},
                {"Name": "area", "Type": "string"},
                {"Name": "status", "Type": "string"},
            ],
        },
    },
)
print("  Created with OpenCSVSerde")

qid = run_query(f"SELECT COUNT(*) FROM {DATABASE}.competitor_atm_locations WHERE status = 'active'", "Test WHERE status=active")
result = athena.get_query_results(QueryExecutionId=qid)
active_count = result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"]
print(f"  Active competitors: {active_count}")

run_query(f"DROP TABLE IF EXISTS {DATABASE}.competitor_atm_locations_pq", "Drop temp parquet table")

start = time.time()
run_query("""
    CREATE TABLE {db}.competitor_atm_locations_pq
    WITH (format = 'PARQUET', parquet_compression = 'SNAPPY')
    AS
    SELECT competitor_atm_id,
           bank_name,
           name,
           CAST(latitude AS DOUBLE) AS latitude,
           CAST(longitude AS DOUBLE) AS longitude,
           location_type,
           area,
           status
    FROM {db}.competitor_atm_locations
""".format(db=DATABASE), "CTAS to Parquet")
elapsed1 = time.time() - start

rows1 = count_rows("competitor_atm_locations_pq")
print(f"  Parquet rows: {rows1}")

pq_info = glue.get_table(DatabaseName=DATABASE, Name="competitor_atm_locations_pq")["Table"]
pq_location = pq_info["StorageDescriptor"]["Location"]
pq_sd = pq_info["StorageDescriptor"]

run_query(f"DROP TABLE IF EXISTS {DATABASE}.competitor_atm_locations", "Drop CSV table")
run_query(f"DROP TABLE IF EXISTS {DATABASE}.competitor_atm_locations_pq", "Drop temp CTAS table")

glue.create_table(
    DatabaseName=DATABASE,
    TableInput={
        "Name": "competitor_atm_locations",
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
print(f"  Final Parquet table created at {pq_location}")
verify1 = count_rows("competitor_atm_locations")
print(f"  Verified: {verify1} rows")

# --- Fix competitor_proximity ---
print("\n--- competitor_proximity ---")

run_query(f"DROP TABLE IF EXISTS {DATABASE}.competitor_proximity", "Drop existing table")

glue.create_table(
    DatabaseName=DATABASE,
    TableInput={
        "Name": "competitor_proximity",
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {"skip.header.line.count": "1", "classification": "csv"},
        "StorageDescriptor": {
            "Location": f"s3://{BUCKET}/competitor_proximity/",
            "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
            "SerdeInfo": {
                "SerializationLibrary": "org.apache.hadoop.hive.serde2.OpenCSVSerde",
                "Parameters": {"separatorChar": ",", "quoteChar": '"'},
            },
            "Columns": [
                {"Name": "neobank_atm_id", "Type": "string"},
                {"Name": "competitor_atm_id", "Type": "string"},
                {"Name": "bank_name", "Type": "string"},
                {"Name": "distance_km", "Type": "string"},
            ],
        },
    },
)
print("  Created with OpenCSVSerde")

qid = run_query(f"SELECT COUNT(*) FROM {DATABASE}.competitor_proximity WHERE neobank_atm_id = 'ATM_SEEF_01'", "Test WHERE neobank_atm_id=ATM_SEEF_01")
result = athena.get_query_results(QueryExecutionId=qid)
seef_count = result["ResultSet"]["Rows"][1]["Data"][0]["VarCharValue"]
print(f"  ATM_SEEF_01 proximity records: {seef_count}")

run_query(f"DROP TABLE IF EXISTS {DATABASE}.competitor_proximity_pq", "Drop temp parquet table")

start = time.time()
run_query("""
    CREATE TABLE {db}.competitor_proximity_pq
    WITH (format = 'PARQUET', parquet_compression = 'SNAPPY')
    AS
    SELECT neobank_atm_id,
           competitor_atm_id,
           bank_name,
           CAST(distance_km AS DOUBLE) AS distance_km
    FROM {db}.competitor_proximity
""".format(db=DATABASE), "CTAS to Parquet")
elapsed2 = time.time() - start

rows2 = count_rows("competitor_proximity_pq")
print(f"  Parquet rows: {rows2}")

pq_info2 = glue.get_table(DatabaseName=DATABASE, Name="competitor_proximity_pq")["Table"]
pq_location2 = pq_info2["StorageDescriptor"]["Location"]
pq_sd2 = pq_info2["StorageDescriptor"]

run_query(f"DROP TABLE IF EXISTS {DATABASE}.competitor_proximity", "Drop CSV table")
run_query(f"DROP TABLE IF EXISTS {DATABASE}.competitor_proximity_pq", "Drop temp CTAS table")

glue.create_table(
    DatabaseName=DATABASE,
    TableInput={
        "Name": "competitor_proximity",
        "TableType": "EXTERNAL_TABLE",
        "Parameters": {"classification": "parquet", "EXTERNAL": "TRUE"},
        "StorageDescriptor": {
            "Location": pq_location2,
            "InputFormat": pq_sd2["InputFormat"],
            "OutputFormat": pq_sd2["OutputFormat"],
            "SerdeInfo": pq_sd2["SerdeInfo"],
            "Columns": pq_sd2["Columns"],
        },
    },
)
print(f"  Final Parquet table created at {pq_location2}")
verify2 = count_rows("competitor_proximity")
print(f"  Verified: {verify2} rows")

print(f"\n{SEP}")
print(f"  PARQUET CONVERSION COMPLETE")
print(f"  competitor_atm_locations: {verify1} rows ({elapsed1:.1f}s)")
print(f"  competitor_proximity: {verify2} rows ({elapsed2:.1f}s)")
print(SEP)
