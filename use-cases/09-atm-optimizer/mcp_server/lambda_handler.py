"""
AWS Lambda handler for ATM Profitability Optimizer MCP Server.

Deployed in me-south-1 (Bahrain) with a Function URL using IAM auth (SigV4).
AgentCore in eu-west-1 calls this Function URL to execute MCP tool calls.

The handler receives JSON tool call requests and routes them to the
appropriate tool function. All tool functions query Athena in me-south-1.

Request format:
    {
        "tool_name": "query_atm_data",
        "parameters": {
            "atm_id": "ATM_SEEF_01",
            "start_date": "2024-01-01",
            "end_date": "2024-01-31"
        }
    }

Response format:
    {
        "status": "success",
        "tool_name": "query_atm_data",
        "result": { ... }
    }
"""

import json
import logging
import os
import sys
import traceback

# Ensure project root is on path for Lambda packaging
LAMBDA_TASK_ROOT = os.environ.get("LAMBDA_TASK_ROOT", "")
if LAMBDA_TASK_ROOT:
    sys.path.insert(0, LAMBDA_TASK_ROOT)

from agent.tools.query_atm_data import query_atm_data
from agent.tools.query_branch_proximity import query_branch_proximity
from agent.tools.query_revenue_data import query_revenue_data
from agent.tools.query_maintenance_costs import query_maintenance_costs
from agent.tools.query_cash_levels import query_cash_levels
from agent.tools.calculate_impact_analysis import calculate_impact_analysis
from agent.tools.detect_anomalies import detect_anomalies
from agent.tools.profitability_ranking import profitability_ranking
from agent.tools.query_competitor_analysis import query_competitor_analysis
from agent.tools.query_coverage_analysis import query_coverage_analysis
from agent.tools.simulate_competitor_scenario import simulate_competitor_scenario
from agent.tools.recommend_atm_placement import recommend_atm_placement

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

# Tool registry — maps tool name to (function, required_params, optional_params)
TOOL_REGISTRY = {
    "query_atm_data": {
        "fn": query_atm_data,
        "required": ["atm_id", "start_date", "end_date"],
        "optional": [],
    },
    "query_branch_proximity": {
        "fn": query_branch_proximity,
        "required": ["atm_id"],
        "optional": ["radius_km"],
    },
    "query_revenue_data": {
        "fn": query_revenue_data,
        "required": ["atm_id"],
        "optional": ["period"],
    },
    "query_maintenance_costs": {
        "fn": query_maintenance_costs,
        "required": ["atm_id", "start_date", "end_date"],
        "optional": [],
    },
    "query_cash_levels": {
        "fn": query_cash_levels,
        "required": ["atm_id"],
        "optional": [],
    },
    "calculate_impact_analysis": {
        "fn": calculate_impact_analysis,
        "required": ["atm_id", "downtime_days"],
        "optional": [],
    },
    "detect_anomalies": {
        "fn": detect_anomalies,
        "required": [],
        "optional": ["atm_id", "period"],
    },
    "profitability_ranking": {
        "fn": profitability_ranking,
        "required": [],
        "optional": ["top_n", "sort"],
    },
    "query_competitor_analysis": {
        "fn": query_competitor_analysis,
        "required": [],
        "optional": ["atm_id", "radius_km"],
    },
    "query_coverage_analysis": {
        "fn": query_coverage_analysis,
        "required": [],
        "optional": ["radius_km"],
    },
    "simulate_competitor_scenario": {
        "fn": simulate_competitor_scenario,
        "required": ["scenario_type", "latitude", "longitude", "bank_name"],
        "optional": ["radius_km"],
    },
    "recommend_atm_placement": {
        "fn": recommend_atm_placement,
        "required": [],
        "optional": ["count", "radius_km"],
    },
}


def _parse_body(event: dict) -> dict:
    """Extract and parse the JSON body from a Function URL event."""
    body = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        import base64
        body = base64.b64decode(body).decode("utf-8")
    if isinstance(body, str):
        body = json.loads(body)
    return body


def _validate_request(body: dict) -> tuple[str | None, dict | None]:
    """Validate the tool call request. Returns (error_message, None) or (None, validated_body)."""
    tool_name = body.get("tool_name")
    if not tool_name:
        return "Missing required field: tool_name", None

    if tool_name not in TOOL_REGISTRY:
        available = sorted(TOOL_REGISTRY.keys())
        return f"Unknown tool: {tool_name}. Available: {available}", None

    params = body.get("parameters", {})
    if not isinstance(params, dict):
        return "parameters must be a JSON object", None

    tool_spec = TOOL_REGISTRY[tool_name]
    missing = [p for p in tool_spec["required"] if p not in params]
    if missing:
        return f"Missing required parameters for {tool_name}: {missing}", None

    return None, body


def _make_response(status_code: int, body: dict) -> dict:
    """Build a Function URL response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def _handle_admin_query(sql: str) -> dict:
    """Execute an arbitrary Athena SQL query (for admin/migration tasks).

    This runs inside the VPC so it has S3 write access for CTAS operations.
    """
    import boto3
    import time as _time

    region = os.environ.get("AWS_REGION", "me-south-1")
    database = os.environ.get("ATM_ATHENA_DATABASE", "atm_optimizer")
    workgroup = os.environ.get("ATM_ATHENA_WORKGROUP", "atm-optimizer")

    client = boto3.client("athena", region_name=region)

    # For CTAS with external_location, we need a non-enforced workgroup
    # Try the conversion workgroup first, fall back to default
    try:
        client.get_work_group(WorkGroup="atm-parquet-conversion")
        use_wg = "atm-parquet-conversion"
    except Exception:
        use_wg = workgroup

    resp = client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        WorkGroup=use_wg,
    )
    qid = resp["QueryExecutionId"]

    # Poll for completion (up to 5 min)
    deadline = _time.monotonic() + 300
    while _time.monotonic() < deadline:
        status = client.get_query_execution(QueryExecutionId=qid)
        state = status["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            stats = status["QueryExecution"].get("Statistics", {})
            return _make_response(200, {
                "status": "success",
                "query_id": qid,
                "state": state,
                "data_scanned_bytes": stats.get("DataScannedInBytes", 0),
                "execution_time_ms": stats.get("EngineExecutionTimeInMillis", 0),
            })
        if state in ("FAILED", "CANCELLED"):
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "")
            return _make_response(500, {
                "status": "error",
                "query_id": qid,
                "state": state,
                "error": reason,
            })
        _time.sleep(2)

    return _make_response(504, {"status": "error", "error": "Query timed out after 300s", "query_id": qid})


def _extract_gateway_tool_name(context) -> str | None:
    """Extract tool name from AgentCore Gateway context.

    AgentCore Gateway passes tool name as: {target_name}___{tool_name}
    We strip the target prefix and return just the tool name.
    """
    try:
        if context and hasattr(context, 'client_context') and context.client_context:
            custom = getattr(context.client_context, 'custom', None) or {}
            full_name = custom.get('bedrockAgentCoreToolName', '')
            if full_name:
                delimiter = "___"
                if delimiter in full_name:
                    return full_name[full_name.index(delimiter) + len(delimiter):]
                return full_name
    except Exception:
        pass
    return None


def _is_gateway_invocation(event: dict, context) -> bool:
    """Detect if this is an AgentCore Gateway invocation vs Function URL.

    Gateway invocations pass tool parameters directly as the event dict
    and tool name via context.client_context.custom.
    Function URL invocations have 'requestContext', 'body', etc.
    """
    # Function URL events have requestContext with http method
    if "requestContext" in event or "body" in event:
        return False
    # Gateway events are just the tool parameters directly
    if _extract_gateway_tool_name(context):
        return True
    return False


def handler(event, context):
    """Lambda handler supporting both Function URL and AgentCore Gateway invocations.

    Function URL: POST with JSON body containing tool_name and parameters.
    AgentCore Gateway: Tool parameters as event, tool name in context.client_context.
    """
    # --- AgentCore Gateway invocation ---
    if _is_gateway_invocation(event, context):
        tool_name = _extract_gateway_tool_name(context)
        if not tool_name or tool_name not in TOOL_REGISTRY:
            return {"error": f"Unknown tool: {tool_name}. Available: {sorted(TOOL_REGISTRY.keys())}"}

        tool_spec = TOOL_REGISTRY[tool_name]
        params = {k: v for k, v in event.items() if k in (tool_spec["required"] + tool_spec["optional"])}
        logger.info("Gateway tool call: %s params=%s", tool_name, list(params.keys()))

        try:
            result = tool_spec["fn"](**params)
            return json.loads(json.dumps(result, default=str))
        except Exception as e:
            logger.error("Gateway tool %s failed: %s\n%s", tool_name, e, traceback.format_exc())
            return {"error": f"Tool execution failed: {str(e)}"}

    # --- Function URL invocation ---
    # Handle health check / GET requests
    method = event.get("requestContext", {}).get("http", {}).get("method", "POST")
    if method == "GET":
        return _make_response(200, {
            "service": "ATM Profitability Optimizer MCP Server",
            "region": os.environ.get("AWS_REGION", "me-south-1"),
            "tools": sorted(TOOL_REGISTRY.keys()),
            "status": "healthy",
        })

    try:
        body = _parse_body(event)
    except (json.JSONDecodeError, Exception) as e:
        logger.error("Failed to parse request body: %s", e)
        return _make_response(400, {"status": "error", "error": f"Invalid JSON: {str(e)}"})

    error, _ = _validate_request(body)
    if error:
        # Check for admin_query (temporary — for CTAS/DDL operations)
        if body.get("admin_query"):
            return _handle_admin_query(body["admin_query"])
        return _make_response(400, {"status": "error", "error": error})

    tool_name = body["tool_name"]
    params = body.get("parameters", {})
    tool_spec = TOOL_REGISTRY[tool_name]

    logger.info("Executing tool: %s with params: %s", tool_name, list(params.keys()))

    try:
        result = tool_spec["fn"](**params)
        return _make_response(200, {
            "status": "success",
            "tool_name": tool_name,
            "result": result,
        })
    except Exception as e:
        logger.error("Tool %s failed: %s\n%s", tool_name, e, traceback.format_exc())
        return _make_response(500, {
            "status": "error",
            "tool_name": tool_name,
            "error": f"Tool execution failed: {str(e)}",
        })
