"""
Property-based tests for competitor analysis tool.

Uses Hypothesis to verify:
  - Property 5: Competition Index Range Invariant
  - Property 6: Competitor Analysis Radius Filtering
"""

import os
import sys
from unittest.mock import patch

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import floats

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import COMPETITION_INDEX_NORM_FACTOR
from agent.tools._data_loader import load_atm_locations, load_competitor_proximity
from agent.tools.query_competitor_analysis import query_competitor_analysis

# Patch target to skip Athena retries and use CSV directly
_ATHENA_PATCH = "agent.tools.query_competitor_analysis._try_athena_competitor_proximity"


def _csv_fallback(neobank_atm_id=None):
    """Direct CSV fallback — skip Athena entirely."""
    return load_competitor_proximity()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_real_atm_ids() -> list[str]:
    """Return list of real ATM IDs from the data."""
    return [a["atm_id"] for a in load_atm_locations()]


# ---------------------------------------------------------------------------
# Property 5: Competition Index Range Invariant
# Feature: competitor-analysis, Property 5: Competition Index Range Invariant
# ---------------------------------------------------------------------------

class TestCompetitionIndexRangeInvariant:
    """
    Feature: competitor-analysis, Property 5: Competition Index Range Invariant

    For any NeoBank ATM and any set of competitor ATMs at any distances,
    the Competition Index must be in [0.0, 1.0].
    """

    @patch(_ATHENA_PATCH, side_effect=_csv_fallback)
    @given(
        radius=floats(min_value=0.1, max_value=50.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_ci_range_single_atm(self, radius, _mock):
        """
        Feature: competitor-analysis, Property 5: Competition Index Range Invariant

        For a real ATM with any positive radius, CI must be in [0, 1].
        """
        atm_ids = _get_real_atm_ids()
        assume(len(atm_ids) > 0)

        idx = int(radius * 100) % len(atm_ids)
        atm_id = atm_ids[idx]

        result = query_competitor_analysis(atm_id=atm_id, radius_km=radius)

        if "error" in result:
            return

        ci = result["competition_index"]
        assert 0.0 <= ci <= 1.0, (
            f"CI out of range for {atm_id} at radius {radius}: {ci}"
        )

    @patch(_ATHENA_PATCH, side_effect=_csv_fallback)
    @given(
        radius=floats(min_value=0.1, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_ci_range_all_atms(self, radius, _mock):
        """
        Feature: competitor-analysis, Property 5: Competition Index Range Invariant

        For all ATMs at any radius, every CI must be in [0, 1].
        """
        result = query_competitor_analysis(atm_id=None, radius_km=radius)

        if "error" in result:
            return

        for atm in result.get("atms", []):
            ci = atm["competition_index"]
            assert 0.0 <= ci <= 1.0, (
                f"CI out of range for {atm['atm_id']}: {ci}"
            )

    @patch(_ATHENA_PATCH, side_effect=_csv_fallback)
    @given(
        radius=floats(min_value=0.1, max_value=50.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_ci_formula_direct(self, radius, _mock):
        """
        Feature: competitor-analysis, Property 5: Competition Index Range Invariant

        Verify CI = min(1.0, sum(1/d) / NORM_FACTOR) is always in [0, 1]
        by checking the formula against the returned value.
        """
        atm_ids = _get_real_atm_ids()
        assume(len(atm_ids) > 0)

        idx = int(radius * 7) % len(atm_ids)
        atm_id = atm_ids[idx]

        result = query_competitor_analysis(atm_id=atm_id, radius_km=radius)
        if "error" in result:
            return

        # Recompute CI from nearby_competitors distances
        nearby = result.get("nearby_competitors", [])
        ci_sum = sum(1.0 / c["distance_km"] for c in nearby if c["distance_km"] > 0)
        expected_ci = min(1.0, ci_sum / COMPETITION_INDEX_NORM_FACTOR)

        assert abs(result["competition_index"] - expected_ci) < 0.001, (
            f"CI mismatch for {atm_id}: returned={result['competition_index']}, "
            f"expected={expected_ci:.4f}"
        )


# ---------------------------------------------------------------------------
# Property 6: Competitor Analysis Radius Filtering
# Feature: competitor-analysis, Property 6: Competitor Analysis Radius Filtering
# ---------------------------------------------------------------------------

class TestCompetitorAnalysisRadiusFiltering:
    """
    Feature: competitor-analysis, Property 6: Competitor Analysis Radius Filtering

    For any call with specific atm_id and radius_km, every competitor in
    nearby_competitors must have distance_km <= radius_km. Response must
    include required fields.
    """

    @patch(_ATHENA_PATCH, side_effect=_csv_fallback)
    @given(
        radius=floats(min_value=0.1, max_value=30.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_all_nearby_within_radius(self, radius, _mock):
        """
        Feature: competitor-analysis, Property 6: Competitor Analysis Radius Filtering

        Every competitor in nearby_competitors must have distance_km <= radius_km.
        """
        atm_ids = _get_real_atm_ids()
        assume(len(atm_ids) > 0)

        idx = int(radius * 13) % len(atm_ids)
        atm_id = atm_ids[idx]

        result = query_competitor_analysis(atm_id=atm_id, radius_km=radius)
        if "error" in result:
            return

        for comp in result.get("nearby_competitors", []):
            assert comp["distance_km"] <= radius, (
                f"Competitor {comp['competitor_atm_id']} at {comp['distance_km']} km "
                f"exceeds radius {radius} km"
            )

    @patch(_ATHENA_PATCH, side_effect=_csv_fallback)
    @given(
        radius=floats(min_value=0.1, max_value=30.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_response_required_fields(self, radius, _mock):
        """
        Feature: competitor-analysis, Property 6: Competitor Analysis Radius Filtering

        Response must include atm_id, competition_index, competitor_count,
        radius_km, and nearby_competitors.
        """
        atm_ids = _get_real_atm_ids()
        assume(len(atm_ids) > 0)

        atm_id = atm_ids[0]
        result = query_competitor_analysis(atm_id=atm_id, radius_km=radius)

        if "error" in result:
            return

        required_fields = {"atm_id", "competition_index", "competitor_count",
                           "radius_km", "nearby_competitors"}
        assert required_fields.issubset(result.keys()), (
            f"Missing fields: {required_fields - result.keys()}"
        )

        # Each nearby competitor must have required fields
        for comp in result.get("nearby_competitors", []):
            comp_fields = {"competitor_atm_id", "bank_name", "distance_km", "status"}
            assert comp_fields.issubset(comp.keys()), (
                f"Missing competitor fields: {comp_fields - comp.keys()}"
            )
