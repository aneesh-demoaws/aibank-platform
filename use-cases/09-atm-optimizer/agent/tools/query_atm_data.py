"""
MCP Tool: query_atm_data

Query ATM transaction summary for a specified ATM and date range.
Access: Operator + Admin

All queries go through AthenaClient → Athena → S3 in me-south-1.

Returns: transaction_count, total_amount, avg_daily_txns, revenue
"""

import logging
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools._athena_queries import query_transactions

logger = logging.getLogger(__name__)


def query_atm_data(atm_id: str, start_date: str, end_date: str) -> dict:
    """Query ATM transaction summary for specified ATM and date range.

    Args:
        atm_id: ATM identifier (e.g., ATM_SEEF_01)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

    Returns:
        dict with transaction_count, total_amount, avg_daily_txns, revenue
    """
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        if end_dt < start_dt:
            return {"error": "end_date must be after start_date"}

        num_days = max((end_dt - start_dt).days + 1, 1)

        txns = query_transactions(atm_id=atm_id, start_date=start_date, end_date=end_date)

        if not txns:
            return {
                "atm_id": atm_id,
                "start_date": start_date,
                "end_date": end_date,
                "transaction_count": 0,
                "total_amount": 0.0,
                "avg_daily_txns": 0.0,
                "revenue": 0.0,
                "currency": "BHD",
            }

        total_amount = sum(t["amount"] for t in txns)
        total_fees = sum(t["fee"] for t in txns)
        txn_count = len(txns)

        return {
            "atm_id": atm_id,
            "start_date": start_date,
            "end_date": end_date,
            "transaction_count": txn_count,
            "total_amount": round(total_amount, 3),
            "avg_daily_txns": round(txn_count / num_days, 1),
            "revenue": round(total_fees, 3),
            "currency": "BHD",
        }

    except Exception as e:
        logger.error("Error querying ATM data for %s: %s", atm_id, e)
        return {"error": f"Failed to query ATM data: {str(e)}"}
