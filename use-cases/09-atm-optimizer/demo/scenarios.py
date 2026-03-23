"""
Demo scenarios for the ATM Profitability Optimizer.

Five scripted scenarios showcasing key features of the system.
Each scenario includes sample natural language queries, expected MCP tools,
and expected output fields — ready for live demo or automated testing.

Usage:
    from demo.scenarios import SCENARIOS, run_scenario

    for scenario in SCENARIOS:
        print(scenario["name"], "-", scenario["description"])
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Scenario 1: Impact Analysis (ATM Downtime)
# ---------------------------------------------------------------------------

IMPACT_ANALYSIS_SCENARIO = {
    "name": "ATM Downtime Impact Analysis",
    "description": (
        "Demonstrates What-If simulation for ATM downtime. Shows revenue "
        "loss calculation, traffic redistribution using inverse-distance "
        "weighting, and actionable recommendations for mitigation."
    ),
    "role": "admin",
    "sample_queries": [
        "What happens if ATM_SEEF_01 at Seef CrediMax Building goes down for 5 days?",
        "Calculate the revenue impact of ATM_MANAMA_01 being offline for a week.",
        "If ATM_AIRPORT_01 is down for 3 days, where will customers go?",
    ],
    "expected_tools": [
        "calculate_impact_analysis",
        "query_atm_data",
        "query_branch_proximity",
    ],
    "expected_output_fields": [
        "atm_id",
        "downtime_days",
        "total_revenue_loss",
        "traffic_redistribution",
        "recommendations",
        "currency",
    ],
}

# ---------------------------------------------------------------------------
# Scenario 2: Anomaly Detection
# ---------------------------------------------------------------------------

ANOMALY_DETECTION_SCENARIO = {
    "name": "ATM Performance Anomaly Detection",
    "description": (
        "Demonstrates anomaly detection across the ATM network. Identifies "
        "ATMs with transaction volumes deviating more than 2 standard "
        "deviations from expected patterns, ranked by revenue impact."
    ),
    "role": "admin",
    "sample_queries": [
        "Are there any unusual patterns in ATM performance over the last 30 days?",
        "Detect anomalies for ATM_BMALL_01 at Bahrain Mall.",
        "Which ATMs have had abnormal transaction volumes recently?",
    ],
    "expected_tools": [
        "detect_anomalies",
        "query_atm_data",
    ],
    "expected_output_fields": [
        "atm_id",
        "date",
        "anomaly_type",
        "deviation",
        "estimated_impact_bhd",
    ],
}

# ---------------------------------------------------------------------------
# Scenario 3: Cash Optimization
# ---------------------------------------------------------------------------

CASH_OPTIMIZATION_SCENARIO = {
    "name": "Cash Level Optimization",
    "description": (
        "Demonstrates cash forecasting and replenishment optimization. "
        "Shows 7-day withdrawal forecast based on day-of-week patterns "
        "and recommends optimal replenishment timing to minimize costs."
    ),
    "role": "admin",
    "sample_queries": [
        "What are the current cash levels for ATM_SEEF_01?",
        "When will ATM_HAMAD_01 run out of cash?",
        "Show me the 7-day cash forecast for ATM_AIRPORT_01.",
    ],
    "expected_tools": [
        "query_cash_levels",
        "query_atm_data",
    ],
    "expected_output_fields": [
        "atm_id",
        "current_balance",
        "avg_daily_withdrawal",
        "forecast_7day",
        "replenishment_recommendation",
        "currency",
    ],
}

# ---------------------------------------------------------------------------
# Scenario 4: Profitability Ranking
# ---------------------------------------------------------------------------

PROFITABILITY_RANKING_SCENARIO = {
    "name": "ATM Profitability Ranking",
    "description": (
        "Demonstrates profitability analysis across the ATM network. "
        "Ranks ATMs by net revenue (transaction fees minus maintenance "
        "and cash handling costs) to identify underperformers for review."
    ),
    "role": "admin",
    "sample_queries": [
        "Which are the top 5 most profitable ATMs?",
        "Rank all ATMs by net revenue.",
        "Which ATMs are losing money and should be reviewed for relocation?",
    ],
    "expected_tools": [
        "profitability_ranking",
        "query_revenue_data",
        "query_maintenance_costs",
    ],
    "expected_output_fields": [
        "atm_id",
        "name",
        "gross_revenue",
        "maintenance_costs",
        "cash_handling_costs",
        "net_revenue",
        "rank",
        "currency",
    ],
}

# ---------------------------------------------------------------------------
# Scenario 5: Traffic Reallocation
# ---------------------------------------------------------------------------

TRAFFIC_REALLOCATION_SCENARIO = {
    "name": "Traffic Reallocation Analysis",
    "description": (
        "Demonstrates traffic reallocation modeling when an ATM goes "
        "offline. Shows how customer traffic is redistributed to nearby "
        "ATMs using inverse-distance weighting, and identifies capacity "
        "risks at receiving ATMs."
    ),
    "role": "admin",
    "sample_queries": [
        "If ATM_JUFFAIR_01 goes down, which nearby ATMs will absorb the traffic?",
        "Show me the traffic reallocation for ATM_SITRA_01 downtime.",
        "What ATMs near ATM_RIFFA_01 could handle extra customers if it goes offline?",
    ],
    "expected_tools": [
        "calculate_impact_analysis",
        "query_branch_proximity",
    ],
    "expected_output_fields": [
        "atm_id",
        "traffic_redistribution",
        "nearby_atm_count",
        "recommendations",
    ],
}

# ---------------------------------------------------------------------------
# All scenarios
# ---------------------------------------------------------------------------

SCENARIOS = [
    IMPACT_ANALYSIS_SCENARIO,
    ANOMALY_DETECTION_SCENARIO,
    CASH_OPTIMIZATION_SCENARIO,
    PROFITABILITY_RANKING_SCENARIO,
    TRAFFIC_REALLOCATION_SCENARIO,
]


def run_scenario(scenario: dict, verbose: bool = True) -> dict:
    """Execute a demo scenario by running the first sample query's expected tools.

    This function calls the actual MCP tool implementations with sample
    parameters to demonstrate the system's capabilities.

    Args:
        scenario: One of the SCENARIOS dicts.
        verbose: If True, print results to stdout.

    Returns:
        dict with tool_name → result mappings.
    """
    import importlib

    results = {}
    name = scenario["name"]

    if verbose:
        print(f"\n{'='*60}")
        print(f"  DEMO: {name}")
        print(f"  {scenario['description']}")
        print(f"{'='*60}")
        print(f"  Role required: {scenario['role']}")
        print(f"  Sample query: {scenario['sample_queries'][0]}")
        print()

    # Import and call the primary expected tool with sample args
    tool_calls = _get_tool_calls_for_scenario(scenario)

    for tool_name, args, kwargs in tool_calls:
        try:
            module = importlib.import_module(f"agent.tools.{tool_name}")
            tool_fn = getattr(module, tool_name)
            result = tool_fn(*args, **kwargs)
            results[tool_name] = result

            if verbose:
                print(f"  Tool: {tool_name}")
                if isinstance(result, dict):
                    for k, v in result.items():
                        if isinstance(v, list) and len(v) > 3:
                            print(f"    {k}: [{len(v)} items]")
                        else:
                            print(f"    {k}: {v}")
                elif isinstance(result, list):
                    print(f"    [{len(result)} results]")
                    for item in result[:3]:
                        print(f"      {item}")
                    if len(result) > 3:
                        print(f"      ... and {len(result) - 3} more")
                print()

        except Exception as e:
            results[tool_name] = {"error": str(e)}
            if verbose:
                print(f"  Tool: {tool_name} — ERROR: {e}\n")

    return results


def _get_tool_calls_for_scenario(scenario: dict) -> list[tuple]:
    """Return (tool_name, args, kwargs) tuples for a scenario."""
    name = scenario["name"]

    if name == "ATM Downtime Impact Analysis":
        return [
            ("calculate_impact_analysis", ("ATM_SEEF_01", 5), {}),
        ]
    elif name == "ATM Performance Anomaly Detection":
        return [
            ("detect_anomalies", (), {"atm_id": None, "period": "30d"}),
        ]
    elif name == "Cash Level Optimization":
        return [
            ("query_cash_levels", ("ATM_SEEF_01",), {}),
        ]
    elif name == "ATM Profitability Ranking":
        return [
            ("profitability_ranking", (), {"top_n": 5, "sort": "net_revenue"}),
        ]
    elif name == "Traffic Reallocation Analysis":
        return [
            ("calculate_impact_analysis", ("ATM_JUFFAIR_01", 1), {}),
            ("query_branch_proximity", ("ATM_JUFFAIR_01",), {"radius_km": 5.0}),
        ]
    return []


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")

    print("ATM Profitability Optimizer — Demo Scenarios")
    print("=" * 60)

    for i, scenario in enumerate(SCENARIOS, 1):
        print(f"\n[{i}/{len(SCENARIOS)}]")
        run_scenario(scenario, verbose=True)

    print("\nAll demo scenarios complete.")
