#!/usr/bin/env python3
"""
Update AgentCore Gateway target with all 12 MCP tool schemas.

Adds the 4 competitor analysis tools that were missing from the original
8-tool deployment. Uses update_gateway_target to modify the existing target
in-place without recreating it.

Usage:
    python3 infrastructure/scripts/update-gateway-tools.py
"""

import boto3
import json
import sys
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "CHANGE_ME")
GATEWAY_REGION = "eu-west-1"
LAMBDA_REGION = "eu-west-1"
LAMBDA_FUNCTION_NAME = "ATM-Profitability-Optimizer-MCP-Gateway"
LAMBDA_ARN = f"arn:aws:lambda:{LAMBDA_REGION}:{ACCOUNT_ID}:function:{LAMBDA_FUNCTION_NAME}"
GATEWAY_NAME = "atm-optimizer-gateway"
TARGET_NAME = "atm-mcp-tools"

# All 12 tool schemas
TOOL_SCHEMAS = [
    {
        "name": "query_atm_data",
        "description": "Query ATM transaction data with location info, filtered by ATM ID and date range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "ATM identifier (e.g. ATM_SEEF_01)"},
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["atm_id", "start_date", "end_date"],
        },
    },
    {
        "name": "query_branch_proximity",
        "description": "Find ATMs and branches within a radius of a given ATM.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "Source ATM identifier"},
                "radius_km": {"type": "number", "description": "Search radius in km (default: 5)"},
            },
            "required": ["atm_id"],
        },
    },
    {
        "name": "query_revenue_data",
        "description": "Query revenue metrics for an ATM with period aggregation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "ATM identifier"},
                "period": {"type": "string", "description": "daily, monthly, quarterly"},
            },
            "required": ["atm_id"],
        },
    },
    {
        "name": "query_maintenance_costs",
        "description": "Query maintenance cost history for an ATM. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "ATM identifier"},
                "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
            },
            "required": ["atm_id", "start_date", "end_date"],
        },
    },
    {
        "name": "query_cash_levels",
        "description": "Query current cash levels and 7-day forecast. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "ATM identifier"},
            },
            "required": ["atm_id"],
        },
    },
    {
        "name": "calculate_impact_analysis",
        "description": "Calculate revenue impact and traffic redistribution for ATM downtime. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "ATM identifier"},
                "downtime_days": {"type": "integer", "description": "Number of downtime days"},
            },
            "required": ["atm_id", "downtime_days"],
        },
    },
    {
        "name": "detect_anomalies",
        "description": "Detect ATMs with unusual transaction patterns. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "Optional ATM to check"},
                "period": {"type": "string", "description": "Analysis period (e.g. 30d)"},
            },
            "required": [],
        },
    },
    {
        "name": "profitability_ranking",
        "description": "Rank ATMs by net revenue. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "description": "Number of ATMs (default: 28)"},
                "sort": {"type": "string", "description": "net_revenue, gross_revenue, or costs"},
            },
            "required": [],
        },
    },
    {
        "name": "query_competitor_analysis",
        "description": "Calculate Competition Index for NeoBank ATMs based on nearby competitor bank ATMs. Returns competition pressure scores from 0.0 (no competition) to 1.0 (high competition). Use this for any question about competition index, competitive pressure, or nearby competitors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "Optional ATM identifier. Omit for all ATMs."},
                "radius_km": {"type": "number", "description": "Search radius in km (default: 2.0)"},
            },
            "required": [],
        },
    },
    {
        "name": "query_coverage_analysis",
        "description": "Identify coverage gaps, advantages, and market share vs competitor banks by governorate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "radius_km": {"type": "number", "description": "Analysis radius in km (default: 2.0)"},
            },
            "required": [],
        },
    },
    {
        "name": "simulate_competitor_scenario",
        "description": "Simulate impact of a competitor bank opening or closing an ATM. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scenario_type": {"type": "string", "description": "'add' or 'remove'"},
                "latitude": {"type": "number", "description": "GPS latitude (Bahrain: 25.5-26.3)"},
                "longitude": {"type": "number", "description": "GPS longitude (Bahrain: 50.4-50.8)"},
                "bank_name": {"type": "string", "description": "Competitor bank name"},
                "radius_km": {"type": "number", "description": "Impact radius in km (default: 2.0)"},
            },
            "required": ["scenario_type", "latitude", "longitude", "bank_name"],
        },
    },
    {
        "name": "recommend_atm_placement",
        "description": "Recommend optimal locations for new NeoBank ATMs based on coverage gaps and competitor density. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "Number of recommendations (default: 3)"},
                "radius_km": {"type": "number", "description": "Analysis radius in km (default: 2.0)"},
            },
            "required": [],
        },
    },
]

agentcore = boto3.client("bedrock-agentcore-control", region_name=GATEWAY_REGION)


def find_gateway():
    """Find the existing gateway by name."""
    resp = agentcore.list_gateways()
    for gw in resp.get("items", []):
        if gw.get("name") == GATEWAY_NAME:
            return gw["gatewayId"]
    return None


def find_target(gateway_id):
    """Find the existing target by name."""
    resp = agentcore.list_gateway_targets(gatewayIdentifier=gateway_id)
    for t in resp.get("items", []):
        if t.get("name") == TARGET_NAME:
            return t["targetId"]
    return None


def main():
    print("=" * 60)
    print("Update AgentCore Gateway: Add Competitor Tools")
    print("=" * 60)

    # Find gateway
    gateway_id = find_gateway()
    if not gateway_id:
        print(f"ERROR: Gateway '{GATEWAY_NAME}' not found in {GATEWAY_REGION}")
        sys.exit(1)
    print(f"Gateway: {gateway_id}")

    # Find target
    target_id = find_target(gateway_id)
    if not target_id:
        print(f"Target '{TARGET_NAME}' not found — creating new target...")
    else:
        print(f"Target: {target_id}")
    print(f"Tools: {len(TOOL_SCHEMAS)}")

    if target_id:
        # Try update_gateway_target first
        try:
            print("\nUpdating target with 12 tool schemas...")
            agentcore.update_gateway_target(
                gatewayIdentifier=gateway_id,
                targetId=target_id,
                name=TARGET_NAME,
                targetConfiguration={
                    "mcp": {
                        "lambda": {
                            "lambdaArn": LAMBDA_ARN,
                            "toolSchema": {
                                "inlinePayload": TOOL_SCHEMAS,
                            },
                        }
                    }
                },
                credentialProviderConfigurations=[
                    {"credentialProviderType": "GATEWAY_IAM_ROLE"}
                ],
            )
            print("Update submitted.")
        except Exception as e:
            print(f"update_gateway_target failed: {e}")
            print("\nFallback: deleting and recreating target...")

            # Delete existing target
            try:
                agentcore.delete_gateway_target(
                    gatewayIdentifier=gateway_id,
                    targetId=target_id,
                )
                print("  Deleted old target. Waiting 10s...")
                time.sleep(10)
            except Exception as de:
                print(f"  Delete failed: {de}")
                sys.exit(1)
            target_id = None

    if not target_id:
        # Create new target
        print(f"\nCreating target '{TARGET_NAME}' with 12 tools...")
        print(f"  Lambda ARN: {LAMBDA_ARN}")
        resp = agentcore.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=TARGET_NAME,
            description="ATM Profitability Optimizer MCP tools (12 tools, Lambda in eu-west-1)",
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": LAMBDA_ARN,
                        "toolSchema": {
                            "inlinePayload": TOOL_SCHEMAS,
                        },
                    }
                }
            },
            credentialProviderConfigurations=[
                {"credentialProviderType": "GATEWAY_IAM_ROLE"}
            ],
        )
        target_id = resp["targetId"]
        print(f"  New target ID: {target_id}")

    # Wait for target to become active
    print("Waiting for target to become active...")
    for _ in range(20):
        details = agentcore.get_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
        )
        status = details.get("status", "UNKNOWN")
        if status in ("READY", "ACTIVE", "AVAILABLE"):
            print(f"Target status: {status}")
            break
        if "FAIL" in status.upper():
            print(f"ERROR: Target failed: {status}")
            reason = details.get("statusReason", "")
            if reason:
                print(f"Reason: {reason}")
            sys.exit(1)
        print(f"  Status: {status}, waiting...")
        time.sleep(3)

    print("\nDone! Gateway now has 12 tools:")
    for t in TOOL_SCHEMAS:
        print(f"  - {t['name']}")


if __name__ == "__main__":
    main()
