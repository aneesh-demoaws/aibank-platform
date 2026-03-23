"""
Frontend configuration for ATM Profitability Optimizer Streamlit app.

Sensitive values (Cognito pool IDs, client IDs, API endpoints) are loaded
from environment variables — never hardcoded.
"""

from __future__ import annotations

import os

from agent.config import BANK_DISPLAY_NAME

# ---------------------------------------------------------------------------
# AWS Cognito (me-south-1) — Authentication
# ---------------------------------------------------------------------------
COGNITO_REGION = "me-south-1"

COGNITO_USER_POOL_ID = os.environ.get("ATM_COGNITO_USER_POOL_ID", "")
COGNITO_APP_CLIENT_ID = os.environ.get("ATM_COGNITO_APP_CLIENT_ID", "")

# Cognito group names for RBAC
COGNITO_ADMIN_GROUP = "admin"
COGNITO_OPERATOR_GROUP = "operator"

# Token validity
TOKEN_EXPIRY_MINUTES = 60
SESSION_TIMEOUT_MINUTES = 30

# ---------------------------------------------------------------------------
# AgentCore Runtime (eu-west-1) — Agent Invocation
# ---------------------------------------------------------------------------
AGENTCORE_RUNTIME_ARN = os.environ.get("ATM_AGENTCORE_RUNTIME_ARN", "")
AGENTCORE_RUNTIME_REGION = "eu-west-1"

# Legacy gateway endpoint (kept for reference, replaced by Runtime invoke)
AGENTCORE_GATEWAY_ENDPOINT = os.environ.get(
    "ATM_AGENTCORE_GATEWAY_ENDPOINT",
    "",
)

# ---------------------------------------------------------------------------
# Streamlit Page Configuration
# ---------------------------------------------------------------------------
PAGE_TITLE = f"{BANK_DISPLAY_NAME} ATM Profitability Optimizer"
PAGE_ICON = "🏧"
PAGE_LAYOUT = "wide"

# ---------------------------------------------------------------------------
# Map Visualization Defaults (Bahrain-centered)
# ---------------------------------------------------------------------------
MAP_CENTER_LAT = 26.2235
MAP_CENTER_LON = 50.5775
MAP_DEFAULT_ZOOM = 11
MAP_TILE_PROVIDER = "CartoDB Positron"

# ---------------------------------------------------------------------------
# Export Settings
# ---------------------------------------------------------------------------
EXPORT_CSV_ENCODING = "utf-8"
EXPORT_PDF_PAGE_SIZE = "A4"

# ---------------------------------------------------------------------------
# UI Feature Flags (role-based visibility)
# ---------------------------------------------------------------------------
ADMIN_FEATURES = [
    "maintenance_costs",
    "cash_optimization",
    "impact_analysis",
    "anomaly_detection",
    "profitability_ranking",
    "data_export",
    "user_management",
    "audit_logs",
    "competitor_analysis",
    "competitor_scenarios",
]

OPERATOR_FEATURES = [
    "atm_queries",
    "branch_proximity",
    "basic_revenue",
    "competitor_overview",
]


# ---------------------------------------------------------------------------
# Heatmap & Competitor Visualization Defaults
# ---------------------------------------------------------------------------
HEATMAP_DEFAULT_RADIUS = 15
HEATMAP_DEFAULT_BLUR = 10
HEATMAP_DEFAULT_MAX_ZOOM = 13


# ---------------------------------------------------------------------------
# Runtime Bank Alias Helpers (session-state aware)
# ---------------------------------------------------------------------------

def get_bank_name() -> str:
    """Return the active bank display name from SSM Parameter Store.

    Checks ``st.session_state["bank_display_name"]`` first (set by the
    Settings tab dropdown), then falls back to SSM / default.
    """
    try:
        import streamlit as st
        from agent.bank_alias import get_bank_alias
        return st.session_state.get("bank_display_name", get_bank_alias())
    except Exception:
        from agent.bank_alias import get_bank_alias
        return get_bank_alias()


def get_excluded_banks() -> list[str]:
    """Return banks to exclude from competitor queries.

    The excluded bank matches the currently selected bank alias.
    """
    try:
        import streamlit as st
        selected = st.session_state.get("bank_display_name", "")
        if selected:
            return [selected]
        from agent.bank_alias import get_excluded_banks as _get
        return _get()
    except Exception:
        from agent.bank_alias import get_excluded_banks as _get
        return _get()
