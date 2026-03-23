"""
Property-based tests for ATM placement recommendation.

Uses Hypothesis to verify:
  - Property 15: Placement Score Range and Response Completeness
"""

import os
import sys

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import floats, integers

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import BAHRAIN_LAT_MIN, BAHRAIN_LAT_MAX, BAHRAIN_LON_MIN, BAHRAIN_LON_MAX
from agent.tools.recommend_atm_placement import recommend_atm_placement


# ---------------------------------------------------------------------------
# Property 15: Placement Score Range and Response Completeness
# Feature: competitor-analysis, Property 15: Placement Score Range and Response Completeness
# ---------------------------------------------------------------------------

class TestPlacementScoreRangeAndCompleteness:
    """
    Feature: competitor-analysis, Property 15: Placement Score Range and Response Completeness

    For any recommendation, placement_score in [0.0, 1.0], coordinates
    within Bahrain bounds, all required fields present.
    """

    @given(
        count=integers(min_value=1, max_value=10),
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_placement_score_range(self, count, radius):
        """
        Feature: competitor-analysis, Property 15: Placement Score Range and Response Completeness

        Every recommendation must have placement_score in [0.0, 1.0].
        """
        result = recommend_atm_placement(count=count, radius_km=radius)

        if "error" in result:
            return

        for rec in result.get("recommendations", []):
            score = rec["placement_score"]
            assert 0.0 <= score <= 1.0, (
                f"Placement score out of range: {score}"
            )

    @given(
        count=integers(min_value=1, max_value=10),
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_coordinates_within_bahrain(self, count, radius):
        """
        Feature: competitor-analysis, Property 15: Placement Score Range and Response Completeness

        Every recommendation must have coordinates within Bahrain bounds.
        """
        result = recommend_atm_placement(count=count, radius_km=radius)

        if "error" in result:
            return

        for rec in result.get("recommendations", []):
            assert BAHRAIN_LAT_MIN <= rec["latitude"] <= BAHRAIN_LAT_MAX, (
                f"Latitude out of Bahrain bounds: {rec['latitude']}"
            )
            assert BAHRAIN_LON_MIN <= rec["longitude"] <= BAHRAIN_LON_MAX, (
                f"Longitude out of Bahrain bounds: {rec['longitude']}"
            )

    @given(
        count=integers(min_value=1, max_value=10),
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_required_fields_present(self, count, radius):
        """
        Feature: competitor-analysis, Property 15: Placement Score Range and Response Completeness

        Every recommendation must include all required fields.
        """
        result = recommend_atm_placement(count=count, radius_km=radius)

        if "error" in result:
            return

        # Top-level required fields
        assert "recommendations" in result, "Missing 'recommendations' key"
        assert "summary" in result, "Missing 'summary' key"

        required_rec_fields = {
            "rank", "latitude", "longitude", "area_name",
            "placement_score", "nearest_neobank_atm_id",
            "nearest_neobank_distance_km", "competitor_count_in_radius",
            "estimated_daily_transactions",
        }

        for rec in result.get("recommendations", []):
            assert required_rec_fields.issubset(rec.keys()), (
                f"Missing recommendation fields: {required_rec_fields - rec.keys()}"
            )

    @given(
        count=integers(min_value=1, max_value=10),
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_recommendation_count_bounded(self, count, radius):
        """
        Feature: competitor-analysis, Property 15: Placement Score Range and Response Completeness

        Number of recommendations must be <= requested count.
        """
        result = recommend_atm_placement(count=count, radius_km=radius)

        if "error" in result:
            return

        recs = result.get("recommendations", [])
        assert len(recs) <= count, (
            f"Got {len(recs)} recommendations, requested at most {count}"
        )

    @given(
        count=integers(min_value=1, max_value=10),
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_ranks_sequential(self, count, radius):
        """
        Feature: competitor-analysis, Property 15: Placement Score Range and Response Completeness

        Recommendation ranks must be sequential starting from 1.
        """
        result = recommend_atm_placement(count=count, radius_km=radius)

        if "error" in result:
            return

        recs = result.get("recommendations", [])
        for i, rec in enumerate(recs, 1):
            assert rec["rank"] == i, (
                f"Expected rank {i}, got {rec['rank']}"
            )
