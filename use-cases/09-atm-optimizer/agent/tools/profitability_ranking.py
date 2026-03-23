"""
MCP Tool: profitability_ranking

Rank ATMs by net revenue (revenue - maintenance - cash handling costs).
Access: Admin only

All queries go through AthenaClient -> Athena -> S3 in me-south-1.

CRITICAL PROPERTY (Property 5 - Revenue Calculation Consistency):
  net_revenue = transaction_revenue - maintenance_costs - cash_handling_costs

Returns: list of {atm_id, gross_revenue, costs, net_revenue, rank}
"""

from __future__ import annotations

import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools._athena_queries import (
    query_profitability_combined,
)

logger = logging.getLogger(__name__)


def compute_net_revenue(transaction_revenue: float, maintenance_costs: float, cash_handling_costs: float) -> float:
    """Compute net revenue ensuring Property 5 consistency.

    net_revenue = transaction_revenue - maintenance_costs - cash_handling_costs
    """
    return transaction_revenue - maintenance_costs - cash_handling_costs


def profitability_ranking(top_n: int = 28, sort: str = "net_revenue") -> list:
    """Rank ATMs by profitability.

    Args:
        top_n: Number of ATMs to return (default: all 28)
        sort: Sort field - 'net_revenue', 'gross_revenue', 'costs'

    Returns:
        list of dicts with atm_id, gross_revenue, maintenance_costs,
        cash_handling_costs, net_revenue, rank
    """
    try:
        if sort not in ("net_revenue", "gross_revenue", "costs"):
            return [{"error": f"Invalid sort '{sort}'. Must be net_revenue, gross_revenue, or costs."}]

        # Single combined query — 1 Athena round-trip instead of 4
        combined = query_profitability_combined()

        rankings = []
        for row in combined:
            aid = row["atm_id"]
            transaction_revenue = row["total_revenue"]
            maintenance_costs = row["total_maintenance_cost"]
            cash_handling_costs = row["total_cash_cost"]

            net_rev = compute_net_revenue(transaction_revenue, maintenance_costs, cash_handling_costs)

            rankings.append({
                "atm_id": aid,
                "name": row["name"],
                "location_type": row["location_type"],
                "gross_revenue": round(transaction_revenue, 3),
                "maintenance_costs": round(maintenance_costs, 3),
                "cash_handling_costs": round(cash_handling_costs, 3),
                "net_revenue": round(net_rev, 3),
                "currency": "BHD",
            })

        if sort == "costs":
            rankings.sort(key=lambda r: r["maintenance_costs"] + r["cash_handling_costs"], reverse=True)
        elif sort == "gross_revenue":
            rankings.sort(key=lambda r: r["gross_revenue"], reverse=True)
        else:
            rankings.sort(key=lambda r: r["net_revenue"], reverse=True)

        for i, r in enumerate(rankings):
            r["rank"] = i + 1

        return rankings[:top_n]

    except Exception as e:
        logger.error("Error computing profitability ranking: %s", e)
        return [{"error": f"Failed to compute profitability ranking: {str(e)}"}]
