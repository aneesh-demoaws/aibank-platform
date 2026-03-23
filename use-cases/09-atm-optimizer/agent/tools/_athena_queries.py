"""
Athena SQL query builder and executor for MCP tools.

All tool data access goes through AthenaClient → Athena → S3 in me-south-1.
This replaces the CSV-based _data_loader.py approach.

The haversine function is kept here for in-memory distance calculations
on Athena result sets (Athena doesn't have a native haversine function).
"""

from __future__ import annotations

import logging
import math
import os
import sys
from typing import Any

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import ATHENA_DATABASE

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in km between two GPS points."""
    lat1_r, lon1_r = math.radians(lat1), math.radians(lon1)
    lat2_r, lon2_r = math.radians(lat2), math.radians(lon2)
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Singleton AthenaClient instance (lazy-initialized)
# ---------------------------------------------------------------------------

_athena_client = None


def get_athena_client():
    """Return a shared AthenaClient instance."""
    global _athena_client
    if _athena_client is None:
        from mcp_server.athena_client import AthenaClient
        _athena_client = AthenaClient()
    return _athena_client


def reset_athena_client():
    """Reset the singleton (for testing)."""
    global _athena_client
    _athena_client = None


def set_athena_client(client):
    """Inject a custom client (for testing)."""
    global _athena_client
    _athena_client = client


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _execute(sql: str) -> list[dict]:
    """Execute SQL via AthenaClient and return rows as list of dicts."""
    return get_athena_client().execute_query(sql)


# ---------------------------------------------------------------------------
# ATM Locations
# ---------------------------------------------------------------------------

def query_atm_locations(atm_id: str | None = None) -> list[dict]:
    """Query ATM locations from Athena."""
    sql = f"SELECT * FROM {ATHENA_DATABASE}.atm_locations"
    if atm_id:
        sql += f" WHERE atm_id = '{atm_id}'"
    rows = _execute(sql)
    for r in rows:
        r["latitude"] = float(r["latitude"])
        r["longitude"] = float(r["longitude"])
        r["daily_capacity"] = int(r["daily_capacity"])
    return rows


# ---------------------------------------------------------------------------
# Branch Locations
# ---------------------------------------------------------------------------

def query_branch_locations() -> list[dict]:
    """Query branch locations from Athena."""
    sql = f"SELECT * FROM {ATHENA_DATABASE}.branch_locations"
    rows = _execute(sql)
    for r in rows:
        r["latitude"] = float(r["latitude"])
        r["longitude"] = float(r["longitude"])
        r["atm_count"] = int(r["atm_count"])
        r["avg_daily_footfall"] = int(r["avg_daily_footfall"])
    return rows


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

def query_transactions(
    atm_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Query transactions from Athena with optional filters."""
    sql = f"SELECT * FROM {ATHENA_DATABASE}.atm_transactions WHERE 1=1"
    if atm_id:
        sql += f" AND atm_id = '{atm_id}'"
    if start_date:
        sql += f" AND CAST(txn_timestamp AS VARCHAR) >= '{start_date}'"
    if end_date:
        sql += f" AND CAST(txn_timestamp AS VARCHAR) <= '{end_date} 23:59:59'"
    rows = _execute(sql)
    for r in rows:
        r["amount"] = float(r["amount"])
        r["fee"] = float(r["fee"])
    return rows


# ---------------------------------------------------------------------------
# Maintenance Costs
# ---------------------------------------------------------------------------

def query_maintenance(
    atm_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Query maintenance records from Athena."""
    sql = f"SELECT * FROM {ATHENA_DATABASE}.maintenance_costs WHERE 1=1"
    if atm_id:
        sql += f" AND atm_id = '{atm_id}'"
    if start_date:
        sql += f" AND date >= '{start_date}'"
    if end_date:
        sql += f" AND date <= '{end_date}'"
    rows = _execute(sql)
    for r in rows:
        r["cost"] = float(r["cost"])
        r["downtime_hours"] = float(r["downtime_hours"])
    return rows


# ---------------------------------------------------------------------------
# Cash Levels
# ---------------------------------------------------------------------------

def query_cash_levels(atm_id: str | None = None) -> list[dict]:
    """Query cash level records from Athena."""
    sql = f"SELECT * FROM {ATHENA_DATABASE}.cash_levels WHERE 1=1"
    if atm_id:
        sql += f" AND atm_id = '{atm_id}'"
    rows = _execute(sql)
    for r in rows:
        for field in ("opening_balance", "closing_balance", "total_withdrawals",
                      "replenishment_amount", "replenishment_cost"):
            r[field] = float(r[field])
    return rows


# ---------------------------------------------------------------------------
# Proximity
# ---------------------------------------------------------------------------

def query_proximity(source_atm_id: str | None = None) -> list[dict]:
    """Query ATM proximity data from Athena."""
    sql = f"SELECT * FROM {ATHENA_DATABASE}.atm_proximity WHERE 1=1"
    if source_atm_id:
        sql += f" AND source_atm_id = '{source_atm_id}'"
    rows = _execute(sql)
    for r in rows:
        r["distance_km"] = float(r["distance_km"])
        r["is_same_branch"] = str(r.get("is_same_branch", "")).lower() == "true"
    return rows


# ---------------------------------------------------------------------------
# Aggregate queries (optimized for Lambda — push computation to Athena)
# ---------------------------------------------------------------------------

def query_revenue_by_atm() -> list[dict]:
    """Aggregate total fee revenue per ATM using Athena SQL."""
    sql = f"""
        SELECT atm_id,
               SUM(fee) AS total_revenue,
               COUNT(*) AS txn_count
        FROM {ATHENA_DATABASE}.atm_transactions
        GROUP BY atm_id
    """
    rows = _execute(sql)
    for r in rows:
        r["total_revenue"] = float(r["total_revenue"])
        r["txn_count"] = int(r["txn_count"])
    return rows


def query_maintenance_cost_by_atm() -> list[dict]:
    """Aggregate total maintenance cost per ATM using Athena SQL."""
    sql = f"""
        SELECT atm_id,
               SUM(CAST(cost AS DOUBLE)) AS total_cost,
               SUM(CAST(downtime_hours AS DOUBLE)) AS total_downtime
        FROM {ATHENA_DATABASE}.maintenance_costs
        GROUP BY atm_id
    """
    rows = _execute(sql)
    for r in rows:
        r["total_cost"] = float(r["total_cost"])
        r["total_downtime"] = float(r["total_downtime"])
    return rows


def query_cash_handling_cost_by_atm() -> list[dict]:
    """Aggregate total cash handling (replenishment) cost per ATM using Athena SQL."""
    sql = f"""
        SELECT atm_id,
               SUM(CAST(replenishment_cost AS DOUBLE)) AS total_cash_cost
        FROM {ATHENA_DATABASE}.cash_levels
        GROUP BY atm_id
    """
    rows = _execute(sql)
    for r in rows:
        r["total_cash_cost"] = float(r["total_cash_cost"])
    return rows


def query_daily_txn_stats(atm_id: str | None = None, period_days: int = 30) -> list[dict]:
    """Get daily transaction count and amount stats per ATM for anomaly detection.

    Uses the pre-aggregated `daily_atm_stats` table (5,152 rows) instead of
    scanning the full `atm_transactions` table (~300k rows). This reduces
    query time from ~180s to <2s.

    Two lightweight queries:
    1. MAX(txn_date) to find the data's latest date
    2. Single query with window functions for stats + daily rows
    """
    from datetime import datetime, timedelta

    where = f"AND atm_id = '{atm_id}'" if atm_id else ""

    # Query 0: Get max date (instant — tiny table)
    max_rows = _execute(
        f"SELECT CAST(MAX(txn_date) AS VARCHAR) AS max_date FROM {ATHENA_DATABASE}.daily_atm_stats"
    )
    if not max_rows or not max_rows[0].get("max_date"):
        return []
    max_date = max_rows[0]["max_date"]
    cutoff = (datetime.strptime(max_date, "%Y-%m-%d") - timedelta(days=period_days)).strftime("%Y-%m-%d")

    # Single query: stats + daily rows from pre-aggregated table
    sql = f"""
        WITH stats AS (
            SELECT atm_id,
                   AVG(txn_count) AS mean_count,
                   STDDEV(txn_count) AS stdev_count,
                   AVG(total_amount) AS mean_amount,
                   STDDEV(total_amount) AS stdev_amount,
                   COUNT(*) AS num_days
            FROM {ATHENA_DATABASE}.daily_atm_stats
            WHERE CAST(txn_date AS VARCHAR) >= '{cutoff}' {where}
            GROUP BY atm_id
            HAVING COUNT(*) >= 7 AND STDDEV(txn_count) > 0
        )
        SELECT d.atm_id,
               CAST(d.txn_date AS VARCHAR) AS day,
               d.txn_count,
               d.total_amount AS txn_amount,
               s.mean_count,
               s.stdev_count,
               s.mean_amount,
               s.stdev_amount
        FROM {ATHENA_DATABASE}.daily_atm_stats d
        JOIN stats s ON d.atm_id = s.atm_id
        WHERE CAST(d.txn_date AS VARCHAR) >= '{cutoff}' {where}
          AND ABS(d.txn_count - s.mean_count) / s.stdev_count > 2.0
        ORDER BY ABS(d.txn_count - s.mean_count) / s.stdev_count DESC
    """
    rows = _execute(sql)

    results = []
    for r in rows:
        results.append({
            "atm_id": r["atm_id"],
            "day": r["day"],
            "txn_count": int(r["txn_count"]),
            "txn_amount": float(r["txn_amount"]),
            "mean_count": float(r["mean_count"]),
            "stdev_count": float(r["stdev_count"]),
            "mean_amount": float(r["mean_amount"]),
            "stdev_amount": float(r["stdev_amount"]),
        })

    return results


def query_atm_daily_averages(atm_id: str) -> dict:
    """Query average daily transactions and fee revenue for an ATM from daily_atm_stats.

    Uses the pre-aggregated daily_atm_stats table (5,152 rows) instead of
    scanning the full atm_transactions table (~1M rows).

    Returns:
        dict with avg_daily_txns, avg_daily_fee, num_days
    """
    sql = (
        f"SELECT AVG(txn_count) AS avg_daily_txns, "
        f"AVG(total_fee) AS avg_daily_fee, "
        f"COUNT(*) AS num_days "
        f"FROM {ATHENA_DATABASE}.daily_atm_stats "
        f"WHERE atm_id = '{atm_id}'"
    )
    rows = _execute(sql)
    if rows and rows[0].get("avg_daily_txns") is not None:
        return {
            "avg_daily_txns": float(rows[0]["avg_daily_txns"]),
            "avg_daily_fee": float(rows[0]["avg_daily_fee"]) if rows[0].get("avg_daily_fee") is not None else 0.0,
            "num_days": int(rows[0]["num_days"]),
        }
    return {"avg_daily_txns": 0.0, "avg_daily_fee": 0.0, "num_days": 0}


def query_atm_daily_averages_batch(atm_ids: list[str]) -> dict[str, dict]:
    """Query average daily transactions and fee for multiple ATMs in one query.

    Eliminates N+1 query pattern by batching all ATM IDs into a single
    Athena query on the pre-aggregated daily_atm_stats table.

    Args:
        atm_ids: List of ATM identifiers

    Returns:
        dict mapping atm_id -> {avg_daily_txns, avg_fee}
    """
    if not atm_ids:
        return {}
    ids_str = ", ".join(f"'{aid}'" for aid in atm_ids)
    sql = (
        f"SELECT atm_id, "
        f"AVG(txn_count) AS avg_daily_txns, "
        f"AVG(total_amount / NULLIF(txn_count, 0)) AS avg_fee "
        f"FROM {ATHENA_DATABASE}.daily_atm_stats "
        f"WHERE atm_id IN ({ids_str}) "
        f"GROUP BY atm_id"
    )
    rows = _execute(sql)
    result = {}
    for r in rows:
        result[r["atm_id"]] = {
            "avg_daily_txns": float(r["avg_daily_txns"]),
            "avg_fee": float(r["avg_fee"]) if r.get("avg_fee") is not None else 0.3,
        }
    # Fill missing ATMs with defaults
    for aid in atm_ids:
        if aid not in result:
            result[aid] = {"avg_daily_txns": 0.0, "avg_fee": 0.3}
    return result


def query_daily_fee_by_period(atm_id: str) -> list[dict]:
    """Query daily fee totals for an ATM from daily_atm_stats for trend analysis.

    Returns per-day fee data that can be bucketed into daily/weekly/monthly
    periods for trend calculation.

    Args:
        atm_id: ATM identifier

    Returns:
        list of dicts with day (YYYY-MM-DD string) and total_fee
    """
    sql = (
        f"SELECT CAST(txn_date AS VARCHAR) AS day, total_fee "
        f"FROM {ATHENA_DATABASE}.daily_atm_stats "
        f"WHERE atm_id = '{atm_id}' "
        f"ORDER BY txn_date"
    )
    rows = _execute(sql)
    for r in rows:
        r["total_fee"] = float(r["total_fee"])
    return rows


def query_profitability_combined() -> list[dict]:
    """Query pre-aggregated atm_profitability table.

    This table has 28 rows with pre-computed revenue, maintenance, and cash
    handling costs per ATM. Single query on tiny table = sub-second response.
    """
    sql = f"""
        SELECT atm_id, name, location_type,
               total_revenue, total_maintenance_cost, total_cash_cost
        FROM {ATHENA_DATABASE}.atm_profitability
    """
    rows = _execute(sql)
    for r in rows:
        r["total_revenue"] = float(r["total_revenue"])
        r["total_maintenance_cost"] = float(r["total_maintenance_cost"])
        r["total_cash_cost"] = float(r["total_cash_cost"])
    return rows


# ---------------------------------------------------------------------------
# Competitor Data
# ---------------------------------------------------------------------------

def query_competitor_locations(bank_name: str | None = None, status: str | None = None) -> list[dict]:
    """Query competitor_atm_locations table with optional bank/status filters."""
    # Get excluded banks at runtime (supports UI-driven alias changes)
    try:
        from frontend.config import get_excluded_banks
        excluded_banks = get_excluded_banks()
    except Exception:
        from agent.bank_alias import get_excluded_banks
        excluded_banks = get_excluded_banks()

    sql = f"SELECT * FROM {ATHENA_DATABASE}.competitor_atm_locations WHERE 1=1"
    if bank_name:
        sql += f" AND bank_name = '{bank_name}'"
    if status:
        sql += f" AND status = '{status}'"
    # Exclude the aliased bank from competitor results
    if excluded_banks:
        excluded = ", ".join(f"'{b}'" for b in excluded_banks)
        sql += f" AND bank_name NOT IN ({excluded})"
    rows = _execute(sql)
    for r in rows:
        r["latitude"] = float(r["latitude"])
        r["longitude"] = float(r["longitude"])
    return rows


def query_competitor_proximity(neobank_atm_id: str | None = None) -> list[dict]:
    """Query competitor_proximity table, optionally filtered by NeoBank ATM."""
    # Get excluded banks at runtime (supports UI-driven alias changes)
    try:
        from frontend.config import get_excluded_banks
        excluded_banks = get_excluded_banks()
    except Exception:
        from agent.bank_alias import get_excluded_banks
        excluded_banks = get_excluded_banks()

    sql = f"SELECT * FROM {ATHENA_DATABASE}.competitor_proximity WHERE 1=1"
    if neobank_atm_id:
        sql += f" AND neobank_atm_id = '{neobank_atm_id}'"
    # Exclude the aliased bank from competitor results
    if excluded_banks:
        excluded = ", ".join(f"'{b}'" for b in excluded_banks)
        sql += f" AND bank_name NOT IN ({excluded})"
    rows = _execute(sql)
    for r in rows:
        r["distance_km"] = float(r["distance_km"])
    return rows


def query_competition_index(atm_id: str | None = None) -> list[dict]:
    """Query pre-aggregated competition_index table.

    This table has 28 rows with pre-computed Competition Index per NeoBank ATM
    at 2km radius. Single query on tiny table = sub-second response.
    Columns: atm_id, name, location_type, competitor_count_2km,
             competition_index, nearest_competitor_km, farthest_competitor_km
    """
    sql = f"""
        SELECT atm_id, name, location_type,
               competitor_count_2km, competition_index,
               nearest_competitor_km, farthest_competitor_km
        FROM {ATHENA_DATABASE}.competition_index
    """
    if atm_id:
        sql += f" WHERE atm_id = '{atm_id}'"
    rows = _execute(sql)
    for r in rows:
        r["competitor_count_2km"] = int(r["competitor_count_2km"])
        r["competition_index"] = float(r["competition_index"])
        r["nearest_competitor_km"] = float(r["nearest_competitor_km"])
        r["farthest_competitor_km"] = float(r["farthest_competitor_km"])
    return rows
