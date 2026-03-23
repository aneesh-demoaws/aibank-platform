"""
MCP Tool: query_revenue_data

Query revenue metrics for an ATM with period aggregation (daily, weekly, monthly).
Access: Operator + Admin

All queries go through AthenaClient → Athena → S3 in me-south-1.

Performance: Uses pre-aggregated atm_profitability (28 rows) for totals and
daily_atm_stats (5,152 rows) for trend analysis, instead of scanning
full atm_transactions (~1M rows).

Returns: gross_revenue, net_revenue, fee_income, trend
"""

from __future__ import annotations

import logging
import os
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools._athena_queries import (
    query_daily_fee_by_period,
    query_profitability_combined,
)

logger = logging.getLogger(__name__)


def query_revenue_data(atm_id: str, period: str = "monthly") -> dict:
    """Query revenue metrics for ATM.

    Args:
        atm_id: ATM identifier
        period: Aggregation period - 'daily', 'weekly', or 'monthly'

    Returns:
        dict with gross_revenue, net_revenue, fee_income, trend, period_breakdown
    """
    try:
        if period not in ("daily", "weekly", "monthly"):
            return {"error": f"Invalid period '{period}'. Must be daily, weekly, or monthly."}

        # Get totals from pre-aggregated atm_profitability table (28 rows, instant)
        combined = query_profitability_combined()
        atm_row = None
        for row in combined:
            if row["atm_id"] == atm_id:
                atm_row = row
                break

        if atm_row is None:
            return {
                "atm_id": atm_id,
                "period": period,
                "gross_revenue": 0.0,
                "net_revenue": 0.0,
                "fee_income": 0.0,
                "trend": "no_data",
                "currency": "BHD",
            }

        fee_income = atm_row["total_revenue"]
        total_maintenance = atm_row["total_maintenance_cost"]
        total_cash_handling = atm_row["total_cash_cost"]
        gross_revenue = fee_income
        net_revenue = gross_revenue - total_maintenance - total_cash_handling

        # Get daily fee data for trend analysis from daily_atm_stats
        daily_fees = query_daily_fee_by_period(atm_id)

        if not daily_fees:
            return {
                "atm_id": atm_id,
                "period": period,
                "gross_revenue": round(gross_revenue, 3),
                "net_revenue": round(net_revenue, 3),
                "fee_income": round(fee_income, 3),
                "maintenance_costs": round(total_maintenance, 3),
                "cash_handling_costs": round(total_cash_handling, 3),
                "trend": "no_data",
                "currency": "BHD",
            }

        # Bucket fees by period for trend calculation
        period_buckets: dict[str, float] = defaultdict(float)
        for row in daily_fees:
            day = row["day"]
            if period == "daily":
                key = day
            elif period == "weekly":
                from datetime import datetime
                dt = datetime.strptime(day, "%Y-%m-%d")
                key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
            else:
                key = day[:7]
            period_buckets[key] += row["total_fee"]

        sorted_periods = sorted(period_buckets.items())

        if len(sorted_periods) >= 2:
            mid = len(sorted_periods) // 2
            first_half_avg = sum(v for _, v in sorted_periods[:mid]) / mid
            second_half_avg = sum(v for _, v in sorted_periods[mid:]) / (len(sorted_periods) - mid)
            if second_half_avg > first_half_avg * 1.05:
                trend = "increasing"
            elif second_half_avg < first_half_avg * 0.95:
                trend = "decreasing"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        return {
            "atm_id": atm_id,
            "period": period,
            "gross_revenue": round(gross_revenue, 3),
            "net_revenue": round(net_revenue, 3),
            "fee_income": round(fee_income, 3),
            "maintenance_costs": round(total_maintenance, 3),
            "cash_handling_costs": round(total_cash_handling, 3),
            "trend": trend,
            "currency": "BHD",
        }

    except Exception as e:
        logger.error("Error querying revenue for %s: %s", atm_id, e)
        return {"error": f"Failed to query revenue data: {str(e)}"}
