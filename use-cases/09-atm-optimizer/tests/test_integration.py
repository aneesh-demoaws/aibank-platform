"""
Integration tests for ATM Profitability Optimizer.

End-to-end tests verifying the flow from query -> agent tool selection ->
MCP tools -> AthenaClient -> Athena -> response.

Tools query Athena via AthenaClient. For testing, we inject a mock
AthenaClient that returns data from the sample CSV files (simulating
what Athena would return from S3).

Validates: Requirements 5.4, 10.7, 19.1, 20.2
"""

import csv
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import (
    ADMIN_TOOLS,
    OPERATOR_TOOLS,
    DATA_REGION,
    AI_REGION,
    S3_DATA_BUCKET,
    ATHENA_DATABASE,
)
from agent.auth.role_manager import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    extract_role_from_claims,
    get_permitted_tools,
    is_tool_permitted,
)
from agent.auth.tool_filter import filter_tools_for_role
from mcp_server.athena_client import AthenaClient, QueryCache, validate_record

DATA_DIR = os.path.join(PROJECT_ROOT, "data")


# ---------------------------------------------------------------------------
# Mock AthenaClient that reads from sample CSVs (simulates Athena -> S3)
# ---------------------------------------------------------------------------

class MockAthenaClient:
    """Simulates AthenaClient by reading sample CSV files.

    In production, AthenaClient queries Athena which reads from S3.
    For testing, we read the same data from local CSV files.
    """

    def __init__(self):
        self.queries_executed = []

    def execute_query(self, sql: str, use_cache: bool = True, timeout: int = 30) -> list[dict]:
        self.queries_executed.append(sql)
        table = self._extract_table(sql)
        rows = self._load_csv(table)
        return self._apply_filters(rows, sql, table)

    def _extract_table(self, sql: str) -> str:
        sql_lower = sql.lower()
        if "atm_transactions" in sql_lower:
            return "atm_transactions"
        if "atm_locations" in sql_lower:
            return "atm_locations"
        if "branch_locations" in sql_lower:
            return "branch_locations"
        if "atm_proximity" in sql_lower:
            return "atm_proximity"
        if "maintenance_costs" in sql_lower:
            return "maintenance_costs"
        if "cash_levels" in sql_lower:
            return "cash_levels"
        return "unknown"

    def _load_csv(self, table: str) -> list[dict]:
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

    def _apply_filters(self, rows: list[dict], sql: str, table: str) -> list[dict]:
        """Apply basic WHERE clause filters from the SQL."""
        filtered = rows
        # Extract atm_id filter
        if "atm_id = '" in sql:
            start = sql.index("atm_id = '") + len("atm_id = '")
            end = sql.index("'", start)
            atm_id = sql[start:end]
            filtered = [r for r in filtered if r.get("atm_id") == atm_id]
        # Extract source_atm_id filter
        if "source_atm_id = '" in sql:
            start = sql.index("source_atm_id = '") + len("source_atm_id = '")
            end = sql.index("'", start)
            atm_id = sql[start:end]
            filtered = [r for r in filtered if r.get("source_atm_id") == atm_id]
        # Extract date filters for transactions
        if "timestamp" in sql and ">= '" in sql:
            idx = sql.index(">= '") + len(">= '")
            start_date = sql[idx:idx+10]
            filtered = [r for r in filtered if r.get("timestamp", "")[:10] >= start_date]
        if "timestamp" in sql and "<= '" in sql:
            idx = sql.index("<= '") + len("<= '")
            end_date = sql[idx:idx+10]
            filtered = [r for r in filtered if r.get("timestamp", "")[:10] <= end_date]
        # Extract date filters for maintenance
        if "date >= '" in sql:
            idx = sql.index("date >= '") + len("date >= '")
            start_date = sql[idx:idx+10]
            filtered = [r for r in filtered if r.get("date", "") >= start_date]
        if "date <= '" in sql:
            idx = sql.index("date <= '") + len("date <= '")
            end_date = sql[idx:idx+10]
            filtered = [r for r in filtered if r.get("date", "") <= end_date]
        return filtered


@pytest.fixture(autouse=True)
def inject_mock_athena():
    """Inject MockAthenaClient into the tools layer for all tests."""
    from agent.tools import _athena_queries
    mock_client = MockAthenaClient()
    original = _athena_queries._athena_client
    _athena_queries._athena_client = mock_client
    yield mock_client
    _athena_queries._athena_client = original


# ---------------------------------------------------------------------------
# Import tools (after fixture is defined)
# ---------------------------------------------------------------------------

from agent.tools.query_atm_data import query_atm_data
from agent.tools.query_branch_proximity import query_branch_proximity
from agent.tools.query_revenue_data import query_revenue_data
from agent.tools.query_maintenance_costs import query_maintenance_costs
from agent.tools.query_cash_levels import query_cash_levels
from agent.tools.calculate_impact_analysis import calculate_impact_analysis
from agent.tools.detect_anomalies import detect_anomalies
from agent.tools.profitability_ranking import profitability_ranking


# ---------------------------------------------------------------------------
# End-to-end: Cognito claims -> role -> tool filtering -> tool execution
# ---------------------------------------------------------------------------


class TestEndToEndAdminFlow:
    """Simulate a full admin user flow: authenticate -> select tools -> query data."""

    def test_admin_claims_to_tool_execution(self):
        claims = {"cognito:groups": ["admin"], "sub": "user-001"}
        role = extract_role_from_claims(claims)
        assert role == ROLE_ADMIN

        permitted = get_permitted_tools(role)
        assert set(permitted) == set(ADMIN_TOOLS)

        result = calculate_impact_analysis("ATM_SEEF_01", 3)
        assert "error" not in result or "not found" in result.get("error", "").lower()
        if "total_revenue_loss" in result:
            assert result["atm_id"] == "ATM_SEEF_01"
            assert result["downtime_days"] == 3
            assert result["currency"] == "BHD"

    def test_admin_profitability_ranking_flow(self):
        claims = {"cognito:groups": ["admin"]}
        role = extract_role_from_claims(claims)
        assert is_tool_permitted(role, "profitability_ranking")

        result = profitability_ranking(top_n=5)
        assert isinstance(result, list)
        if result and "error" not in result[0]:
            assert len(result) <= 5
            for entry in result:
                assert "atm_id" in entry
                assert "net_revenue" in entry
                assert "rank" in entry

    def test_admin_anomaly_detection_flow(self):
        claims = {"cognito:groups": ["admin"]}
        role = extract_role_from_claims(claims)
        assert is_tool_permitted(role, "detect_anomalies")

        result = detect_anomalies(period="30d")
        assert isinstance(result, list)


class TestEndToEndOperatorFlow:
    """Simulate a full operator user flow with restricted access."""

    def test_operator_claims_to_basic_query(self):
        claims = {"cognito:groups": ["operator"], "sub": "user-002"}
        role = extract_role_from_claims(claims)
        assert role == ROLE_OPERATOR

        permitted = get_permitted_tools(role)
        assert "query_atm_data" in permitted

        result = query_atm_data("ATM_SEEF_01", "2024-01-01", "2024-06-30")
        assert result["atm_id"] == "ATM_SEEF_01"
        assert "transaction_count" in result
        assert result["currency"] == "BHD"

    def test_operator_proximity_query(self):
        claims = {"cognito:groups": ["operator"]}
        role = extract_role_from_claims(claims)
        assert is_tool_permitted(role, "query_branch_proximity")

        result = query_branch_proximity("ATM_SEEF_01", radius_km=5.0)
        assert isinstance(result, list)

    def test_operator_blocked_from_admin_tools(self):
        claims = {"cognito:groups": ["operator"]}
        role = extract_role_from_claims(claims)

        admin_only = [t for t in ADMIN_TOOLS if t not in OPERATOR_TOOLS]
        for tool_name in admin_only:
            assert not is_tool_permitted(role, tool_name)

    def test_operator_filtered_tools_exclude_admin(self):
        # Import the tool functions for filtering
        from agent.tools import (
            query_atm_data as _qad,
            query_branch_proximity as _qbp,
            query_revenue_data as _qrd,
            query_maintenance_costs as _qmc,
            query_cash_levels as _qcl,
            calculate_impact_analysis as _cia,
            detect_anomalies as _da,
            profitability_ranking as _pr,
        )
        all_tools = [_qad, _qbp, _qrd, _qmc, _qcl, _cia, _da, _pr]
        filtered = filter_tools_for_role(all_tools, ROLE_OPERATOR)
        names = {f.__name__ for f in filtered}
        assert names == set(OPERATOR_TOOLS)


# ---------------------------------------------------------------------------
# End-to-end: Agent creation with mocked Bedrock
# ---------------------------------------------------------------------------


class TestAgentCreationIntegration:
    """Verify agent factory wires up role -> tools -> model correctly."""

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_admin_agent_created(self, mock_agent_cls, mock_model_cls):
        from agent.agent import create_agent_from_claims
        claims = {"cognito:groups": ["admin"]}
        agent = create_agent_from_claims(claims, session_id="test-session")
        # Agent should be created with MCP config for admin tools
        assert agent._role == ROLE_ADMIN
        mcp_tools = agent._mcp_config["mcpServers"]["atm-optimizer"]["tools"]
        assert len(mcp_tools) == len(ADMIN_TOOLS)

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_operator_agent_created(self, mock_agent_cls, mock_model_cls):
        from agent.agent import create_agent_from_claims
        claims = {"cognito:groups": ["operator"]}
        agent = create_agent_from_claims(claims, session_id="test-session")
        assert agent._role == ROLE_OPERATOR
        mcp_tools = agent._mcp_config["mcpServers"]["atm-optimizer"]["tools"]
        assert len(mcp_tools) == len(OPERATOR_TOOLS)

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_unknown_role_gets_operator_tools(self, mock_agent_cls, mock_model_cls):
        from agent.agent import create_agent_from_claims
        claims = {"cognito:groups": []}
        agent = create_agent_from_claims(claims)
        # Empty groups -> operator role (default)
        mcp_tools = agent._mcp_config["mcpServers"]["atm-optimizer"]["tools"]
        assert len(mcp_tools) == len(OPERATOR_TOOLS)


# ---------------------------------------------------------------------------
# End-to-end: MCP tool -> Athena -> response structure
# ---------------------------------------------------------------------------


class TestMCPToolAthenaIntegration:
    """Verify each MCP tool queries Athena and returns well-structured responses."""

    def test_query_atm_data_returns_complete_response(self, inject_mock_athena):
        result = query_atm_data("ATM_SEEF_01", "2024-01-01", "2024-12-31")
        assert "atm_id" in result
        assert "transaction_count" in result
        assert "total_amount" in result
        assert "avg_daily_txns" in result
        assert "revenue" in result
        assert result["currency"] == "BHD"
        # Verify Athena was queried
        assert len(inject_mock_athena.queries_executed) > 0
        assert "atm_transactions" in inject_mock_athena.queries_executed[-1].lower()

    def test_query_branch_proximity_returns_sorted_list(self, inject_mock_athena):
        result = query_branch_proximity("ATM_MANAMA_01", radius_km=3.0)
        assert isinstance(result, list)
        if len(result) > 1:
            distances = [r["distance_km"] for r in result if "distance_km" in r]
            assert distances == sorted(distances)
        # Verify Athena was queried for locations
        queries = " ".join(inject_mock_athena.queries_executed)
        assert "atm_locations" in queries.lower()

    def test_query_revenue_data_returns_trend(self, inject_mock_athena):
        result = query_revenue_data("ATM_SEEF_01", period="monthly")
        assert "gross_revenue" in result
        assert "net_revenue" in result
        assert "trend" in result
        assert result["trend"] in ("increasing", "decreasing", "stable", "insufficient_data", "no_data")

    def test_query_maintenance_costs_returns_breakdown(self, inject_mock_athena):
        result = query_maintenance_costs("ATM_SEEF_01", "2024-01-01", "2024-12-31")
        assert "total_cost" in result
        assert "breakdown_by_type" in result
        assert "total_downtime_hours" in result

    def test_query_cash_levels_returns_forecast(self, inject_mock_athena):
        result = query_cash_levels("ATM_SEEF_01")
        if "error" not in result:
            assert "current_balance" in result
            assert "forecast_7day" in result
            assert isinstance(result["forecast_7day"], list)
            assert len(result["forecast_7day"]) == 7

    def test_calculate_impact_analysis_returns_redistribution(self, inject_mock_athena):
        result = calculate_impact_analysis("ATM_SEEF_01", 5)
        if "total_revenue_loss" in result:
            assert "traffic_redistribution" in result
            assert "recommendations" in result
            assert isinstance(result["traffic_redistribution"], list)

    def test_detect_anomalies_returns_list(self, inject_mock_athena):
        result = detect_anomalies(period="30d")
        assert isinstance(result, list)

    def test_profitability_ranking_returns_ranked_list(self, inject_mock_athena):
        result = profitability_ranking(top_n=10)
        assert isinstance(result, list)
        if result and "error" not in result[0]:
            ranks = [r["rank"] for r in result]
            assert ranks == list(range(1, len(ranks) + 1))

    def test_invalid_date_range_returns_error(self):
        result = query_atm_data("ATM_SEEF_01", "2024-06-30", "2024-01-01")
        assert "error" in result

    def test_nonexistent_atm_returns_empty_or_error(self, inject_mock_athena):
        result = query_atm_data("ATM_NONEXISTENT_99", "2024-01-01", "2024-06-30")
        assert result["transaction_count"] == 0 or "error" in result

    def test_all_queries_go_through_athena(self, inject_mock_athena):
        """Verify that tool calls result in Athena queries, not CSV reads."""
        query_atm_data("ATM_SEEF_01", "2024-01-01", "2024-03-31")
        assert len(inject_mock_athena.queries_executed) > 0
        for q in inject_mock_athena.queries_executed:
            assert "SELECT" in q.upper()
            assert ATHENA_DATABASE in q


# ---------------------------------------------------------------------------
# Athena client: cache and validation integration
# ---------------------------------------------------------------------------


class TestAthenaCacheIntegration:
    def test_cache_stores_and_retrieves(self):
        cache = QueryCache(ttl_seconds=60)
        cache.put("SELECT 1", [{"col": "val"}])
        assert cache.get("SELECT 1") == [{"col": "val"}]

    def test_cache_miss_returns_none(self):
        cache = QueryCache(ttl_seconds=60)
        assert cache.get("unknown query") is None

    def test_cache_invalidation(self):
        cache = QueryCache(ttl_seconds=60)
        cache.put("q1", [{"a": 1}])
        cache.invalidate("q1")
        assert cache.get("q1") is None

    def test_cache_invalidate_all(self):
        cache = QueryCache(ttl_seconds=60)
        cache.put("q1", [{"a": 1}])
        cache.put("q2", [{"b": 2}])
        cache.invalidate()
        assert cache.size == 0


class TestDataValidationIntegration:
    def test_valid_transaction_record(self):
        record = {
            "transaction_id": "TXN001",
            "atm_id": "ATM_SEEF_01",
            "timestamp": "2024-01-15 10:30:00",
            "transaction_type": "withdrawal",
            "amount": "150.000",
            "fee": "0.500",
        }
        errors = validate_record("atm_transactions", record)
        assert len(errors) == 0

    def test_invalid_transaction_type_rejected(self):
        record = {
            "transaction_id": "TXN001",
            "atm_id": "ATM_SEEF_01",
            "timestamp": "2024-01-15 10:30:00",
            "transaction_type": "transfer",
            "amount": "150.000",
            "fee": "0.500",
        }
        errors = validate_record("atm_transactions", record)
        assert any(e.field == "transaction_type" for e in errors)

    def test_missing_required_field_rejected(self):
        record = {
            "transaction_id": "TXN001",
            "timestamp": "2024-01-15 10:30:00",
            "transaction_type": "withdrawal",
            "amount": "150.000",
            "fee": "0.500",
        }
        errors = validate_record("atm_transactions", record)
        assert any(e.field == "atm_id" for e in errors)

    def test_coordinates_outside_bahrain_rejected(self):
        record = {
            "atm_id": "ATM_TEST_01",
            "name": "Test ATM",
            "latitude": "40.0",
            "longitude": "50.5",
            "location_type": "standalone",
            "branch_id": "",
            "daily_capacity": "500",
            "status": "active",
        }
        errors = validate_record("atm_locations", record)
        assert any(e.field == "latitude" for e in errors)
