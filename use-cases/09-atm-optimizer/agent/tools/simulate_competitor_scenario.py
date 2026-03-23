"""
MCP Tool: simulate_competitor_scenario

Simulate impact of competitor ATM addition/removal on NeoBank network.
Access: Admin only

Uses inverse-distance weighting with transaction conservation invariant.
All data sourced from Athena (no CSV fallback).
"""

from __future__ import annotations

import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import (
    BAHRAIN_LAT_MAX, BAHRAIN_LAT_MIN,
    BAHRAIN_LON_MAX, BAHRAIN_LON_MIN,
    ESTIMATED_COMPETITOR_DAILY_TXNS,
)
from agent.tools._athena_queries import haversine, query_atm_locations, query_atm_daily_averages_batch

logger = logging.getLogger(__name__)


def simulate_competitor_scenario(
    scenario_type: str,
    latitude: float,
    longitude: float,
    bank_name: str,
    radius_km: float = 2.0,
) -> dict:
    """Simulate impact of competitor ATM addition/removal.

    Args:
        scenario_type: "add" or "remove"
        latitude: GPS latitude of simulated ATM
        longitude: GPS longitude of simulated ATM
        bank_name: Competitor bank name
        radius_km: Impact radius (default 2.0 km)

    Returns:
        Impact analysis with affected ATMs and revenue changes
    """
    try:
        if scenario_type not in ("add", "remove"):
            return {"error": "Invalid scenario_type. Must be 'add' or 'remove'."}

        if not (BAHRAIN_LAT_MIN <= latitude <= BAHRAIN_LAT_MAX):
            return {"error": f"Invalid coordinates. Latitude must be {BAHRAIN_LAT_MIN}-{BAHRAIN_LAT_MAX}, longitude must be {BAHRAIN_LON_MIN}-{BAHRAIN_LON_MAX}."}

        if not (BAHRAIN_LON_MIN <= longitude <= BAHRAIN_LON_MAX):
            return {"error": f"Invalid coordinates. Latitude must be {BAHRAIN_LAT_MIN}-{BAHRAIN_LAT_MAX}, longitude must be {BAHRAIN_LON_MIN}-{BAHRAIN_LON_MAX}."}

        if radius_km <= 0:
            return {"error": "radius_km must be a positive number."}

        atm_locations = query_atm_locations()
        if not atm_locations:
            return {"error": "No ATM location data available"}

        # Find NeoBank ATMs within radius (distance calculation only, no DB queries)
        nearby = []
        for atm in atm_locations:
            dist = haversine(latitude, longitude, atm["latitude"], atm["longitude"])
            if dist <= radius_km and dist > 0:
                nearby.append((atm, round(dist, 3)))

        # Batch query: single Athena call for all affected ATMs (eliminates N+1)
        if nearby:
            nearby_ids = [atm["atm_id"] for atm, _ in nearby]
            stats_batch = query_atm_daily_averages_batch(nearby_ids)
        else:
            stats_batch = {}

        affected = []
        for atm, dist in nearby:
            stats = stats_batch.get(atm["atm_id"], {"avg_daily_txns": 0.0, "avg_fee": 0.3})
            affected.append({
                "atm_id": atm["atm_id"],
                "name": atm.get("name", atm["atm_id"]),
                "distance_km": dist,
                "current_daily_transactions": round(stats["avg_daily_txns"]),
                "avg_fee": stats["avg_fee"],
            })

        if not affected:
            return {
                "scenario_type": scenario_type,
                "simulated_location": {"latitude": latitude, "longitude": longitude, "bank_name": bank_name},
                "affected_atms": [],
                "summary": {
                    "total_affected_atms": 0,
                    "total_projected_revenue_change": 0.0,
                    "recommendations": ["No NeoBank ATMs within the specified radius."],
                },
            }

        # Inverse-distance weighting for transaction redistribution
        est_volume = ESTIMATED_COMPETITOR_DAILY_TXNS
        weights = [1.0 / a["distance_km"] for a in affected]
        total_weight = sum(weights)

        result_atms = []
        total_rev_change = 0.0
        allocated = 0

        for i, atm in enumerate(affected):
            if i == len(affected) - 1:
                txn_change = est_volume - allocated
            else:
                txn_change = round(est_volume * weights[i] / total_weight)
                txn_change = min(txn_change, est_volume - allocated)
                allocated += txn_change

            if scenario_type == "add":
                projected = max(0, atm["current_daily_transactions"] - txn_change)
                rev_change = -txn_change * atm["avg_fee"]
            else:
                projected = atm["current_daily_transactions"] + txn_change
                rev_change = txn_change * atm["avg_fee"]

            total_rev_change += rev_change
            result_atms.append({
                "atm_id": atm["atm_id"],
                "name": atm["name"],
                "distance_km": atm["distance_km"],
                "current_daily_transactions": atm["current_daily_transactions"],
                "projected_daily_transactions": projected,
                "projected_daily_revenue_change": round(rev_change, 3),
            })

        recommendations = []
        if scenario_type == "add":
            if abs(total_rev_change) > 50:
                recommendations.append(f"Significant revenue impact of {total_rev_change:.3f} BHD/day. Consider promotional campaigns for affected ATMs.")
            recommendations.append(f"{bank_name} ATM at ({latitude:.4f}, {longitude:.4f}) would affect {len(affected)} NeoBank ATMs.")
        else:
            if total_rev_change > 20:
                recommendations.append(f"Opportunity to capture {total_rev_change:.3f} BHD/day in additional revenue.")
            recommendations.append(f"Removal of {bank_name} ATM would benefit {len(affected)} NeoBank ATMs.")

        return {
            "scenario_type": scenario_type,
            "simulated_location": {"latitude": latitude, "longitude": longitude, "bank_name": bank_name},
            "affected_atms": result_atms,
            "summary": {
                "total_affected_atms": len(result_atms),
                "total_projected_revenue_change": round(total_rev_change, 3),
                "recommendations": recommendations,
            },
        }

    except Exception as e:
        logger.error("Error simulating scenario: %s", e)
        return {"error": f"Failed to simulate scenario: {str(e)}"}
