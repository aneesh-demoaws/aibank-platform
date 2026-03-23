"""
Property-based tests for competitor scenario simulation.

Uses Hypothesis to verify:
  - Property 11: Scenario Impact Direction
  - Property 12: Scenario Coordinate Validation
  - Property 13: Transaction Conservation
"""

import os
import sys

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import floats, sampled_from

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import (
    BAHRAIN_LAT_MIN, BAHRAIN_LAT_MAX,
    BAHRAIN_LON_MIN, BAHRAIN_LON_MAX,
    ESTIMATED_COMPETITOR_DAILY_TXNS,
)
from agent.tools.simulate_competitor_scenario import simulate_competitor_scenario


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Valid Bahrain coordinates
bahrain_lat_st = floats(
    min_value=BAHRAIN_LAT_MIN, max_value=BAHRAIN_LAT_MAX,
    allow_nan=False, allow_infinity=False,
)
bahrain_lon_st = floats(
    min_value=BAHRAIN_LON_MIN, max_value=BAHRAIN_LON_MAX,
    allow_nan=False, allow_infinity=False,
)


# ---------------------------------------------------------------------------
# Property 11: Scenario Impact Direction
# Feature: competitor-analysis, Property 11: Scenario Impact Direction
# ---------------------------------------------------------------------------

class TestScenarioImpactDirection:
    """
    Feature: competitor-analysis, Property 11: Scenario Impact Direction

    For "add": projected <= current for all affected ATMs.
    For "remove": projected >= current for all affected ATMs.
    """

    @given(lat=bahrain_lat_st, lon=bahrain_lon_st)
    @settings(max_examples=100, deadline=None)
    def test_add_decreases_transactions(self, lat, lon):
        """
        Feature: competitor-analysis, Property 11: Scenario Impact Direction

        When adding a competitor, projected_daily_transactions <= current
        for all affected ATMs.
        """
        result = simulate_competitor_scenario("add", lat, lon, "TestBank")

        if "error" in result:
            return

        for atm in result.get("affected_atms", []):
            assert atm["projected_daily_transactions"] <= atm["current_daily_transactions"], (
                f"Add scenario: projected ({atm['projected_daily_transactions']}) > "
                f"current ({atm['current_daily_transactions']}) for {atm['atm_id']}"
            )

    @given(lat=bahrain_lat_st, lon=bahrain_lon_st)
    @settings(max_examples=100, deadline=None)
    def test_remove_increases_transactions(self, lat, lon):
        """
        Feature: competitor-analysis, Property 11: Scenario Impact Direction

        When removing a competitor, projected_daily_transactions >= current
        for all affected ATMs.
        """
        result = simulate_competitor_scenario("remove", lat, lon, "TestBank")

        if "error" in result:
            return

        for atm in result.get("affected_atms", []):
            assert atm["projected_daily_transactions"] >= atm["current_daily_transactions"], (
                f"Remove scenario: projected ({atm['projected_daily_transactions']}) < "
                f"current ({atm['current_daily_transactions']}) for {atm['atm_id']}"
            )


# ---------------------------------------------------------------------------
# Property 12: Scenario Coordinate Validation
# Feature: competitor-analysis, Property 12: Scenario Coordinate Validation
# ---------------------------------------------------------------------------

class TestScenarioCoordinateValidation:
    """
    Feature: competitor-analysis, Property 12: Scenario Coordinate Validation

    For coordinates outside Bahrain bounds, must return error.
    """

    @given(lat=floats(min_value=27.0, max_value=90.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100, deadline=None)
    def test_invalid_lat_high(self, lat):
        """
        Feature: competitor-analysis, Property 12: Scenario Coordinate Validation

        Latitude above Bahrain max must return error.
        """
        result = simulate_competitor_scenario("add", lat, 50.6, "TestBank")
        assert "error" in result, (
            f"Expected error for lat={lat} (above Bahrain), got: {result}"
        )

    @given(lat=floats(min_value=-90.0, max_value=25.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100, deadline=None)
    def test_invalid_lat_low(self, lat):
        """
        Feature: competitor-analysis, Property 12: Scenario Coordinate Validation

        Latitude below Bahrain min must return error.
        """
        result = simulate_competitor_scenario("add", lat, 50.6, "TestBank")
        assert "error" in result, (
            f"Expected error for lat={lat} (below Bahrain), got: {result}"
        )

    @given(lon=floats(min_value=51.0, max_value=180.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100, deadline=None)
    def test_invalid_lon_high(self, lon):
        """
        Feature: competitor-analysis, Property 12: Scenario Coordinate Validation

        Longitude above Bahrain max must return error.
        """
        result = simulate_competitor_scenario("add", 26.0, lon, "TestBank")
        assert "error" in result, (
            f"Expected error for lon={lon} (above Bahrain), got: {result}"
        )

    @given(lon=floats(min_value=-180.0, max_value=50.0, allow_nan=False, allow_infinity=False))
    @settings(max_examples=100, deadline=None)
    def test_invalid_lon_low(self, lon):
        """
        Feature: competitor-analysis, Property 12: Scenario Coordinate Validation

        Longitude below Bahrain min must return error.
        """
        result = simulate_competitor_scenario("add", 26.0, lon, "TestBank")
        assert "error" in result, (
            f"Expected error for lon={lon} (below Bahrain), got: {result}"
        )


# ---------------------------------------------------------------------------
# Property 13: Transaction Conservation
# Feature: competitor-analysis, Property 13: Transaction Conservation
# ---------------------------------------------------------------------------

class TestTransactionConservation:
    """
    Feature: competitor-analysis, Property 13: Transaction Conservation

    The absolute sum of projected transaction changes across all affected
    NeoBank ATMs must equal ESTIMATED_COMPETITOR_DAILY_TXNS (150), provided
    no ATM is clipped to 0 transactions.
    """

    @given(lat=bahrain_lat_st, lon=bahrain_lon_st)
    @settings(max_examples=100, deadline=None)
    def test_add_transaction_conservation(self, lat, lon):
        """
        Feature: competitor-analysis, Property 13: Transaction Conservation

        For "add" scenario, sum of abs(projected - current) across affected
        ATMs must equal 150 when no clipping occurs.
        """
        result = simulate_competitor_scenario("add", lat, lon, "TestBank")

        if "error" in result:
            return

        affected = result.get("affected_atms", [])
        if not affected:
            return

        # Check if any ATM was clipped to 0
        clipped = any(
            atm["projected_daily_transactions"] == 0
            and atm["current_daily_transactions"] > 0
            for atm in affected
        )

        if clipped:
            # When clipping occurs, sum may be less than 150
            total_change = sum(
                abs(atm["current_daily_transactions"] - atm["projected_daily_transactions"])
                for atm in affected
            )
            assert total_change <= ESTIMATED_COMPETITOR_DAILY_TXNS, (
                f"Total change {total_change} exceeds {ESTIMATED_COMPETITOR_DAILY_TXNS}"
            )
        else:
            total_change = sum(
                abs(atm["current_daily_transactions"] - atm["projected_daily_transactions"])
                for atm in affected
            )
            assert total_change == ESTIMATED_COMPETITOR_DAILY_TXNS, (
                f"Transaction conservation violated: total_change={total_change}, "
                f"expected={ESTIMATED_COMPETITOR_DAILY_TXNS}"
            )

    @given(lat=bahrain_lat_st, lon=bahrain_lon_st)
    @settings(max_examples=100, deadline=None)
    def test_remove_transaction_conservation(self, lat, lon):
        """
        Feature: competitor-analysis, Property 13: Transaction Conservation

        For "remove" scenario, sum of (projected - current) across affected
        ATMs must equal 150 (no clipping for remove since transactions increase).
        """
        result = simulate_competitor_scenario("remove", lat, lon, "TestBank")

        if "error" in result:
            return

        affected = result.get("affected_atms", [])
        if not affected:
            return

        total_change = sum(
            atm["projected_daily_transactions"] - atm["current_daily_transactions"]
            for atm in affected
        )
        assert total_change == ESTIMATED_COMPETITOR_DAILY_TXNS, (
            f"Transaction conservation violated for remove: total_change={total_change}, "
            f"expected={ESTIMATED_COMPETITOR_DAILY_TXNS}"
        )
