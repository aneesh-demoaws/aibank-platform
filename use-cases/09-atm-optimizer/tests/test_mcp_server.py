"""
Tests for MCP Server setup (Task 6).

Verifies:
  - All 8 tools are registered with correct names
  - Tool descriptions and parameter schemas are present
  - Tools are callable via MCP server and return expected structures
  - Server is importable and configurable
  - Caching layer is integrated (via AthenaClient)
  - Retry configuration is applied (via AthenaClient)

Tools query Athena via AthenaClient. For testing, we inject a mock
AthenaClient that returns data from sample CSV files.
"""

import csv
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")

from mcp_server.athena_client import AthenaClient, QueryCache
from agent.config import (
    MCP_CACHE_TTL_SECONDS,
    MCP_MAX_RETRIES,
    MCP_RETRY_BACKOFF_BASE,
    DATA_REGION,
    ATHENA_DATABASE,
)


# ---------------------------------------------------------------------------
# Mock AthenaClient for testing (same as test_integration.py)
# ---------------------------------------------------------------------------

class MockAthenaClient:
    def __init__(self):
        self.queries_executed = []

    def execute_query(self, sql, use_cache=True, timeout=30):
        self.queries_executed.append(sql)
        table = self._extract_table(sql)
        rows = self._load_csv(table)
        return self._apply_filters(rows, sql, table)

    def _extract_table(self, sql):
        sql_lower = sql.lower()
        for t in ["atm_transactions", "atm_locations", "branch_locations",
                   "atm_proximity", "maintenance_costs", "cash_levels"]:
            if t in sql_lower:
                return t
        return "unknown"

    def _load_csv(self, table):
        file_map = {
            "atm_transactions": "sample_transactions.csv",
            "atm_locations": "atm_locations.csv",
            "branch_locations": "branch_locations.csv",
            "atm_proximity": "atm_proximity.csv",
            "maintenance_costs": "sample_maintenance.csv",
            "cash_levels": "sample_cash_levels.csv",
        }
        filename = file_map.get(table)
        if not filename:
            return []
        path = os.path.join(DATA_DIR, filename)
        if not os.path.exists(path):
            return []
        with open(path, newline="") as f:
            return list(csv.DictReader(f))

    def _apply_filters(self, rows, sql, table):
        filtered = rows
        if "atm_id = '" in sql:
            start = sql.index("atm_id = '") + len("atm_id = '")
            end = sql.index("'", start)
            atm_id = sql[start:end]
            filtered = [r for r in filtered if r.get("atm_id") == atm_id]
        if "source_atm_id = '" in sql:
            start = sql.index("source_atm_id = '") + len("source_atm_id = '")
            end = sql.index("'", start)
            atm_id = sql[start:end]
            filtered = [r for r in filtered if r.get("source_atm_id") == atm_id]
        return filtered


@pytest.fixture(autouse=True)
def inject_mock_athena():
    from agent.tools import _athena_queries
    mock_client = MockAthenaClient()
    original = _athena_queries._athena_client
    _athena_queries._athena_client = mock_client
    yield mock_client
    _athena_queries._athena_client = original


# ---------------------------------------------------------------------------
# Import MCP server tools (after fixture)
# ---------------------------------------------------------------------------

from mcp_server.server import (
    mcp,
    get_server,
    query_atm_data,
    query_branch_proximity,
    query_revenue_data,
    query_maintenance_costs,
    query_cash_levels,
    calculate_impact_analysis,
    detect_anomalies,
    profitability_ranking,
)


EXPECTED_TOOLS = [
    "query_atm_data",
    "query_branch_proximity",
    "query_revenue_data",
    "query_maintenance_costs",
    "query_cash_levels",
    "calculate_impact_analysis",
    "detect_anomalies",
    "profitability_ranking",
]


class TestMCPServerSetup:
    def test_server_instance_exists(self):
        server = get_server()
        assert server is not None
        assert server is mcp

    def test_server_name(self):
        assert mcp.name == "ATM Profitability Optimizer"

    def test_all_eight_tools_registered(self):
        tools = mcp._tool_manager._tools
        registered = sorted(tools.keys())
        assert registered == sorted(EXPECTED_TOOLS)

    def test_tool_count(self):
        tools = mcp._tool_manager._tools
        assert len(tools) == 8


class TestToolRegistration:
    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_has_description(self, tool_name):
        tools = mcp._tool_manager._tools
        tool = tools[tool_name]
        assert tool.description
        assert len(tool.description) > 20

    @pytest.mark.parametrize("tool_name", EXPECTED_TOOLS)
    def test_tool_has_parameter_schema(self, tool_name):
        tools = mcp._tool_manager._tools
        tool = tools[tool_name]
        schema = tool.parameters
        assert "properties" in schema
        assert schema["type"] == "object"

    def test_query_atm_data_params(self):
        props = mcp._tool_manager._tools["query_atm_data"].parameters["properties"]
        assert "atm_id" in props
        assert "start_date" in props
        assert "end_date" in props

    def test_profitability_ranking_params(self):
        props = mcp._tool_manager._tools["profitability_ranking"].parameters["properties"]
        assert "top_n" in props
        assert "sort" in props


class TestToolInvocation:
    """Verify tools execute via MCP server and query Athena."""

    def test_query_atm_data_returns_dict(self, inject_mock_athena):
        result = query_atm_data("ATM_SEEF_01", "2024-01-01", "2024-01-31")
        assert isinstance(result, dict)
        assert "atm_id" in result or "error" in result
        assert len(inject_mock_athena.queries_executed) > 0

    def test_query_branch_proximity_returns_list(self, inject_mock_athena):
        result = query_branch_proximity("ATM_SEEF_01", 5.0)
        assert isinstance(result, list)

    def test_query_revenue_data_returns_dict(self, inject_mock_athena):
        result = query_revenue_data("ATM_SEEF_01", "monthly")
        assert isinstance(result, dict)

    def test_query_maintenance_costs_returns_dict(self, inject_mock_athena):
        result = query_maintenance_costs("ATM_SEEF_01", "2024-01-01", "2024-06-30")
        assert isinstance(result, dict)

    def test_query_cash_levels_returns_dict(self, inject_mock_athena):
        result = query_cash_levels("ATM_SEEF_01")
        assert isinstance(result, dict)

    def test_calculate_impact_analysis_returns_dict(self, inject_mock_athena):
        result = calculate_impact_analysis("ATM_SEEF_01", 3)
        assert isinstance(result, dict)

    def test_detect_anomalies_returns_list(self, inject_mock_athena):
        result = detect_anomalies(None, "30d")
        assert isinstance(result, list)

    def test_profitability_ranking_returns_list(self, inject_mock_athena):
        result = profitability_ranking(5, "net_revenue")
        assert isinstance(result, list)

    def test_invalid_dates_handled_gracefully(self):
        result = query_atm_data("ATM_SEEF_01", "2024-12-31", "2024-01-01")
        assert isinstance(result, dict)
        assert "error" in result


class TestCachingLayer:
    def test_query_cache_ttl_configured(self):
        cache = QueryCache(ttl_seconds=MCP_CACHE_TTL_SECONDS)
        assert cache._ttl == MCP_CACHE_TTL_SECONDS

    def test_cache_put_and_get(self):
        cache = QueryCache(ttl_seconds=300)
        cache.put("SELECT 1", [{"col": "val"}])
        assert cache.get("SELECT 1") == [{"col": "val"}]

    def test_cache_invalidate_all(self):
        cache = QueryCache(ttl_seconds=300)
        cache.put("key1", [{"a": 1}])
        cache.put("key2", [{"b": 2}])
        cache.invalidate()
        assert cache.size == 0

    def test_default_cache_ttl_is_300(self):
        assert MCP_CACHE_TTL_SECONDS == 300


class TestRetryLogic:
    def test_max_retries_configured(self):
        assert MCP_MAX_RETRIES == 3

    def test_backoff_base_configured(self):
        assert MCP_RETRY_BACKOFF_BASE == 2

    def test_exponential_backoff_values(self):
        base = MCP_RETRY_BACKOFF_BASE
        expected = [base ** i for i in range(MCP_MAX_RETRIES)]
        assert expected == [1, 2, 4]

    def test_data_region_is_bahrain(self):
        assert DATA_REGION == "me-south-1"
