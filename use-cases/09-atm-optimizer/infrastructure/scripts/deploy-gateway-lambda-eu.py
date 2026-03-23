#!/usr/bin/env python3
"""
Deploy Lambda MCP Server in eu-west-1 for AgentCore Gateway integration.

Architecture:
  - Lambda in eu-west-1 (same region as Gateway) — no VPC needed
  - Lambda queries Athena cross-region in me-south-1 (data stays in Bahrain)
  - Gateway invokes Lambda via IAM role (no Function URL needed)
  - Original Lambda in me-south-1 remains for direct Function URL access

Steps:
  1. Create Lambda execution role in eu-west-1 with cross-region Athena/S3/Glue/KMS
  2. Package Lambda code (same code as me-south-1 Lambda)
  3. Deploy Lambda function in eu-west-1
  4. Update Lambda resource policy for Gateway role
  5. Add Lambda target to existing AgentCore Gateway
  6. Deploy updated Lambda code to me-south-1 Lambda (dual-mode handler)

Security:
  - Lambda resource policy scoped to Gateway role only
  - No wildcard principals (security-rules.md compliant)
  - Cross-region Athena access scoped to specific database/workgroup
"""

import boto3
import json
import os
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "CHANGE_ME")
GATEWAY_REGION = "eu-west-1"
LAMBDA_DATA_REGION = "me-south-1"  # Where Athena/S3 data lives

# Gateway (already created)
GATEWAY_ID = os.environ.get("ATM_GATEWAY_ID", "CHANGE_ME")
GATEWAY_ROLE_NAME = "AgentCore-ATMOptimizer-Gateway-Role"
GATEWAY_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/{GATEWAY_ROLE_NAME}"

# New Lambda in eu-west-1
EU_LAMBDA_NAME = "ATM-Profitability-Optimizer-MCP-Gateway"
EU_LAMBDA_ROLE_NAME = "ATM-Profitability-Optimizer-Lambda-MCP-EU-Role"
EU_LAMBDA_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/{EU_LAMBDA_ROLE_NAME}"

# Existing Lambda in me-south-1
ME_LAMBDA_NAME = "ATM-Profitability-Optimizer-MCP-Server"

# Data resources in me-south-1
S3_BUCKET = f"atm-optimizer-data-{ACCOUNT_ID}-{LAMBDA_DATA_REGION}"
ATHENA_DATABASE = "atm_optimizer"
ATHENA_WORKGROUP = "atm-optimizer"
KMS_KEY_ARN = f"arn:aws:kms:{LAMBDA_DATA_REGION}:{ACCOUNT_ID}:key/2a25cf61-ef23-4684-8e52-13cf2ce14361"

# Project paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
BUILD_DIR = os.path.join(PROJECT_ROOT, ".build")


# ---------------------------------------------------------------------------
# Tool schemas (same as deploy-agentcore-gateway.py)
# ---------------------------------------------------------------------------
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
                "period": {"type": "string", "description": "Aggregation: daily, monthly, quarterly"},
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
        "description": "Calculate revenue impact for ATM downtime scenarios. Admin only.",
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
                "atm_id": {"type": "string", "description": "Optional: specific ATM to check"},
                "period": {"type": "string", "description": "Analysis period (e.g. 30d, 60d)"},
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


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
iam = boto3.client("iam")
lambda_eu = boto3.client("lambda", region_name=GATEWAY_REGION)
lambda_me = boto3.client("lambda", region_name=LAMBDA_DATA_REGION)
agentcore = boto3.client("bedrock-agentcore-control", region_name=GATEWAY_REGION)


def step1_create_eu_lambda_role():
    """Create IAM role for the eu-west-1 Lambda with cross-region Athena/S3 access."""
    print("\n=== Step 1: Create EU Lambda Execution Role ===")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    # Cross-region access to Athena/S3/Glue/KMS in me-south-1
    cross_region_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AthenaQueryMeSouth1",
                "Effect": "Allow",
                "Action": [
                    "athena:StartQueryExecution",
                    "athena:GetQueryExecution",
                    "athena:GetQueryResults",
                    "athena:StopQueryExecution",
                    "athena:GetWorkGroup",
                ],
                "Resource": [
                    f"arn:aws:athena:{LAMBDA_DATA_REGION}:{ACCOUNT_ID}:workgroup/{ATHENA_WORKGROUP}",
                ],
            },
            {
                "Sid": "GlueReadMeSouth1",
                "Effect": "Allow",
                "Action": [
                    "glue:GetTable",
                    "glue:GetTables",
                    "glue:GetDatabase",
                    "glue:GetDatabases",
                    "glue:GetPartitions",
                ],
                "Resource": [
                    f"arn:aws:glue:{LAMBDA_DATA_REGION}:{ACCOUNT_ID}:catalog",
                    f"arn:aws:glue:{LAMBDA_DATA_REGION}:{ACCOUNT_ID}:database/{ATHENA_DATABASE}",
                    f"arn:aws:glue:{LAMBDA_DATA_REGION}:{ACCOUNT_ID}:table/{ATHENA_DATABASE}/*",
                ],
            },
            {
                "Sid": "S3DataAccessMeSouth1",
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:GetBucketLocation",
                    "s3:PutObject",
                ],
                "Resource": [
                    f"arn:aws:s3:::{S3_BUCKET}",
                    f"arn:aws:s3:::{S3_BUCKET}/*",
                ],
            },
            {
                "Sid": "KmsDecryptMeSouth1",
                "Effect": "Allow",
                "Action": [
                    "kms:Decrypt",
                    "kms:GenerateDataKey",
                ],
                "Resource": [KMS_KEY_ARN],
            },
        ],
    }

    try:
        iam.get_role(RoleName=EU_LAMBDA_ROLE_NAME)
        print(f"  Role {EU_LAMBDA_ROLE_NAME} already exists, updating policies...")
        iam.update_assume_role_policy(
            RoleName=EU_LAMBDA_ROLE_NAME,
            PolicyDocument=json.dumps(trust_policy),
        )
    except iam.exceptions.NoSuchEntityException:
        print(f"  Creating role {EU_LAMBDA_ROLE_NAME}...")
        iam.create_role(
            RoleName=EU_LAMBDA_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Lambda execution role for ATM Optimizer MCP in eu-west-1 (cross-region Athena)",
            Tags=[{"Key": "Project", "Value": "ATM-Profitability-Optimizer"}],
        )

    # Attach managed policies for CloudWatch Logs
    for policy_arn in [
        "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    ]:
        iam.attach_role_policy(RoleName=EU_LAMBDA_ROLE_NAME, PolicyArn=policy_arn)

    # Attach inline policy for cross-region data access
    iam.put_role_policy(
        RoleName=EU_LAMBDA_ROLE_NAME,
        PolicyName="CrossRegionAthenaAccess",
        PolicyDocument=json.dumps(cross_region_policy),
    )

    print(f"  Role ARN: {EU_LAMBDA_ROLE_ARN}")
    print("  Waiting for IAM role propagation (10s)...")
    time.sleep(10)
    return EU_LAMBDA_ROLE_ARN


def step2_package_lambda():
    """Package Lambda code into a ZIP file."""
    print("\n=== Step 2: Package Lambda Code ===")

    os.makedirs(BUILD_DIR, exist_ok=True)
    zip_path = os.path.join(BUILD_DIR, "lambda-mcp-eu.zip")

    # Remove old zip
    if os.path.exists(zip_path):
        os.remove(zip_path)

    print("  Creating deployment package...")
    subprocess.run(
        ["zip", "-r", zip_path, "agent/", "mcp_server/",
         "-x", "*__pycache__*", "*.pyc", "*_data_loader.py"],
        cwd=PROJECT_ROOT,
        check=True, capture_output=True,
    )

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"  Package: {zip_path} ({size_mb:.1f} MB)")
    return zip_path


def step3_deploy_eu_lambda(zip_path: str):
    """Deploy Lambda function in eu-west-1."""
    print("\n=== Step 3: Deploy Lambda in eu-west-1 ===")

    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    eu_lambda_arn = f"arn:aws:lambda:{GATEWAY_REGION}:{ACCOUNT_ID}:function:{EU_LAMBDA_NAME}"

    # Check if function exists
    try:
        lambda_eu.get_function(FunctionName=EU_LAMBDA_NAME)
        print(f"  Lambda '{EU_LAMBDA_NAME}' exists, updating code...")
        lambda_eu.update_function_code(
            FunctionName=EU_LAMBDA_NAME,
            ZipFile=zip_bytes,
        )
        # Wait for update to complete
        print("  Waiting for code update...")
        waiter = lambda_eu.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=EU_LAMBDA_NAME, WaiterConfig={"Delay": 3, "MaxAttempts": 30})

        # Update configuration
        lambda_eu.update_function_configuration(
            FunctionName=EU_LAMBDA_NAME,
            Environment={
                "Variables": {
                    "ATM_ATHENA_DATABASE": ATHENA_DATABASE,
                    "ATM_ATHENA_WORKGROUP": ATHENA_WORKGROUP,
                    "ATM_S3_DATA_BUCKET": S3_BUCKET,
                    "LOG_LEVEL": "INFO",
                    # No VPC endpoint — Lambda in eu-west-1 calls Athena public endpoint in me-south-1
                }
            },
            Timeout=300,
            MemorySize=1024,
        )
        waiter.wait(FunctionName=EU_LAMBDA_NAME, WaiterConfig={"Delay": 3, "MaxAttempts": 30})

    except lambda_eu.exceptions.ResourceNotFoundException:
        print(f"  Creating Lambda '{EU_LAMBDA_NAME}' in {GATEWAY_REGION}...")
        resp = lambda_eu.create_function(
            FunctionName=EU_LAMBDA_NAME,
            Runtime="python3.11",
            Role=EU_LAMBDA_ROLE_ARN,
            Handler="mcp_server/lambda_handler.handler",
            Code={"ZipFile": zip_bytes},
            Description="ATM Optimizer MCP Server (eu-west-1) - queries Athena cross-region in me-south-1",
            Timeout=300,
            MemorySize=1024,
            Environment={
                "Variables": {
                    "ATM_ATHENA_DATABASE": ATHENA_DATABASE,
                    "ATM_ATHENA_WORKGROUP": ATHENA_WORKGROUP,
                    "ATM_S3_DATA_BUCKET": S3_BUCKET,
                    "LOG_LEVEL": "INFO",
                }
            },
            Tags={
                "Project": "ATM-Profitability-Optimizer",
                "Component": "MCP-Gateway-Lambda",
                "DataRegion": LAMBDA_DATA_REGION,
            },
        )
        eu_lambda_arn = resp["FunctionArn"]
        print(f"  Created: {eu_lambda_arn}")

        # Wait for function to become active
        print("  Waiting for function to become active...")
        waiter = lambda_eu.get_waiter("function_active_v2")
        waiter.wait(FunctionName=EU_LAMBDA_NAME, WaiterConfig={"Delay": 3, "MaxAttempts": 30})

    print(f"  Lambda ARN: {eu_lambda_arn}")
    return eu_lambda_arn


def step4_update_lambda_resource_policy():
    """Set Lambda resource policy: ONLY Gateway role can invoke.

    SECURITY: Scoped to Gateway service role only. No wildcards.
    """
    print("\n=== Step 4: Update Lambda Resource Policy ===")

    # Remove existing permissions
    try:
        policy_resp = lambda_eu.get_policy(FunctionName=EU_LAMBDA_NAME)
        policy = json.loads(policy_resp["Policy"])
        for stmt in policy.get("Statement", []):
            sid = stmt.get("Sid", "")
            if sid:
                print(f"  Removing existing permission: {sid}")
                try:
                    lambda_eu.remove_permission(FunctionName=EU_LAMBDA_NAME, StatementId=sid)
                except Exception:
                    pass
    except lambda_eu.exceptions.ResourceNotFoundException:
        print("  No existing resource policy")
    except Exception as e:
        print(f"  Note: {e}")

    # Add permission for Gateway role only
    print(f"  Adding permission for Gateway role: {GATEWAY_ROLE_ARN}")
    lambda_eu.add_permission(
        FunctionName=EU_LAMBDA_NAME,
        StatementId="AgentCoreGatewayInvoke",
        Action="lambda:InvokeFunction",
        Principal=GATEWAY_ROLE_ARN,
    )

    # Verify
    policy_resp = lambda_eu.get_policy(FunctionName=EU_LAMBDA_NAME)
    policy = json.loads(policy_resp["Policy"])
    print("  Resource policy:")
    for stmt in policy.get("Statement", []):
        print(f"    Sid={stmt.get('Sid')}, Principal={stmt.get('Principal')}, Action={stmt.get('Action')}")
    print("  Lambda resource policy scoped to Gateway role only.")



def step5_update_gateway_role():
    """Update Gateway role to invoke the eu-west-1 Lambda (instead of me-south-1)."""
    print("\n=== Step 5: Update Gateway Role for EU Lambda ===")

    eu_lambda_arn = f"arn:aws:lambda:{GATEWAY_REGION}:{ACCOUNT_ID}:function:{EU_LAMBDA_NAME}"

    lambda_invoke_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeMCPLambdaEU",
                "Effect": "Allow",
                "Action": [
                    "lambda:InvokeFunction",
                    "lambda:GetFunction",
                ],
                "Resource": eu_lambda_arn,
            }
        ],
    }

    iam.put_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyName="InvokeMCPLambda",
        PolicyDocument=json.dumps(lambda_invoke_policy),
    )
    print(f"  Updated Gateway role to invoke: {eu_lambda_arn}")


def step6_add_gateway_target():
    """Add eu-west-1 Lambda as Gateway target (same region = works)."""
    print("\n=== Step 6: Add Lambda Target to Gateway ===")

    eu_lambda_arn = f"arn:aws:lambda:{GATEWAY_REGION}:{ACCOUNT_ID}:function:{EU_LAMBDA_NAME}"
    target_name = "atm-mcp-tools"

    # Check if target already exists
    try:
        targets = agentcore.list_gateway_targets(gatewayIdentifier=GATEWAY_ID)
        for t in targets.get("items", []):
            if t.get("name") == target_name:
                target_id = t["targetId"]
                print(f"  Target '{target_name}' already exists: {target_id}")
                # Update it to point to eu-west-1 Lambda
                print(f"  Updating target to use eu-west-1 Lambda...")
                agentcore.update_gateway_target(
                    gatewayIdentifier=GATEWAY_ID,
                    targetId=target_id,
                    targetConfiguration={
                        "mcp": {
                            "lambda": {
                                "lambdaArn": eu_lambda_arn,
                                "toolSchema": {
                                    "inlinePayload": TOOL_SCHEMAS,
                                },
                            }
                        }
                    },
                )
                # Wait for update
                print("  Waiting for target update...")
                for _ in range(20):
                    details = agentcore.get_gateway_target(
                        gatewayIdentifier=GATEWAY_ID, targetId=target_id,
                    )
                    status = details.get("status", "UNKNOWN")
                    if status in ("READY", "ACTIVE", "AVAILABLE"):
                        print(f"  Target status: {status}")
                        return target_id
                    if "FAIL" in status.upper():
                        reason = details.get("statusReasons", [])
                        print(f"  ERROR: Target update failed: {status} {reason}")
                        sys.exit(1)
                    print(f"  Status: {status}, waiting...")
                    time.sleep(3)
                return target_id
    except Exception as e:
        print(f"  Note: {e}")

    # Create new target
    print(f"  Creating Lambda target '{target_name}'...")
    print(f"  Lambda ARN: {eu_lambda_arn}")
    print(f"  Tools: {len(TOOL_SCHEMAS)}")

    resp = agentcore.create_gateway_target(
        gatewayIdentifier=GATEWAY_ID,
        name=target_name,
        description="ATM Profitability Optimizer MCP tools (Lambda in eu-west-1, Athena in me-south-1)",
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": eu_lambda_arn,
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
    for _ in range(30):
        details = agentcore.get_gateway_target(
            gatewayIdentifier=GATEWAY_ID, targetId=target_id,
        )
        status = details.get("status", "UNKNOWN")
        if status in ("READY", "ACTIVE", "AVAILABLE"):
            print(f"  Target status: {status}")
            break
        if "FAIL" in status.upper():
            reason = details.get("statusReasons", [])
            print(f"  ERROR: Target creation failed: {status}")
            if reason:
                print(f"  Reasons: {reason}")
            sys.exit(1)
        print(f"  Status: {status}, waiting...")
        time.sleep(3)

    return target_id


def step7_deploy_me_lambda_code():
    """Also deploy updated code to the me-south-1 Lambda (dual-mode handler)."""
    print("\n=== Step 7: Update me-south-1 Lambda Code ===")

    zip_path = os.path.join(BUILD_DIR, "lambda-mcp-eu.zip")
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    print(f"  Updating {ME_LAMBDA_NAME} in {LAMBDA_DATA_REGION}...")
    lambda_me.update_function_code(
        FunctionName=ME_LAMBDA_NAME,
        ZipFile=zip_bytes,
    )
    print("  Waiting for update...")
    waiter = lambda_me.get_waiter("function_updated_v2")
    waiter.wait(FunctionName=ME_LAMBDA_NAME, WaiterConfig={"Delay": 3, "MaxAttempts": 30})
    print("  me-south-1 Lambda updated.")


def main():
    eu_lambda_arn = f"arn:aws:lambda:{GATEWAY_REGION}:{ACCOUNT_ID}:function:{EU_LAMBDA_NAME}"

    print("=" * 65)
    print("  AgentCore Gateway + EU Lambda Deployment")
    print(f"  Gateway Region:  {GATEWAY_REGION}")
    print(f"  Lambda Region:   {GATEWAY_REGION} (NEW — same region as Gateway)")
    print(f"  Data Region:     {LAMBDA_DATA_REGION} (Athena/S3 — cross-region query)")
    print(f"  Account:         {ACCOUNT_ID}")
    print("=" * 65)

    # Step 1: Create Lambda execution role
    step1_create_eu_lambda_role()

    # Step 2: Package Lambda code
    zip_path = step2_package_lambda()

    # Step 3: Deploy Lambda in eu-west-1
    step3_deploy_eu_lambda(zip_path)

    # Step 4: Lambda resource policy (Gateway role only)
    step4_update_lambda_resource_policy()

    # Step 5: Update Gateway role to invoke eu-west-1 Lambda
    step5_update_gateway_role()

    # Step 6: Add/update Gateway target
    target_id = step6_add_gateway_target()

    # Step 7: Also update me-south-1 Lambda code
    step7_deploy_me_lambda_code()

    print()
    print("=" * 65)
    print("  DEPLOYMENT COMPLETE")
    print("=" * 65)
    print(f"  Gateway ID:       {GATEWAY_ID}")
    print(f"  Gateway URL:      https://{GATEWAY_ID}.gateway.bedrock-agentcore.{GATEWAY_REGION}.amazonaws.com/mcp")
    print(f"  Gateway Region:   {GATEWAY_REGION}")
    print(f"  Target ID:        {target_id}")
    print(f"  EU Lambda ARN:    {eu_lambda_arn}")
    print(f"  EU Lambda Region: {GATEWAY_REGION}")
    print(f"  ME Lambda ARN:    arn:aws:lambda:{LAMBDA_DATA_REGION}:{ACCOUNT_ID}:function:{ME_LAMBDA_NAME}")
    print(f"  Data Region:      {LAMBDA_DATA_REGION}")
    print(f"  Gateway Role:     {GATEWAY_ROLE_ARN}")
    print()
    print("  Architecture:")
    print(f"    Agent → Gateway (eu-west-1) → Lambda (eu-west-1) → Athena (me-south-1) → S3 (me-south-1)")
    print(f"    Data never leaves Bahrain. Only query results flow to eu-west-1 Lambda.")
    print()
    print("  Security:")
    print("    - Gateway auth: AWS_IAM (SigV4)")
    print("    - EU Lambda policy: Gateway role only (no account-wide access)")
    print("    - No wildcard principals")
    print("    - Cross-region Athena access scoped to specific database/workgroup")
    print()


if __name__ == "__main__":
    main()
