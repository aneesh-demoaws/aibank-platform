"""
MCP Tool: query_coverage_analysis

Identify coverage gaps, advantages, and market share vs competitors.
Access: Operator + Admin

All data sourced from Athena (no CSV fallback).
"""

from __future__ import annotations

import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import DEFAULT_COMPETITOR_RADIUS_KM
from agent.tools._athena_queries import (
    haversine,
    query_atm_locations,
    query_competitor_locations,
)

logger = logging.getLogger(__name__)


def query_coverage_analysis(radius_km: float = 2.0) -> dict:
    """Identify coverage gaps, advantages, and market share.

    Args:
        radius_km: Radius for coverage analysis (default 2.0 km)

    Returns:
        Coverage gaps, advantages, market share by governorate
    """
    try:
        if radius_km <= 0:
            return {"error": "radius_km must be a positive number."}

        neobank = query_atm_locations()
        competitors = query_competitor_locations()

        if not neobank:
            return {"error": "No NeoBank ATM data available"}
        if not competitors:
            return {"error": "No competitor ATM data available"}

        active_competitors = [c for c in competitors if c.get("status") == "active"]

        # Coverage gaps: competitor ATMs with no NeoBank ATM within radius
        gaps = []
        for comp in active_competitors:
            nearest_dist = float("inf")
            nearest_id = None
            for nb in neobank:
                d = haversine(comp["latitude"], comp["longitude"],
                              nb["latitude"], nb["longitude"])
                if d < nearest_dist:
                    nearest_dist = d
                    nearest_id = nb["atm_id"]
            if nearest_dist > radius_km:
                gaps.append({
                    "competitor_atm_id": comp["competitor_atm_id"],
                    "bank_name": comp["bank_name"],
                    "latitude": comp["latitude"],
                    "longitude": comp["longitude"],
                    "area": comp.get("area", "Unknown"),
                    "nearest_neobank_atm_id": nearest_id,
                    "nearest_neobank_distance_km": round(nearest_dist, 3),
                })

        # Coverage advantages: NeoBank ATMs with no competitor within radius
        advantages = []
        for nb in neobank:
            nearest_dist = float("inf")
            for comp in active_competitors:
                d = haversine(nb["latitude"], nb["longitude"],
                              comp["latitude"], comp["longitude"])
                if d < nearest_dist:
                    nearest_dist = d
            if nearest_dist > radius_km:
                advantages.append({
                    "atm_id": nb["atm_id"],
                    "name": nb.get("name", nb["atm_id"]),
                    "nearest_competitor_distance_km": round(nearest_dist, 3),
                })

        # Market share by governorate
        gov_neobank = {}
        gov_competitor = {}
        for nb in neobank:
            area = nb.get("area", _assign_governorate(nb["latitude"], nb["longitude"]))
            gov_neobank[area] = gov_neobank.get(area, 0) + 1
        for comp in active_competitors:
            area = comp.get("area", "Unknown")
            gov_competitor[area] = gov_competitor.get(area, 0) + 1

        all_govs = set(list(gov_neobank.keys()) + list(gov_competitor.keys()))
        market_share = {}
        for gov in sorted(all_govs):
            nb_count = gov_neobank.get(gov, 0)
            comp_count = gov_competitor.get(gov, 0)
            total = nb_count + comp_count
            market_share[gov] = round((nb_count / total) * 100, 1) if total > 0 else 0.0

        total_nb = len(neobank)
        total_comp = len(active_competitors)
        overall = round((total_nb / (total_nb + total_comp)) * 100, 1) if (total_nb + total_comp) > 0 else 0.0

        return {
            "coverage_gaps": gaps,
            "coverage_advantages": advantages,
            "market_share": {
                "overall": overall,
                "by_governorate": market_share,
            },
            "summary": {
                "gap_count": len(gaps),
                "advantage_count": len(advantages),
                "overall_market_share": overall,
            },
            "radius_km": radius_km,
        }

    except Exception as e:
        logger.error("Error in coverage analysis: %s", e)
        return {"error": f"Failed to analyze coverage: {str(e)}"}


def _assign_governorate(lat: float, lon: float) -> str:
    """Assign a governorate based on coordinates."""
    if 26.24 <= lat <= 26.30 and 50.60 <= lon <= 50.68:
        return "Muharraq"
    if 26.19 <= lat <= 26.27 and 50.53 <= lon <= 50.62:
        return "Capital"
    if 26.10 <= lat <= 26.24 and 50.44 <= lon <= 50.56:
        return "Northern"
    return "Southern"
