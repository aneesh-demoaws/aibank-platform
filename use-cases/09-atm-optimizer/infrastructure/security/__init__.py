"""
Security configuration modules for ATM Profitability Optimizer.

Provides Python-dict representations of:
- S3 bucket policies (block public access, enforce KMS, VPC-only)
- IAM least-privilege roles (EC2, MCP server, AgentCore agent)
- VPC Flow Logs configuration
"""

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
