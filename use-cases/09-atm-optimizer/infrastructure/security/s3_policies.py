"""
S3 bucket security policies for ATM Profitability Optimizer.

Generates CloudFormation-compatible policy documents as Python dicts:
- Block all public access
- Enforce KMS encryption on every PutObject
- Restrict access to the VPC S3 gateway endpoint only
- Deny non-TLS requests

References:
  - Requirement 16: Private network, VPC endpoints, no public access
  - Requirement 18: KMS encryption at rest, separate keys per classification
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_REGION = "me-south-1"
S3_DATA_BUCKET = "atm-optimizer-data-me-south-1"

# S3 prefixes and their KMS key aliases
S3_DATA_PREFIXES = {
    "atm_transactions/": "alias/neobank-atm-transaction-key",
    "atm_locations/": "alias/neobank-atm-application-key",
    "branch_locations/": "alias/neobank-atm-application-key",
    "proximity_data/": "alias/neobank-atm-application-key",
    "maintenance_costs/": "alias/neobank-atm-transaction-key",
    "cash_levels/": "alias/neobank-atm-transaction-key",
    "athena_results/": "alias/neobank-atm-application-key",
}


# ---------------------------------------------------------------------------
# Public Access Block
# ---------------------------------------------------------------------------
def get_bucket_public_access_block(bucket_name: str = S3_DATA_BUCKET) -> dict:
    """Return the S3 PublicAccessBlockConfiguration (all flags True)."""
    return {
        "Bucket": bucket_name,
        "PublicAccessBlockConfiguration": {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        },
    }


# ---------------------------------------------------------------------------
# Bucket Encryption Configuration
# ---------------------------------------------------------------------------
def get_bucket_encryption_config(
    kms_key_arn: str,
    bucket_name: str = S3_DATA_BUCKET,
) -> dict:
    """Return ServerSideEncryptionConfiguration enforcing KMS-CMK."""
    return {
        "Bucket": bucket_name,
        "ServerSideEncryptionConfiguration": {
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "aws:kms",
                        "KMSMasterKeyID": kms_key_arn,
                    },
                    "BucketKeyEnabled": True,
                }
            ]
        },
    }


# ---------------------------------------------------------------------------
# Bucket Policy
# ---------------------------------------------------------------------------
def get_bucket_policy(
    bucket_name: str = S3_DATA_BUCKET,
    vpc_endpoint_id: str = "vpce-PLACEHOLDER",
    kms_key_arn: str = "arn:aws:kms:me-south-1:123456789012:key/PLACEHOLDER",
) -> dict:
    """
    Return a CloudFormation-compatible S3 bucket policy document.

    Statements:
      1. Deny any request not using TLS (SecureTransport=false)
      2. Deny any request not originating from the VPC S3 gateway endpoint
      3. Deny PutObject unless server-side encryption is aws:kms
      4. Deny PutObject if the wrong KMS key is used
    """
    bucket_arn = f"arn:aws:s3:::{bucket_name}"

    return {
        "Version": "2012-10-17",
        "Statement": [
            # --- 1. Enforce TLS ---
            {
                "Sid": "DenyNonTLSRequests",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
                "Condition": {
                    "Bool": {"aws:SecureTransport": "false"}
                },
            },
            # --- 2. Restrict to VPC endpoint ---
            {
                "Sid": "DenyNonVPCEndpointAccess",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
                "Condition": {
                    "StringNotEquals": {
                        "aws:sourceVpce": vpc_endpoint_id,
                    }
                },
            },
            # --- 3. Enforce KMS encryption on PutObject ---
            {
                "Sid": "DenyUnencryptedPutObject",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:PutObject",
                "Resource": f"{bucket_arn}/*",
                "Condition": {
                    "StringNotEquals": {
                        "s3:x-amz-server-side-encryption": "aws:kms",
                    }
                },
            },
            # --- 4. Enforce correct KMS key ---
            {
                "Sid": "DenyWrongKMSKey",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:PutObject",
                "Resource": f"{bucket_arn}/*",
                "Condition": {
                    "StringNotEquals": {
                        "s3:x-amz-server-side-encryption-aws-kms-key-id": kms_key_arn,
                    }
                },
            },
        ],
    }
