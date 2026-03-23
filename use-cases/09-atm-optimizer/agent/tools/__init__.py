# ATM Profitability Optimizer - Agent Tools Package
#
# All tools query data via AthenaClient -> Athena -> S3 in me-south-1.
# The _athena_queries module provides the shared data access layer.

from agent.tools.query_atm_data import query_atm_data
from agent.tools.query_branch_proximity import query_branch_proximity
from agent.tools.query_revenue_data import query_revenue_data
from agent.tools.query_maintenance_costs import query_maintenance_costs
from agent.tools.query_cash_levels import query_cash_levels
from agent.tools.calculate_impact_analysis import calculate_impact_analysis, redistribute_traffic
from agent.tools.detect_anomalies import detect_anomalies
from agent.tools.profitability_ranking import profitability_ranking, compute_net_revenue
from agent.tools.query_competitor_analysis import query_competitor_analysis
from agent.tools.query_coverage_analysis import query_coverage_analysis
from agent.tools.simulate_competitor_scenario import simulate_competitor_scenario
from agent.tools.recommend_atm_placement import recommend_atm_placement

__all__ = [
    "query_atm_data",
    "query_branch_proximity",
    "query_revenue_data",
    "query_maintenance_costs",
    "query_cash_levels",
    "calculate_impact_analysis",
    "redistribute_traffic",
    "detect_anomalies",
    "profitability_ranking",
    "compute_net_revenue",
    "query_competitor_analysis",
    "query_coverage_analysis",
    "simulate_competitor_scenario",
    "recommend_atm_placement",
]
