"""
Main Streamlit application for the NeoBank ATM Profitability Optimizer.

Integrates authentication, chat, and tabbed content layout with
Architecture, Data Analytics, FAQs, Memory, and Map tabs.

Run with:  streamlit run frontend/app.py --server.port 8503

Validates: Requirements 8.1-8.5, 10.5-10.8, 17.8
"""

from __future__ import annotations

import logging

import streamlit as st

from frontend.auth import (
    ensure_authenticated,
    get_current_role,
    has_feature_access,
    logout,
    render_login_form,
)
from frontend.components.chat import clear_chat, render_chat, _process_prompt
from frontend.components.export import render_export
from frontend.components.map_view import render_map
from frontend.components.tabs import (
    render_architecture,
    render_database,
    render_faqs,
    render_memory,
)
from agent.tools._athena_queries import query_competitor_locations
from agent.bank_alias import AVAILABLE_BANKS, get_bank_alias, set_bank_alias
from frontend.config import (
    PAGE_ICON,
    PAGE_LAYOUT,
    PAGE_TITLE,
    get_bank_name,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page configuration (must be the first Streamlit command)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=PAGE_TITLE,
    page_icon=PAGE_ICON,
    layout=PAGE_LAYOUT,
)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _render_sidebar() -> None:
    """Render sidebar with user info, admin tools, and quick actions."""
    role = get_current_role()
    username = st.session_state.get("username", "User")

    with st.sidebar:
        st.markdown(f"### {PAGE_ICON} {get_bank_name()} ATM Optimizer")
        st.divider()

        # User info
        role_badge = "🔑 Admin" if role == "admin" else "👤 Operator"
        st.markdown(f"**{username}** &nbsp; {role_badge}")
        st.divider()

        # Admin-only quick actions
        if role == "admin":
            st.markdown("**Admin Tools**")

            if has_feature_access("impact_analysis"):
                if st.button("📊 Impact Analysis", use_container_width=True):
                    st.session_state["pending_query"] = (
                        "Run an impact analysis on the ATM network. "
                        "Which ATMs are at risk if any go offline?"
                    )
                    st.rerun()

            if has_feature_access("anomaly_detection"):
                if st.button("🔍 Anomaly Detection", use_container_width=True):
                    st.session_state["pending_query"] = (
                        "Detect anomalies across the ATM network. "
                        "Are there any unusual patterns in transactions, cash levels, or maintenance?"
                    )
                    st.rerun()

            if has_feature_access("cash_optimization"):
                if st.button("💰 Cash Optimization", use_container_width=True):
                    st.session_state["pending_query"] = (
                        "Analyze cash levels across all ATMs and recommend optimal cash fill "
                        "amounts to minimize costs while avoiding cash-outs."
                    )
                    st.rerun()

            if has_feature_access("profitability_ranking"):
                if st.button("📈 Profitability Ranking", use_container_width=True):
                    st.session_state["pending_query"] = (
                        "Rank all ATMs by profitability. "
                        "Show revenue, costs, and net profit for each ATM."
                    )
                    st.rerun()

            if has_feature_access("data_export"):
                if st.button("📥 Export Data", use_container_width=True):
                    st.session_state["show_export"] = not st.session_state.get("show_export", False)
                    st.rerun()

                # Render export panel directly in sidebar
                if st.session_state.get("show_export"):
                    render_export()

            st.divider()

        # Actions
        if st.button("✨ New Session", use_container_width=True):
            clear_chat()
            st.toast("New session started", icon="✨")
            st.rerun()

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🗑️ Clear Chat", use_container_width=True):
                clear_chat()
                st.rerun()
        with col2:
            if st.button("🚪 Logout", use_container_width=True):
                logout()
                st.rerun()

        # Operator info
        if role == "operator":
            st.info(
                "You have Operator access. Some features like impact analysis, "
                "anomaly detection, and data export require Admin privileges."
            )


# ---------------------------------------------------------------------------
# Settings (admin-only)
# ---------------------------------------------------------------------------

def _render_settings() -> None:
    """Render admin settings page with bank alias configuration."""
    st.title("⚙️ Settings")
    st.caption("Configure the ATM Optimizer for different bank demos")

    st.header("🏦 Bank Alias Configuration")
    st.markdown(
        "Select which bank this optimizer represents. "
        "The selected bank will be excluded from competitor analysis. "
        "Changes are saved to AWS Parameter Store and take effect across "
        "all components (UI, Agent, Athena queries)."
    )

    # Initialize session state from SSM
    if "bank_display_name" not in st.session_state:
        st.session_state["bank_display_name"] = get_bank_alias()

    current = st.session_state["bank_display_name"]
    idx = AVAILABLE_BANKS.index(current) if current in AVAILABLE_BANKS else 0

    selected = st.selectbox(
        "Bank Name",
        options=AVAILABLE_BANKS,
        index=idx,
        help="Choose the bank to demo. Saved to SSM Parameter Store — both UI and AgentCore read from it.",
    )

    if selected != current:
        success = set_bank_alias(selected)
        if success:
            st.session_state["bank_display_name"] = selected
            st.toast(f"Bank alias updated to **{selected}**", icon="✅")
            st.rerun()
        else:
            st.error("Failed to save bank alias to Parameter Store. Check IAM permissions.")

    st.divider()

    st.markdown(f"""
**Current Configuration:**
- Display name: **{get_bank_name()}**
- Excluded from competitors: **{get_bank_name()}**
- SSM Parameter: `/atm-optimizer/bank-alias`
- Region: me-south-1
    """)

    st.info(
        "Changes are persisted to AWS SSM Parameter Store. "
        "The AgentCore Runtime reads the same parameter, so the agent's "
        "system prompt will use the updated bank name within 60 seconds."
    )


# ---------------------------------------------------------------------------
# Main content with tabs
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def _cached_competitor_locations() -> list:
    """Cache competitor locations for 5 minutes to avoid redundant Athena queries."""
    try:
        return query_competitor_locations()
    except Exception:
        logger.warning("Failed to load competitor locations for map")
        return []


def _render_main_content() -> None:
    """Render the tabbed content layout."""
    role = get_current_role()
    tab_labels = [
        "💬 Ask the ATM Optimizer",
        "🗺️ Map",
        "🏗️ Architecture",
        "📊 Data Analytics",
        "❓ FAQs",
        "🧠 Memory",
    ]
    if role == "admin":
        tab_labels.append("⚙️ Settings")

    tabs = st.tabs(tab_labels)
    tab_chat, tab_map, tab_arch, tab_db, tab_faq, tab_memory = tabs[:6]

    with tab_chat:
        render_chat()

    with tab_map:
        # Fetch competitor ATM locations from Athena (cached 5 min)
        competitors = _cached_competitor_locations()
        show_heatmap = st.checkbox(
            "Show competitor density heatmap",
            value=False,
            key="map_heatmap_toggle",
        )
        render_map(
            competitor_locations=competitors or None,
            show_competitor_heatmap=show_heatmap,
        )

    with tab_arch:
        render_architecture()

    with tab_db:
        render_database()

    with tab_faq:
        render_faqs()

    with tab_memory:
        render_memory()

    if role == "admin":
        with tabs[6]:
            _render_settings()

    # Chat input at page level (outside tabs) — stays pinned to bottom
    prompt = st.chat_input("Ask about ATM performance, downtime impact, or profitability…")
    if prompt:
        _process_prompt(prompt)


# ---------------------------------------------------------------------------
# App entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Application entry point."""
    # Authentication gate
    if not ensure_authenticated():
        render_login_form()
        return

    # Authenticated — render full app
    _render_sidebar()
    _render_main_content()


if __name__ == "__main__":
    main()
else:
    # When Streamlit imports the module directly
    main()
