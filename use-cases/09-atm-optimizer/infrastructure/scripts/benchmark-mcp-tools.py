#!/usr/bin/env python3
"""
Benchmark all 8 MCP tools via AWS Lambda invoke (SigV4).
Region: me-south-1
Lambda: ATM-Profitability-Optimizer-MCP-Server
"""

import boto3
import json
import time
import base64
import re
from datetime import datetime, timezone

FUNCTION_NAME = "ATM-Profitability-Optimizer-MCP-Server"
REGION = "me-south-1"

TOOLS = [
    ("query_atm_data", {"atm_id": "ATM_SEEF_01", "start_date": "2024-01-01", "end_date": "2024-12-31"}),
    ("query_branch_proximity", {"atm_id": "ATM_SEEF_01", "radius_km": 5}),
    ("query_revenue_data", {"atm_id": "ATM_SEEF_01", "period": "monthly"}),
    ("query_maintenance_costs", {"atm_id": "ATM_SEEF_01", "start_date": "2024-01-01", "end_date": "2024-12-31"}),
    ("query_cash_levels", {"atm_id": "ATM_SEEF_01"}),
    ("calculate_impact_analysis", {"atm_id": "ATM_SEEF_01", "downtime_days": 5}),
    ("profitability_ranking", {"top_n": 5, "sort": "net_revenue"}),
    ("detect_anomalies", {"period": "30d"}),
]

client = boto3.client("lambda", region_name=REGION)

print("=" * 60)
print("  MCP Tool Performance Baseline")
print(f"  Lambda: {FUNCTION_NAME}")
print(f"  Region: {REGION}")
print(f"  Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
print("=" * 60)
print()

results = []

for idx, (tool_name, params) in enumerate(TOOLS, 1):
    # Wrap in Function URL event envelope — handler reads event["body"]
    inner = json.dumps({"tool_name": tool_name, "parameters": params})
    payload = json.dumps({"body": inner})
    print(f"[{idx}/8] Testing: {tool_name} ...", end=" ", flush=True)

    start = time.time()
    resp = client.invoke(
        FunctionName=FUNCTION_NAME,
        InvocationType="RequestResponse",
        LogType="Tail",
        Payload=payload.encode("utf-8"),
    )
    wall_ms = int((time.time() - start) * 1000)

    # Parse Lambda response
    resp_payload = json.loads(resp["Payload"].read().decode("utf-8"))
    status_code = resp_payload.get("statusCode", "N/A")
    body = resp_payload.get("body", "{}")
    if isinstance(body, str):
        body = json.loads(body)
    status = body.get("status", "unknown")

    # Parse REPORT line from log tail
    lambda_dur = billed_dur = max_mem = "N/A"
    log_tail = base64.b64decode(resp.get("LogResult", "")).decode("utf-8", "ignore")
    for line in log_tail.split("\n"):
        if "REPORT" in line:
            m = re.search(r"Duration:\s+([\d.]+)\s+ms", line)
            if m:
                lambda_dur = m.group(1)
            m = re.search(r"Billed Duration:\s+(\d+)\s+ms", line)
            if m:
                billed_dur = m.group(1)
            m = re.search(r"Max Memory Used:\s+(\d+)\s+MB", line)
            if m:
                max_mem = m.group(1)

    # Count result items
    result_data = body.get("result", [])
    item_count = len(result_data) if isinstance(result_data, list) else "obj"

    # Error detail if failed
    error_msg = ""
    if status != "success":
        error_msg = body.get("error", "")[:80]

    results.append({
        "tool": tool_name,
        "wall_ms": wall_ms,
        "lambda_ms": lambda_dur,
        "billed_ms": billed_dur,
        "max_mem_mb": max_mem,
        "status": status,
        "http": status_code,
        "items": item_count,
        "error": error_msg,
    })

    if status == "success":
        print(f"OK  wall={wall_ms}ms  lambda={lambda_dur}ms  mem={max_mem}MB  items={item_count}")
    else:
        print(f"FAIL  wall={wall_ms}ms  error={error_msg}")

print()
print("=" * 60)
print("  BASELINE RESULTS SUMMARY")
print("=" * 60)
print()
print(f"{'Tool':<30} {'Wall(ms)':>9} {'Lambda(ms)':>11} {'Billed(ms)':>11} {'Mem(MB)':>8} {'Items':>6} {'Status':>8}")
print(f"{'-'*30} {'-'*9} {'-'*11} {'-'*11} {'-'*8} {'-'*6} {'-'*8}")

for r in results:
    print(f"{r['tool']:<30} {r['wall_ms']:>9} {r['lambda_ms']:>11} {r['billed_ms']:>11} {r['max_mem_mb']:>8} {str(r['items']):>6} {r['status']:>8}")

# Print any errors
errors = [r for r in results if r["status"] != "success"]
if errors:
    print()
    print("ERRORS:")
    for r in errors:
        print(f"  {r['tool']}: {r['error']}")

print()
print("Done.")
