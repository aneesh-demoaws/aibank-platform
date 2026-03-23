"""
Property-based tests for RBAC tool access enforcement.

Uses Hypothesis to verify:
  - Property 14: RBAC Tool Access Enforcement
"""

import os
import sys

import pytest
from hypothesis import given, settings
from hypothesis.strategies import sampled_from

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import OPERATOR_TOOLS, ADMIN_TOOLS


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OPERATOR_REQUIRED = {"query_competitor_analysis", "query_coverage_analysis"}
OPERATOR_FORBIDDEN = {"simulate_competitor_scenario", "recommend_atm_placement"}
ADMIN_REQUIRED = {
    "query_competitor_analysis", "query_coverage_analysis",
    "simulate_competitor_scenario", "recommend_atm_placement",
}


# ---------------------------------------------------------------------------
# Property 14: RBAC Tool Access Enforcement
# Feature: competitor-analysis, Property 14: RBAC Tool Access Enforcement
# ---------------------------------------------------------------------------

class TestRBACToolAccessEnforcement:
    """
    Feature: competitor-analysis, Property 14: RBAC Tool Access Enforcement

    Operator tools must include query_competitor_analysis and
    query_coverage_analysis but NOT simulate_competitor_scenario or
    recommend_atm_placement. Admin tools must include all four.
    """

    @given(tool=sampled_from(sorted(OPERATOR_REQUIRED)))
    @settings(max_examples=100)
    def test_operator_has_required_tools(self, tool):
        """
        Feature: competitor-analysis, Property 14: RBAC Tool Access Enforcement

        Operator role must include the required competitor analysis tools.
        """
        assert tool in OPERATOR_TOOLS, (
            f"Operator tools missing required tool: {tool}"
        )

    @given(tool=sampled_from(sorted(OPERATOR_FORBIDDEN)))
    @settings(max_examples=100)
    def test_operator_lacks_admin_only_tools(self, tool):
        """
        Feature: competitor-analysis, Property 14: RBAC Tool Access Enforcement

        Operator role must NOT include admin-only competitor tools.
        """
        assert tool not in OPERATOR_TOOLS, (
            f"Operator tools should not include admin-only tool: {tool}"
        )

    @given(tool=sampled_from(sorted(ADMIN_REQUIRED)))
    @settings(max_examples=100)
    def test_admin_has_all_competitor_tools(self, tool):
        """
        Feature: competitor-analysis, Property 14: RBAC Tool Access Enforcement

        Admin role must include all competitor analysis tools.
        """
        assert tool in ADMIN_TOOLS, (
            f"Admin tools missing required tool: {tool}"
        )

    def test_operator_tools_complete_check(self):
        """
        Feature: competitor-analysis, Property 14: RBAC Tool Access Enforcement

        Verify the complete set of operator tool constraints.
        """
        op_set = set(OPERATOR_TOOLS)
        assert OPERATOR_REQUIRED.issubset(op_set), (
            f"Operator missing: {OPERATOR_REQUIRED - op_set}"
        )
        assert OPERATOR_FORBIDDEN.isdisjoint(op_set), (
            f"Operator has forbidden tools: {OPERATOR_FORBIDDEN & op_set}"
        )

    def test_admin_tools_complete_check(self):
        """
        Feature: competitor-analysis, Property 14: RBAC Tool Access Enforcement

        Verify the complete set of admin tool constraints.
        """
        admin_set = set(ADMIN_TOOLS)
        assert ADMIN_REQUIRED.issubset(admin_set), (
            f"Admin missing: {ADMIN_REQUIRED - admin_set}"
        )
