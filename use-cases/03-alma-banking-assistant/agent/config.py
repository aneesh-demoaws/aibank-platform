"""AI Bank Graph Agent — Shared Configuration."""
import os
import boto3

# Regions
REGION = os.environ.get("AWS_REGION", "eu-west-1")
DB_REGION = os.environ.get("DB_REGION", "eu-west-1")

# Aurora
CLUSTER_ARN = os.environ.get("CLUSTER_ARN", "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr")
SECRET_ARN = os.environ.get("SECRET_ARN", "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6")
DB_NAME = os.environ.get("DB_NAME", "corebanking")

# DynamoDB
KYC_TABLE = os.environ.get("KYC_TABLE", "aibank-customer-kyc")
LOAN_TABLE = os.environ.get("LOAN_TABLE", "aibank-personal-loan")
CONFIG_TABLE = os.environ.get("CONFIG_TABLE", "aibank-loan-config")

# S3
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "aibank-loan-uploads-519124228967")

# Lambda
KYC_PRESIGNED_URL_LAMBDA = os.environ.get("KYC_PRESIGNED_URL_LAMBDA", "aibank-kyc-presigned-url")

# Models
ROUTER_MODEL = os.environ.get("ROUTER_MODEL", "eu.amazon.nova-2-lite-v1:0")
SPECIALIST_MODEL = os.environ.get("SPECIALIST_MODEL", "eu.anthropic.claude-sonnet-4-20250514-v1:0")

# AgentCore Memory (STM_ONLY). AgentCore Runtime normally injects
# BEDROCK_AGENTCORE_MEMORY_ID; the literal below is the id recorded in
# .bedrock_agentcore.yaml and is used only as a last-resort fallback so a
# misconfigured container cannot silently disable conversational memory.
MEMORY_ID = os.environ.get(
    "BEDROCK_AGENTCORE_MEMORY_ID",
    os.environ.get("MEMORY_ID", "alma_graph_mem-sTlMSG4Tf3"),
)

# Clients (shared across tools)
rds = boto3.client("rds-data", region_name=DB_REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
lambda_client = boto3.client("lambda", region_name=REGION)
s3 = boto3.client("s3", region_name=REGION)
