#!/bin/bash
set -e
source "$(dirname "$0")/../../config/env.sh"
echo "=== Aurora Serverless v2 (${DATA_REGION}) ==="
aws rds create-db-cluster --db-cluster-identifier "${AURORA_CLUSTER_NAME}" --engine aurora-mysql --engine-version 8.0.mysql_aurora.3.04.0 --serverless-v2-scaling-configuration MinCapacity=0.5,MaxCapacity=4 --master-username admin --manage-master-user-password --database-name "${AURORA_DB_NAME}" --enable-http-endpoint --region "${DATA_REGION}" 2>/dev/null || echo "Cluster exists"
aws rds create-db-instance --db-instance-identifier "${AURORA_CLUSTER_NAME}-instance-1" --db-cluster-identifier "${AURORA_CLUSTER_NAME}" --engine aurora-mysql --db-instance-class db.serverless --region "${DATA_REGION}" 2>/dev/null || echo "Instance exists"
echo "Waiting for cluster..."
aws rds wait db-cluster-available --db-cluster-identifier "${AURORA_CLUSTER_NAME}" --region "${DATA_REGION}"
CLUSTER_ARN=$(aws rds describe-db-clusters --db-cluster-identifier "${AURORA_CLUSTER_NAME}" --region "${DATA_REGION}" --query 'DBClusters[0].DBClusterArn' --output text)
SECRET_ARN=$(aws rds describe-db-clusters --db-cluster-identifier "${AURORA_CLUSTER_NAME}" --region "${DATA_REGION}" --query 'DBClusters[0].MasterUserSecret.SecretArn' --output text)
echo ">>> Add to config/env.sh:"
echo "export CLUSTER_ARN=\"${CLUSTER_ARN}\""
echo "export DB_SECRET_ARN=\"${SECRET_ARN}\""
echo ""
echo "Apply schema: Run statements from foundation/01-aurora/schema.sql via RDS Data API"
