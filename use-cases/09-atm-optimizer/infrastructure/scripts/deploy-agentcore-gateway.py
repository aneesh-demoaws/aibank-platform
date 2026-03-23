#!/usr/bin/env python3
"""
Deploy AgentCore Gateway in eu-west-1 with Lambda MCP target in me-south-1.

Steps:
1. Create Gateway service role (eu-west-1) with Lambda invoke permission
2. Create AgentCore Gateway with IAM auth
3. Add Lambda target with tool schemas for all 8 MCP tools
4. Update Lambda resource policy: ONLY Gateway role can invoke (no account-wide access)

Security:
- Gateway uses IAM auth (no public access)
- Lambda resource policy scoped to Gateway service role only
- No wildcard principals anywhere
"""

import boto3
import json
import time
import sys

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "CHANGE_ME")
GATEWAY_REGION = "eu-west-1"
LAMBDA_REGION = "me-south-1"
LAMBDA_FUNCTION_NAME = "ATM-Profitability-Optimizer-MCP-Server"
LAMBDA_ARN = f"arn:aws:lambda:{LAMBDA_REGION}:{ACCOUNT_ID}:function:{LAMBDA_FUNCTION_NAME}"
GATEWAY_NAME = "atm-optimizer-gateway"
GATEWAY_ROLE_NAME = "AgentCore-ATMOptimizer-Gateway-Role"
GATEWAY_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/{GATEWAY_ROLE_NAME}"


# ---------------------------------------------------------------------------
# Tool schemas for AgentCore Gateway Lambda target
# ---------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "name": "query_atm_data",
        "description": "Query ATM transaction data with location info, filtered by ATM ID and date range. Returns transaction summary, location details, and daily capacity.",
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
        "description": "Find ATMs and branches within a radius of a given ATM. Returns nearby locations with distances.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "Source ATM identifier"},
                "radius_km": {"type": "number", "description": "Search radius in kilometers (default: 5)"},
            },
            "required": ["atm_id"],
        },
    },
    {
        "name": "query_revenue_data",
        "description": "Query revenue metrics for an ATM with period aggregation (daily/monthly/quarterly).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "ATM identifier"},
                "period": {"type": "string", "description": "Aggregation period: daily, monthly, quarterly"},
            },
            "required": ["atm_id"],
        },
    },
    {
        "name": "query_maintenance_costs",
        "description": "Query maintenance cost history for an ATM within a date range. Admin only.",
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
        "description": "Query current cash levels and 7-day forecast for an ATM. Admin only.",
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
        "description": "Calculate revenue impact and traffic redistribution for ATM downtime scenarios. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "ATM identifier"},
                "downtime_days": {"type": "integer", "description": "Number of downtime days to simulate"},
            },
            "required": ["atm_id", "downtime_days"],
        },
    },
    {
        "name": "detect_anomalies",
        "description": "Detect ATMs with unusual transaction patterns using statistical analysis. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "atm_id": {"type": "string", "description": "Optional: specific ATM to check"},
                "period": {"type": "string", "description": "Analysis period (e.g. 30d, 60d)"},
            },
            "required": [],
        },
    },
    {
        "name": "profitability_ranking",
        "description": "Rank ATMs by net revenue (revenue - maintenance - cash handling costs). Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "top_n": {"type": "integer", "description": "Number of ATMs to return (default: 28)"},
                "sort": {"type": "string", "description": "Sort by: net_revenue, gross_revenue, or costs"},
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
                "atm_id": {"type": "string", "description": "Optional ATM identifier. If omitted, returns scores for all ATMs."},
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
        "description": "Simulate impact of a competitor bank opening or closing an ATM near NeoBank locations. Admin only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scenario_type": {"type": "string", "description": "'add' for new competitor ATM, 'remove' for closure"},
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


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
iam = boto3.client("iam")
lambda_client = boto3.client("lambda", region_name=LAMBDA_REGION)
agentcore = boto3.client("bedrock-agentcore-control", region_name=GATEWAY_REGION)


def step1_create_gateway_service_role():
    """Create IAM role for AgentCore Gateway with Lambda invoke permission."""
    print("\n=== Step 1: Create Gateway Service Role ===")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AgentCoreGatewayAssume",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
                },
            }
        ],
    }

    lambda_invoke_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeMCPLambda",
                "Effect": "Allow",
                "Action": [
                    "lambda:InvokeFunction",
                    "lambda:GetFunction",
                ],
                "Resource": LAMBDA_ARN,
            }
        ],
    }

    try:
        iam.get_role(RoleName=GATEWAY_ROLE_NAME)
        print(f"  Role {GATEWAY_ROLE_NAME} already exists, updating policies...")
        # Update trust policy
        iam.update_assume_role_policy(
            RoleName=GATEWAY_ROLE_NAME,
            PolicyDocument=json.dumps(trust_policy),
        )
    except iam.exceptions.NoSuchEntityException:
        print(f"  Creating role {GATEWAY_ROLE_NAME}...")
        iam.create_role(
            RoleName=GATEWAY_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Gateway role for ATM Optimizer - invokes Lambda MCP in me-south-1",
            Tags=[{"Key": "Project", "Value": "ATM-Profitability-Optimizer"}],
        )

    # Attach inline policy for Lambda invoke
    iam.put_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyName="InvokeMCPLambda",
        PolicyDocument=json.dumps(lambda_invoke_policy),
    )
    print(f"  Role ARN: {GATEWAY_ROLE_ARN}")

    # Wait for role propagation
    print("  Waiting for IAM role propagation (10s)...")
    time.sleep(10)
    return GATEWAY_ROLE_ARN


def step2_create_gateway():
    """Create AgentCore Gateway with IAM auth in eu-west-1."""
    print("\n=== Step 2: Create AgentCore Gateway ===")

    # Check if gateway already exists
    try:
        gateways = agentcore.list_gateways()
        for gw in gateways.get("items", []):
            if gw.get("name") == GATEWAY_NAME:
                gw_id = gw["gatewayId"]
                print(f"  Gateway '{GATEWAY_NAME}' already exists: {gw_id}")
                # Get full details
                details = agentcore.get_gateway(gatewayIdentifier=gw_id)
                return gw_id, details.get("gatewayUrl", "")
    except Exception as e:
        print(f"  Note: {e}")

    print(f"  Creating gateway '{GATEWAY_NAME}' with IAM auth...")
    resp = agentcore.create_gateway(
        name=GATEWAY_NAME,
        description="NeoBank ATM Profitability Optimizer - MCP Gateway for 8 ATM analysis tools",
        roleArn=GATEWAY_ROLE_ARN,
        protocolType="MCP",
        authorizerType="AWS_IAM",
    )

    gw_id = resp["gatewayId"]
    gw_url = resp.get("gatewayUrl", "")
    print(f"  Gateway ID: {gw_id}")
    print(f"  Gateway URL: {gw_url}")

    # Wait for gateway to become ACTIVE
    print("  Waiting for gateway to become ACTIVE...")
    for _ in range(30):
        details = agentcore.get_gateway(gatewayIdentifier=gw_id)
        status = details.get("status", "UNKNOWN")
        if status in ("READY", "ACTIVE", "AVAILABLE"):
            print(f"  Gateway status: {status}")
            gw_url = details.get("gatewayUrl", gw_url)
            break
        if status in ("FAILED", "CREATE_FAILED"):
            print(f"  ERROR: Gateway creation failed: {status}")
            sys.exit(1)
        print(f"  Status: {status}, waiting...")
        time.sleep(5)

    return gw_id, gw_url


def step3_add_lambda_target(gateway_id: str):
    """Add Lambda MCP target with all 12 tool schemas."""
    print("\n=== Step 3: Add Lambda Target ===")

    target_name = "atm-mcp-tools"

    # Check if target already exists
    try:
        targets = agentcore.list_gateway_targets(gatewayIdentifier=gateway_id)
        for t in targets.get("items", []):
            if t.get("name") == target_name:
                print(f"  Target '{target_name}' already exists: {t['targetId']}")
                return t["targetId"]
    except Exception as e:
        print(f"  Note: {e}")

    print(f"  Adding Lambda target '{target_name}'...")
    print(f"  Lambda ARN: {LAMBDA_ARN}")
    print(f"  Tools: {len(TOOL_SCHEMAS)}")

    resp = agentcore.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description="ATM Profitability Optimizer MCP tools (Lambda in me-south-1)",
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
    print(f"  Target ID: {target_id}")

    # Wait for target to become active
    print("  Waiting for target to become active...")
    for _ in range(20):
        details = agentcore.get_gateway_target(
            gatewayIdentifier=gateway_id,
            targetId=target_id,
        )
        status = details.get("status", "UNKNOWN")
        if status in ("READY", "ACTIVE", "AVAILABLE"):
            print(f"  Target status: {status}")
            break
        if "FAIL" in status.upper():
            print(f"  ERROR: Target creation failed: {status}")
            reason = details.get("statusReason", "")
            if reason:
                print(f"  Reason: {reason}")
            sys.exit(1)
        print(f"  Status: {status}, waiting...")
        time.sleep(3)

    return target_id


def step4_update_lambda_resource_policy():
    """Update Lambda resource policy: ONLY Gateway role can invoke.

    SECURITY: Removes any account-wide permissions and scopes to Gateway role only.
    This follows the security-rules.md steering: never use Principal: '*'.
    """
    print("\n=== Step 4: Update Lambda Resource Policy ===")

    # Remove existing permissions first
    try:
        policy_resp = lambda_client.get_policy(FunctionName=LAMBDA_FUNCTION_NAME)
        policy = json.loads(policy_resp["Policy"])
        for stmt in policy.get("Statement", []):
            sid = stmt.get("Sid", "")
            if sid:
                print(f"  Removing existing permission: {sid}")
                try:
                    lambda_client.remove_permission(
                        FunctionName=LAMBDA_FUNCTION_NAME,
                        StatementId=sid,
                    )
                except Exception:
                    pass
    except lambda_client.exceptions.ResourceNotFoundException:
        print("  No existing resource policy found")
    except Exception as e:
        print(f"  Note getting policy: {e}")

    # Add permission ONLY for Gateway service role
    print(f"  Adding permission for Gateway role: {GATEWAY_ROLE_ARN}")
    lambda_client.add_permission(
        FunctionName=LAMBDA_FUNCTION_NAME,
        StatementId="AgentCoreGatewayInvoke",
        Action="lambda:InvokeFunction",
        Principal=GATEWAY_ROLE_ARN,
    )
    # Also allow GetFunction so Gateway can validate the Lambda during target creation
    try:
        lambda_client.add_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId="AgentCoreGatewayGetFunction",
            Action="lambda:GetFunction",
            Principal=GATEWAY_ROLE_ARN,
        )
    except Exception as e:
        print(f"  Note (GetFunction permission): {e}")

    # Verify
    policy_resp = lambda_client.get_policy(FunctionName=LAMBDA_FUNCTION_NAME)
    policy = json.loads(policy_resp["Policy"])
    print("  Updated resource policy:")
    for stmt in policy.get("Statement", []):
        principal = stmt.get("Principal", {})
        print(f"    Sid={stmt.get('Sid')}, Principal={principal}, Action={stmt.get('Action')}")

    print("  Lambda resource policy scoped to Gateway role only.")


def main():
    print("=" * 60)
    print("  AgentCore Gateway Deployment")
    print(f"  Gateway Region: {GATEWAY_REGION}")
    print(f"  Lambda Region:  {LAMBDA_REGION}")
    print(f"  Account:        {ACCOUNT_ID}")
    print("=" * 60)

    # Step 1: IAM role
    step1_create_gateway_service_role()

    # Step 2: Create gateway
    gw_id, gw_url = step2_create_gateway()

    # Step 3: Update Lambda resource policy FIRST (Gateway needs invoke permission to validate target)
    step4_update_lambda_resource_policy()

    # Step 4: Add Lambda target (now Gateway role can reach the Lambda)
    target_id = step3_add_lambda_target(gw_id)

    # Deploy updated Lambda code (with Gateway handler support)
    print("\n=== Step 5: Deploy Updated Lambda Code ===")
    import subprocess
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.chdir(project_root)

    print("  Packaging Lambda code...")
    subprocess.run(["rm", "-f", ".build/lambda-mcp-update.zip"], check=True)
    subprocess.run(
        ["zip", "-r", ".build/lambda-mcp-update.zip", "agent/", "mcp_server/",
         "-x", "*__pycache__*", "*.pyc"],
        check=True, capture_output=True,
    )
    print("  Deploying to Lambda...")
    subprocess.run(
        ["aws", "lambda", "update-function-code",
         "--function-name", LAMBDA_FUNCTION_NAME,
         "--zip-file", "fileb://.build/lambda-mcp-update.zip",
         "--region", LAMBDA_REGION,
         "--output", "json",
         "--query", "{FunctionName: FunctionName, LastModified: LastModified}"],
        check=True,
    )

    print("\n" + "=" * 60)
    print("  DEPLOYMENT COMPLETE")
    print("=" * 60)
    print(f"  Gateway ID:     {gw_id}")
    print(f"  Gateway URL:    {gw_url}")
    print(f"  Gateway Region: {GATEWAY_REGION}")
    print(f"  Target ID:      {target_id}")
    print(f"  Lambda ARN:     {LAMBDA_ARN}")
    print(f"  Lambda Region:  {LAMBDA_REGION}")
    print(f"  Gateway Role:   {GATEWAY_ROLE_ARN}")
    print()
    print("  Security:")
    print("    - Gateway auth: AWS_IAM (SigV4)")
    print("    - Lambda policy: Gateway role only (no account-wide access)")
    print("    - No wildcard principals")
    print()


if __name__ == "__main__":
    main()
