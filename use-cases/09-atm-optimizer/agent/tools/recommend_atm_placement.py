"""
MCP Tool: recommend_atm_placement

Recommend optimal locations for new NeoBank ATMs based on coverage gaps.
Access: Admin only

Placement Score = w1*gap_proximity + w2*competitor_density + w3*neobank_distance
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
    PLACEMENT_WEIGHTS,
)
from agent.tools._athena_queries import (
    haversine,
    query_atm_locations,
    query_competitor_locations,
)

logger = logging.getLogger(__name__)


def _assign_area(lat: float, lon: float) -> str:
    """Assign area name based on coordinates."""
    if 26.24 <= lat <= 26.30 and 50.60 <= lon <= 50.68:
        return "Muharraq"
    if 26.19 <= lat <= 26.27 and 50.53 <= lon <= 50.62:
        return "Capital"
    if 26.10 <= lat <= 26.24 and 50.44 <= lon <= 50.56:
        return "Northern"
    return "Southern"


def recommend_atm_placement(count: int = 3, radius_km: float = 2.0) -> dict:
    """Recommend optimal locations for new NeoBank ATMs.

    Args:
        count: Number of recommendations (default 3)
        radius_km: Analysis radius (default 2.0 km)

    Returns:
        Ranked placement recommendations with scores
    """
    try:
        if count <= 0:
            return {"error": "count must be a positive integer."}
        if radius_km <= 0:
            return {"error": "radius_km must be a positive number."}

        neobank = query_atm_locations()
        competitors = query_competitor_locations()

        if not neobank:
            return {"error": "No NeoBank ATM data available"}
        if not competitors:
            return {"error": "No competitor ATM data available"}

        active_competitors = [c for c in competitors if c.get("status") == "active"]

        # Find coverage gaps — competitor locations far from NeoBank
        candidates = []
        for comp in active_competitors:
            nearest_nb_dist = float("inf")
            nearest_nb_id = None
            for nb in neobank:
                d = haversine(comp["latitude"], comp["longitude"],
                              nb["latitude"], nb["longitude"])
                if d < nearest_nb_dist:
                    nearest_nb_dist = d
                    nearest_nb_id = nb["atm_id"]

            if nearest_nb_dist > radius_km:
                # Count competitors nearby this gap
                comp_count = sum(
                    1 for c2 in active_competitors
                    if haversine(comp["latitude"], comp["longitude"],
                                 c2["latitude"], c2["longitude"]) <= radius_km
                    and c2["competitor_atm_id"] != comp["competitor_atm_id"]
                )

                candidates.append({
                    "latitude": comp["latitude"],
                    "longitude": comp["longitude"],
                    "area": comp.get("area", _assign_area(comp["latitude"], comp["longitude"])),
                    "nearest_neobank_atm_id": nearest_nb_id,
                    "nearest_neobank_distance_km": nearest_nb_dist,
                    "competitor_count_in_radius": comp_count + 1,  # include the gap ATM itself
                })

        if not candidates:
            return {
                "recommendations": [],
                "summary": {"total_estimated_revenue_uplift_bhd": 0.0},
                "message": "No coverage gaps found. NeoBank has good coverage.",
            }

        # Score candidates
        w1 = PLACEMENT_WEIGHTS["gap_proximity"]
        w2 = PLACEMENT_WEIGHTS["competitor_density"]
        w3 = PLACEMENT_WEIGHTS["neobank_distance"]

        # Normalize factors
        max_comp = max(c["competitor_count_in_radius"] for c in candidates) or 1
        max_nb_dist = max(c["nearest_neobank_distance_km"] for c in candidates) or 1

        for c in candidates:
            gap_prox = 1.0  # All candidates are gaps, so proximity score is 1.0
            comp_density = c["competitor_count_in_radius"] / max_comp
            nb_distance = min(c["nearest_neobank_distance_km"] / max_nb_dist, 1.0)
            c["placement_score"] = round(
                min(1.0, w1 * gap_prox + w2 * comp_density + w3 * nb_distance), 4
            )

        # Sort by score descending
        candidates.sort(key=lambda x: x["placement_score"], reverse=True)

        # Deduplicate: skip candidates within 1 km of an already-selected one
        MIN_DISTANCE_KM = 1.0
        top: list[dict] = []
        for c in candidates:
            if len(top) >= count:
                break
            too_close = any(
                haversine(c["latitude"], c["longitude"],
                          sel["latitude"], sel["longitude"]) < MIN_DISTANCE_KM
                for sel in top
            )
            if not too_close:
                top.append(c)

        total_uplift = 0.0
        recommendations = []
        for rank, c in enumerate(top, 1):
            est_txns = round(ESTIMATED_COMPETITOR_DAILY_TXNS * c["placement_score"])
            est_rev = round(est_txns * 0.3, 3)  # avg fee ~0.3 BHD
            total_uplift += est_rev
            recommendations.append({
                "rank": rank,
                "latitude": round(c["latitude"], 6),
                "longitude": round(c["longitude"], 6),
                "area_name": c["area"],
                "placement_score": c["placement_score"],
                "nearest_neobank_atm_id": c["nearest_neobank_atm_id"],
                "nearest_neobank_distance_km": round(c["nearest_neobank_distance_km"], 3),
                "competitor_count_in_radius": c["competitor_count_in_radius"],
                "estimated_daily_transactions": est_txns,
            })

        return {
            "recommendations": recommendations,
            "summary": {
                "total_estimated_revenue_uplift_bhd": round(total_uplift, 3),
            },
        }

    except Exception as e:
        logger.error("Error recommending placement: %s", e)
        return {"error": f"Failed to recommend placement: {str(e)}"}
