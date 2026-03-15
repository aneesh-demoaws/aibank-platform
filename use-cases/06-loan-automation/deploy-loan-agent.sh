#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
REGION="${AWS_REGION:-eu-west-1}"

echo "=== Deploying Loan AI Agent (A2A) ==="

# 1. Create ECR repo
aws ecr create-repository \
  --repository-name bedrock-agentcore-loan-agent \
  --region "${REGION}" 2>/dev/null || echo "ECR repo exists"

# 2. Deploy agent to AgentCore as A2A
cd "${DIR}/agent"
pip install bedrock-agentcore-starter-toolkit 2>/dev/null
agentcore configure -e main.py --protocol A2A -n loan_agent_a2a 2>/dev/null || true
agentcore deploy 2>&1 | tail -5

LOAN_ARN=$(grep 'agent_arn' .bedrock_agentcore.yaml 2>/dev/null | head -1 | awk '{print $2}')
echo ""
echo "LOAN_AGENT_ARN=${LOAN_ARN}"
echo ""
echo "Next steps:"
echo "1. Update Alma Banking Assistant env var: LOAN_AGENT_ARN=${LOAN_ARN}"
echo "2. Update Alma Banking voice server env var: LOAN_AGENT_ARN=${LOAN_ARN}"
echo "3. Redeploy Alma Banking Assistant"
