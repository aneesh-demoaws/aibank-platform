"""
MCP Tool: query_branch_proximity

Find nearby ATMs and branches within a given radius using haversine distances.
Access: Operator + Admin

All queries go through AthenaClient → Athena → S3 in me-south-1.

Returns: list of {atm_id, name, distance_km, capacity_utilization}
"""

import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import DEFAULT_PROXIMITY_RADIUS_KM
from agent.tools._athena_queries import haversine, query_atm_locations, query_branch_locations

logger = logging.getLogger(__name__)


def query_branch_proximity(atm_id: str, radius_km: float = DEFAULT_PROXIMITY_RADIUS_KM) -> list:
    """Find nearby ATMs and branches within radius.

    Args:
        atm_id: Source ATM identifier
        radius_km: Search radius in kilometers (default 5.0)

    Returns:
        list of dicts with atm_id/branch_id, name, distance_km, type, capacity_utilization
    """
    try:
        atm_locations = query_atm_locations()
        branch_locations = query_branch_locations()

        source = None
        for loc in atm_locations:
            if loc["atm_id"] == atm_id:
                source = loc
                break

        if source is None:
            return [{"error": f"ATM {atm_id} not found"}]

        nearby = []

        for loc in atm_locations:
            if loc["atm_id"] == atm_id:
                continue
            dist = haversine(source["latitude"], source["longitude"],
                             loc["latitude"], loc["longitude"])
            if dist <= radius_km:
                nearby.append({
                    "id": loc["atm_id"],
                    "name": loc["name"],
                    "type": "atm",
                    "location_type": loc["location_type"],
                    "distance_km": round(dist, 3),
                    "daily_capacity": loc["daily_capacity"],
                    "status": loc["status"],
                })

        for br in branch_locations:
            dist = haversine(source["latitude"], source["longitude"],
                             br["latitude"], br["longitude"])
            if dist <= radius_km:
                nearby.append({
                    "id": br["branch_id"],
                    "name": br["name"],
                    "type": "branch",
                    "distance_km": round(dist, 3),
                    "atm_count": br["atm_count"],
                    "avg_daily_footfall": br["avg_daily_footfall"],
                })

        nearby.sort(key=lambda x: x["distance_km"])
        return nearby

    except Exception as e:
        logger.error("Error querying proximity for %s: %s", atm_id, e)
        return [{"error": f"Failed to query proximity: {str(e)}"}]
