#!/usr/bin/env python3
"""
Deploy the NeoBank ATM Profitability Optimizer to AgentCore Runtime (eu-west-1).

Steps:
  1. Create IAM execution role for AgentCore Runtime
  2. Create ECR repository and build/push ARM64 Docker image
  3. Create AgentCore Memory resource (one-time, with LTM strategies)
  4. Create AgentCore Runtime via bedrock-agentcore-control API
  5. Wait for READY status
  6. Test with invoke_agent_runtime

Usage:
    python3 infrastructure/scripts/deploy-agentcore-runtime.py
    python3 infrastructure/scripts/deploy-agentcore-runtime.py --skip-docker
    python3 infrastructure/scripts/deploy-agentcore-runtime.py --skip-iam
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "CHANGE_ME")
DEPLOY_REGION = "eu-west-1"
DATA_REGION = "me-south-1"

AGENT_NAME = "neobank-atm-profitability-optimizer"
ECR_REPO_NAME = "neobank-atm-optimizer-agent"
ROLE_NAME = "AgentCore-ATMOptimizer-Runtime-Role"
MEMORY_NAME = "NeoBank-ATM-Optimizer-Memory"

MODEL_ID = "anthropic.claude-sonnet-4-20250514-v1:0"

GATEWAY_ENDPOINT = (
    "https://CHANGE_ME_GATEWAY_ID"
    ".gateway.bedrock-agentcore.eu-west-1.amazonaws.com/mcp"
)

# Cognito (me-south-1)
COGNITO_USER_POOL_ID = "me-south-1_U5z7GXAUv"
COGNITO_APP_CLIENT_ID = "5j9ai31n4pgk98lnq2pi207io4"

# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------
iam = boto3.client("iam")
ecr = boto3.client("ecr", region_name=DEPLOY_REGION)
sts = boto3.client("sts", region_name=DEPLOY_REGION)


def get_agentcore_control_client():
    return boto3.client("bedrock-agentcore-control", region_name=DEPLOY_REGION)


def get_agentcore_client():
    return boto3.client("bedrock-agentcore", region_name=DEPLOY_REGION)


# ---------------------------------------------------------------------------
# Step 1: IAM Role
# ---------------------------------------------------------------------------
TRUST_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Sid": "AssumeRolePolicy",
        "Effect": "Allow",
        "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
        "Condition": {
            "StringEquals": {"aws:SourceAccount": ACCOUNT_ID},
            "ArnLike": {
                "aws:SourceArn": f"arn:aws:bedrock-agentcore:{DEPLOY_REGION}:{ACCOUNT_ID}:*"
            },
        },
    }],
})

EXECUTION_POLICY = json.dumps({
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "ECRImageAccess",
            "Effect": "Allow",
            "Action": ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
            "Resource": [f"arn:aws:ecr:{DEPLOY_REGION}:{ACCOUNT_ID}:repository/{ECR_REPO_NAME}"],
        },
        {
            "Sid": "ECRToken",
            "Effect": "Allow",
            "Action": ["ecr:GetAuthorizationToken"],
            "Resource": "*",
        },
        {
            "Sid": "CloudWatchLogs",
            "Effect": "Allow",
            "Action": ["logs:CreateLogGroup", "logs:DescribeLogStreams", "logs:DescribeLogGroups"],
            "Resource": [
                f"arn:aws:logs:{DEPLOY_REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*",
                f"arn:aws:logs:{DEPLOY_REGION}:{ACCOUNT_ID}:log-group:*",
            ],
        },
        {
            "Sid": "CloudWatchLogStream",
            "Effect": "Allow",
            "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
            "Resource": [
                f"arn:aws:logs:{DEPLOY_REGION}:{ACCOUNT_ID}:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*"
            ],
        },
        {
            "Sid": "XRay",
            "Effect": "Allow",
            "Action": [
                "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                "xray:GetSamplingRules", "xray:GetSamplingTargets",
            ],
            "Resource": ["*"],
        },
        {
            "Sid": "CloudWatchMetrics",
            "Effect": "Allow",
            "Action": ["cloudwatch:PutMetricData"],
            "Resource": ["*"],
            "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
        },
        {
            "Sid": "BedrockModelInvocation",
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": [
                "arn:aws:bedrock:*::foundation-model/*",
                f"arn:aws:bedrock:{DEPLOY_REGION}:{ACCOUNT_ID}:*",
            ],
        },
        {
            "Sid": "AgentCoreWorkloadIdentity",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:GetWorkloadAccessToken",
                "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                "bedrock-agentcore:GetWorkloadAccessTokenForUserId",
            ],
            "Resource": [
                f"arn:aws:bedrock-agentcore:{DEPLOY_REGION}:{ACCOUNT_ID}:workload-identity-directory/default",
                f"arn:aws:bedrock-agentcore:{DEPLOY_REGION}:{ACCOUNT_ID}:workload-identity-directory/default/workload-identity/{AGENT_NAME}-*",
            ],
        },
        {
            "Sid": "AgentCoreMemory",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreateMemory",
                "bedrock-agentcore:GetMemory",
                "bedrock-agentcore:ListMemories",
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:GetMemoryRecord",
                "bedrock-agentcore:ListMemoryRecords",
            ],
            "Resource": [
                f"arn:aws:bedrock-agentcore:{DEPLOY_REGION}:{ACCOUNT_ID}:memory/*",
            ],
        },
        {
            "Sid": "AgentCoreGatewayInvoke",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:InvokeGateway"],
            "Resource": [
                f"arn:aws:bedrock-agentcore:{DEPLOY_REGION}:{ACCOUNT_ID}:gateway/*",
            ],
        },
    ],
})


def step1_create_iam_role():
    """Create or update the AgentCore Runtime execution role."""
    print("\n" + "=" * 60)
    print("  Step 1: IAM Execution Role")
    print("=" * 60)

    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{ROLE_NAME}"

    try:
        iam.get_role(RoleName=ROLE_NAME)
        print(f"  Role {ROLE_NAME} already exists")
        # Update trust policy
        iam.update_assume_role_policy(RoleName=ROLE_NAME, PolicyDocument=TRUST_POLICY)
        print("  Updated trust policy")
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=TRUST_POLICY,
            Description="AgentCore Runtime execution role for NeoBank ATM Optimizer",
            Tags=[{"Key": "Project", "Value": "neobank-atm-optimizer"}],
        )
        print(f"  ✅ Created role: {ROLE_NAME}")

    # Put inline policy
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="AgentCoreRuntimeExecution",
        PolicyDocument=EXECUTION_POLICY,
    )
    print("  ✅ Attached execution policy")

    # Wait for IAM propagation
    print("  Waiting 10s for IAM propagation...")
    time.sleep(10)
    return role_arn


# ---------------------------------------------------------------------------
# Step 2: ECR + Docker
# ---------------------------------------------------------------------------

def step2_build_and_push_docker():
    """Create ECR repo, build ARM64 image, push to ECR."""
    print("\n" + "=" * 60)
    print("  Step 2: ECR + Docker Build")
    print("=" * 60)

    ecr_uri = f"{ACCOUNT_ID}.dkr.ecr.{DEPLOY_REGION}.amazonaws.com"
    image_uri = f"{ecr_uri}/{ECR_REPO_NAME}:latest"

    # Create ECR repo if needed
    try:
        ecr.create_repository(
            repositoryName=ECR_REPO_NAME,
            imageTagMutability="MUTABLE",
            imageScanningConfiguration={"scanOnPush": True},
        )
        print(f"  ✅ Created ECR repo: {ECR_REPO_NAME}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        print(f"  ECR repo {ECR_REPO_NAME} already exists")

    # ECR login
    print("  Logging into ECR...")
    token_resp = ecr.get_authorization_token()
    auth = token_resp["authorizationData"][0]
    import base64
    user_pass = base64.b64decode(auth["authorizationToken"]).decode()
    password = user_pass.split(":")[1]

    login_cmd = f"echo '{password}' | docker login --username AWS --password-stdin {ecr_uri}"
    subprocess.run(login_cmd, shell=True, check=True, capture_output=True)
    print("  ✅ ECR login successful")

    # Setup buildx for ARM64
    print("  Setting up Docker buildx for ARM64...")
    subprocess.run(
        ["docker", "buildx", "create", "--use", "--name", "arm64builder"],
        capture_output=True,
    )

    # Build and push
    print(f"  Building ARM64 image and pushing to {image_uri}...")
    result = subprocess.run(
        [
            "docker", "buildx", "build",
            "--platform", "linux/arm64",
            "-t", image_uri,
            "--push",
            ".",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        print(f"  ❌ Docker build failed:\n{result.stderr}")
        sys.exit(1)

    print(f"  ✅ Image pushed: {image_uri}")
    return image_uri


# ---------------------------------------------------------------------------
# Step 3: AgentCore Memory
# ---------------------------------------------------------------------------

def step3_create_memory():
    """Create AgentCore Memory resource with LTM strategies (one-time)."""
    print("\n" + "=" * 60)
    print("  Step 3: AgentCore Memory")
    print("=" * 60)

    from bedrock_agentcore.memory import MemoryClient

    client = MemoryClient(region_name=DEPLOY_REGION)

    # Check if memory already exists
    try:
        memories = client.list_memories()
        for mem in memories.get("memories", []):
            if mem.get("name") == MEMORY_NAME:
                memory_id = mem["id"]
                print(f"  Memory already exists: {memory_id}")
                return memory_id
    except Exception as e:
        logger.warning("Could not list memories: %s", e)

    # Create with all three LTM strategies
    print(f"  Creating memory: {MEMORY_NAME}")
    memory = client.create_memory_and_wait(
        name=MEMORY_NAME,
        description="NeoBank ATM Profitability Optimizer — user preferences, session summaries, and facts",
        strategies=[
            {
                "summaryMemoryStrategy": {
                    "name": "SessionSummarizer",
                    "namespaces": ["/summaries/{actorId}/{sessionId}"],
                }
            },
            {
                "userPreferenceMemoryStrategy": {
                    "name": "PreferenceLearner",
                    "namespaces": ["/preferences/{actorId}"],
                }
            },
            {
                "semanticMemoryStrategy": {
                    "name": "FactExtractor",
                    "namespaces": ["/facts/{actorId}"],
                }
            },
        ],
    )

    memory_id = memory.get("id")
    print(f"  ✅ Memory created: {memory_id}")
    return memory_id


# ---------------------------------------------------------------------------
# Step 4: Create AgentCore Runtime
# ---------------------------------------------------------------------------

def step4_create_runtime(image_uri: str, role_arn: str, memory_id: str):
    """Create the AgentCore Runtime with environment variables."""
    print("\n" + "=" * 60)
    print("  Step 4: AgentCore Runtime")
    print("=" * 60)

    client = get_agentcore_control_client()

    # Check if runtime already exists
    try:
        runtimes = client.list_agent_runtimes()
        for rt in runtimes.get("agentRuntimeSummaries", []):
            if rt.get("agentRuntimeName") == AGENT_NAME:
                runtime_id = rt["agentRuntimeId"]
                runtime_arn = rt.get("agentRuntimeArn", "")
                print(f"  Runtime already exists: {runtime_id}")
                print(f"  Updating runtime with new image...")
                client.update_agent_runtime(
                    agentRuntimeId=runtime_id,
                    agentRuntimeArtifact={
                        "containerConfiguration": {"containerUri": image_uri}
                    },
                    networkConfiguration={"networkMode": "PUBLIC"},
                    roleArn=role_arn,
                    environmentVariables={
                        "ATM_MODEL_ID": MODEL_ID,
                        "ATM_MODEL_REGION": DEPLOY_REGION,
                        "ATM_MEMORY_ID": memory_id,
                        "ATM_MEMORY_REGION": DEPLOY_REGION,
                        "ATM_GATEWAY_ENDPOINT": GATEWAY_ENDPOINT,
                        "LOG_LEVEL": "INFO",
                    },
                )
                print(f"  ✅ Runtime updated: {runtime_id}")
                return runtime_id, runtime_arn
    except Exception as e:
        logger.warning("Could not list runtimes: %s", e)

    # Create new runtime
    print(f"  Creating runtime: {AGENT_NAME}")
    response = client.create_agent_runtime(
        agentRuntimeName=AGENT_NAME,
        agentRuntimeArtifact={
            "containerConfiguration": {"containerUri": image_uri}
        },
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
        roleArn=role_arn,
        environmentVariables={
            "ATM_MODEL_ID": MODEL_ID,
            "ATM_MODEL_REGION": DEPLOY_REGION,
            "ATM_MEMORY_ID": memory_id,
            "ATM_MEMORY_REGION": DEPLOY_REGION,
            "ATM_GATEWAY_ENDPOINT": GATEWAY_ENDPOINT,
            "LOG_LEVEL": "INFO",
        },
    )

    runtime_arn = response.get("agentRuntimeArn", "")
    runtime_id = response.get("agentRuntimeId", "")
    status = response.get("status", "")
    print(f"  ✅ Runtime created: {runtime_id}")
    print(f"     ARN: {runtime_arn}")
    print(f"     Status: {status}")
    return runtime_id, runtime_arn


# ---------------------------------------------------------------------------
# Step 5: Wait for READY
# ---------------------------------------------------------------------------

def step5_wait_for_ready(runtime_id: str, timeout: int = 300):
    """Poll until the runtime status is READY."""
    print("\n" + "=" * 60)
    print("  Step 5: Waiting for Runtime READY")
    print("=" * 60)

    client = get_agentcore_control_client()
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = resp.get("status", "UNKNOWN")
        print(f"  Status: {status}")

        if status == "READY":
            print("  ✅ Runtime is READY")
            return resp
        elif status in ("FAILED", "DELETED"):
            print(f"  ❌ Runtime failed: {status}")
            failure = resp.get("statusReasons", [])
            if failure:
                print(f"     Reasons: {failure}")
            sys.exit(1)

        time.sleep(15)

    print("  ❌ Timed out waiting for READY")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Step 6: Test invocation
# ---------------------------------------------------------------------------

def step6_test_invocation(runtime_arn: str):
    """Send a test query to the deployed agent."""
    print("\n" + "=" * 60)
    print("  Step 6: Test Invocation")
    print("=" * 60)

    client = get_agentcore_client()

    # Session ID must be 33+ characters
    session_id = f"test-session-{int(time.time())}-{'x' * 20}"

    payload = json.dumps({
        "input": {
            "prompt": "What is the total number of ATMs in the network?",
            "user_role": "admin",
            "session_id": session_id,
            "actor_id": "deploy-test",
        }
    })

    print(f"  Invoking with session: {session_id[:40]}...")
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=payload,
            qualifier="DEFAULT",
        )

        body = response["response"].read()
        data = json.loads(body)
        print(f"  ✅ Response received:")
        resp_text = data.get("output", {}).get("response", "")
        print(f"     {resp_text[:200]}...")
        return True
    except Exception as e:
        print(f"  ⚠️  Test invocation failed: {e}")
        print("     (This may be expected if MCP tools aren't connected yet)")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Deploy ATM Optimizer to AgentCore Runtime")
    parser.add_argument("--skip-iam", action="store_true", help="Skip IAM role creation")
    parser.add_argument("--skip-docker", action="store_true", help="Skip Docker build/push")
    parser.add_argument("--skip-memory", action="store_true", help="Skip memory creation")
    parser.add_argument("--skip-test", action="store_true", help="Skip test invocation")
    args = parser.parse_args()

    print("=" * 60)
    print("  NeoBank ATM Profitability Optimizer — AgentCore Runtime Deploy")
    print(f"  Region: {DEPLOY_REGION} | Model: {MODEL_ID}")
    print(f"  Agent: {AGENT_NAME}")
    print("=" * 60)

    ecr_uri = f"{ACCOUNT_ID}.dkr.ecr.{DEPLOY_REGION}.amazonaws.com"
    image_uri = f"{ecr_uri}/{ECR_REPO_NAME}:latest"
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{ROLE_NAME}"

    # Step 1: IAM
    if not args.skip_iam:
        role_arn = step1_create_iam_role()
    else:
        print("\n  [Skipping IAM role creation]")

    # Step 2: Docker
    if not args.skip_docker:
        image_uri = step2_build_and_push_docker()
    else:
        print(f"\n  [Skipping Docker build, using: {image_uri}]")

    # Step 3: Memory
    memory_id = ""
    if not args.skip_memory:
        memory_id = step3_create_memory()
    else:
        memory_id = os.environ.get("ATM_MEMORY_ID", "")
        print(f"\n  [Skipping memory creation, using: {memory_id}]")

    # Step 4: Create Runtime
    runtime_id, runtime_arn = step4_create_runtime(image_uri, role_arn, memory_id)

    # Step 5: Wait
    step5_wait_for_ready(runtime_id)

    # Step 6: Test
    if not args.skip_test:
        step6_test_invocation(runtime_arn)

    # Summary
    print("\n" + "=" * 60)
    print("  ✅ Deployment Complete!")
    print(f"  Runtime ID:  {runtime_id}")
    print(f"  Runtime ARN: {runtime_arn}")
    print(f"  Memory ID:   {memory_id}")
    print(f"  Model:       {MODEL_ID}")
    print(f"  Image:       {image_uri}")
    print("=" * 60)

    # Output config for Streamlit
    print("\n  Environment variables for Streamlit:")
    print(f"  ATM_AGENTCORE_RUNTIME_ARN={runtime_arn}")
    print(f"  ATM_MEMORY_ID={memory_id}")


if __name__ == "__main__":
    main()
