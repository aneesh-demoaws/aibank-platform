"""
AgentCore Identity configuration for ATM Profitability Optimizer.

Sets up:
- Inbound JWT authorizer for Cognito tokens issued in me-south-1
- Credential providers for cross-region Athena/S3 access in me-south-1

Validates: Requirement 23 (AgentCore Identity)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_REGION = "me-south-1"
AI_REGION = "eu-west-1"

COGNITO_REGION = DATA_REGION


@dataclass(frozen=True)
class JWTAuthorizerConfig:
    """Inbound JWT authorizer that validates Cognito tokens.

    The authorizer verifies tokens issued by the Cognito User Pool in
    me-south-1 and extracts ``cognito:groups`` to determine the user role
    (admin / operator).
    """

    user_pool_id: str = field(
        default_factory=lambda: os.environ.get("ATM_COGNITO_USER_POOL_ID", "")
    )
    app_client_id: str = field(
        default_factory=lambda: os.environ.get("ATM_COGNITO_APP_CLIENT_ID", "")
    )
    region: str = COGNITO_REGION

    @property
    def issuer_url(self) -> str:
        """OIDC issuer URL for the Cognito User Pool."""
        return (
            f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"
        )

    @property
    def jwks_uri(self) -> str:
        """JSON Web Key Set URI for token signature verification."""
        return f"{self.issuer_url}/.well-known/jwks.json"

    def to_dict(self) -> dict:
        """Serialise to a dict suitable for AgentCore API calls."""
        return {
            "type": "JWT",
            "issuer": self.issuer_url,
            "audience": [self.app_client_id],
            "jwksUri": self.jwks_uri,
            "claimsMapping": {
                "cognito:groups": "user_role",
                "sub": "user_id",
                "email": "user_email",
            },
        }


@dataclass(frozen=True)
class CrossRegionCredentialProvider:
    """Credential provider for accessing data-region resources from eu-west-1.

    Uses STS AssumeRole to obtain temporary credentials scoped to
    me-south-1 S3 and Athena resources.
    """

    role_arn: str = field(
        default_factory=lambda: os.environ.get(
            "ATM_CROSS_REGION_ROLE_ARN",
            "",
        )
    )
    external_id: str = field(
        default_factory=lambda: os.environ.get(
            "ATM_CROSS_REGION_EXTERNAL_ID",
            "",
        )
    )
    target_region: str = DATA_REGION
    session_duration_seconds: int = 3600  # 1 hour

    def to_dict(self) -> dict:
        """Serialise to a dict suitable for AgentCore API calls."""
        config: dict = {
            "type": "STS_ASSUME_ROLE",
            "roleArn": self.role_arn,
            "region": self.target_region,
            "sessionDurationSeconds": self.session_duration_seconds,
            "sessionName": "agentcore-atm-optimizer",
        }
        if self.external_id:
            config["externalId"] = self.external_id
        return config


@dataclass(frozen=True)
class IdentityConfig:
    """Top-level AgentCore Identity configuration.

    Combines the JWT authorizer with credential providers needed for
    cross-region data access.
    """

    jwt_authorizer: JWTAuthorizerConfig = field(
        default_factory=JWTAuthorizerConfig
    )
    athena_credentials: CrossRegionCredentialProvider = field(
        default_factory=CrossRegionCredentialProvider
    )
    s3_credentials: CrossRegionCredentialProvider = field(
        default_factory=CrossRegionCredentialProvider
    )

    def to_dict(self) -> dict:
        """Full identity configuration for AgentCore deployment."""
        return {
            "inboundAuthorizer": self.jwt_authorizer.to_dict(),
            "credentialProviders": {
                "athena": self.athena_credentials.to_dict(),
                "s3": self.s3_credentials.to_dict(),
            },
        }
