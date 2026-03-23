"""
Property-based tests for MCP Tools.

Uses Hypothesis to verify:
  - Property 1: Traffic Conservation — sum of redistributed transactions
    equals original ATM daily count
  - Property 5: Revenue Calculation Consistency — net_revenue =
    transaction_revenue - maintenance_costs - cash_handling_costs
"""

import math
import os
import sys

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import (
    composite,
    floats,
    integers,
    lists,
    text,
    from_regex,
)

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools.calculate_impact_analysis import redistribute_traffic
from agent.tools.profitability_ranking import compute_net_revenue


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Generate a positive daily transaction count (realistic ATM range)
daily_txn_count_st = integers(min_value=1, max_value=2000)

# Generate a distance in km (must be > 0 for inverse-distance weighting)
distance_km_st = floats(min_value=0.01, max_value=50.0, allow_nan=False, allow_infinity=False)

# Generate ATM capacity
capacity_st = integers(min_value=100, max_value=1000)

# ATM id strategy
atm_id_st = from_regex(r"ATM_[A-Z]{3,8}_\d{2}", fullmatch=True)


@composite
def nearby_atm(draw):
    """Generate a single nearby ATM entry for traffic redistribution."""
    return {
        "atm_id": draw(atm_id_st),
        "name": f"ATM {draw(text(min_size=1, max_size=10))}",
        "distance_km": draw(distance_km_st),
        "daily_capacity": draw(capacity_st),
    }


# Generate a list of 1-15 nearby ATMs
nearby_atm_list_st = lists(nearby_atm(), min_size=1, max_size=15)

# Revenue/cost values in BHD (non-negative)
revenue_st = floats(min_value=0.0, max_value=1_000_000.0, allow_nan=False, allow_infinity=False)
cost_st = floats(min_value=0.0, max_value=500_000.0, allow_nan=False, allow_infinity=False)


# ---------------------------------------------------------------------------
# Property 1: Traffic Conservation
# ---------------------------------------------------------------------------

class TestTrafficConservation:
    """
    **Validates: Requirements 3.4** (Property 1)

    When traffic is reallocated from a downed ATM, the total transaction
    volume across the network must be preserved. The sum of redistributed
    transactions must equal the original ATM's daily transaction count.
    """

    @given(daily_count=daily_txn_count_st, nearby=nearby_atm_list_st)
    @settings(max_examples=500)
    def test_sum_of_redistributed_equals_original(self, daily_count, nearby):
        """
        **Validates: Requirements 3.4**

        For any positive daily transaction count and any non-empty set of
        nearby ATMs with positive distances, the sum of allocated transactions
        must exactly equal the original daily count.
        """
        result = redistribute_traffic(daily_count, nearby)

        # Must have results when we have nearby ATMs
        assert len(result) > 0, "No redistribution results for non-empty nearby list"

        total_redistributed = sum(r["allocated_transactions"] for r in result)
        assert total_redistributed == daily_count, (
            f"Traffic not conserved: redistributed {total_redistributed} != original {daily_count}. "
            f"Nearby ATMs: {len(nearby)}, allocations: {[r['allocated_transactions'] for r in result]}"
        )

    @given(daily_count=daily_txn_count_st, nearby=nearby_atm_list_st)
    @settings(max_examples=300)
    def test_all_allocations_non_negative(self, daily_count, nearby):
        """
        **Validates: Requirements 3.4**

        All individual allocations must be non-negative (no ATM receives
        negative traffic).
        """
        result = redistribute_traffic(daily_count, nearby)

        for r in result:
            assert r["allocated_transactions"] >= 0, (
                f"Negative allocation for {r['atm_id']}: {r['allocated_transactions']}"
            )

    @given(daily_count=daily_txn_count_st, nearby=nearby_atm_list_st)
    @settings(max_examples=300)
    def test_closer_atms_get_more_traffic(self, daily_count, nearby):
        """
        **Validates: Requirements 3.1, 3.2**

        ATMs closer to the downed ATM should receive higher weight
        (inverse-distance weighting). Weights are proportional to 1/distance.
        """
        result = redistribute_traffic(daily_count, nearby)

        if len(result) < 2:
            return  # Can't compare with single ATM

        # Verify: smaller distance → larger or equal weight (inverse-distance)
        for i in range(len(result)):
            for j in range(i + 1, len(result)):
                if result[i]["distance_km"] < result[j]["distance_km"]:
                    assert result[i]["weight"] >= result[j]["weight"], (
                        f"Closer ATM {result[i]['atm_id']} (d={result[i]['distance_km']}) "
                        f"has lower weight ({result[i]['weight']}) than farther "
                        f"{result[j]['atm_id']} (d={result[j]['distance_km']}, w={result[j]['weight']})"
                    )

    def test_empty_nearby_returns_empty(self):
        """Edge case: no nearby ATMs returns empty redistribution."""
        result = redistribute_traffic(100, [])
        assert result == []

    def test_zero_transactions_returns_empty(self):
        """Edge case: zero transactions returns empty redistribution."""
        result = redistribute_traffic(0, [{"atm_id": "ATM_TEST_01", "name": "Test", "distance_km": 1.0, "daily_capacity": 500}])
        assert result == []


# ---------------------------------------------------------------------------
# Property 5: Revenue Calculation Consistency
# ---------------------------------------------------------------------------

class TestRevenueCalculationConsistency:
    """
    **Validates: Requirements 2.1, 15.2** (Property 5)

    Net revenue for any ATM must equal:
      transaction_revenue - maintenance_costs - cash_handling_costs

    The profitability ranking must be consistent with individual ATM
    net revenue calculations.
    """

    @given(
        transaction_revenue=revenue_st,
        maintenance_costs=cost_st,
        cash_handling_costs=cost_st,
    )
    @settings(max_examples=500)
    def test_net_revenue_formula(self, transaction_revenue, maintenance_costs, cash_handling_costs):
        """
        **Validates: Requirements 15.2**

        net_revenue must exactly equal transaction_revenue - maintenance_costs - cash_handling_costs
        for all non-negative input values.
        """
        net = compute_net_revenue(transaction_revenue, maintenance_costs, cash_handling_costs)
        expected = transaction_revenue - maintenance_costs - cash_handling_costs

        assert net == pytest.approx(expected, abs=1e-10), (
            f"Revenue inconsistency: compute_net_revenue({transaction_revenue}, "
            f"{maintenance_costs}, {cash_handling_costs}) = {net}, expected {expected}"
        )

    @given(
        transaction_revenue=revenue_st,
        maintenance_costs=cost_st,
        cash_handling_costs=cost_st,
    )
    @settings(max_examples=300)
    def test_higher_costs_lower_net_revenue(self, transaction_revenue, maintenance_costs, cash_handling_costs):
        """
        **Validates: Requirements 15.2**

        Increasing any cost component while keeping revenue fixed must
        decrease (or maintain) net revenue.
        """
        base_net = compute_net_revenue(transaction_revenue, maintenance_costs, cash_handling_costs)

        # Increase maintenance by 1 BHD
        higher_maint_net = compute_net_revenue(transaction_revenue, maintenance_costs + 1.0, cash_handling_costs)
        assert higher_maint_net <= base_net, (
            f"Higher maintenance didn't reduce net revenue: {higher_maint_net} > {base_net}"
        )

        # Increase cash handling by 1 BHD
        higher_cash_net = compute_net_revenue(transaction_revenue, maintenance_costs, cash_handling_costs + 1.0)
        assert higher_cash_net <= base_net, (
            f"Higher cash handling didn't reduce net revenue: {higher_cash_net} > {base_net}"
        )

    @given(
        transaction_revenue=revenue_st,
        maintenance_costs=cost_st,
        cash_handling_costs=cost_st,
    )
    @settings(max_examples=300)
    def test_zero_costs_net_equals_gross(self, transaction_revenue, maintenance_costs, cash_handling_costs):
        """
        **Validates: Requirements 15.2**

        When both cost components are zero, net revenue equals transaction revenue.
        """
        net = compute_net_revenue(transaction_revenue, 0.0, 0.0)
        assert net == pytest.approx(transaction_revenue, abs=1e-10), (
            f"With zero costs, net ({net}) should equal revenue ({transaction_revenue})"
        )

    def test_known_values(self):
        """Verify with specific known values."""
        # 1000 BHD revenue - 200 BHD maintenance - 50 BHD cash handling = 750 BHD net
        net = compute_net_revenue(1000.0, 200.0, 50.0)
        assert net == pytest.approx(750.0, abs=1e-10)

        # Zero revenue with costs = negative net
        net = compute_net_revenue(0.0, 100.0, 50.0)
        assert net == pytest.approx(-150.0, abs=1e-10)
