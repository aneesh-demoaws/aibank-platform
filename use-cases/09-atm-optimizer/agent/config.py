"""
Agent configuration for ATM Profitability Optimizer.

All sensitive values (bucket names, database names, endpoints) are loaded
from environment variables. Region settings are constants since they are
part of the architectural design, not secrets.
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Regional Architecture
# ---------------------------------------------------------------------------
# Data layer — all banking data stays in Bahrain
DATA_REGION = "me-south-1"

# AI services — Bedrock AgentCore runs in Ireland
AI_REGION = "eu-west-1"

# ---------------------------------------------------------------------------
# S3 Data Lake (me-south-1)
# ---------------------------------------------------------------------------
S3_DATA_BUCKET = os.environ.get(
    "ATM_S3_DATA_BUCKET",
    "atm-optimizer-data-me-south-1",
)
S3_RESULTS_PREFIX = "athena_results/"

# S3 data prefixes
S3_PREFIXES = {
    "transactions": "atm_transactions/",
    "atm_locations": "atm_locations/",
    "branch_locations": "branch_locations/",
    "proximity": "proximity_data/",
    "maintenance": "maintenance_costs/",
    "cash_levels": "cash_levels/",
    "competitor_atm_locations": "competitor_atm_locations/",
    "competitor_proximity": "competitor_proximity/",
}

# ---------------------------------------------------------------------------
# Amazon Athena (me-south-1)
# ---------------------------------------------------------------------------
ATHENA_DATABASE = os.environ.get("ATM_ATHENA_DATABASE", "atm_optimizer")
ATHENA_OUTPUT_LOCATION = f"s3://{S3_DATA_BUCKET}/{S3_RESULTS_PREFIX}"
ATHENA_QUERY_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# Bedrock AgentCore (eu-west-1)
# ---------------------------------------------------------------------------
AGENTCORE_RUNTIME_MEMORY_MB = 2048
AGENTCORE_RUNTIME_TIMEOUT_SECONDS = 60

# ---------------------------------------------------------------------------
# Model Configuration
# ---------------------------------------------------------------------------
MODEL_ID = os.environ.get(
    "ATM_MODEL_ID",
    "anthropic.claude-sonnet-4-20250514-v1:0",
)
MODEL_REGION = AI_REGION
MODEL_MAX_TOKENS = 4096
MODEL_TEMPERATURE = 0.1  # Low temperature for factual banking analysis

# ---------------------------------------------------------------------------
# MCP Tool Settings
# ---------------------------------------------------------------------------
MCP_TOOL_TIMEOUT_SECONDS = 10
MCP_CACHE_TTL_SECONDS = 300  # 5-minute cache for frequently accessed data
MCP_MAX_RETRIES = 3
MCP_RETRY_BACKOFF_BASE = 2  # Exponential backoff base in seconds

# ---------------------------------------------------------------------------
# Role-Based Access Control
# ---------------------------------------------------------------------------
OPERATOR_TOOLS = [
    "query_atm_data",
    "query_branch_proximity",
    "query_revenue_data",
    "query_competitor_analysis",
    "query_coverage_analysis",
]

ADMIN_TOOLS = OPERATOR_TOOLS + [
    "query_maintenance_costs",
    "query_cash_levels",
    "calculate_impact_analysis",
    "detect_anomalies",
    "profitability_ranking",
    "simulate_competitor_scenario",
    "recommend_atm_placement",
]

# ---------------------------------------------------------------------------
# Geographic Bounds — Bahrain
# ---------------------------------------------------------------------------
BAHRAIN_LAT_MIN = 25.5
BAHRAIN_LAT_MAX = 26.3
BAHRAIN_LON_MIN = 50.4
BAHRAIN_LON_MAX = 50.8
BAHRAIN_MAX_DISTANCE_KM = 60  # Maximum extent of Bahrain

# Default proximity search radius
DEFAULT_PROXIMITY_RADIUS_KM = 5.0

# ---------------------------------------------------------------------------
# Competitor Analysis
# ---------------------------------------------------------------------------
COMPETITION_INDEX_NORM_FACTOR = 5.0
DEFAULT_COMPETITOR_RADIUS_KM = 2.0
ESTIMATED_COMPETITOR_DAILY_TXNS = 150
PLACEMENT_WEIGHTS = {"gap_proximity": 0.4, "competitor_density": 0.3, "neobank_distance": 0.3}
IMPACT_THRESHOLDS = {"low": 0.05, "medium": 0.15}

# ---------------------------------------------------------------------------
# Bank Aliasing — centralized via SSM Parameter Store
# ---------------------------------------------------------------------------
# Import from the SSM-backed module. Both Streamlit and AgentCore read from
# the same SSM parameter /atm-optimizer/bank-alias in me-south-1.
from agent.bank_alias import (  # noqa: E402
    AVAILABLE_BANKS,
    get_bank_alias,
    get_excluded_banks,
    set_bank_alias,
)

# Convenience constant — reads from SSM (cached 60s)
BANK_DISPLAY_NAME = get_bank_alias()

# Backward compat — reads from SSM
EXCLUDED_COMPETITOR_BANKS = get_excluded_banks()
