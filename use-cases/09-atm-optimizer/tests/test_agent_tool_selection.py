"""
Agent tool selection tests.

Verify the agent selects the correct MCP tools based on query type
and user role. Tests cover the full tool selection pipeline:
  1. Role extraction from JWT claims
  2. Tool filtering by role
  3. Correct tool count per role
  4. Defence-in-depth: role gate blocks unauthorized calls

Validates: Requirements 10.5, 10.6, 10.7, 10.8, 20.2
"""

import os
import sys
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import ADMIN_TOOLS, OPERATOR_TOOLS
from agent.auth.role_manager import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    extract_role_from_claims,
    get_permitted_tools,
    is_tool_permitted,
)
from agent.auth.tool_filter import filter_tools_for_role, role_gate
from agent.agent import ALL_TOOLS, create_agent, create_agent_from_claims


# ---------------------------------------------------------------------------
# Tool selection by query type
# ---------------------------------------------------------------------------

# Mapping of query intents to the tools they should invoke
QUERY_TOOL_MAP = {
    "impact_analysis": {
        "description": "What happens if ATM_SEEF_01 goes down for 3 days?",
        "expected_tools": ["calculate_impact_analysis"],
        "min_role": "admin",
    },
    "anomaly_detection": {
        "description": "Are there any unusual patterns in ATM performance?",
        "expected_tools": ["detect_anomalies"],
        "min_role": "admin",
    },
    "cash_optimization": {
        "description": "What are the cash levels for ATM_SEEF_01?",
        "expected_tools": ["query_cash_levels"],
        "min_role": "admin",
    },
    "profitability_ranking": {
        "description": "Which ATMs are most profitable?",
        "expected_tools": ["profitability_ranking"],
        "min_role": "admin",
    },
    "basic_atm_query": {
        "description": "Show me transaction data for ATM_SEEF_01",
        "expected_tools": ["query_atm_data"],
        "min_role": "operator",
    },
    "proximity_query": {
        "description": "What ATMs are near ATM_MANAMA_01?",
        "expected_tools": ["query_branch_proximity"],
        "min_role": "operator",
    },
    "revenue_query": {
        "description": "What is the revenue for ATM_SEEF_01?",
        "expected_tools": ["query_revenue_data"],
        "min_role": "operator",
    },
    "maintenance_query": {
        "description": "Show maintenance costs for ATM_SEEF_01",
        "expected_tools": ["query_maintenance_costs"],
        "min_role": "admin",
    },
}


class TestToolSelectionByRole:
    """Verify correct tools are available for each role."""

    def test_admin_has_all_eight_tools(self):
        permitted = get_permitted_tools(ROLE_ADMIN)
        assert len(permitted) == 8
        assert set(permitted) == set(ADMIN_TOOLS)

    def test_operator_has_three_tools(self):
        permitted = get_permitted_tools(ROLE_OPERATOR)
        assert len(permitted) == 3
        assert set(permitted) == set(OPERATOR_TOOLS)

    def test_operator_tools_are_subset_of_admin(self):
        assert set(OPERATOR_TOOLS).issubset(set(ADMIN_TOOLS))

    @pytest.mark.parametrize("query_type,spec", QUERY_TOOL_MAP.items())
    def test_admin_can_access_all_query_tools(self, query_type, spec):
        """Admin should have access to every tool needed for any query type."""
        for tool_name in spec["expected_tools"]:
            assert is_tool_permitted(ROLE_ADMIN, tool_name), (
                f"Admin should access {tool_name} for {query_type}"
            )

    @pytest.mark.parametrize("query_type,spec", QUERY_TOOL_MAP.items())
    def test_operator_access_matches_min_role(self, query_type, spec):
        """Operator can only access tools where min_role is 'operator'."""
        for tool_name in spec["expected_tools"]:
            if spec["min_role"] == "operator":
                assert is_tool_permitted(ROLE_OPERATOR, tool_name), (
                    f"Operator should access {tool_name} for {query_type}"
                )
            else:
                assert not is_tool_permitted(ROLE_OPERATOR, tool_name), (
                    f"Operator should NOT access {tool_name} for {query_type}"
                )


class TestToolFilteringPipeline:
    """Verify filter_tools_for_role returns correctly gated tools."""

    def test_admin_filter_returns_all_tools(self):
        filtered = filter_tools_for_role(ALL_TOOLS, ROLE_ADMIN)
        assert len(filtered) == len(ADMIN_TOOLS)
        names = {f.__name__ for f in filtered}
        assert names == set(ADMIN_TOOLS)

    def test_operator_filter_returns_basic_tools(self):
        filtered = filter_tools_for_role(ALL_TOOLS, ROLE_OPERATOR)
        assert len(filtered) == len(OPERATOR_TOOLS)
        names = {f.__name__ for f in filtered}
        assert names == set(OPERATOR_TOOLS)

    def test_unknown_role_filter_returns_empty(self):
        filtered = filter_tools_for_role(ALL_TOOLS, "unknown")
        assert len(filtered) == 0

    def test_filtered_operator_tools_are_role_gated(self):
        """Even filtered tools have role gates for defence in depth."""
        filtered = filter_tools_for_role(ALL_TOOLS, ROLE_OPERATOR)
        for tool in filtered:
            # Each tool should be callable (role gate wraps it)
            assert callable(tool)
            assert tool.__name__ in OPERATOR_TOOLS


class TestRoleGateDefenceInDepth:
    """Verify role_gate blocks unauthorized tool calls at execution time."""

    @staticmethod
    def _make_tool(name):
        def tool(**kwargs):
            return {"result": f"{name} executed", **kwargs}
        tool.__name__ = name
        return tool

    def test_operator_gate_blocks_admin_tool(self):
        for admin_tool in ["query_maintenance_costs", "query_cash_levels",
                           "calculate_impact_analysis", "detect_anomalies",
                           "profitability_ranking"]:
            tool = self._make_tool(admin_tool)
            gated = role_gate(ROLE_OPERATOR)(tool)
            result = gated()
            assert "error" in result
            assert "Access denied" in result["error"]

    def test_operator_gate_allows_operator_tool(self):
        for op_tool in OPERATOR_TOOLS:
            tool = self._make_tool(op_tool)
            gated = role_gate(ROLE_OPERATOR)(tool)
            result = gated()
            assert "result" in result
            assert "executed" in result["result"]

    def test_admin_gate_allows_all_tools(self):
        for tool_name in ADMIN_TOOLS:
            tool = self._make_tool(tool_name)
            gated = role_gate(ROLE_ADMIN)(tool)
            result = gated()
            assert "result" in result


class TestJWTClaimsToToolAccess:
    """End-to-end: JWT claims → role → tool access decisions."""

    def test_admin_claims_grant_full_access(self):
        claims = {"cognito:groups": ["admin"]}
        role = extract_role_from_claims(claims)
        permitted = get_permitted_tools(role)
        assert len(permitted) == 8

    def test_operator_claims_grant_basic_access(self):
        claims = {"cognito:groups": ["operator"]}
        role = extract_role_from_claims(claims)
        permitted = get_permitted_tools(role)
        assert len(permitted) == 3

    def test_dual_group_membership_prefers_admin(self):
        claims = {"cognito:groups": ["operator", "admin"]}
        role = extract_role_from_claims(claims)
        assert role == ROLE_ADMIN
        permitted = get_permitted_tools(role)
        assert len(permitted) == 8

    def test_empty_groups_grant_no_access(self):
        claims = {"cognito:groups": []}
        role = extract_role_from_claims(claims)
        assert role == "unknown"
        permitted = get_permitted_tools(role)
        assert len(permitted) == 0

    def test_missing_groups_claim_grant_no_access(self):
        claims = {}
        role = extract_role_from_claims(claims)
        assert role == "unknown"
        permitted = get_permitted_tools(role)
        assert len(permitted) == 0


class TestAgentToolWiring:
    """Verify create_agent wires the correct MCP tool config per role."""

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_admin_agent_wired_with_8_tools(self, mock_agent, mock_model):
        agent = create_agent(role=ROLE_ADMIN)
        mcp_tools = agent._mcp_config["mcpServers"]["atm-optimizer"]["tools"]
        assert len(mcp_tools) == 8

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_operator_agent_wired_with_3_tools(self, mock_agent, mock_model):
        agent = create_agent(role=ROLE_OPERATOR)
        mcp_tools = agent._mcp_config["mcpServers"]["atm-optimizer"]["tools"]
        assert len(mcp_tools) == 3

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_agent_receives_system_prompt(self, mock_agent, mock_model):
        create_agent(role=ROLE_ADMIN)
        prompt = mock_agent.call_args.kwargs.get("system_prompt", "")
        assert "NeoBank ATM Profitability Optimizer" in prompt
        assert "Admin role" in prompt

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_operator_agent_prompt_mentions_restrictions(self, mock_agent, mock_model):
        create_agent(role=ROLE_OPERATOR)
        prompt = mock_agent.call_args.kwargs.get("system_prompt", "")
        assert "Operator role" in prompt
        assert "do NOT have access" in prompt
