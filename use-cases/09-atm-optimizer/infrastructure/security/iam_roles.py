"""
IAM least-privilege role definitions for ATM Profitability Optimizer.

Generates CloudFormation-compatible IAM policy documents as Python dicts for:
- EC2 Streamlit instance role (me-south-1)
- MCP Server role (me-south-1)
- AgentCore Agent role (eu-west-1) with cross-region STS assume-role

References:
  - Requirement 23: IAM least-privilege, cross-region STS
  - Requirement 16: Private network, VPC endpoints, no public access
  - Requirement 18: KMS encryption at rest
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_REGION = "me-south-1"
AI_REGION = "eu-west-1"
S3_DATA_BUCKET = "atm-optimizer-data-me-south-1"
ATHENA_DATABASE = "atm_optimizer"
ATHENA_WORKGROUP = "atm-optimizer"


def _account_arn(service: str, resource: str, region: str = "") -> str:
    """Build an ARN string with a placeholder account ID."""
    region_part = region if region else ""
    return f"arn:aws:{service}:{region_part}:${{AWS::AccountId}}:{resource}"


# ---------------------------------------------------------------------------
# EC2 Streamlit Instance Role
# ---------------------------------------------------------------------------
def get_ec2_role(
    account_id: str = "${AWS::AccountId}",
    bucket_name: str = S3_DATA_BUCKET,
) -> dict:
    """
    IAM role for the EC2 instance running Streamlit.

    Permissions:
    - Read-only S3 access to the data bucket
    - Cognito user pool auth operations
    - CloudWatch Logs for application logging
    - KMS decrypt for reading encrypted objects
    """
    bucket_arn = f"arn:aws:s3:::{bucket_name}"

    return {
        "RoleName": "neobank-atm-optimizer-ec2-role",
        "AssumeRolePolicyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        },
        "Policies": [
            {
                "PolicyName": "EC2S3ReadOnly",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "S3ReadData",
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                                "s3:ListBucket",
                                "s3:GetBucketLocation",
                            ],
                            "Resource": [
                                bucket_arn,
                                f"{bucket_arn}/*",
                            ],
                        },
                    ],
                },
            },
            {
                "PolicyName": "EC2CognitoAuth",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "CognitoAuth",
                            "Effect": "Allow",
                            "Action": [
                                "cognito-idp:InitiateAuth",
                                "cognito-idp:RespondToAuthChallenge",
                                "cognito-idp:GetUser",
                            ],
                            "Resource": f"arn:aws:cognito-idp:{DATA_REGION}:{account_id}:userpool/*",
                        },
                    ],
                },
            },
            {
                "PolicyName": "EC2CloudWatchLogs",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "CloudWatchLogs",
                            "Effect": "Allow",
                            "Action": [
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            "Resource": f"arn:aws:logs:{DATA_REGION}:{account_id}:log-group:/ec2/neobank-atm-optimizer:*",
                        },
                    ],
                },
            },
            {
                "PolicyName": "EC2KMSDecrypt",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "KMSDecrypt",
                            "Effect": "Allow",
                            "Action": [
                                "kms:Decrypt",
                                "kms:DescribeKey",
                            ],
                            "Resource": f"arn:aws:kms:{DATA_REGION}:{account_id}:key/*",
                            "Condition": {
                                "StringEquals": {
                                    "kms:ViaService": f"s3.{DATA_REGION}.amazonaws.com",
                                }
                            },
                        },
                    ],
                },
            },
        ],
        "Tags": [
            {"Key": "Project", "Value": "ATM-Profitability-Optimizer"},
            {"Key": "Component", "Value": "EC2-Streamlit"},
        ],
    }


# ---------------------------------------------------------------------------
# MCP Server Role
# ---------------------------------------------------------------------------
def get_mcp_server_role(
    account_id: str = "${AWS::AccountId}",
    bucket_name: str = S3_DATA_BUCKET,
) -> dict:
    """
    IAM role for the MCP server (Lambda/ECS) in me-south-1.

    Permissions:
    - S3 read/write for data bucket (read data + write Athena results)
    - Athena query execution scoped to the workgroup
    - Glue catalog read for table metadata
    - KMS encrypt/decrypt for data operations
    """
    bucket_arn = f"arn:aws:s3:::{bucket_name}"

    return {
        "RoleName": "neobank-atm-optimizer-mcp-server-role",
        "AssumeRolePolicyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                },
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                },
            ],
        },
        "Policies": [
            {
                "PolicyName": "MCPS3Access",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "S3ReadData",
                            "Effect": "Allow",
                            "Action": [
                                "s3:GetObject",
                                "s3:ListBucket",
                                "s3:GetBucketLocation",
                            ],
                            "Resource": [
                                bucket_arn,
                                f"{bucket_arn}/*",
                            ],
                        },
                        {
                            "Sid": "S3WriteAthenaResults",
                            "Effect": "Allow",
                            "Action": [
                                "s3:PutObject",
                                "s3:GetObject",
                                "s3:AbortMultipartUpload",
                            ],
                            "Resource": f"{bucket_arn}/athena_results/*",
                        },
                    ],
                },
            },
            {
                "PolicyName": "MCPAthenaAccess",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AthenaQueryExecution",
                            "Effect": "Allow",
                            "Action": [
                                "athena:StartQueryExecution",
                                "athena:GetQueryExecution",
                                "athena:GetQueryResults",
                                "athena:StopQueryExecution",
                            ],
                            "Resource": f"arn:aws:athena:{DATA_REGION}:{account_id}:workgroup/{ATHENA_WORKGROUP}",
                        },
                    ],
                },
            },
            {
                "PolicyName": "MCPGlueCatalog",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "GlueCatalogRead",
                            "Effect": "Allow",
                            "Action": [
                                "glue:GetDatabase",
                                "glue:GetTable",
                                "glue:GetTables",
                                "glue:GetPartitions",
                            ],
                            "Resource": [
                                f"arn:aws:glue:{DATA_REGION}:{account_id}:catalog",
                                f"arn:aws:glue:{DATA_REGION}:{account_id}:database/{ATHENA_DATABASE}",
                                f"arn:aws:glue:{DATA_REGION}:{account_id}:table/{ATHENA_DATABASE}/*",
                            ],
                        },
                    ],
                },
            },
            {
                "PolicyName": "MCPKMSAccess",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "KMSDataOperations",
                            "Effect": "Allow",
                            "Action": [
                                "kms:Decrypt",
                                "kms:DescribeKey",
                                "kms:GenerateDataKey",
                            ],
                            "Resource": f"arn:aws:kms:{DATA_REGION}:{account_id}:key/*",
                            "Condition": {
                                "StringEquals": {
                                    "kms:ViaService": [
                                        f"s3.{DATA_REGION}.amazonaws.com",
                                        f"athena.{DATA_REGION}.amazonaws.com",
                                    ],
                                }
                            },
                        },
                    ],
                },
            },
            {
                "PolicyName": "MCPCloudWatchLogs",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "CloudWatchLogs",
                            "Effect": "Allow",
                            "Action": [
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            "Resource": f"arn:aws:logs:{DATA_REGION}:{account_id}:log-group:/mcp/neobank-atm-optimizer:*",
                        },
                    ],
                },
            },
        ],
        "Tags": [
            {"Key": "Project", "Value": "ATM-Profitability-Optimizer"},
            {"Key": "Component", "Value": "MCP-Server"},
        ],
    }


# ---------------------------------------------------------------------------
# AgentCore Agent Role (eu-west-1 with cross-region STS)
# ---------------------------------------------------------------------------
def get_agentcore_agent_role(
    account_id: str = "${AWS::AccountId}",
    bucket_name: str = S3_DATA_BUCKET,
) -> dict:
    """
    IAM role for the Strands agent running in AgentCore (eu-west-1).

    This role uses STS assume-role to access me-south-1 resources
    cross-region. The agent itself runs in eu-west-1 but needs to
    invoke MCP tools that query data in me-south-1.

    Permissions:
    - Bedrock model invocation (eu-west-1)
    - STS assume-role for cross-region data access (me-south-1)
    - CloudWatch Logs for observability (eu-west-1)
    """
    return {
        "RoleName": "neobank-atm-optimizer-agentcore-role",
        "AssumeRolePolicyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "bedrock.amazonaws.com",
                    },
                    "Action": "sts:AssumeRole",
                    "Condition": {
                        "StringEquals": {
                            "aws:SourceAccount": account_id,
                        }
                    },
                },
            ],
        },
        "Policies": [
            {
                "PolicyName": "AgentCoreBedrockInvoke",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "BedrockModelInvoke",
                            "Effect": "Allow",
                            "Action": [
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream",
                            ],
                            "Resource": [
                                f"arn:aws:bedrock:{AI_REGION}::foundation-model/anthropic.claude-3-5-sonnet-*",
                                f"arn:aws:bedrock:{AI_REGION}::foundation-model/amazon.nova-pro-*",
                            ],
                        },
                    ],
                },
            },
            {
                "PolicyName": "AgentCoreCrossRegionSTS",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AssumeDataRegionRole",
                            "Effect": "Allow",
                            "Action": "sts:AssumeRole",
                            "Resource": f"arn:aws:iam::{account_id}:role/neobank-atm-optimizer-mcp-server-role",
                        },
                    ],
                },
            },
            {
                "PolicyName": "AgentCoreObservability",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "CloudWatchLogs",
                            "Effect": "Allow",
                            "Action": [
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                            "Resource": f"arn:aws:logs:{AI_REGION}:{account_id}:log-group:/agentcore/neobank-atm-optimizer:*",
                        },
                        {
                            "Sid": "CloudWatchMetrics",
                            "Effect": "Allow",
                            "Action": [
                                "cloudwatch:PutMetricData",
                            ],
                            "Resource": "*",
                            "Condition": {
                                "StringEquals": {
                                    "cloudwatch:namespace": "NeoBank/ATMOptimizer",
                                }
                            },
                        },
                    ],
                },
            },
            {
                "PolicyName": "AgentCoreMemory",
                "PolicyDocument": {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "AgentCoreMemoryAccess",
                            "Effect": "Allow",
                            "Action": [
                                "bedrock:GetAgentMemory",
                                "bedrock:PutAgentMemory",
                                "bedrock:DeleteAgentMemory",
                            ],
                            "Resource": f"arn:aws:bedrock:{AI_REGION}:{account_id}:agent/*",
                        },
                    ],
                },
            },
        ],
        "Tags": [
            {"Key": "Project", "Value": "ATM-Profitability-Optimizer"},
            {"Key": "Component", "Value": "AgentCore-Agent"},
            {"Key": "Region", "Value": AI_REGION},
        ],
    }
