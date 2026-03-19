#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
source "${DIR}/../../config/env.sh"

echo "=== Deploying Alma FAQ Agent ==="

# 1. Deploy agent to AgentCore
cd "${DIR}/agent"
pip install bedrock-agentcore-starter-toolkit 2>/dev/null
agentcore configure -e alma_agentcore.py --protocol HTTP 2>/dev/null || true
agentcore deploy 2>&1 | tail -5

ALMA_ARN=$(grep 'agent_arn' .bedrock_agentcore.yaml | head -1 | awk '{print $2}')
echo "ALMA_RUNTIME_ARN=${ALMA_ARN}"

# 2. Create DynamoDB session routing table
aws dynamodb create-table \
  --table-name aibank-session-routing \
  --attribute-definitions AttributeName=session_id,AttributeType=S \
  --key-schema AttributeName=session_id,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "${COMPUTE_REGION}" 2>/dev/null || echo "Table exists"

aws dynamodb update-time-to-live \
  --table-name aibank-session-routing \
  --time-to-live-specification AttributeName=ttl,Enabled=true \
  --region "${COMPUTE_REGION}" 2>/dev/null || true

# 3. Deploy Lambda proxy
cd "${DIR}/lambda"
zip -j /tmp/alma-proxy.zip handler.py
ROLE_ARN=$(aws iam get-role --role-name alma-public-lambda-role --query 'Role.Arn' --output text 2>/dev/null || echo "")
if [ -z "${ROLE_ARN}" ]; then
  aws iam create-role --role-name alma-public-lambda-role \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --region "${COMPUTE_REGION}"
  sleep 10
  ROLE_ARN=$(aws iam get-role --role-name alma-public-lambda-role --query 'Role.Arn' --output text)
fi

aws lambda create-function \
  --function-name alma-public-api \
  --runtime python3.12 \
  --handler handler.handler \
  --role "${ROLE_ARN}" \
  --zip-file fileb:///tmp/alma-proxy.zip \
  --timeout 180 --memory-size 128 \
  --region "${COMPUTE_REGION}" 2>/dev/null || \
aws lambda update-function-code \
  --function-name alma-public-api \
  --zip-file fileb:///tmp/alma-proxy.zip \
  --region "${COMPUTE_REGION}"

echo ""
echo ">>> Add to config/env.sh:"
echo "export ALMA_RUNTIME_ARN=\"${ALMA_ARN}\""
echo ""
echo "Next: Update lambda/handler.py with correct ALMA_ARN and ONBOARDING_ARN values"
echo "Next: Create API Gateway and attach Lambda"
