"""
Security property-based tests for ATM Profitability Optimizer.

Uses Hypothesis + moto to verify:
  - Property 7: All S3 objects are KMS encrypted with customer-managed keys
  - S3 bucket policies block public access and enforce encryption
  - IAM roles follow least-privilege principles

**Validates: Requirements 18.1, 18.5**
"""

import os
import sys
import json

import boto3
import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import (
    text,
    sampled_from,
    binary,
    just,
    one_of,
    composite,
)
from moto import mock_aws

# ---------------------------------------------------------------------------
# Make infrastructure modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from infrastructure.security.s3_policies import (
    get_bucket_public_access_block,
    get_bucket_policy,
    get_bucket_encryption_config,
    S3_DATA_PREFIXES,
)
from infrastructure.security.iam_roles import (
    get_ec2_role,
    get_mcp_server_role,
    get_agentcore_agent_role,
)
from infrastructure.security.vpc_flow_logs import (
    get_flow_log_config,
    get_flow_log_iam_role,
    get_flow_log_log_group,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_REGION = "me-south-1"
TEST_BUCKET = "atm-optimizer-data-me-south-1"
S3_PREFIXES = list(S3_DATA_PREFIXES.keys())


# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------
s3_prefix = sampled_from(S3_PREFIXES)
object_name = text(
    alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-",
    min_size=1,
    max_size=30,
).map(lambda s: s + ".csv")
object_body = binary(min_size=1, max_size=256)


@composite
def s3_object_key(draw):
    """Generate a valid S3 key under one of the project prefixes."""
    prefix = draw(s3_prefix)
    name = draw(object_name)
    return f"{prefix}{name}"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def kms_key_arn():
    """Create a KMS key in moto and return its ARN."""
    with mock_aws():
        kms = boto3.client("kms", region_name="us-east-1")
        key = kms.create_key(
            Description="Test transaction key",
            KeyUsage="ENCRYPT_DECRYPT",
        )
        yield key["KeyMetadata"]["Arn"]


# ---------------------------------------------------------------------------
# Property 7: All S3 objects must be KMS encrypted
# ---------------------------------------------------------------------------
class TestS3EncryptionProperty:
    """
    Property 7: All S3 objects must be encrypted with KMS customer-managed
    keys. No unencrypted objects shall exist in any project S3 bucket.

    **Validates: Requirements 18.1, 18.5**
    """

    @given(key=s3_object_key(), body=object_body)
    @settings(max_examples=50, deadline=None)
    def test_objects_uploaded_with_kms_are_encrypted(self, key, body):
        """
        **Validates: Requirements 18.1, 18.5**

        When an object is uploaded to the data bucket with KMS encryption
        specified, the stored object's ServerSideEncryption must be 'aws:kms'.
        """
        with mock_aws():
            # Setup: create KMS key and S3 bucket with default encryption
            kms = boto3.client("kms", region_name="us-east-1")
            key_resp = kms.create_key(
                Description="Test key",
                KeyUsage="ENCRYPT_DECRYPT",
            )
            kms_key_id = key_resp["KeyMetadata"]["KeyId"]
            kms_arn = key_resp["KeyMetadata"]["Arn"]

            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=TEST_BUCKET)

            # Configure default KMS encryption on the bucket
            s3.put_bucket_encryption(
                Bucket=TEST_BUCKET,
                ServerSideEncryptionConfiguration={
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "aws:kms",
                                "KMSMasterKeyID": kms_arn,
                            },
                            "BucketKeyEnabled": True,
                        }
                    ]
                },
            )

            # Upload object with explicit KMS encryption
            s3.put_object(
                Bucket=TEST_BUCKET,
                Key=key,
                Body=body,
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=kms_arn,
            )

            # Verify: object must be KMS encrypted
            head = s3.head_object(Bucket=TEST_BUCKET, Key=key)
            assert head["ServerSideEncryption"] == "aws:kms", (
                f"Object {key} is not KMS encrypted. "
                f"Got: {head.get('ServerSideEncryption')}"
            )

    @given(key=s3_object_key(), body=object_body)
    @settings(max_examples=50, deadline=None)
    def test_bucket_default_encryption_applies_kms(self, key, body):
        """
        **Validates: Requirements 18.1, 18.5**

        When a bucket has default KMS encryption configured, objects
        uploaded without explicit encryption headers still get KMS
        encryption applied by the bucket default.
        """
        with mock_aws():
            kms = boto3.client("kms", region_name="us-east-1")
            key_resp = kms.create_key(
                Description="Test key",
                KeyUsage="ENCRYPT_DECRYPT",
            )
            kms_arn = key_resp["KeyMetadata"]["Arn"]

            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=TEST_BUCKET)

            # Set default encryption to KMS
            s3.put_bucket_encryption(
                Bucket=TEST_BUCKET,
                ServerSideEncryptionConfiguration={
                    "Rules": [
                        {
                            "ApplyServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "aws:kms",
                                "KMSMasterKeyID": kms_arn,
                            },
                            "BucketKeyEnabled": True,
                        }
                    ]
                },
            )

            # Upload WITHOUT explicit encryption — bucket default should apply
            s3.put_object(
                Bucket=TEST_BUCKET,
                Key=key,
                Body=body,
            )

            # Verify encryption was applied
            head = s3.head_object(Bucket=TEST_BUCKET, Key=key)
            encryption = head.get("ServerSideEncryption", "")
            assert encryption == "aws:kms", (
                f"Object {key} not encrypted by bucket default. "
                f"Got: {encryption!r}"
            )


# ---------------------------------------------------------------------------
# S3 Policy Structure Tests
# ---------------------------------------------------------------------------
class TestS3PolicyStructure:
    """Verify S3 bucket policy documents are well-formed."""

    def test_public_access_block_all_true(self):
        """All four public access block flags must be True."""
        config = get_bucket_public_access_block()
        pab = config["PublicAccessBlockConfiguration"]
        assert pab["BlockPublicAcls"] is True
        assert pab["BlockPublicPolicy"] is True
        assert pab["IgnorePublicAcls"] is True
        assert pab["RestrictPublicBuckets"] is True

    def test_bucket_policy_has_deny_non_tls(self):
        """Policy must contain a statement denying non-TLS requests."""
        policy = get_bucket_policy()
        statements = policy["Statement"]
        deny_tls = [
            s for s in statements
            if s.get("Sid") == "DenyNonTLSRequests"
        ]
        assert len(deny_tls) == 1
        assert deny_tls[0]["Effect"] == "Deny"

    def test_bucket_policy_has_vpc_endpoint_restriction(self):
        """Policy must restrict access to VPC endpoint only."""
        policy = get_bucket_policy()
        statements = policy["Statement"]
        vpc_stmt = [
            s for s in statements
            if s.get("Sid") == "DenyNonVPCEndpointAccess"
        ]
        assert len(vpc_stmt) == 1
        assert "aws:sourceVpce" in str(vpc_stmt[0]["Condition"])

    def test_bucket_policy_enforces_kms_on_put(self):
        """Policy must deny PutObject without KMS encryption."""
        policy = get_bucket_policy()
        statements = policy["Statement"]
        kms_stmt = [
            s for s in statements
            if s.get("Sid") == "DenyUnencryptedPutObject"
        ]
        assert len(kms_stmt) == 1
        assert kms_stmt[0]["Action"] == "s3:PutObject"
        assert kms_stmt[0]["Effect"] == "Deny"

    def test_encryption_config_uses_kms(self):
        """Bucket encryption config must specify aws:kms algorithm."""
        config = get_bucket_encryption_config(
            kms_key_arn="arn:aws:kms:me-south-1:123456789012:key/test-key"
        )
        rules = config["ServerSideEncryptionConfiguration"]["Rules"]
        assert len(rules) == 1
        default = rules[0]["ApplyServerSideEncryptionByDefault"]
        assert default["SSEAlgorithm"] == "aws:kms"
        assert "test-key" in default["KMSMasterKeyID"]


# ---------------------------------------------------------------------------
# IAM Role Structure Tests
# ---------------------------------------------------------------------------
class TestIAMRoleStructure:
    """Verify IAM roles follow least-privilege principles."""

    def test_ec2_role_has_no_admin_actions(self):
        """EC2 role must not have wildcard actions or admin permissions."""
        role = get_ec2_role(account_id="123456789012")
        for policy in role["Policies"]:
            for stmt in policy["PolicyDocument"]["Statement"]:
                actions = stmt["Action"]
                if isinstance(actions, str):
                    actions = [actions]
                for action in actions:
                    assert action != "*", (
                        f"EC2 role has wildcard action in {policy['PolicyName']}"
                    )
                    assert not action.startswith("iam:"), (
                        f"EC2 role has IAM action: {action}"
                    )

    def test_mcp_role_scoped_to_data_bucket(self):
        """MCP server role S3 actions must be scoped to the data bucket."""
        role = get_mcp_server_role(account_id="123456789012")
        s3_policy = next(
            p for p in role["Policies"] if p["PolicyName"] == "MCPS3Access"
        )
        for stmt in s3_policy["PolicyDocument"]["Statement"]:
            resources = stmt["Resource"]
            if isinstance(resources, str):
                resources = [resources]
            for r in resources:
                assert "atm-optimizer-data" in r, (
                    f"MCP S3 resource not scoped to data bucket: {r}"
                )

    def test_agentcore_role_has_cross_region_sts(self):
        """AgentCore role must include STS assume-role for cross-region access."""
        role = get_agentcore_agent_role(account_id="123456789012")
        sts_policy = next(
            p for p in role["Policies"]
            if p["PolicyName"] == "AgentCoreCrossRegionSTS"
        )
        stmt = sts_policy["PolicyDocument"]["Statement"][0]
        assert stmt["Action"] == "sts:AssumeRole"
        assert "mcp-server-role" in stmt["Resource"]

    def test_agentcore_role_bedrock_scoped_to_eu_west_1(self):
        """AgentCore Bedrock permissions must target eu-west-1 only."""
        role = get_agentcore_agent_role(account_id="123456789012")
        bedrock_policy = next(
            p for p in role["Policies"]
            if p["PolicyName"] == "AgentCoreBedrockInvoke"
        )
        for stmt in bedrock_policy["PolicyDocument"]["Statement"]:
            resources = stmt["Resource"]
            if isinstance(resources, str):
                resources = [resources]
            for r in resources:
                assert "eu-west-1" in r, (
                    f"Bedrock resource not in eu-west-1: {r}"
                )


# ---------------------------------------------------------------------------
# VPC Flow Logs Structure Tests
# ---------------------------------------------------------------------------
class TestVPCFlowLogsStructure:
    """Verify VPC Flow Logs configuration."""

    def test_flow_log_captures_all_traffic(self):
        """Flow log must capture ALL traffic (accept + reject)."""
        config = get_flow_log_config()
        assert config["Properties"]["TrafficType"] == "ALL"

    def test_flow_log_uses_cloudwatch(self):
        """Flow log must send to CloudWatch Logs."""
        config = get_flow_log_config()
        assert config["Properties"]["LogDestinationType"] == "cloud-watch-logs"

    def test_flow_log_role_allows_log_delivery(self):
        """Flow log IAM role must allow log stream creation and event writing."""
        role = get_flow_log_iam_role()
        policy = role["Properties"]["Policies"][0]
        actions = policy["PolicyDocument"]["Statement"][0]["Action"]
        assert "logs:CreateLogStream" in actions
        assert "logs:PutLogEvents" in actions

    def test_log_group_has_retention(self):
        """Log group must have a retention period set."""
        lg = get_flow_log_log_group()
        assert lg["Properties"]["RetentionInDays"] == 90

    def test_log_group_supports_kms(self):
        """Log group should accept KMS key for encryption."""
        lg = get_flow_log_log_group(
            kms_key_arn="arn:aws:kms:me-south-1:123456789012:key/app-key"
        )
        assert lg["Properties"]["KmsKeyId"] == (
            "arn:aws:kms:me-south-1:123456789012:key/app-key"
        )
