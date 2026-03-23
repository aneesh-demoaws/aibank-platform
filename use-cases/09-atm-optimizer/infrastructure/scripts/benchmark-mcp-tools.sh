#!/bin/bash
# Benchmark all 8 MCP tools via Lambda invoke (SigV4 auth)
# Region: me-south-1
# Lambda: ATM-Profitability-Optimizer-MCP-Server

FUNCTION_NAME="ATM-Profitability-Optimizer-MCP-Server"
REGION="me-south-1"
OUTDIR="/tmp/benchmark_results"
mkdir -p "$OUTDIR"

echo "=============================================="
echo "  MCP Tool Performance Baseline"
echo "  Lambda: $FUNCTION_NAME"
echo "  Region: $REGION"
echo "  Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=============================================="
echo ""

# Tool payloads
declare -a TOOLS=(
  "query_atm_data"
  "query_branch_proximity"
  "query_revenue_data"
  "query_maintenance_costs"
  "query_cash_levels"
  "calculate_impact_analysis"
  "profitability_ranking"
  "detect_anomalies"
)

declare -a PAYLOADS=(
  '{"tool_name":"query_atm_data","parameters":{"atm_id":"ATM_SEEF_01","start_date":"2024-01-01","end_date":"2024-12-31"}}'
  '{"tool_name":"query_branch_proximity","parameters":{"atm_id":"ATM_SEEF_01","radius_km":5}}'
  '{"tool_name":"query_revenue_data","parameters":{"atm_id":"ATM_SEEF_01","period":"monthly"}}'
  '{"tool_name":"query_maintenance_costs","parameters":{"atm_id":"ATM_SEEF_01","start_date":"2024-01-01","end_date":"2024-12-31"}}'
  '{"tool_name":"query_cash_levels","parameters":{"atm_id":"ATM_SEEF_01"}}'
  '{"tool_name":"calculate_impact_analysis","parameters":{"atm_id":"ATM_SEEF_01","downtime_days":5}}'
  '{"tool_name":"profitability_ranking","parameters":{"top_n":5,"sort":"net_revenue"}}'
  '{"tool_name":"detect_anomalies","parameters":{"period":"30d"}}'
)

# Results array
declare -a RESULTS=()

for i in "${!TOOLS[@]}"; do
  TOOL="${TOOLS[$i]}"
  PAYLOAD="${PAYLOADS[$i]}"
  OUTFILE="$OUTDIR/${TOOL}.json"
  LOGFILE="$OUTDIR/${TOOL}.log"

  echo "[$((i+1))/8] Testing: $TOOL"

  # Write payload to temp file
  echo "$PAYLOAD" > "$OUTDIR/${TOOL}_payload.json"

  # Time the invocation
  START_MS=$(python3 -c "import time; print(int(time.time()*1000))")

  aws lambda invoke \
    --function-name "$FUNCTION_NAME" \
    --region "$REGION" \
    --payload "file://$OUTDIR/${TOOL}_payload.json" \
    --cli-read-timeout 300 \
    --log-type Tail \
    "$OUTFILE" > "$LOGFILE" 2>&1

  END_MS=$(python3 -c "import time; print(int(time.time()*1000))")
  WALL_MS=$((END_MS - START_MS))

  # Extract Lambda duration from log tail
  LOG_RESULT=$(cat "$LOGFILE")
  LAMBDA_DURATION=$(echo "$LOG_RESULT" | python3 -c "
import sys, json, base64
try:
    data = json.load(sys.stdin)
    log = base64.b64decode(data.get('LogResult','')).decode('utf-8','ignore')
    for line in log.split('\n'):
        if 'REPORT' in line and 'Duration' in line:
            parts = line.split()
            for j, p in enumerate(parts):
                if p == 'Duration:':
                    print(parts[j+1])
                    break
except:
    print('N/A')
" 2>/dev/null)

  # Extract billed duration
  BILLED_DURATION=$(echo "$LOG_RESULT" | python3 -c "
import sys, json, base64
try:
    data = json.load(sys.stdin)
    log = base64.b64decode(data.get('LogResult','')).decode('utf-8','ignore')
    for line in log.split('\n'):
        if 'REPORT' in line and 'Billed Duration' in line:
            parts = line.split()
            for j, p in enumerate(parts):
                if p == 'Billed' and j+1 < len(parts) and parts[j+1] == 'Duration:':
                    print(parts[j+2])
                    break
except:
    print('N/A')
" 2>/dev/null)

  # Extract max memory used
  MAX_MEMORY=$(echo "$LOG_RESULT" | python3 -c "
import sys, json, base64
try:
    data = json.load(sys.stdin)
    log = base64.b64decode(data.get('LogResult','')).decode('utf-8','ignore')
    for line in log.split('\n'):
        if 'REPORT' in line and 'Max Memory Used' in line:
            parts = line.split()
            for j, p in enumerate(parts):
                if p == 'Max' and j+2 < len(parts) and parts[j+1] == 'Memory' and parts[j+2] == 'Used:':
                    print(parts[j+3])
                    break
except:
    print('N/A')
" 2>/dev/null)

  # Check status from response
  STATUS=$(python3 -c "
import json
try:
    with open('$OUTFILE') as f:
        resp = json.load(f)
    body = json.loads(resp.get('body','{}')) if isinstance(resp.get('body'), str) else resp.get('body',{})
    print(body.get('status','unknown'))
except:
    print('error')
" 2>/dev/null)

  # Check HTTP status code
  HTTP_CODE=$(python3 -c "
import json
try:
    with open('$OUTFILE') as f:
        resp = json.load(f)
    print(resp.get('statusCode','N/A'))
except:
    print('N/A')
" 2>/dev/null)

  RESULT_LINE="$TOOL|${WALL_MS}ms|${LAMBDA_DURATION}ms|${BILLED_DURATION}ms|${MAX_MEMORY}MB|$STATUS|$HTTP_CODE"
  RESULTS+=("$RESULT_LINE")

  echo "       Wall: ${WALL_MS}ms | Lambda: ${LAMBDA_DURATION}ms | Billed: ${BILLED_DURATION}ms | Mem: ${MAX_MEMORY}MB | Status: $STATUS"
  echo ""
done

echo ""
echo "=============================================="
echo "  BASELINE RESULTS SUMMARY"
echo "=============================================="
echo ""
printf "%-30s %10s %12s %12s %10s %8s %6s\n" "Tool" "Wall(ms)" "Lambda(ms)" "Billed(ms)" "Mem(MB)" "Status" "HTTP"
printf "%-30s %10s %12s %12s %10s %8s %6s\n" "------------------------------" "----------" "------------" "------------" "----------" "--------" "------"

for r in "${RESULTS[@]}"; do
  IFS='|' read -r tool wall lambda billed mem status http <<< "$r"
  printf "%-30s %10s %12s %12s %10s %8s %6s\n" "$tool" "$wall" "$lambda" "$billed" "$mem" "$status" "$http"
done

echo ""
echo "Raw outputs saved to: $OUTDIR/"
