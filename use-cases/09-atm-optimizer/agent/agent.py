"""
Main Strands agent for the NeoBank ATM Profitability Optimizer.

Creates a role-aware agent backed by Claude 3.5 Sonnet on Amazon Bedrock.
The agent connects to the MCP Server via AgentCore Gateway, which routes
tool calls to the MCP Server in me-south-1. The MCP Server queries Athena
for all data access.

Data flow:
  Strands Agent (eu-west-1) -> AgentCore Gateway -> MCP Server (me-south-1)
  -> AthenaClient -> Athena -> S3

Validates: Requirements 5.1, 5.7, 10.7, 20.2, 20.3, 20.4, 20.9, 21.1
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Optional

from strands import Agent
from strands.models.bedrock import BedrockModel

from agent.auth.role_manager import ROLE_ADMIN, extract_role_from_claims
from agent.auth.tool_filter import filter_tools_for_role
from agent.config import (
    AGENTCORE_RUNTIME_MEMORY_MB,
    AGENTCORE_RUNTIME_TIMEOUT_SECONDS,
    MODEL_ID,
    MODEL_MAX_TOKENS,
    MODEL_REGION,
    MODEL_TEMPERATURE,
)
from agent.system_prompt import build_system_prompt

# Import tool functions for backward compatibility (used by tool_filter and tests)
from agent.tools import (
    calculate_impact_analysis,
    detect_anomalies,
    profitability_ranking,
    query_atm_data,
    query_branch_proximity,
    query_cash_levels,
    query_maintenance_costs,
    query_revenue_data,
    query_competitor_analysis,
    query_coverage_analysis,
    simulate_competitor_scenario,
    recommend_atm_placement,
)

logger = logging.getLogger(__name__)


def _wrap_tool_with_error_handling(tool_fn):
    """Wrap a tool function with user-friendly error handling.

    Catches common exceptions and returns structured error dicts
    instead of letting exceptions propagate to the agent.
    """
    import functools

    @functools.wraps(tool_fn)
    def wrapper(*args, **kwargs):
        try:
            return tool_fn(*args, **kwargs)
        except TimeoutError as e:
            return {"error": f"{tool_fn.__name__} took too long to respond. Please try again."}
        except ConnectionError as e:
            return {"error": f"Unable to reach the data service for {tool_fn.__name__}. Please try again later."}
        except Exception as e:
            return {"error": f"An unexpected error occurred in {tool_fn.__name__}: {str(e)}"}

    return wrapper


# Full tool registry as Python functions (for role filtering and testing)
ALL_TOOLS: list = [
    query_atm_data,
    query_branch_proximity,
    query_revenue_data,
    query_maintenance_costs,
    query_cash_levels,
    calculate_impact_analysis,
    detect_anomalies,
    profitability_ranking,
    query_competitor_analysis,
    query_coverage_analysis,
    simulate_competitor_scenario,
    recommend_atm_placement,
]

# -- MCP Server connection configuration ----------------------------------

MCP_SERVER_ENDPOINT = os.environ.get(
    "ATM_MCP_SERVER_ENDPOINT",
    # Lambda Function URL in me-south-1 with IAM auth (SigV4)
    # Set via CloudFormation output after deploying atm-optimizer-lambda-mcp stack
    "https://n23jzelsiafj2uoivsovaiccdm0umpyi.lambda-url.me-south-1.on.aws/",
)

# -- AgentCore Memory configuration ---------------------------------------

SESSION_MEMORY_TTL_MINUTES = 60

# -- MCP tool definitions for AgentCore Gateway ---------------------------
# These are the tool schemas registered with AgentCore Gateway.
# The Gateway routes calls to the MCP Server in me-south-1 via PrivateLink.

MCP_TOOL_DEFINITIONS = [
    {
        "name": "query_atm_data",
        "description": (
            "Query ATM transaction summary for a specified ATM and date range. "
            "Returns transaction count, total amount, average daily transactions, "
            "and revenue (fee income) in BHD."
        ),
        "parameters": {
            "atm_id": {"type": "string", "description": "ATM identifier, e.g. ATM_SEEF_01"},
            "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
        },
        "access": ["operator", "admin"],
    },
    {
        "name": "query_branch_proximity",
        "description": (
            "Find nearby ATMs and branches within a given radius using haversine distance."
        ),
        "parameters": {
            "atm_id": {"type": "string", "description": "Source ATM identifier"},
            "radius_km": {"type": "number", "description": "Search radius in km (default 5.0)"},
        },
        "access": ["operator", "admin"],
    },
    {
        "name": "query_revenue_data",
        "description": (
            "Query revenue metrics for an ATM with period aggregation (daily/weekly/monthly)."
        ),
        "parameters": {
            "atm_id": {"type": "string", "description": "ATM identifier"},
            "period": {"type": "string", "description": "daily, weekly, or monthly"},
        },
        "access": ["operator", "admin"],
    },
    {
        "name": "query_maintenance_costs",
        "description": "Query maintenance cost history with type breakdown.",
        "parameters": {
            "atm_id": {"type": "string", "description": "ATM identifier"},
            "start_date": {"type": "string", "description": "Start date YYYY-MM-DD"},
            "end_date": {"type": "string", "description": "End date YYYY-MM-DD"},
        },
        "access": ["admin"],
    },
    {
        "name": "query_cash_levels",
        "description": "Query current and forecasted cash levels with 7-day forecast.",
        "parameters": {
            "atm_id": {"type": "string", "description": "ATM identifier"},
        },
        "access": ["admin"],
    },
    {
        "name": "calculate_impact_analysis",
        "description": (
            "Calculate revenue impact and traffic reallocation for ATM downtime. "
            "Uses inverse-distance weighting. Guarantees traffic conservation."
        ),
        "parameters": {
            "atm_id": {"type": "string", "description": "ATM identifier"},
            "downtime_days": {"type": "integer", "description": "Number of downtime days"},
        },
        "access": ["admin"],
    },
    {
        "name": "detect_anomalies",
        "description": (
            "Detect ATMs with transaction volumes deviating >2 standard deviations."
        ),
        "parameters": {
            "atm_id": {"type": "string", "description": "Optional ATM identifier"},
            "period": {"type": "string", "description": "7d, 30d, or 90d"},
        },
        "access": ["admin"],
    },
    {
        "name": "profitability_ranking",
        "description": (
            "Rank ATMs by profitability (net_revenue = revenue - maintenance - cash handling)."
        ),
        "parameters": {
            "top_n": {"type": "integer", "description": "Number of ATMs to return"},
            "sort": {"type": "string", "description": "net_revenue, gross_revenue, or costs"},
        },
        "access": ["admin"],
    },
    {
        "name": "query_competitor_analysis",
        "description": (
            "Calculate Competition Index for NeoBank ATMs based on nearby competitor bank ATMs. "
            "USE THIS for any question about competition index, competitive pressure, or nearby competitors. "
            "Returns competition pressure scores from 0.0 (no competition) to 1.0 (high competition)."
        ),
        "parameters": {
            "atm_id": {"type": "string", "description": "Optional ATM identifier. If omitted, returns scores for all ATMs."},
            "radius_km": {"type": "number", "description": "Search radius in km (default 2.0)"},
        },
        "access": ["operator", "admin"],
    },
    {
        "name": "query_coverage_analysis",
        "description": (
            "Identify coverage gaps, advantages, and market share vs competitor banks by governorate."
        ),
        "parameters": {
            "radius_km": {"type": "number", "description": "Analysis radius in km (default 2.0)"},
        },
        "access": ["operator", "admin"],
    },
    {
        "name": "simulate_competitor_scenario",
        "description": (
            "Simulate impact of a competitor bank opening or closing an ATM near NeoBank locations."
        ),
        "parameters": {
            "scenario_type": {"type": "string", "description": "'add' for new competitor ATM, 'remove' for closure"},
            "latitude": {"type": "number", "description": "GPS latitude (Bahrain: 25.5-26.3)"},
            "longitude": {"type": "number", "description": "GPS longitude (Bahrain: 50.4-50.8)"},
            "bank_name": {"type": "string", "description": "Competitor bank name"},
            "radius_km": {"type": "number", "description": "Impact radius in km (default 2.0)"},
        },
        "access": ["admin"],
    },
    {
        "name": "recommend_atm_placement",
        "description": (
            "Recommend optimal locations for new NeoBank ATMs based on coverage gaps and competitor density."
        ),
        "parameters": {
            "count": {"type": "integer", "description": "Number of recommendations (default 3)"},
            "radius_km": {"type": "number", "description": "Analysis radius in km (default 2.0)"},
        },
        "access": ["admin"],
    },
]


# -- Tool name lists for role filtering -----------------------------------

ALL_TOOL_NAMES = [t["name"] for t in MCP_TOOL_DEFINITIONS]
OPERATOR_TOOL_NAMES = [t["name"] for t in MCP_TOOL_DEFINITIONS if "operator" in t["access"]]
ADMIN_TOOL_NAMES = [t["name"] for t in MCP_TOOL_DEFINITIONS if "admin" in t["access"]]


def get_permitted_tool_names(role: str) -> list[str]:
    """Return tool names permitted for the given role."""
    if role == ROLE_ADMIN:
        return ADMIN_TOOL_NAMES
    return OPERATOR_TOOL_NAMES


def _build_bedrock_model() -> BedrockModel:
    """Instantiate the BedrockModel for Claude 3.5 Sonnet in eu-west-1."""
    return BedrockModel(
        model_id=MODEL_ID,
        region_name=MODEL_REGION,
        max_tokens=MODEL_MAX_TOKENS,
        temperature=MODEL_TEMPERATURE,
    )


def _build_mcp_tool_config(role: str) -> dict:
    """Build MCP tool configuration for AgentCore Gateway.

    Returns the tool config that tells the Strands Agent to route tool
    calls through AgentCore Gateway to the MCP Server in me-south-1.
    """
    permitted = get_permitted_tool_names(role)
    return {
        "mcpServers": {
            "atm-optimizer": {
                "endpoint": MCP_SERVER_ENDPOINT,
                "tools": [
                    t for t in MCP_TOOL_DEFINITIONS
                    if t["name"] in permitted
                ],
            }
        }
    }


# -- Agent factory --------------------------------------------------------

def create_agent(
    role: str = ROLE_ADMIN,
    session_id: Optional[str] = None,
) -> Agent:
    """Create a role-aware Strands Agent that calls tools via MCP protocol.

    The agent connects to the MCP Server through AgentCore Gateway.
    Tool calls are routed: Agent -> Gateway (eu-west-1) -> MCP Server
    (me-south-1) -> Athena -> S3.

    Parameters
    ----------
    role:
        User role from Cognito ("admin" or "operator").
    session_id:
        Optional session identifier for multi-turn conversation.

    Returns
    -------
    Agent
        A configured Strands Agent ready to process queries.
    """
    if session_id is None:
        session_id = str(uuid.uuid4())

    model = _build_bedrock_model()
    system_prompt = build_system_prompt(role)

    # Build MCP tool config — the agent will call tools via MCP protocol
    # through AgentCore Gateway, not by importing Python functions directly
    mcp_config = _build_mcp_tool_config(role)

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
    )

    # Store MCP config and session metadata on the agent for Gateway routing
    agent._mcp_config = mcp_config
    agent._session_id = session_id
    agent._role = role

    logger.info(
        "Created agent: role=%s, session=%s, mcp_tools=%d, endpoint=%s",
        role,
        session_id,
        len(mcp_config["mcpServers"]["atm-optimizer"]["tools"]),
        MCP_SERVER_ENDPOINT,
    )

    return agent


def create_agent_from_claims(
    claims: dict,
    session_id: Optional[str] = None,
) -> Agent:
    """Create an agent using decoded JWT claims to determine the role.

    This is the primary entry point when the request comes through
    AgentCore Identity, which decodes the Cognito JWT and passes the
    claims dict to the agent runtime.

    Parameters
    ----------
    claims:
        Decoded JWT payload from Cognito (must contain cognito:groups).
    session_id:
        Optional session identifier for multi-turn conversation.

    Returns
    -------
    Agent
        A configured Strands Agent scoped to the caller's role.
    """
    role = extract_role_from_claims(claims)
    logger.info("Resolved role=%s from JWT claims", role)
    return create_agent(role=role, session_id=session_id)
