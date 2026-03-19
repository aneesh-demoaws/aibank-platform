#!/bin/bash
set -e
source "$(dirname "$0")/../../config/env.sh"
echo "=== Cognito User Pool (${DATA_REGION}) ==="
POOL_ID=$(aws cognito-idp create-user-pool --pool-name "${COGNITO_POOL_NAME}" --auto-verified-attributes email --username-attributes email --schema Name=email,Required=true,Mutable=true Name=given_name,Required=true,Mutable=true Name=family_name,Required=true,Mutable=true Name=phone_number,Required=false,Mutable=true Name=birthdate,Required=false,Mutable=true Name=customer_id,AttributeDataType=String,Mutable=true,Required=false --region "${DATA_REGION}" --query 'UserPool.Id' --output text 2>/dev/null || aws cognito-idp list-user-pools --max-results 10 --region "${DATA_REGION}" --query "UserPools[?Name=='${COGNITO_POOL_NAME}'].Id" --output text)
CLIENT_ID=$(aws cognito-idp create-user-pool-client --user-pool-id "${POOL_ID}" --client-name "${COGNITO_POOL_NAME}-client" --no-generate-secret --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH ALLOW_USER_SRP_AUTH --region "${DATA_REGION}" --query 'UserPoolClient.ClientId' --output text 2>/dev/null || echo "exists")
echo ">>> Add to config/env.sh:"
echo "export COGNITO_POOL_ID=\"${POOL_ID}\""
echo "export COGNITO_CLIENT_ID=\"${CLIENT_ID}\""
