"""
Tests for Role-Based Access Control.

Includes property-based tests (Hypothesis) verifying Property 3:
Role Authorization Enforcement — an Operator user must never be able
to execute Admin-only MCP tools.

**Validates: Requirements 10.5, 10.6, 10.7, 10.8**
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.auth.role_manager import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    extract_role_from_claims,
    get_access_denied_response,
    get_permitted_tools,
    is_tool_permitted,
)
from agent.auth.tool_filter import filter_tools_for_role, role_gate
from agent.config import ADMIN_TOOLS, OPERATOR_TOOLS


# ── Strategies ────────────────────────────────────────────────────────────

# All known tool names from the config
all_tool_names = st.sampled_from(ADMIN_TOOLS)

# Random tool names that may or may not exist
random_tool_names = st.text(
    alphabet=st.characters(whitelist_categories=("L", "Nd", "Pc")),
    min_size=1,
    max_size=40,
)

# Any tool name: either a real one or a random string
any_tool_name = st.one_of(all_tool_names, random_tool_names)

# Admin-only tools (those in ADMIN_TOOLS but NOT in OPERATOR_TOOLS)
admin_only_tools = st.sampled_from(
    [t for t in ADMIN_TOOLS if t not in OPERATOR_TOOLS]
)

roles = st.sampled_from([ROLE_ADMIN, ROLE_OPERATOR])


# ── Property-Based Tests (Property 3) ────────────────────────────────────


class TestProperty3RoleAuthorisationEnforcement:
    """**Validates: Requirements 10.7, 10.8**

    Property 3: An Operator user must never be able to execute Admin-only
    MCP tools.  For any query from an Operator, the set of tools invoked
    must be a subset of {query_atm_data, query_branch_proximity,
    query_revenue_data}.
    """

    @given(tool_name=admin_only_tools)
    @settings(max_examples=50)
    def test_operator_denied_admin_tools(self, tool_name: str) -> None:
        """**Validates: Requirements 10.7**

        For ANY admin-only tool, is_tool_permitted must return False for
        the operator role.
        """
        assert not is_tool_permitted(ROLE_OPERATOR, tool_name), (
            f"Operator should NOT have access to admin-only tool '{tool_name}'"
        )

    @given(tool_name=any_tool_name)
    @settings(max_examples=100)
    def test_admin_permits_all_known_tools(self, tool_name: str) -> None:
        """**Validates: Requirements 10.5**

        For the admin role, every tool in ADMIN_TOOLS must be permitted.
        Unknown tools should be denied.
        """
        expected = tool_name in ADMIN_TOOLS
        assert is_tool_permitted(ROLE_ADMIN, tool_name) == expected

    @given(tool_name=any_tool_name)
    @settings(max_examples=100)
    def test_operator_permits_only_operator_tools(self, tool_name: str) -> None:
        """**Validates: Requirements 10.6**

        For the operator role, ONLY tools in OPERATOR_TOOLS are permitted.
        """
        expected = tool_name in OPERATOR_TOOLS
        assert is_tool_permitted(ROLE_OPERATOR, tool_name) == expected

    @given(tool_name=admin_only_tools)
    @settings(max_examples=50)
    def test_operator_permitted_set_excludes_admin_tools(
        self, tool_name: str
    ) -> None:
        """**Validates: Requirements 10.7**

        The permitted tool list for operator must never contain any
        admin-only tool.
        """
        permitted = get_permitted_tools(ROLE_OPERATOR)
        assert tool_name not in permitted

    @given(tool_name=all_tool_names)
    @settings(max_examples=50)
    def test_admin_permitted_set_contains_all_tools(
        self, tool_name: str
    ) -> None:
        """**Validates: Requirements 10.5**

        The permitted tool list for admin must contain every known tool.
        """
        permitted = get_permitted_tools(ROLE_ADMIN)
        assert tool_name in permitted

    @given(tool_name=random_tool_names)
    @settings(max_examples=50)
    def test_unknown_role_denies_everything(self, tool_name: str) -> None:
        """**Validates: Requirements 10.7**

        An unrecognised role must have zero permissions.
        """
        assert not is_tool_permitted("unknown", tool_name)
        assert get_permitted_tools("unknown") == []


# ── Unit Tests ────────────────────────────────────────────────────────────


class TestRoleManager:
    """Unit tests for role_manager functions."""

    def test_operator_tools_are_subset_of_admin(self) -> None:
        assert set(OPERATOR_TOOLS).issubset(set(ADMIN_TOOLS))

    def test_get_permitted_tools_admin(self) -> None:
        tools = get_permitted_tools(ROLE_ADMIN)
        assert tools == ADMIN_TOOLS
        assert len(tools) == 8

    def test_get_permitted_tools_operator(self) -> None:
        tools = get_permitted_tools(ROLE_OPERATOR)
        assert tools == OPERATOR_TOOLS
        assert len(tools) == 3

    def test_get_permitted_tools_unknown_role(self) -> None:
        assert get_permitted_tools("manager") == []

    def test_get_permitted_tools_returns_copy(self) -> None:
        """Mutating the returned list must not affect the source."""
        tools = get_permitted_tools(ROLE_ADMIN)
        tools.clear()
        assert len(get_permitted_tools(ROLE_ADMIN)) == 8

    def test_is_tool_permitted_operator_allowed(self) -> None:
        for tool in OPERATOR_TOOLS:
            assert is_tool_permitted(ROLE_OPERATOR, tool)

    def test_is_tool_permitted_operator_denied(self) -> None:
        admin_only = set(ADMIN_TOOLS) - set(OPERATOR_TOOLS)
        for tool in admin_only:
            assert not is_tool_permitted(ROLE_OPERATOR, tool)

    def test_access_denied_response_contains_tool_name(self) -> None:
        msg = get_access_denied_response("detect_anomalies")
        assert "detect_anomalies" in msg
        assert "Admin" in msg

    def test_extract_role_admin(self) -> None:
        claims = {"cognito:groups": ["admin"]}
        assert extract_role_from_claims(claims) == "admin"

    def test_extract_role_operator(self) -> None:
        claims = {"cognito:groups": ["operator"]}
        assert extract_role_from_claims(claims) == "operator"

    def test_extract_role_both_groups_prefers_admin(self) -> None:
        claims = {"cognito:groups": ["operator", "admin"]}
        assert extract_role_from_claims(claims) == "admin"

    def test_extract_role_no_groups(self) -> None:
        assert extract_role_from_claims({}) == "unknown"

    def test_extract_role_empty_groups(self) -> None:
        claims = {"cognito:groups": []}
        assert extract_role_from_claims(claims) == "unknown"


class TestToolFilter:
    """Unit tests for tool_filter middleware."""

    @staticmethod
    def _make_tool(name: str):
        """Create a dummy callable with the given __name__."""
        def tool(**kwargs):
            return {"result": f"{name} executed"}
        tool.__name__ = name
        return tool

    def test_filter_tools_for_admin_returns_all(self) -> None:
        tools = [self._make_tool(n) for n in ADMIN_TOOLS]
        filtered = filter_tools_for_role(tools, ROLE_ADMIN)
        assert len(filtered) == len(ADMIN_TOOLS)

    def test_filter_tools_for_operator_returns_subset(self) -> None:
        tools = [self._make_tool(n) for n in ADMIN_TOOLS]
        filtered = filter_tools_for_role(tools, ROLE_OPERATOR)
        assert len(filtered) == len(OPERATOR_TOOLS)
        names = {f.__name__ for f in filtered}
        assert names == set(OPERATOR_TOOLS)

    def test_role_gate_blocks_operator_on_admin_tool(self) -> None:
        tool = self._make_tool("detect_anomalies")
        gated = role_gate(ROLE_OPERATOR)(tool)
        result = gated()
        assert "error" in result
        assert "Access denied" in result["error"]

    def test_role_gate_allows_operator_on_operator_tool(self) -> None:
        tool = self._make_tool("query_atm_data")
        gated = role_gate(ROLE_OPERATOR)(tool)
        result = gated()
        assert result == {"result": "query_atm_data executed"}

    def test_role_gate_allows_admin_on_any_tool(self) -> None:
        for name in ADMIN_TOOLS:
            tool = self._make_tool(name)
            gated = role_gate(ROLE_ADMIN)(tool)
            result = gated()
            assert result == {"result": f"{name} executed"}
