"""
AgentCore Gateway configuration for ATM Profitability Optimizer.

Registers all 8 MCP tools and configures routing from the AgentCore
Gateway in eu-west-1 to the MCP server running in me-south-1 via
PrivateLink.

Validates: Requirement 19 (AgentCore Gateway)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DATA_REGION = "me-south-1"
AI_REGION = "eu-west-1"

# Role constants matching Cognito group names
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"


# ---------------------------------------------------------------------------
# MCP Tool definitions
# ---------------------------------------------------------------------------

MCP_TOOLS: list[dict] = [
    {
        "name": "query_atm_data",
        "description": (
            "Query ATM transaction summary for a specified ATM and date range. "
            "Returns transaction_count, total_amount, avg_daily_txns, revenue."
        ),
        "allowed_roles": [ROLE_OPERATOR, ROLE_ADMIN],
    },
    {
        "name": "query_branch_proximity",
        "description": (
            "Find nearby ATMs and branches within a given radius. "
            "Returns list of atm_id, name, distance_km, capacity_utilization."
        ),
        "allowed_roles": [ROLE_OPERATOR, ROLE_ADMIN],
    },
    {
        "name": "query_revenue_data",
        "description": (
            "Query revenue metrics for an ATM. "
            "Returns gross_revenue, net_revenue, fee_income, trend."
        ),
        "allowed_roles": [ROLE_OPERATOR, ROLE_ADMIN],
    },
    {
        "name": "query_maintenance_costs",
        "description": (
            "Query maintenance cost history for an ATM. "
            "Returns total_cost, breakdown_by_type, downtime_hours."
        ),
        "allowed_roles": [ROLE_ADMIN],
    },
    {
        "name": "query_cash_levels",
        "description": (
            "Query current and forecasted cash levels for an ATM. "
            "Returns current_balance, forecast_7day, replenishment_recommendation."
        ),
        "allowed_roles": [ROLE_ADMIN],
    },
    {
        "name": "calculate_impact_analysis",
        "description": (
            "Calculate revenue impact and traffic reallocation for ATM downtime. "
            "Returns revenue_loss, traffic_redistribution, recommendations."
        ),
        "allowed_roles": [ROLE_ADMIN],
    },
    {
        "name": "detect_anomalies",
        "description": (
            "Detect anomalies in ATM performance over a given period. "
            "Returns list of atm_id, anomaly_type, deviation, impact."
        ),
        "allowed_roles": [ROLE_ADMIN],
    },
    {
        "name": "profitability_ranking",
        "description": (
            "Rank ATMs by profitability metrics. "
            "Returns list of atm_id, gross_revenue, costs, net_revenue, rank."
        ),
        "allowed_roles": [ROLE_ADMIN],
    },
]


@dataclass(frozen=True)
class PrivateLinkRoute:
    """PrivateLink routing configuration from eu-west-1 to me-south-1."""

    endpoint_url: str = field(
        default_factory=lambda: os.environ.get(
            "ATM_MCP_PRIVATELINK_ENDPOINT", ""
        )
    )
    vpc_endpoint_id: str = field(
        default_factory=lambda: os.environ.get(
            "ATM_MCP_VPC_ENDPOINT_ID", ""
        )
    )
    target_region: str = DATA_REGION
    port: int = 443

    def to_dict(self) -> dict:
        return {
            "endpointUrl": self.endpoint_url,
            "vpcEndpointId": self.vpc_endpoint_id,
            "targetRegion": self.target_region,
            "port": self.port,
            "protocol": "HTTPS",
        }


@dataclass(frozen=True)
class GatewayConfig:
    """Top-level AgentCore Gateway configuration.

    Registers MCP tools and sets up routing to the MCP server in
    me-south-1 via PrivateLink.
    """

    agent_name: str = "neobank-atm-profitability-optimizer"
    mcp_tools: list[dict] = field(default_factory=lambda: list(MCP_TOOLS))
    route: PrivateLinkRoute = field(default_factory=PrivateLinkRoute)
    cache_ttl_seconds: int = 300  # 5-minute cache for MCP responses

    def get_tools_for_role(self, role: str) -> list[dict]:
        """Return only the MCP tools permitted for *role*."""
        return [
            tool for tool in self.mcp_tools
            if role in tool["allowed_roles"]
        ]

    def to_dict(self) -> dict:
        """Full gateway configuration for AgentCore deployment."""
        return {
            "agentName": self.agent_name,
            "mcpTools": [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "allowedRoles": t["allowed_roles"],
                }
                for t in self.mcp_tools
            ],
            "routing": {
                "mcpServer": self.route.to_dict(),
                "cacheTtlSeconds": self.cache_ttl_seconds,
            },
        }
