"""
MCP Tool: query_competitor_analysis

Calculate Competition Index for NeoBank ATMs based on nearby competitor ATMs.
Access: Operator + Admin

Competition Index = min(1.0, sum(1/distance for competitors within radius) / NORM_FACTOR)

Performance optimization:
  - "All ATMs" case (no atm_id): uses pre-aggregated `competition_index` table (28 rows, sub-second)
  - "Single ATM" case (with atm_id): uses `competitor_proximity` table with WHERE filter
  - All data sourced from Athena (no CSV fallback)
"""

from __future__ import annotations

import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import COMPETITION_INDEX_NORM_FACTOR, DEFAULT_COMPETITOR_RADIUS_KM
from agent.tools._athena_queries import (
    query_atm_locations,
    query_competition_index,
    query_competitor_locations,
    query_competitor_proximity,
)

logger = logging.getLogger(__name__)


def query_competitor_analysis(atm_id: str | None = None, radius_km: float = 2.0) -> dict:
    """Calculate Competition Index for NeoBank ATMs.

    Args:
        atm_id: Optional specific ATM to analyze
        radius_km: Search radius in km (default 2.0)

    Returns:
        Competition Index data for one or all ATMs
    """
    try:
        if radius_km <= 0:
            return {"error": "radius_km must be a positive number."}

        if atm_id:
            # --- Single ATM: use competitor_proximity with WHERE filter ---
            atm_locations = query_atm_locations(atm_id=atm_id)
            if not atm_locations:
                return {"error": f"ATM ID not found: {atm_id}"}

            atm = atm_locations[0]
            proximity_data = query_competitor_proximity(neobank_atm_id=atm_id)

            # Build competitor status lookup
            comp_locations = query_competitor_locations()
            comp_status_map = {c["competitor_atm_id"]: c.get("status", "active") for c in comp_locations}

            nearby = [p for p in proximity_data
                      if p.get("neobank_atm_id") == atm_id and p["distance_km"] <= radius_km]
            ci_sum = sum(1.0 / p["distance_km"] for p in nearby if p["distance_km"] > 0)
            ci = min(1.0, ci_sum / COMPETITION_INDEX_NORM_FACTOR)

            return {
                "atm_id": atm_id,
                "name": atm.get("name", atm_id),
                "competition_index": round(ci, 4),
                "competitor_count": len(nearby),
                "radius_km": radius_km,
                "nearby_competitors": [
                    {
                        "competitor_atm_id": p["competitor_atm_id"],
                        "bank_name": p["bank_name"],
                        "distance_km": round(p["distance_km"], 3),
                        "status": comp_status_map.get(p["competitor_atm_id"], "active"),
                    }
                    for p in sorted(nearby, key=lambda x: x["distance_km"])
                ],
            }
        else:
            # --- All ATMs at default radius: use pre-aggregated competition_index table ---
            if radius_km == DEFAULT_COMPETITOR_RADIUS_KM:
                ci_rows = query_competition_index()
                results = []
                for r in ci_rows:
                    results.append({
                        "atm_id": r["atm_id"],
                        "name": r["name"],
                        "competition_index": round(r["competition_index"], 4),
                        "competitor_count": r["competitor_count_2km"],
                    })
                results.sort(key=lambda x: x["competition_index"], reverse=True)
                return {"atms": results, "radius_km": radius_km}

            # --- All ATMs at non-default radius: compute from proximity data ---
            atm_locations = query_atm_locations()
            if not atm_locations:
                return {"error": "No ATM location data available"}

            proximity_data = query_competitor_proximity()

            results = []
            for atm in atm_locations:
                aid = atm["atm_id"]
                nearby = [p for p in proximity_data
                          if p.get("neobank_atm_id") == aid and p["distance_km"] <= radius_km]
                ci_sum = sum(1.0 / p["distance_km"] for p in nearby if p["distance_km"] > 0)
                ci = min(1.0, ci_sum / COMPETITION_INDEX_NORM_FACTOR)
                results.append({
                    "atm_id": aid,
                    "name": atm.get("name", aid),
                    "competition_index": round(ci, 4),
                    "competitor_count": len(nearby),
                })

            results.sort(key=lambda x: x["competition_index"], reverse=True)
            return {"atms": results, "radius_km": radius_km}

    except Exception as e:
        logger.error("Error in competitor analysis: %s", e)
        return {"error": f"Failed to analyze competition: {str(e)}"}
