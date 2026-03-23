"""
VPC Flow Logs configuration for ATM Profitability Optimizer.

Generates CloudFormation-compatible resource definitions as Python dicts for:
- CloudWatch Log Group (KMS encrypted with application key)
- IAM role for VPC Flow Logs delivery
- VPC Flow Log resource capturing ALL traffic

References:
  - Requirement 16.7: VPC Flow Logs for network traffic monitoring
  - Requirement 18: KMS encryption for logs
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_REGION = "me-south-1"
PROJECT_NAME = "ATM-Profitability-Optimizer"
LOG_GROUP_NAME = f"/vpc/{PROJECT_NAME}/flow-logs"
LOG_RETENTION_DAYS = 90


# ---------------------------------------------------------------------------
# CloudWatch Log Group
# ---------------------------------------------------------------------------
def get_flow_log_log_group(
    account_id: str = "${AWS::AccountId}",
    kms_key_arn: str | None = None,
) -> dict:
    """
    Return a CloudWatch Logs LogGroup config for VPC Flow Logs.

    The log group is encrypted with the application-data KMS key and
    retains logs for 90 days.
    """
    config: dict = {
        "Type": "AWS::Logs::LogGroup",
        "Properties": {
            "LogGroupName": LOG_GROUP_NAME,
            "RetentionInDays": LOG_RETENTION_DAYS,
            "Tags": [
                {"Key": "Project", "Value": PROJECT_NAME},
                {"Key": "Purpose", "Value": "VPCFlowLogs"},
            ],
        },
    }
    if kms_key_arn:
        config["Properties"]["KmsKeyId"] = kms_key_arn
    return config


# ---------------------------------------------------------------------------
# IAM Role for Flow Log Delivery
# ---------------------------------------------------------------------------
def get_flow_log_iam_role(
    account_id: str = "${AWS::AccountId}",
) -> dict:
    """
    Return an IAM role that allows VPC Flow Logs to write to CloudWatch Logs.
    """
    return {
        "Type": "AWS::IAM::Role",
        "Properties": {
            "RoleName": "neobank-atm-optimizer-vpc-flow-log-role",
            "AssumeRolePolicyDocument": {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {
                            "Service": "vpc-flow-logs.amazonaws.com",
                        },
                        "Action": "sts:AssumeRole",
                    }
                ],
            },
            "Policies": [
                {
                    "PolicyName": "VPCFlowLogDelivery",
                    "PolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Sid": "AllowLogDelivery",
                                "Effect": "Allow",
                                "Action": [
                                    "logs:CreateLogStream",
                                    "logs:PutLogEvents",
                                    "logs:DescribeLogGroups",
                                    "logs:DescribeLogStreams",
                                ],
                                "Resource": f"arn:aws:logs:{DATA_REGION}:{account_id}:log-group:{LOG_GROUP_NAME}:*",
                            },
                        ],
                    },
                }
            ],
            "Tags": [
                {"Key": "Project", "Value": PROJECT_NAME},
                {"Key": "Component", "Value": "VPCFlowLogs"},
            ],
        },
    }


# ---------------------------------------------------------------------------
# VPC Flow Log Resource
# ---------------------------------------------------------------------------
def get_flow_log_config(
    vpc_id: str = "PLACEHOLDER_VPC_ID",
    log_group_name: str = LOG_GROUP_NAME,
    flow_log_role_arn: str = "PLACEHOLDER_ROLE_ARN",
) -> dict:
    """
    Return a VPC Flow Log resource config capturing ALL traffic.

    Traffic types: ALL (accept + reject) for full security auditing.
    Destination: CloudWatch Logs (encrypted log group).
    """
    return {
        "Type": "AWS::EC2::FlowLog",
        "Properties": {
            "ResourceId": vpc_id,
            "ResourceType": "VPC",
            "TrafficType": "ALL",
            "LogDestinationType": "cloud-watch-logs",
            "LogGroupName": log_group_name,
            "DeliverLogsPermissionArn": flow_log_role_arn,
            "MaxAggregationInterval": 60,
            "Tags": [
                {"Key": "Project", "Value": PROJECT_NAME},
                {"Key": "Purpose", "Value": "NetworkSecurityAudit"},
            ],
        },
    }
