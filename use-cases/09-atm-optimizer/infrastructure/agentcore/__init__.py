"""
AgentCore service configurations for ATM Profitability Optimizer.

All AgentCore services run in eu-west-1 (Ireland) while data services
(S3, Athena, Cognito) remain in me-south-1 (Bahrain).

Modules
-------
identity_config
    JWT authorizer and cross-region credential providers.
gateway_config
    MCP tool registration and routing to me-south-1.
memory_config
    Session and long-term memory configuration.
observability_config
    OpenTelemetry tracing and CloudWatch alarms.
"""

from infrastructure.agentcore.identity_config import IdentityConfig
from infrastructure.agentcore.gateway_config import GatewayConfig
from infrastructure.agentcore.memory_config import MemoryConfig
from infrastructure.agentcore.observability_config import ObservabilityConfig

__all__ = [
    "IdentityConfig",
    "GatewayConfig",
    "MemoryConfig",
    "ObservabilityConfig",
]
