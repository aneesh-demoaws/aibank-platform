#!/usr/bin/env bash
# Deploy Lambda MCP Server for ATM Profitability Optimizer
#
# Usage: ./infrastructure/scripts/deploy-lambda-mcp.sh
#
# Prerequisites:
#   - AWS CLI configured with me-south-1 credentials
#   - Python 3.11 installed (for pip install)
#   - S3 data bucket already exists
#
# This script:
#   1. Creates a Lambda deployment package (ZIP) with tool code + dependencies
#   2. Uploads the ZIP to S3
#   3. Deploys/updates the CloudFormation stack

set -euo pipefail

# --- Configuration ---
ACCOUNT_ID="${AWS_ACCOUNT_ID:-CHANGE_ME}"
REGION="me-south-1"
PROJECT_NAME="ATM-Profitability-Optimizer"
STACK_NAME="atm-optimizer-lambda-mcp"
S3_BUCKET="atm-optimizer-data-${ACCOUNT_ID}-${REGION}"
S3_KEY="lambda/mcp-server.zip"
ATHENA_DB="atm_optimizer"

# KMS key ARN (from kms stack output)
KMS_KEY_ARN="arn:aws:kms:${REGION}:${ACCOUNT_ID}:alias/atm-transaction-key"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
BUILD_DIR="${PROJECT_ROOT}/.build/lambda-mcp"

echo "=== Building Lambda deployment package ==="

# Clean build directory
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}/package"

# Install Python dependencies into package
pip install boto3 --target "${BUILD_DIR}/package" --quiet --upgrade 2>/dev/null || true

# Copy project source files
cp -r "${PROJECT_ROOT}/agent" "${BUILD_DIR}/package/agent"
cp -r "${PROJECT_ROOT}/mcp_server" "${BUILD_DIR}/package/mcp_server"

# Remove unnecessary files from package
find "${BUILD_DIR}/package" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${BUILD_DIR}/package" -name "*.pyc" -delete 2>/dev/null || true
rm -rf "${BUILD_DIR}/package/agent/tools/_data_loader.py"

# Create ZIP
cd "${BUILD_DIR}/package"
zip -r "${BUILD_DIR}/mcp-server.zip" . -x "*.pyc" "__pycache__/*" > /dev/null
cd "${PROJECT_ROOT}"

ZIP_SIZE=$(du -h "${BUILD_DIR}/mcp-server.zip" | cut -f1)
echo "Package size: ${ZIP_SIZE}"

echo "=== Uploading to S3 ==="
aws s3 cp "${BUILD_DIR}/mcp-server.zip" "s3://${S3_BUCKET}/${S3_KEY}" \
    --region "${REGION}" \
    --sse aws:kms

echo "=== Deploying CloudFormation stack ==="
aws cloudformation deploy \
    --template-file "${PROJECT_ROOT}/infrastructure/cloudformation/lambda-mcp.yaml" \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides \
        ProjectName="${PROJECT_NAME}" \
        S3CodeBucket="${S3_BUCKET}" \
        S3CodeKey="${S3_KEY}" \
        AthenaDatabase="${ATHENA_DB}" \
        AthenaOutputBucket="${S3_BUCKET}" \
        KmsTransactionKeyArn="${KMS_KEY_ARN}" \
    --tags \
        Key=Project,Value="${PROJECT_NAME}" \
        Key=Component,Value=MCP-Server

echo "=== Getting Function URL ==="
FUNCTION_URL=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[?OutputKey=='FunctionUrl'].OutputValue" \
    --output text)

echo ""
echo "Deployment complete."
echo "Function URL: ${FUNCTION_URL}"
echo ""
echo "Set this in agent config:"
echo "  export ATM_MCP_SERVER_ENDPOINT=${FUNCTION_URL}"

# Cleanup
rm -rf "${BUILD_DIR}"
