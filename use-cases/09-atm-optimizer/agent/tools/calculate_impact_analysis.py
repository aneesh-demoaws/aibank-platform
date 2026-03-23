"""
MCP Tool: calculate_impact_analysis

Calculate revenue loss and traffic reallocation using inverse-distance weighting.
Access: Admin only

All queries go through AthenaClient → Athena → S3 in me-south-1.

Performance: Uses pre-aggregated daily_atm_stats table for daily averages
instead of scanning full atm_transactions (~1M rows).

CRITICAL PROPERTY (Property 1 - Traffic Conservation):
  The sum of redistributed transactions MUST equal the original ATM's daily count.

Returns: revenue_loss, traffic_redistribution, recommendations
"""

from __future__ import annotations

import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import DEFAULT_PROXIMITY_RADIUS_KM
from agent.tools._athena_queries import (
    haversine,
    query_atm_daily_averages,
    query_atm_locations,
)

logger = logging.getLogger(__name__)


def redistribute_traffic(daily_txn_count: int, nearby_atms: list[dict]) -> list[dict]:
    """Redistribute traffic from a downed ATM using inverse-distance weighting.

    This function guarantees traffic conservation: the sum of redistributed
    transactions equals the original daily_txn_count exactly.

    Args:
        daily_txn_count: Number of daily transactions to redistribute
        nearby_atms: List of dicts with 'atm_id', 'name', 'distance_km', 'daily_capacity'

    Returns:
        List of dicts with atm_id, name, distance_km, allocated_transactions, weight
    """
    if daily_txn_count <= 0 or not nearby_atms:
        return []

    valid = [a for a in nearby_atms if a["distance_km"] > 0]
    if not valid:
        return []

    weights = [1.0 / a["distance_km"] for a in valid]
    total_weight = sum(weights)

    if total_weight == 0:
        return []

    norm_weights = [w / total_weight for w in weights]

    allocations = []
    allocated_so_far = 0

    for i, atm in enumerate(valid):
        if i == len(valid) - 1:
            alloc = daily_txn_count - allocated_so_far
        else:
            alloc = round(daily_txn_count * norm_weights[i])
            remaining = daily_txn_count - allocated_so_far
            alloc = max(0, min(alloc, remaining))
            allocated_so_far += alloc

        allocations.append({
            "atm_id": atm["atm_id"],
            "name": atm.get("name", atm["atm_id"]),
            "distance_km": atm["distance_km"],
            "allocated_transactions": alloc,
            "weight": round(norm_weights[i], 4),
        })

    return allocations


def calculate_impact_analysis(atm_id: str, downtime_days: int) -> dict:
    """Calculate revenue impact and traffic reallocation for ATM downtime.

    Args:
        atm_id: ATM identifier
        downtime_days: Number of days the ATM will be down

    Returns:
        dict with revenue_loss, traffic_redistribution, recommendations
    """
    try:
        if downtime_days <= 0:
            return {"error": "downtime_days must be positive"}

        atm_locations = query_atm_locations()

        source = None
        for loc in atm_locations:
            if loc["atm_id"] == atm_id:
                source = loc
                break

        if source is None:
            return {"error": f"ATM {atm_id} not found"}

        # Use pre-aggregated daily_atm_stats instead of scanning ~35K raw transactions
        daily_avg = query_atm_daily_averages(atm_id)
        if daily_avg["num_days"] == 0:
            return {
                "atm_id": atm_id,
                "downtime_days": downtime_days,
                "error": "No transaction history available for this ATM",
            }

        avg_daily_txns = daily_avg["avg_daily_txns"]
        avg_daily_revenue = daily_avg["avg_daily_fee"]
        total_revenue_loss = avg_daily_revenue * downtime_days

        nearby = []
        for loc in atm_locations:
            if loc["atm_id"] == atm_id:
                continue
            if loc["status"] != "active":
                continue
            dist = haversine(source["latitude"], source["longitude"],
                             loc["latitude"], loc["longitude"])
            if dist <= DEFAULT_PROXIMITY_RADIUS_KM:
                nearby.append({
                    "atm_id": loc["atm_id"],
                    "name": loc["name"],
                    "distance_km": round(dist, 3),
                    "daily_capacity": loc["daily_capacity"],
                })

        daily_txn_count = round(avg_daily_txns)
        redistribution = redistribute_traffic(daily_txn_count, nearby)

        recommendations = []
        if total_revenue_loss > 100:
            recommendations.append(
                f"Consider deploying a mobile ATM unit. Estimated revenue loss: {total_revenue_loss:.3f} BHD"
            )

        for r in redistribution:
            matching = [a for a in nearby if a["atm_id"] == r["atm_id"]]
            if matching:
                cap = matching[0]["daily_capacity"]
                if r["allocated_transactions"] > cap * 0.8:
                    recommendations.append(
                        f"{r['atm_id']} may exceed capacity with {r['allocated_transactions']} "
                        f"additional transactions (capacity: {cap})"
                    )

        if not nearby:
            recommendations.append(
                "No nearby ATMs within 5km radius. Mobile ATM deployment strongly recommended."
            )

        return {
            "atm_id": atm_id,
            "downtime_days": downtime_days,
            "avg_daily_transactions": round(avg_daily_txns, 1),
            "avg_daily_revenue": round(avg_daily_revenue, 3),
            "total_revenue_loss": round(total_revenue_loss, 3),
            "traffic_redistribution": redistribution,
            "nearby_atm_count": len(nearby),
            "recommendations": recommendations,
            "currency": "BHD",
        }

    except Exception as e:
        logger.error("Error calculating impact for %s: %s", atm_id, e)
        return {"error": f"Failed to calculate impact analysis: {str(e)}"}
