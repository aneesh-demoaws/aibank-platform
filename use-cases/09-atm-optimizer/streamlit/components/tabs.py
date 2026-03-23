"""
Tab components for the ATM Profitability Optimizer.

Provides Architecture, Data Analytics, FAQs, and Memory tabs
similar to the Bank ABC reference application.
"""

from __future__ import annotations

import os
import uuid

import pandas as pd
import streamlit as st

from frontend.config import get_bank_name

# Path to pre-generated diagram images
_ASSETS_DIR = os.path.join(os.path.dirname(__file__), '..', 'assets')


# ---------------------------------------------------------------------------
# 🏗️ Architecture Tab
# ---------------------------------------------------------------------------


def render_architecture() -> None:
    """Render the technical architecture page."""
    st.title("🏗️ Technical Architecture")
    st.caption(f"{get_bank_name()} ATM Profitability Optimizer — System Design & Components")

    # Architecture Diagram — Pre-generated PNG
    st.header("System Architecture")
    arch_img = os.path.join(_ASSETS_DIR, 'architecture_diagram.png')
    if os.path.exists(arch_img):
        st.image(arch_img, use_container_width=True)
    else:
        st.info("Architecture diagram not found. Run `python gen_diagrams.py` to generate.")

    # Component Details
    st.header("Component Details")

    c1, c2 = st.columns(2)

    with c1:
        st.subheader("🗄️ Data Layer (me-south-1 — Bahrain)")
        st.markdown("""
**Amazon S3 (Parquet Data Lake)**
- Format: Apache Parquet (columnar, compressed)
- Encryption: AWS KMS (SSE-KMS with BucketKey)
- 6 datasets: ATM locations, transactions, branches, proximity, maintenance, cash levels
- ~1M transaction records across 28 ATMs

**Amazon Athena**
- Workgroup: `atm-optimizer`
- Database: `atm_optimizer` (6 Glue tables)
- Pre-aggregated tables for sub-150ms query performance
- Results encrypted with KMS

**Amazon Cognito**
- User Pool with MFA (TOTP)
- Groups: `admin` (8 tools), `operator` (3 tools)
- JWT-based authentication
        """)

        st.subheader("🔧 Lambda MCP Server (eu-west-1)")
        st.markdown("""
**8 MCP Tools exposed via AgentCore Gateway:**

| Tool | Access |
|------|--------|
| `query_atm_data` | All |
| `query_branch_proximity` | All |
| `query_revenue_data` | All |
| `query_maintenance_costs` | Admin |
| `query_cash_levels` | Admin |
| `calculate_impact_analysis` | Admin |
| `detect_anomalies` | Admin |
| `profitability_ranking` | Admin |
        """)

    with c2:
        st.subheader("🧠 AI Layer (eu-west-1 — Ireland)")
        st.markdown("""
**Strands Agent on AgentCore Runtime**
- Model: Claude Sonnet 4 (`eu.anthropic.claude-sonnet-4-20250514-v1:0`)
- Framework: Strands Agents SDK
- Deployment: AgentCore Runtime (containerized, ARM64)
- Auth: IAM (SigV4 signed requests)

**AgentCore Gateway**
- Protocol: MCP (Model Context Protocol)
- Auth: AWS IAM
- Routes tool calls to Lambda MCP Server

**AgentCore Memory**
- Short-term: Turn-by-turn conversation context
- Long-term: Facts, preferences, session summaries
- Strategies: SessionSummarizer, PreferenceLearner, FactExtractor
        """)

        st.subheader("🔐 Security Architecture")
        st.markdown("""
| Layer | Mechanism |
|-------|-----------|
| Frontend → AgentCore | IAM Instance Profile |
| AgentCore → Gateway | IAM SigV4 |
| Gateway → Lambda | IAM Resource Policy |
| Lambda → Athena | IAM Role |
| Athena → S3 | KMS Encryption |
| User Auth | Cognito + MFA |

**Zero hardcoded credentials** — all auth via IAM roles.
        """)

    # Cross-Region Design
    st.header("Cross-Region Design")
    cr1, cr2 = st.columns(2)
    with cr1:
        st.markdown("""
**Why two regions?**
- 🏦 **me-south-1 (Bahrain)**: Data residency — all banking data stays in-region
- 🧠 **eu-west-1 (Ireland)**: AI models — Claude Sonnet 4 + AgentCore available here

**Data never leaves Bahrain** — only query results (JSON) cross the region boundary.
Raw Parquet data stays in S3 me-south-1. Athena processes queries in me-south-1.
        """)
    with cr2:
        st.markdown("""
**Performance Profile**

| Hop | Latency |
|-----|---------|
| Lambda MCP Tool | ~80-150ms |
| Athena Query (Parquet) | ~50-100ms |
| LLM Reasoning (Claude) | ~8-15s |
| **Total E2E** | **~10-20s** |

LLM reasoning is 85-90% of total time. Cross-region overhead is negligible.
        """)

    # Data Flow
    st.header("End-to-End Data Flow")
    st.code(
        "User → Streamlit (me-south-1) → AgentCore Runtime (eu-west-1)\n"
        "  → Strands Agent [Claude Sonnet 4 reasoning + tool selection]\n"
        "  → AgentCore Gateway (eu-west-1) → Lambda MCP Server (eu-west-1)\n"
        "  → Athena (me-south-1) → S3 Parquet (me-south-1)\n"
        "  → Response back through chain",
        language="text",
    )

    # Tech Stack
    st.header("Technology Stack")
    t1, t2, t3 = st.columns(3)
    with t1:
        st.markdown("""
**AWS Services**
- Amazon Bedrock (Claude Sonnet 4)
- Bedrock AgentCore Runtime
- AgentCore Gateway (MCP)
- AgentCore Memory (STM + LTM)
- AWS Lambda (Python 3.12)
- Amazon Athena + Glue
- Amazon S3 (Parquet)
- Amazon Cognito
- Amazon EC2
- AWS KMS
        """)
    with t2:
        st.markdown("""
**Frameworks**
- Strands Agents SDK
- Model Context Protocol (MCP)
- Streamlit
- Apache Parquet
- boto3 (AWS SDK)
        """)
    with t3:
        st.markdown("""
**Patterns**
- Agentic AI (tool-use loop)
- MCP (standardized tool interface)
- Cross-region data sovereignty
- Pre-aggregated analytics
- Role-based tool filtering
- IAM-everywhere (zero secrets)
        """)


# ---------------------------------------------------------------------------
# 📊 Data Analytics Tab
# ---------------------------------------------------------------------------

def render_database() -> None:
    """Render data analytics schema and sample data page."""
    st.title("📊 Data Analytics")
    st.caption(f"{get_bank_name()} ATM Optimizer — Athena/S3 Parquet Data Lake — Schema, Relationships & Sample Data")

    st.markdown("""
### Business Context
This data lake represents a **Bahrain-focused retail bank's ATM network analytics platform**, containing
ATM locations, transaction history, branch data, proximity matrices, maintenance records, cash management data,
and **competitor intelligence** across 5 rival banks.

It supports network planners, operations managers, and banking executives in making data-driven decisions
about ATM placement, profitability optimization, competitive positioning, and cash management across Bahrain.

All data is stored as **Apache Parquet** in S3 (me-south-1) and queried via **Amazon Athena** for
sub-second analytical performance. Pre-aggregated tables provide instant profitability and competition metrics.
    """)

    # ER Diagram — Pre-generated PNG
    st.header("Entity Relationships")
    er_img = os.path.join(_ASSETS_DIR, 'er_diagram.png')
    if os.path.exists(er_img):
        st.image(er_img, use_container_width=True)
    else:
        st.info("ER diagram not found. Run `python gen_diagrams.py` to generate.")

    st.markdown("""
**Key Relationships:**
- `atm_transactions`, `maintenance_costs`, and `cash_levels` join to `atm_locations` via `atm_id`
- `branch_locations` links to `atm_locations` via `branch_id`
- `competitor_proximity` links `atm_locations` to `competitor_atm_locations` via `neobank_atm_id` → `competitor_atm_id`
- `competition_index` and `atm_profitability` are pre-aggregated from base tables, keyed by `atm_id`
- `daily_atm_stats` is pre-aggregated from `atm_transactions`, keyed by `atm_id` + `txn_date`
    """)

    # Table Details
    st.header("Table Schemas & Sample Data")
    st.caption("11 tables organized into 3 groups: Core Network, Competitor Intelligence, and Pre-Aggregated Analytics")

    st.subheader("Core Network Tables")

    # --- atm_locations ---
    with st.expander("📍 atm_locations — 28 rows | ATM network locations across Bahrain", expanded=True):
        st.markdown("All ATM locations with GPS coordinates, type classification, branch association, and capacity.")
        cols_df = pd.DataFrame([
            ("atm_id", "STRING", "PK — Unique ATM identifier (ATM_SEEF_01)"),
            ("name", "STRING", "Human-readable location name"),
            ("latitude", "DOUBLE", "GPS latitude"),
            ("longitude", "DOUBLE", "GPS longitude"),
            ("location_type", "STRING", "branch / mall / airport / hospital / standalone"),
            ("branch_id", "STRING", "FK → branch_locations.branch_id (nullable)"),
            ("daily_capacity", "INT", "Max daily transactions"),
            ("status", "STRING", "active / inactive"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        st.markdown("**Sample Data:**")
        sample = pd.DataFrame([
            ("ATM_SEEF_01", "Seef - CrediMax Building", 26.2285, 50.5280, "branch", "BR_SEEF", 500),
            ("ATM_AALI_01", "Al Aali Shopping Complex", 26.2180, 50.5150, "mall", "—", 800),
            ("ATM_AIRPORT_01", "Bahrain International Airport", 26.2708, 50.6336, "airport", "—", 600),
            ("ATM_JUFFAIR_01", "Juffair", 26.2120, 50.6010, "standalone", "—", 300),
            ("ATM_SALMANIYA_01", "Salmaniya Medical Complex", 26.2200, 50.5600, "hospital", "—", 400),
        ], columns=["ATM ID", "Name", "Lat", "Lon", "Type", "Branch", "Capacity"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- atm_transactions ---
    with st.expander("💳 atm_transactions — 997,717 rows | Transaction history", expanded=False):
        st.markdown("6 months of transaction data across all 28 ATMs. Withdrawals, balance inquiries, deposits, and transfers with fee tracking.")
        cols_df = pd.DataFrame([
            ("transaction_id", "STRING", "PK — UUID"),
            ("atm_id", "STRING", "FK → atm_locations.atm_id"),
            ("timestamp", "TIMESTAMP", "Transaction date/time"),
            ("transaction_type", "STRING", "withdrawal / balance_inquiry / deposit / transfer"),
            ("amount", "DOUBLE", "Transaction amount in BHD"),
            ("fee", "DOUBLE", "Fee charged in BHD"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        sample = pd.DataFrame([
            ("5b3ec697…", "ATM_SEEF_01", "2025-08-01 04:17", "withdrawal", "373.506 BHD", "0.371 BHD"),
            ("a79f9a75…", "ATM_SEEF_01", "2025-08-01 19:05", "withdrawal", "34.303 BHD", "0.187 BHD"),
            ("8d3a6c4a…", "ATM_SEEF_01", "2025-08-01 09:37", "balance_inquiry", "0.000 BHD", "0.103 BHD"),
            ("b8b56cd4…", "ATM_AIRPORT_01", "2025-08-02 14:22", "withdrawal", "200.000 BHD", "0.500 BHD"),
            ("3b8dfdfd…", "ATM_JUFFAIR_01", "2025-08-01 18:44", "withdrawal", "94.630 BHD", "0.483 BHD"),
        ], columns=["Transaction ID", "ATM", "Timestamp", "Type", "Amount", "Fee"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- branch_locations ---
    with st.expander("🏦 branch_locations — 8 rows | Bank branch locations", expanded=False):
        st.markdown("NeoBank branch offices across Bahrain with footfall data for proximity analysis.")
        cols_df = pd.DataFrame([
            ("branch_id", "STRING", "PK — Unique branch identifier"),
            ("name", "STRING", "Branch name"),
            ("latitude", "DOUBLE", "GPS latitude"),
            ("longitude", "DOUBLE", "GPS longitude"),
            ("atm_count", "INT", "Number of ATMs at branch"),
            ("avg_daily_footfall", "INT", "Average daily visitors"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        sample = pd.DataFrame([
            ("BR_SEEF", "Seef Branch - CrediMax Building", 26.2285, 50.5280, 1, 850),
            ("BR_SITRA", "Sitra Branch", 26.1540, 50.6180, 1, 400),
            ("BR_HAMAD", "Hamad Town Branch", 26.1150, 50.4850, 1, 500),
            ("BR_RIFFA", "East Riffa Branch", 26.1300, 50.5550, 1, 550),
            ("BR_MUHARRAQ", "Muharraq Branch", 26.2570, 50.6120, 1, 600),
        ], columns=["Branch ID", "Name", "Lat", "Lon", "ATMs", "Daily Footfall"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- atm_proximity ---
    with st.expander("📏 atm_proximity — 784 rows | Distance matrix between ATMs", expanded=False):
        st.markdown("Pairwise distance matrix for all 28 ATMs. Used for traffic redistribution modeling in What-If scenarios.")
        cols_df = pd.DataFrame([
            ("source_atm_id", "STRING", "FK → atm_locations.atm_id"),
            ("target_atm_id", "STRING", "FK → atm_locations.atm_id"),
            ("distance_km", "DOUBLE", "Distance in kilometers"),
            ("is_same_branch", "BOOLEAN", "Whether both ATMs belong to same branch"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        sample = pd.DataFrame([
            ("ATM_SEEF_01", "ATM_AALI_01", 1.74, False),
            ("ATM_SEEF_01", "ATM_BMALL_01", 5.02, False),
            ("ATM_SEEF_01", "ATM_AIRPORT_01", 11.53, False),
            ("ATM_MANAMA_01", "ATM_MANAMA_02", 0.58, False),
            ("ATM_MUHARRAQ_01", "ATM_HIDD_01", 4.21, False),
        ], columns=["Source ATM", "Target ATM", "Distance (km)", "Same Branch"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- maintenance_costs ---
    with st.expander("🔧 maintenance_costs — 263 rows | Maintenance records", expanded=False):
        st.markdown("Preventive and corrective maintenance records with cost and downtime tracking per ATM.")
        cols_df = pd.DataFrame([
            ("atm_id", "STRING", "FK → atm_locations.atm_id"),
            ("date", "DATE", "Maintenance date"),
            ("maintenance_type", "STRING", "preventive / corrective / emergency"),
            ("cost", "DOUBLE", "Maintenance cost in BHD"),
            ("downtime_hours", "DOUBLE", "Hours ATM was offline"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        sample = pd.DataFrame([
            ("ATM_SEEF_01", "2025-08-13", "preventive", "17.956 BHD", 2.0),
            ("ATM_SEEF_01", "2025-09-18", "preventive", "36.195 BHD", 1.1),
            ("ATM_AIRPORT_01", "2025-10-05", "corrective", "125.400 BHD", 4.5),
            ("ATM_JUFFAIR_01", "2025-11-12", "emergency", "250.000 BHD", 8.0),
            ("ATM_MANAMA_01", "2025-12-01", "preventive", "22.500 BHD", 1.5),
        ], columns=["ATM", "Date", "Type", "Cost", "Downtime (hrs)"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- cash_levels ---
    with st.expander("💵 cash_levels — 5,152 rows | Daily cash management", expanded=False):
        st.markdown("Daily opening/closing balances, withdrawal totals, and replenishment data for cash optimization analysis.")
        cols_df = pd.DataFrame([
            ("atm_id", "STRING", "FK → atm_locations.atm_id"),
            ("date", "DATE", "Date"),
            ("opening_balance", "DOUBLE", "Opening cash balance in BHD"),
            ("closing_balance", "DOUBLE", "Closing cash balance in BHD"),
            ("total_withdrawals", "DOUBLE", "Total withdrawals in BHD"),
            ("replenishment_amount", "DOUBLE", "Cash added in BHD"),
            ("replenishment_cost", "DOUBLE", "Cost of replenishment in BHD"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        sample = pd.DataFrame([
            ("ATM_SEEF_01", "2025-08-01", "23,940 BHD", "20,376 BHD", "3,563 BHD", "0 BHD", "0 BHD"),
            ("ATM_SEEF_01", "2025-08-05", "5,902 BHD", "20,890 BHD", "5,268 BHD", "20,255 BHD", "18.47 BHD"),
            ("ATM_AIRPORT_01", "2025-08-01", "30,000 BHD", "24,500 BHD", "5,500 BHD", "0 BHD", "0 BHD"),
            ("ATM_MANAMA_01", "2025-08-03", "18,200 BHD", "12,800 BHD", "5,400 BHD", "0 BHD", "0 BHD"),
            ("ATM_JUFFAIR_01", "2025-08-02", "8,500 BHD", "5,200 BHD", "3,300 BHD", "0 BHD", "0 BHD"),
        ], columns=["ATM", "Date", "Opening", "Closing", "Withdrawals", "Replenishment", "Repl. Cost"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- Competitor Intelligence Tables ---
    st.subheader("Competitor Intelligence Tables")

    # --- competitor_atm_locations ---
    with st.expander("🏦 competitor_atm_locations — 82 rows | Competitor bank ATM network", expanded=False):
        st.markdown("ATM locations for 5 competitor banks across Bahrain: Red Bank (22), Gold Bank (18), Green Bank (16), Purple Bank (13), Teal Bank (13). Includes status tracking for active, planned, and closed ATMs.")
        cols_df = pd.DataFrame([
            ("competitor_atm_id", "STRING", "PK — Unique competitor ATM ID (COMP_RED_01)"),
            ("bank_name", "STRING", "Competitor bank name (Red Bank, Gold Bank, Green Bank, Purple Bank, Teal Bank)"),
            ("name", "STRING", "Human-readable location name"),
            ("latitude", "DOUBLE", "GPS latitude"),
            ("longitude", "DOUBLE", "GPS longitude"),
            ("location_type", "STRING", "branch / mall / standalone / airport"),
            ("area", "STRING", "Governorate area (Capital, Muharraq, Northern, Southern)"),
            ("status", "STRING", "active / planned / closed"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        st.markdown("**Sample Data:**")
        sample = pd.DataFrame([
            ("COMP_RED_01", "Red Bank", "Red Bank Seef Branch", 26.2290, 50.5300, "branch", "Capital", "active"),
            ("COMP_GOLD_01", "Gold Bank", "Gold Bank Diplomatic Area", 26.2350, 50.5450, "branch", "Capital", "active"),
            ("COMP_GREEN_01", "Green Bank", "Green Bank City Centre", 26.2280, 50.5380, "mall", "Capital", "active"),
            ("COMP_PURPLE_01", "Purple Bank", "Purple Bank Muharraq", 26.2600, 50.6150, "standalone", "Muharraq", "planned"),
            ("COMP_TEAL_01", "Teal Bank", "Teal Bank Riffa", 26.1300, 50.5500, "branch", "Southern", "active"),
        ], columns=["Competitor ID", "Bank", "Name", "Lat", "Lon", "Type", "Area", "Status"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- competitor_proximity ---
    with st.expander("📏 competitor_proximity — 2,296 rows | NeoBank-to-competitor distance matrix", expanded=False):
        st.markdown("Haversine distance between every NeoBank ATM (28) and every competitor ATM (82). Used for Competition Index calculation and coverage gap analysis.")
        cols_df = pd.DataFrame([
            ("neobank_atm_id", "STRING", "FK → atm_locations.atm_id"),
            ("competitor_atm_id", "STRING", "FK → competitor_atm_locations.competitor_atm_id"),
            ("bank_name", "STRING", "Competitor bank name"),
            ("distance_km", "DOUBLE", "Haversine distance in kilometers"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        st.markdown("**Sample Data:**")
        sample = pd.DataFrame([
            ("ATM_SEEF_01", "COMP_RED_01", "Red Bank", 0.22),
            ("ATM_SEEF_01", "COMP_GOLD_01", "Gold Bank", 1.85),
            ("ATM_SEEF_01", "COMP_GREEN_01", "Green Bank", 1.12),
            ("ATM_AIRPORT_01", "COMP_RED_05", "Red Bank", 3.45),
            ("ATM_JUFFAIR_01", "COMP_GOLD_03", "Gold Bank", 0.78),
        ], columns=["NeoBank ATM", "Competitor ATM", "Bank", "Distance (km)"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- Pre-Aggregated Analytics Tables ---
    st.subheader("Pre-Aggregated Analytics Tables")
    st.caption("CTAS-generated Parquet tables for sub-second query performance")

    # --- daily_atm_stats ---
    with st.expander("📈 daily_atm_stats — 5,152 rows | Daily transaction aggregates", expanded=False):
        st.markdown("Pre-aggregated daily transaction counts and amounts per ATM. Replaces full `atm_transactions` scans for anomaly detection and trend analysis — reduces query time from ~180s to <2s.")
        cols_df = pd.DataFrame([
            ("atm_id", "STRING", "FK → atm_locations.atm_id"),
            ("txn_date", "DATE", "Transaction date"),
            ("txn_count", "BIGINT", "Number of transactions that day"),
            ("total_amount", "DOUBLE", "Total transaction amount in BHD"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        st.markdown("**Sample Data:**")
        sample = pd.DataFrame([
            ("ATM_SEEF_01", "2025-08-01", 187, "28,450.30 BHD"),
            ("ATM_SEEF_01", "2025-08-02", 203, "31,220.15 BHD"),
            ("ATM_AIRPORT_01", "2025-08-01", 156, "42,100.00 BHD"),
            ("ATM_JUFFAIR_01", "2025-08-01", 89, "12,340.50 BHD"),
            ("ATM_MANAMA_01", "2025-08-01", 142, "19,870.25 BHD"),
        ], columns=["ATM", "Date", "Txn Count", "Total Amount"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- atm_profitability ---
    with st.expander("💰 atm_profitability — 28 rows | Per-ATM profitability summary", expanded=False):
        st.markdown("Pre-computed revenue, maintenance cost, and cash handling cost per ATM. Single query on 28-row table provides instant profitability ranking.")
        cols_df = pd.DataFrame([
            ("atm_id", "STRING", "FK → atm_locations.atm_id"),
            ("name", "STRING", "ATM location name"),
            ("location_type", "STRING", "ATM type classification"),
            ("total_revenue", "DOUBLE", "Total fee revenue in BHD"),
            ("total_maintenance_cost", "DOUBLE", "Total maintenance cost in BHD"),
            ("total_cash_cost", "DOUBLE", "Total cash handling cost in BHD"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        st.markdown("**Sample Data:**")
        sample = pd.DataFrame([
            ("ATM_SEEF_01", "Seef - CrediMax Building", "branch", "8,245.30 BHD", "312.50 BHD", "185.20 BHD"),
            ("ATM_AALI_01", "Al Aali Shopping Complex", "mall", "12,890.75 BHD", "425.00 BHD", "220.40 BHD"),
            ("ATM_AIRPORT_01", "Bahrain International Airport", "airport", "15,320.00 BHD", "580.00 BHD", "310.50 BHD"),
            ("ATM_JUFFAIR_01", "Juffair", "standalone", "4,120.50 BHD", "180.00 BHD", "95.30 BHD"),
            ("ATM_MANAMA_01", "Manama Souq", "standalone", "6,780.25 BHD", "245.00 BHD", "142.80 BHD"),
        ], columns=["ATM", "Name", "Type", "Revenue", "Maintenance", "Cash Cost"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    # --- competition_index ---
    with st.expander("🏆 competition_index — 28 rows | Pre-computed competition metrics", expanded=False):
        st.markdown("Pre-aggregated Competition Index per NeoBank ATM at 2km radius. Provides instant competitive pressure ranking without scanning the full proximity table.")
        cols_df = pd.DataFrame([
            ("atm_id", "STRING", "FK → atm_locations.atm_id"),
            ("name", "STRING", "ATM location name"),
            ("location_type", "STRING", "ATM type classification"),
            ("competitor_count_2km", "INT", "Number of competitor ATMs within 2km"),
            ("competition_index", "DOUBLE", "Competition Index score (0.0 — 1.0)"),
            ("nearest_competitor_km", "DOUBLE", "Distance to nearest competitor in km"),
            ("farthest_competitor_km", "DOUBLE", "Distance to farthest competitor within 2km"),
        ], columns=["Column", "Type", "Description"])
        st.dataframe(cols_df, use_container_width=True, hide_index=True)

        st.markdown("**Sample Data:**")
        sample = pd.DataFrame([
            ("ATM_SEEF_01", "Seef - CrediMax Building", "branch", 8, 0.8542, 0.22, 1.95),
            ("ATM_AALI_01", "Al Aali Shopping Complex", "mall", 5, 0.6120, 0.45, 1.88),
            ("ATM_AIRPORT_01", "Bahrain International Airport", "airport", 2, 0.2340, 1.20, 1.85),
            ("ATM_JUFFAIR_01", "Juffair", "standalone", 6, 0.7230, 0.35, 1.92),
            ("ATM_HIDD_01", "Hidd", "standalone", 1, 0.1050, 1.80, 1.80),
        ], columns=["ATM", "Name", "Type", "Competitors (2km)", "Competition Index", "Nearest (km)", "Farthest (km)"])
        st.dataframe(sample, use_container_width=True, hide_index=True)

    st.divider()
    st.markdown("""
**📊 Data Summary:** 11 tables | ~1,011,524 total records | 28 NeoBank ATMs | 82 competitor ATMs | 8 branches | 5 competitor banks | Apache Parquet on S3 | Queried via Athena

| Category | Tables | Total Rows |
|----------|--------|------------|
| Core Network | atm_locations, atm_transactions, branch_locations, atm_proximity, maintenance_costs, cash_levels | ~1,008,916 |
| Competitor Intelligence | competitor_atm_locations, competitor_proximity | 2,378 |
| Pre-Aggregated Analytics | daily_atm_stats, atm_profitability, competition_index | 5,208 |
    """)


# ---------------------------------------------------------------------------
# ❓ FAQs Tab
# ---------------------------------------------------------------------------

def render_faqs() -> None:
    """Render FAQs page with sample queries for business users."""
    st.title("❓ Frequently Asked Questions")
    st.caption("Copy any query below and paste it into the 💬 Chat tab to test the ATM Optimizer")

    def query_card(q: str, desc: str, color: str) -> None:
        st.markdown(
            f'<div style="background:#0f1724;border-left:4px solid {color};'
            f'padding:12px 16px;border-radius:0 8px 8px 0;margin-bottom:8px">'
            f'<span style="color:{color};font-size:15px;font-family:monospace">{q}</span>'
            f'<div style="color:#8896a8;font-size:13px;margin-top:4px">{desc}</div></div>',
            unsafe_allow_html=True,
        )

    # --- Basic Queries ---
    st.header("📍 ATM Network Overview")
    st.caption("Basic queries to explore the ATM network — available to all users")
    query_card("List all ATMs with their locations", "Shows all 28 ATMs with area, type, and status", "#22d3ee")
    query_card("Which ATMs are located in malls?", "Filters by location_type = mall", "#22d3ee")
    query_card("Show me ATMs near Juffair", "Location-based search with proximity data", "#22d3ee")
    query_card("What is the total number of ATMs per area?", "Aggregation by geographic area", "#22d3ee")
    query_card("Which ATMs are closest to Bahrain International Airport?", "Proximity analysis using distance matrix", "#22d3ee")

    # --- Revenue & Performance ---
    st.header("💰 Revenue & Performance Analysis")
    st.caption("Revenue metrics and performance comparisons")
    query_card("What is the revenue for ATM_SEEF_01 in the last 3 months?", "Time-series revenue analysis for a specific ATM", "#f59e0b")
    query_card("Which are the top 5 ATMs by revenue?", "Profitability ranking with revenue metrics", "#f59e0b")
    query_card("Compare revenue between mall ATMs and standalone ATMs", "Category-level performance comparison", "#f59e0b")
    query_card("Show me the daily transaction volume trend for Manama ATMs", "Time-series trend analysis", "#f59e0b")
    query_card("Which ATMs have the highest fee income?", "Fee revenue analysis across the network", "#f59e0b")

    # --- What-If Scenarios ---
    st.header("🔮 What-If Scenarios")
    st.caption("Impact analysis for strategic decisions — Admin access required")

    st.subheader("Internal Network Scenarios")
    query_card(
        "What would happen if we shut down ATM_JUFFAIR_01 for 2 weeks?",
        "Models revenue loss and traffic redistribution to nearby ATMs using inverse-distance weighting",
        "#a78bfa",
    )
    query_card(
        "If ATM_AIRPORT_01 goes offline for maintenance, which ATMs absorb the traffic?",
        "Traffic reallocation analysis with capacity checks on neighboring ATMs",
        "#a78bfa",
    )
    query_card(
        "What is the financial impact of closing the 3 lowest-performing ATMs?",
        "Multi-ATM closure scenario with network-wide revenue impact",
        "#a78bfa",
    )
    query_card(
        "If we add a new ATM in Bahrain Bay area, how would it affect nearby ATM traffic?",
        "New ATM placement analysis with cannibalization modeling",
        "#a78bfa",
    )
    query_card(
        "What happens to the network if Seef Mall ATM capacity is doubled?",
        "Capacity expansion scenario with traffic redistribution",
        "#a78bfa",
    )

    st.subheader("Competitor What-If Scenarios")
    query_card(
        "What if Red Bank opens a new ATM near Seef Mall at coordinates 26.2285, 50.5250?",
        "Simulates a competitor ATM addition — shows projected revenue loss per affected NeoBank ATM using inverse-distance weighting",
        "#a78bfa",
    )
    query_card(
        "What if Gold Bank closes their ATM in Juffair?",
        "Simulates competitor ATM removal — shows projected revenue gain for nearby NeoBank ATMs",
        "#a78bfa",
    )
    query_card(
        "Simulate Green Bank adding an ATM at 26.21, 50.55 with 3km impact radius",
        "Custom radius competitor scenario — wider impact analysis beyond the default 2km",
        "#a78bfa",
    )
    query_card(
        "What if Purple Bank opens 2 new ATMs in Muharraq — one near the airport and one in Hidd?",
        "Multi-location competitor expansion scenario with cumulative revenue impact",
        "#a78bfa",
    )
    query_card(
        "If Teal Bank removes their ATM near Adliya, how much revenue can we capture?",
        "Competitor exit scenario — quantifies the revenue opportunity for NeoBank",
        "#a78bfa",
    )

    # --- Competitor Intelligence ---
    st.header("🏦 Competitor Intelligence")
    st.caption("Competitor analysis queries available to all users. Simulation and placement tools require Admin access.")

    st.subheader("Competitor Overview")
    query_card("Show me all competitor ATMs in Bahrain", "Overview of competitor network across 5 banks: Red Bank, Gold Bank, Green Bank, Purple Bank, Teal Bank", "#f87171")
    query_card("How many ATMs does each competitor bank have?", "Bank-by-bank ATM count comparison across the network", "#f87171")
    query_card("Which competitors are expanding? Show planned ATMs", "Filter competitor ATMs by planned status to track expansion trends", "#f87171")
    query_card("Show me closed competitor ATMs — any recent exits?", "Track competitor ATM closures for market opportunity signals", "#f87171")

    st.subheader("Competition Analysis")
    query_card("Which NeoBank ATMs face the most competition?", "Competition Index ranking — higher index means more competitive pressure", "#f87171")
    query_card("Show the competition index for ATM_SEEF_01 with nearby competitors", "Detailed single-ATM analysis with competitor distances and bank names", "#f87171")
    query_card("Which of our ATMs have zero competitors within 2km?", "Identify NeoBank ATMs with exclusive coverage advantage", "#f87171")
    query_card("Show me the competitor density heatmap for Manama", "Visualize competitor concentration across the Capital governorate", "#f87171")

    st.subheader("Coverage & Market Share")
    query_card("Where are our coverage gaps vs competitors?", "Areas where competitors have ATMs but NeoBank doesn't", "#f87171")
    query_card("What is our market share by governorate?", "Market share breakdown across Capital, Muharraq, Northern, and Southern governorates", "#f87171")
    query_card("Compare our ATM coverage in Capital vs Northern governorate", "Regional coverage comparison with market share metrics", "#f87171")
    query_card("Which areas have the highest competitor density but no NeoBank presence?", "Strategic gap analysis for expansion planning", "#f87171")

    st.subheader("Strategic Placement (Admin)")
    query_card("Where should we place our next ATM?", "Top 3 placement recommendations based on coverage gaps and competitor density", "#f87171")
    query_card("Recommend 5 new ATM locations with a 3km analysis radius", "Extended placement analysis with custom count and radius", "#f87171")
    query_card("What is the estimated revenue uplift from our top placement recommendation?", "Revenue projection for the highest-scored placement candidate", "#f87171")

    # --- Maintenance & Operations ---
    st.header("🔧 Maintenance & Operations")
    st.caption("Operational insights — Admin access required")
    query_card("Which ATMs have the highest maintenance costs?", "Cost ranking across the network", "#34d399")
    query_card("Show me ATMs with more than 10 hours of downtime this quarter", "Downtime analysis with threshold", "#34d399")
    query_card("What is the average maintenance cost per ATM type (mall vs standalone)?", "Category-level cost comparison", "#34d399")
    query_card("Which ATMs need cash replenishment most frequently?", "Cash management optimization", "#34d399")

    # --- Anomaly Detection ---
    st.header("🔍 Anomaly Detection")
    st.caption("Detect unusual patterns — Admin access required")
    query_card("Are there any anomalies in ATM transaction patterns?", "Statistical anomaly detection across the network", "#f472b6")
    query_card("Which ATMs show unusual revenue drops?", "Revenue anomaly identification", "#f472b6")
    query_card("Detect ATMs with abnormal cash depletion rates", "Cash level anomaly analysis", "#f472b6")

    # --- Executive Dashboard ---
    st.header("🏢 Executive-Level Queries")
    st.caption("Strategic insights for banking executives")
    query_card("Give me a profitability ranking of all ATMs", "Complete network profitability analysis with revenue, costs, and net profit", "#fb923c")
    query_card("What is the overall network health — revenue, costs, and utilization?", "Network-wide KPI dashboard", "#fb923c")
    query_card("Which areas of Bahrain are underserved by our ATM network?", "Coverage gap analysis", "#fb923c")
    query_card("Recommend which ATMs should be relocated for better profitability", "Strategic relocation recommendations based on data", "#fb923c")

    st.divider()
    st.markdown("""
**💡 Tips:**
- Start with basic ATM queries, then try Competitor What-If scenarios for the wow factor
- Try "What if Red Bank opens a new ATM near Seef Mall?" to see real-time revenue impact simulation
- The agent generates Athena SQL dynamically — no queries are hardcoded
- Admin users have access to all tools; Operators can use read-only query tools
- Each query shows execution trace with the actual tool calls made by Claude
    """)


# ---------------------------------------------------------------------------
# 🧠 Memory Tab
# ---------------------------------------------------------------------------

def render_memory() -> None:
    """Render memory testing page."""
    st.title("🧠 Memory Testing")
    st.caption("Test AgentCore Memory — Short-term (within session) & Long-term (across sessions)")

    st.markdown("""
This tab lets you test the **AgentCore Memory** feature. The agent remembers context within a session
and learns preferences across sessions. Follow the guided scenario below to see it in action.
    """)

    # Session controls
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"**Current Session:** `{st.session_state.get('session_id', 'none')}`")
    with col2:
        if st.button("🔄 New Session", key="mem_new_session", use_container_width=True):
            st.session_state["session_id"] = f"atm-{uuid.uuid4().hex}"
            st.session_state["messages"] = []
            st.rerun()

    st.divider()

    # Guided Test Scenario
    st.header("📋 Guided Test Scenario")

    st.subheader("Session 1 — Build Context")
    st.markdown("Run these queries in order in the **💬 Chat** tab:")

    session1 = [
        ("1️⃣ Establish focus area", "Show me all ATMs in the Manama area"),
        ("2️⃣ Express interest", "Which of these ATMs has the highest revenue?"),
        ("3️⃣ Deep dive", "Show me the maintenance costs for that ATM"),
        ("4️⃣ Set preference", "I'm interested in mall ATMs specifically. Remember that for future queries."),
        ("5️⃣ What-If scenario", "What would happen if we shut down the Seef Mall ATM for a week?"),
    ]

    for label, query in session1:
        st.markdown(
            f'<div style="background:#0f1724;border-left:4px solid #22d3ee;'
            f'padding:10px 14px;border-radius:0 8px 8px 0;margin-bottom:6px">'
            f'<span style="color:#22d3ee;font-size:14px;font-weight:bold">{label}</span><br/>'
            f'<span style="color:#e2e8f0;font-size:14px">{query}</span></div>',
            unsafe_allow_html=True,
        )

    st.markdown("")
    st.warning("⚡ After completing Session 1, click **🔄 New Session** above, wait 30 seconds, then proceed to Session 2.")

    st.subheader("Session 2 — Test Memory Recall")
    st.markdown("In the new session, try these queries — the agent should recall context from Session 1:")

    session2 = [
        ("6️⃣ Interest recall", "Show me the ATMs I was looking at", "Should recall you were focused on Manama / mall ATMs"),
        ("7️⃣ Preference recall", "What are the latest revenue numbers?", "Should focus on mall ATMs based on your stated preference"),
        ("8️⃣ Context chain", "How does that compare to last time?", "Should recall previous revenue data and compare"),
    ]

    for label, query, expected in session2:
        st.markdown(
            f'<div style="background:#0f1724;border-left:4px solid #f59e0b;'
            f'padding:10px 14px;border-radius:0 8px 8px 0;margin-bottom:6px">'
            f'<span style="color:#f59e0b;font-size:14px;font-weight:bold">{label}</span><br/>'
            f'<span style="color:#e2e8f0;font-size:14px">{query}</span><br/>'
            f'<span style="color:#34d399;font-size:12px">✅ Expected: {expected}</span></div>',
            unsafe_allow_html=True,
        )

    st.divider()

    # Memory Status Dashboard
    st.header("📊 Memory Status")

    if st.button("🔍 Check Memory Status", use_container_width=True):
        try:
            import boto3
            mem_client = boto3.client("bedrock-agentcore", region_name="eu-west-1")
            ctrl_client = boto3.client("bedrock-agentcore-control", region_name="eu-west-1")

            MEMORY_ID = "NeoBank_ATM_Optimizer_Memory-5lNWRgEgqu"
            ACTOR = st.session_state.get("username", "anonymous")

            # Memory config
            mem_info = ctrl_client.get_memory(memoryId=MEMORY_ID)
            mem_data = mem_info.get("memory", {})
            st.success(f"Memory: **{mem_data.get('name', MEMORY_ID)}** | Status: **{mem_data.get('status', 'Unknown')}**")

            # Strategies
            strategies = mem_data.get("strategies", [])
            if strategies:
                strat_data = []
                for s in strategies:
                    strat_data.append({
                        "Name": s.get("name", ""),
                        "Type": s.get("type", ""),
                        "Status": s.get("status", ""),
                        "Namespace": ", ".join(s.get("namespaces", [])),
                    })
                st.dataframe(strat_data, use_container_width=True, hide_index=True)

            # Short-term sessions
            st.subheader("Short-term Memory (Sessions)")
            sessions = mem_client.list_sessions(memoryId=MEMORY_ID, actorId=ACTOR)
            sess_list = sessions.get("sessions", [])
            if sess_list:
                for s in sess_list[:5]:
                    sid = s.get("sessionId", "")
                    events = mem_client.list_events(memoryId=MEMORY_ID, sessionId=sid, actorId=ACTOR)
                    count = len(events.get("events", []))
                    st.markdown(f"✅ `{sid[:40]}…` — **{count} events**")
            else:
                st.info("No sessions found yet. Start a conversation in the Chat tab.")

            # Long-term records
            st.subheader("Long-term Memory (Extracted Records)")
            total_lt = 0
            for ns_label, ns in [
                ("Summaries", f"/summaries/{ACTOR}/"),
                ("Preferences", f"/preferences/{ACTOR}/"),
                ("Facts", f"/facts/{ACTOR}/"),
            ]:
                recs = mem_client.list_memory_records(memoryId=MEMORY_ID, namespace=ns)
                records = recs.get("memoryRecords", [])
                total_lt += len(records)
                if records:
                    st.markdown(f"✅ **{ns_label}**: {len(records)} records")
                    for r in records[:3]:
                        content = r.get("content", {})
                        text = content.get("text", str(content)[:200])
                        st.code(text[:300], language="text")
                else:
                    st.markdown(f"⏳ **{ns_label}**: 0 records (async extraction pending)")

            if total_lt == 0:
                st.info("Long-term extraction is async and may take several minutes after conversations end.")

        except Exception as e:
            st.error(f"Error checking memory: {e}")

    st.divider()

    # How It Works
    st.header("How AgentCore Memory Works")
    m1, m2 = st.columns(2)
    with m1:
        st.markdown("""
**Short-term Memory**
- Stores turn-by-turn events per session
- Enables multi-turn context within a session
- Agent recalls previous questions/answers
- Powered by `ListEvents` / `GetEvent` APIs
- Immediate — no delay
        """)
    with m2:
        st.markdown("""
**Long-term Memory**
- Extracts facts, preferences, summaries
- Persists across sessions
- Semantic search for relevant memories
- 3 strategies: SessionSummarizer, PreferenceLearner, FactExtractor
- Async extraction (minutes to process)
        """)
