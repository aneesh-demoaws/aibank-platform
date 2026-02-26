#!/bin/bash
set -e
source "$(dirname "$0")/../../config/env.sh"
[ -z "${SES_ACCOUNT_ID}" ] && { echo "SES_ACCOUNT_ID not set — skipping. Agents use default SES."; exit 0; }
echo "=== Cross-Account SES Credentials ==="
read -p "SES Access Key ID: " AK; read -sp "SES Secret Access Key: " SK; echo ""
ARN=$(aws secretsmanager create-secret --name "aibank-ses-credentials" --secret-string "{\"access_key_id\":\"${AK}\",\"secret_access_key\":\"${SK}\",\"region\":\"${SES_REGION}\",\"sender\":\"${SES_SENDER}\"}" --region "${COMPUTE_REGION}" --query 'ARN' --output text 2>/dev/null || aws secretsmanager describe-secret --secret-id "aibank-ses-credentials" --region "${COMPUTE_REGION}" --query 'ARN' --output text)
echo ">>> Add to config/env.sh:"
echo "export SES_SECRET_ARN=\"${ARN}\""
