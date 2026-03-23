"""
MCP Server for ATM Profitability Optimizer.

Exposes all 12 ATM analysis tools via Model Context Protocol (MCP) using FastMCP.
Configured for deployment in me-south-1 (Bahrain) region.

Features:
  - 12 registered tools with full descriptions and parameter schemas
  - Caching layer via AthenaClient (configurable TTL, default 300s)
  - Retry logic with exponential backoff via AthenaClient
  - AgentCore Gateway compatible tool registration
  - Graceful error handling for all tool invocations

Caching Configuration (from agent/config.py):
  - MCP_CACHE_TTL_SECONDS: 300 (5 minutes) — in-memory cache for Athena results
  - Cache is managed by AthenaClient.QueryCache with automatic TTL expiry

Retry Configuration (from agent/config.py):
  - MCP_MAX_RETRIES: 3 — maximum retry attempts for transient failures
  - MCP_RETRY_BACKOFF_BASE: 2 — exponential backoff base in seconds
  - Retried errors: ThrottlingException, TooManyRequestsException,
    InternalServerException, ServiceUnavailableException
"""

from __future__ import annotations

import logging
import os
import sys

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import (
    DATA_REGION,
    MCP_CACHE_TTL_SECONDS,
    MCP_MAX_RETRIES,
    MCP_RETRY_BACKOFF_BASE,
    MCP_TOOL_TIMEOUT_SECONDS,
)
from agent.tools.query_atm_data import query_atm_data as _query_atm_data
from agent.tools.query_branch_proximity import query_branch_proximity as _query_branch_proximity
from agent.tools.query_revenue_data import query_revenue_data as _query_revenue_data
from agent.tools.query_maintenance_costs import query_maintenance_costs as _query_maintenance_costs
from agent.tools.query_cash_levels import query_cash_levels as _query_cash_levels
from agent.tools.calculate_impact_analysis import calculate_impact_analysis as _calculate_impact_analysis
from agent.tools.detect_anomalies import detect_anomalies as _detect_anomalies
from agent.tools.profitability_ranking import profitability_ranking as _profitability_ranking
from agent.tools.query_competitor_analysis import query_competitor_analysis as _query_competitor_analysis
from agent.tools.query_coverage_analysis import query_coverage_analysis as _query_coverage_analysis
from agent.tools.simulate_competitor_scenario import simulate_competitor_scenario as _simulate_competitor_scenario
from agent.tools.recommend_atm_placement import recommend_atm_placement as _recommend_atm_placement

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MCP Server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "ATM Profitability Optimizer",
    instructions=(
        "MCP server for NeoBank ATM Profitability Optimizer. "
        "Provides tools to query ATM transaction data, branch proximity, "
        "revenue metrics, maintenance costs, cash levels, impact analysis, "
        "anomaly detection, profitability rankings, competitor analysis, "
        "coverage analysis, scenario simulation, and ATM placement recommendations. "
        f"All data operations target AWS {DATA_REGION} (Bahrain) region. "
        f"Query results are cached for {MCP_CACHE_TTL_SECONDS}s. "
        f"Transient failures are retried up to {MCP_MAX_RETRIES} times "
        f"with exponential backoff (base {MCP_RETRY_BACKOFF_BASE}s)."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1: query_atm_data (Operator + Admin)
# ---------------------------------------------------------------------------

@mcp.tool()
def query_atm_data(atm_id: str, start_date: str, end_date: str) -> dict:
    """Query ATM transaction summary for a specified ATM and date range.

    Returns transaction count, total amount, average daily transactions,
    and revenue (fee income) in BHD for the given ATM and period.

    Args:
        atm_id: ATM identifier, e.g. 'ATM_SEEF_01'
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        dict with atm_id, transaction_count, total_amount, avg_daily_txns,
        revenue, and currency fields
    """
    try:
        return _query_atm_data(atm_id, start_date, end_date)
    except Exception as e:
        logger.error("query_atm_data failed: %s", e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 2: query_branch_proximity (Operator + Admin)
# ---------------------------------------------------------------------------

@mcp.tool()
def query_branch_proximity(atm_id: str, radius_km: float = 5.0) -> list:
    """Find nearby ATMs and branches within a given radius.

    Uses haversine distance to find all ATMs and NeoBank branches within
    the specified radius of the source ATM. Results are sorted by distance.

    Args:
        atm_id: Source ATM identifier, e.g. 'ATM_SEEF_01'
        radius_km: Search radius in kilometers (default 5.0)

    Returns:
        list of dicts with id, name, type, distance_km, and capacity info
    """
    try:
        return _query_branch_proximity(atm_id, radius_km)
    except Exception as e:
        logger.error("query_branch_proximity failed: %s", e)
        return [{"error": f"Tool execution failed: {str(e)}"}]


# ---------------------------------------------------------------------------
# Tool 3: query_revenue_data (Operator + Admin)
# ---------------------------------------------------------------------------

@mcp.tool()
def query_revenue_data(atm_id: str, period: str = "monthly") -> dict:
    """Query revenue metrics for an ATM with period aggregation.

    Calculates gross revenue (fee income), net revenue after deducting
    maintenance and cash handling costs, and identifies revenue trend.

    Args:
        atm_id: ATM identifier, e.g. 'ATM_SEEF_01'
        period: Aggregation period — 'daily', 'weekly', or 'monthly'

    Returns:
        dict with gross_revenue, net_revenue, fee_income, maintenance_costs,
        cash_handling_costs, trend, and currency fields
    """
    try:
        return _query_revenue_data(atm_id, period)
    except Exception as e:
        logger.error("query_revenue_data failed: %s", e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 4: query_maintenance_costs (Admin only)
# ---------------------------------------------------------------------------

@mcp.tool()
def query_maintenance_costs(atm_id: str, start_date: str, end_date: str) -> dict:
    """Query maintenance cost history with type breakdown.

    Returns total maintenance cost, breakdown by type (preventive,
    corrective, emergency), total downtime hours, and event count.

    Args:
        atm_id: ATM identifier, e.g. 'ATM_SEEF_01'
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        dict with total_cost, breakdown_by_type, total_downtime_hours,
        event_count, and currency fields
    """
    try:
        return _query_maintenance_costs(atm_id, start_date, end_date)
    except Exception as e:
        logger.error("query_maintenance_costs failed: %s", e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 5: query_cash_levels (Admin only)
# ---------------------------------------------------------------------------

@mcp.tool()
def query_cash_levels(atm_id: str) -> dict:
    """Query current and forecasted cash levels for an ATM.

    Returns the current cash balance, 7-day withdrawal forecast based on
    day-of-week patterns, and replenishment recommendations.

    Args:
        atm_id: ATM identifier, e.g. 'ATM_SEEF_01'

    Returns:
        dict with current_balance, avg_daily_withdrawal, forecast_7day,
        replenishment_recommendation, and currency fields
    """
    try:
        return _query_cash_levels(atm_id)
    except Exception as e:
        logger.error("query_cash_levels failed: %s", e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 6: calculate_impact_analysis (Admin only)
# ---------------------------------------------------------------------------

@mcp.tool()
def calculate_impact_analysis(atm_id: str, downtime_days: int) -> dict:
    """Calculate revenue impact and traffic reallocation for ATM downtime.

    Models the financial impact of an ATM being offline for a given number
    of days. Uses inverse-distance weighting to redistribute traffic to
    nearby ATMs within 5 km. Guarantees traffic conservation (total
    redistributed transactions equals original daily count).

    Args:
        atm_id: ATM identifier, e.g. 'ATM_SEEF_01'
        downtime_days: Number of days the ATM will be down (must be positive)

    Returns:
        dict with total_revenue_loss, traffic_redistribution list,
        recommendations, and currency fields
    """
    try:
        return _calculate_impact_analysis(atm_id, downtime_days)
    except Exception as e:
        logger.error("calculate_impact_analysis failed: %s", e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 7: detect_anomalies (Admin only)
# ---------------------------------------------------------------------------

@mcp.tool()
def detect_anomalies(atm_id: str = None, period: str = "30d") -> list:
    """Detect anomalies in ATM performance.

    Identifies ATMs with transaction volumes deviating more than 2 standard
    deviations from expected patterns. Anomalies are ranked by deviation
    magnitude and include estimated revenue impact.

    Args:
        atm_id: Optional ATM identifier. If omitted, checks all ATMs.
        period: Analysis period — '7d', '30d', or '90d' (default '30d')

    Returns:
        list of dicts with atm_id, date, anomaly_type, deviation,
        estimated_impact_bhd fields, sorted by deviation magnitude
    """
    try:
        return _detect_anomalies(atm_id, period)
    except Exception as e:
        logger.error("detect_anomalies failed: %s", e)
        return [{"error": f"Tool execution failed: {str(e)}"}]


# ---------------------------------------------------------------------------
# Tool 8: profitability_ranking (Admin only)
# ---------------------------------------------------------------------------

@mcp.tool()
def profitability_ranking(top_n: int = 28, sort: str = "net_revenue") -> list:
    """Rank ATMs by profitability metrics.

    Computes net revenue (transaction_revenue - maintenance_costs -
    cash_handling_costs) for each ATM and returns a ranked list.
    Useful for identifying underperforming ATMs for review.

    Args:
        top_n: Number of ATMs to return (default 28 = all)
        sort: Sort field — 'net_revenue', 'gross_revenue', or 'costs'

    Returns:
        list of dicts with atm_id, name, gross_revenue, maintenance_costs,
        cash_handling_costs, net_revenue, rank, and currency fields
    """
    try:
        return _profitability_ranking(top_n, sort)
    except Exception as e:
        logger.error("profitability_ranking failed: %s", e)
        return [{"error": f"Tool execution failed: {str(e)}"}]


# ---------------------------------------------------------------------------
# Tool 9: query_competitor_analysis (Operator + Admin)
# ---------------------------------------------------------------------------

@mcp.tool()
def query_competitor_analysis(atm_id: str = None, radius_km: float = 2.0) -> dict:
    """Calculate Competition Index for NeoBank ATMs based on nearby competitors.

    Returns competition pressure scores for one or all NeoBank ATMs.
    Competition Index ranges from 0.0 (no competition) to 1.0 (high competition).

    Args:
        atm_id: Optional ATM identifier. If omitted, returns scores for all ATMs.
        radius_km: Search radius in kilometers (default 2.0)

    Returns:
        dict with competition_index, competitor_count, and nearby competitor details
    """
    try:
        return _query_competitor_analysis(atm_id, radius_km)
    except Exception as e:
        logger.error("query_competitor_analysis failed: %s", e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 10: query_coverage_analysis (Operator + Admin)
# ---------------------------------------------------------------------------

@mcp.tool()
def query_coverage_analysis(radius_km: float = 2.0) -> dict:
    """Identify coverage gaps, advantages, and market share vs competitors.

    Finds areas where competitors have ATMs but NeoBank doesn't (gaps),
    areas where NeoBank has exclusive coverage (advantages), and calculates
    market share by governorate.

    Args:
        radius_km: Analysis radius in kilometers (default 2.0)

    Returns:
        dict with coverage_gaps, coverage_advantages, market_share, and summary
    """
    try:
        return _query_coverage_analysis(radius_km)
    except Exception as e:
        logger.error("query_coverage_analysis failed: %s", e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 11: simulate_competitor_scenario (Admin only)
# ---------------------------------------------------------------------------

@mcp.tool()
def simulate_competitor_scenario(
    scenario_type: str,
    latitude: float,
    longitude: float,
    bank_name: str,
    radius_km: float = 2.0,
) -> dict:
    """Simulate impact of a competitor opening or closing an ATM.

    Models transaction volume redistribution using inverse-distance weighting.
    Preserves transaction conservation invariant.

    Args:
        scenario_type: 'add' for new competitor ATM, 'remove' for closure
        latitude: GPS latitude (must be within Bahrain: 25.5-26.3)
        longitude: GPS longitude (must be within Bahrain: 50.4-50.8)
        bank_name: Competitor bank name
        radius_km: Impact radius in kilometers (default 2.0)

    Returns:
        dict with affected_atms, projected revenue changes, and recommendations
    """
    try:
        return _simulate_competitor_scenario(scenario_type, latitude, longitude, bank_name, radius_km)
    except Exception as e:
        logger.error("simulate_competitor_scenario failed: %s", e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Tool 12: recommend_atm_placement (Admin only)
# ---------------------------------------------------------------------------

@mcp.tool()
def recommend_atm_placement(count: int = 3, radius_km: float = 2.0) -> dict:
    """Recommend optimal locations for new NeoBank ATMs.

    Scores candidate locations based on coverage gaps, competitor density,
    and distance from existing NeoBank ATMs to avoid cannibalization.

    Args:
        count: Number of recommendations to return (default 3)
        radius_km: Analysis radius in kilometers (default 2.0)

    Returns:
        dict with ranked recommendations and estimated revenue uplift
    """
    try:
        return _recommend_atm_placement(count, radius_km)
    except Exception as e:
        logger.error("recommend_atm_placement failed: %s", e)
        return {"error": f"Tool execution failed: {str(e)}"}


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------

def get_server() -> FastMCP:
    """Return the configured MCP server instance.

    Used for programmatic access and testing.
    """
    return mcp


if __name__ == "__main__":
    mcp.run()
