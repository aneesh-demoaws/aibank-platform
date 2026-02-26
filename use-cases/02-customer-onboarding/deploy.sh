#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
source "${DIR}/../../config/env.sh"

echo "=== Deploying Customer Onboarding A2A Agent ==="

# 1. Create ECR repo
aws ecr create-repository \
  --repository-name bedrock-agentcore-customeronboarding-agent \
  --region "${COMPUTE_REGION}" 2>/dev/null || echo "ECR repo exists"

# 2. Deploy agent to AgentCore as A2A
cd "${DIR}/agent"
pip install bedrock-agentcore-starter-toolkit 2>/dev/null
agentcore configure -e main.py --protocol A2A 2>/dev/null || true
agentcore deploy 2>&1 | tail -5

ONB_ARN=$(grep 'agent_arn' .bedrock_agentcore.yaml | head -1 | awk '{print $2}')
echo "ONBOARDING_RUNTIME_ARN=${ONB_ARN}"

echo ""
echo ">>> Add to config/env.sh:"
echo "export ONBOARDING_RUNTIME_ARN=\"${ONB_ARN}\""
echo ""
echo "Next: Add IAM policy to Alma's execution role to invoke this runtime"
echo "Next: Update use-cases/01-alma-faq/agent/alma_agentcore.py ONBOARDING_ARN"
echo "Next: Update use-cases/01-alma-faq/lambda/handler.py ONBOARDING_ARN"
