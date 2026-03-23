"""
MCP Tool: query_cash_levels

Query current cash levels and generate 7-day forecast.
Access: Admin only

All queries go through AthenaClient → Athena → S3 in me-south-1.

Returns: current_balance, forecast_7day, replenishment_recommendation
"""

import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools._athena_queries import query_cash_levels as athena_query_cash_levels

logger = logging.getLogger(__name__)


def query_cash_levels(atm_id: str) -> dict:
    """Query current and forecasted cash levels.

    Args:
        atm_id: ATM identifier

    Returns:
        dict with current_balance, forecast_7day, replenishment_recommendation
    """
    try:
        records = athena_query_cash_levels(atm_id=atm_id)

        if not records:
            return {
                "atm_id": atm_id,
                "error": f"No cash level data found for {atm_id}",
            }

        records.sort(key=lambda r: r["date"])

        latest = records[-1]
        current_balance = latest["closing_balance"]

        recent = records[-30:] if len(records) >= 30 else records
        avg_daily_withdrawal = sum(r["total_withdrawals"] for r in recent) / len(recent)

        dow_withdrawals: dict[int, list[float]] = defaultdict(list)
        for r in recent:
            dt = datetime.strptime(r["date"], "%Y-%m-%d")
            dow_withdrawals[dt.weekday()].append(r["total_withdrawals"])

        dow_avg = {}
        for dow, vals in dow_withdrawals.items():
            dow_avg[dow] = sum(vals) / len(vals)

        forecast = []
        projected_balance = current_balance
        last_date = datetime.strptime(latest["date"], "%Y-%m-%d")

        for day_offset in range(1, 8):
            forecast_date = last_date + timedelta(days=day_offset)
            dow = forecast_date.weekday()
            expected_withdrawal = dow_avg.get(dow, avg_daily_withdrawal)
            projected_balance -= expected_withdrawal
            forecast.append({
                "date": forecast_date.strftime("%Y-%m-%d"),
                "projected_balance": round(projected_balance, 3),
                "expected_withdrawal": round(expected_withdrawal, 3),
            })

        min_projected = min(f["projected_balance"] for f in forecast)
        needs_replenishment = min_projected < 0
        days_until_empty = None
        for i, f in enumerate(forecast):
            if f["projected_balance"] <= 0:
                days_until_empty = i + 1
                break

        recommendation = {
            "needs_replenishment": needs_replenishment,
            "days_until_empty": days_until_empty,
            "suggested_amount": round(avg_daily_withdrawal * 7, 3) if needs_replenishment else 0.0,
        }

        return {
            "atm_id": atm_id,
            "current_balance": round(current_balance, 3),
            "avg_daily_withdrawal": round(avg_daily_withdrawal, 3),
            "forecast_7day": forecast,
            "replenishment_recommendation": recommendation,
            "currency": "BHD",
        }

    except Exception as e:
        logger.error("Error querying cash levels for %s: %s", atm_id, e)
        return {"error": f"Failed to query cash levels: {str(e)}"}
