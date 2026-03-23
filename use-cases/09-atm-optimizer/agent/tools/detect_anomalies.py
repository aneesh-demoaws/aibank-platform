"""
MCP Tool: detect_anomalies

Detect ATMs with transaction volumes deviating >2 standard deviations from expected.
Access: Admin only

All queries go through AthenaClient -> Athena -> S3 in me-south-1.

Performance: Uses pre-aggregated daily_atm_stats table (5,152 rows) instead of
scanning full atm_transactions (~1M rows). Reduces query time from ~180s to <2s.

Returns: list of {atm_id, anomaly_type, deviation, impact}
"""

from __future__ import annotations

import logging
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools._athena_queries import query_daily_txn_stats

logger = logging.getLogger(__name__)

# Anomaly threshold: >2 standard deviations
ANOMALY_THRESHOLD_STDEV = 2.0


def detect_anomalies(atm_id: str = None, period: str = "30d") -> list:
    """Detect anomalies in ATM performance.

    Args:
        atm_id: Optional ATM identifier. If None, checks all ATMs.
        period: Analysis period - '7d', '30d', '90d'

    Returns:
        list of dicts with atm_id, anomaly_type, deviation, impact
    """
    try:
        period_days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 30)

        # query_daily_txn_stats uses pre-aggregated daily_atm_stats table
        # and returns only anomalous days (deviation > 2.0) with stats
        rows = query_daily_txn_stats(atm_id=atm_id, period_days=period_days)

        anomalies = []
        for r in rows:
            deviation = (r["txn_count"] - r["mean_count"]) / r["stdev_count"]
            anomaly_type = "high_volume" if deviation > 0 else "low_volume"

            # Estimated impact: excess transactions * average amount per transaction
            avg_per_txn = r["mean_amount"] / r["mean_count"] if r["mean_count"] > 0 else 0
            impact = abs(r["txn_count"] - r["mean_count"]) * avg_per_txn

            anomalies.append({
                "atm_id": r["atm_id"],
                "date": r["day"],
                "anomaly_type": anomaly_type,
                "metric": "transaction_count",
                "value": r["txn_count"],
                "mean": round(r["mean_count"], 1),
                "std_dev": round(r["stdev_count"], 1),
                "deviation": round(deviation, 2),
                "estimated_impact_bhd": round(impact, 3),
            })

        # Sort by absolute deviation descending (most anomalous first)
        anomalies.sort(key=lambda a: abs(a["deviation"]), reverse=True)

        return anomalies

    except Exception as e:
        logger.error("Error detecting anomalies: %s", e)
        return [{"error": f"Failed to detect anomalies: {str(e)}"}]
