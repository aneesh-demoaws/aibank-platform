"""
MCP Tool: query_maintenance_costs

Query maintenance cost history with type breakdown.
Access: Admin only

All queries go through AthenaClient → Athena → S3 in me-south-1.

Returns: total_cost, breakdown_by_type, downtime_hours
"""

import logging
import os
import sys
from collections import defaultdict
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools._athena_queries import query_maintenance

logger = logging.getLogger(__name__)


def query_maintenance_costs(atm_id: str, start_date: str, end_date: str) -> dict:
    """Query maintenance cost history.

    Args:
        atm_id: ATM identifier
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        dict with total_cost, breakdown_by_type, downtime_hours, records
    """
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        if end_dt < start_dt:
            return {"error": "end_date must be after start_date"}

        records = query_maintenance(atm_id=atm_id, start_date=start_date, end_date=end_date)

        if not records:
            return {
                "atm_id": atm_id,
                "start_date": start_date,
                "end_date": end_date,
                "total_cost": 0.0,
                "breakdown_by_type": {},
                "total_downtime_hours": 0.0,
                "event_count": 0,
                "currency": "BHD",
            }

        total_cost = sum(r["cost"] for r in records)
        total_downtime = sum(r["downtime_hours"] for r in records)

        breakdown: dict[str, dict] = defaultdict(lambda: {"cost": 0.0, "count": 0, "downtime_hours": 0.0})
        for r in records:
            mtype = r["maintenance_type"]
            breakdown[mtype]["cost"] += r["cost"]
            breakdown[mtype]["count"] += 1
            breakdown[mtype]["downtime_hours"] += r["downtime_hours"]

        for mtype in breakdown:
            breakdown[mtype]["cost"] = round(breakdown[mtype]["cost"], 3)
            breakdown[mtype]["downtime_hours"] = round(breakdown[mtype]["downtime_hours"], 1)

        return {
            "atm_id": atm_id,
            "start_date": start_date,
            "end_date": end_date,
            "total_cost": round(total_cost, 3),
            "breakdown_by_type": dict(breakdown),
            "total_downtime_hours": round(total_downtime, 1),
            "event_count": len(records),
            "currency": "BHD",
        }

    except Exception as e:
        logger.error("Error querying maintenance costs for %s: %s", atm_id, e)
        return {"error": f"Failed to query maintenance costs: {str(e)}"}
