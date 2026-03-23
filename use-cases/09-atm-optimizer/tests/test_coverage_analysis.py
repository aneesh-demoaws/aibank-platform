"""
Property-based tests for coverage analysis tool.

Uses Hypothesis to verify:
  - Property 7: Coverage Gap Correctness
  - Property 8: Coverage Advantage Correctness
  - Property 9: Coverage Summary Consistency
  - Property 10: Market Share Formula and Range
"""

import os
import sys

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import floats

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools._data_loader import load_atm_locations, load_competitor_locations
from agent.tools.query_coverage_analysis import query_coverage_analysis


# ---------------------------------------------------------------------------
# Property 7: Coverage Gap Correctness
# Feature: competitor-analysis, Property 7: Coverage Gap Correctness
# ---------------------------------------------------------------------------

class TestCoverageGapCorrectness:
    """
    Feature: competitor-analysis, Property 7: Coverage Gap Correctness

    For any entry in coverage_gaps, nearest_neobank_distance_km > radius_km.
    """

    @given(
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_gaps_exceed_radius(self, radius):
        """
        Feature: competitor-analysis, Property 7: Coverage Gap Correctness

        Every coverage gap must have nearest_neobank_distance_km > radius_km.
        """
        result = query_coverage_analysis(radius_km=radius)

        if "error" in result:
            return

        for gap in result.get("coverage_gaps", []):
            assert gap["nearest_neobank_distance_km"] > radius, (
                f"Gap {gap['competitor_atm_id']} has nearest_neobank_distance_km="
                f"{gap['nearest_neobank_distance_km']} which is <= radius {radius}"
            )


# ---------------------------------------------------------------------------
# Property 8: Coverage Advantage Correctness
# Feature: competitor-analysis, Property 8: Coverage Advantage Correctness
# ---------------------------------------------------------------------------

class TestCoverageAdvantageCorrectness:
    """
    Feature: competitor-analysis, Property 8: Coverage Advantage Correctness

    For any entry in coverage_advantages, nearest_competitor_distance_km > radius_km.
    """

    @given(
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_advantages_exceed_radius(self, radius):
        """
        Feature: competitor-analysis, Property 8: Coverage Advantage Correctness

        Every coverage advantage must have nearest_competitor_distance_km > radius_km.
        """
        result = query_coverage_analysis(radius_km=radius)

        if "error" in result:
            return

        for adv in result.get("coverage_advantages", []):
            assert adv["nearest_competitor_distance_km"] > radius, (
                f"Advantage {adv['atm_id']} has nearest_competitor_distance_km="
                f"{adv['nearest_competitor_distance_km']} which is <= radius {radius}"
            )


# ---------------------------------------------------------------------------
# Property 9: Coverage Summary Consistency
# Feature: competitor-analysis, Property 9: Coverage Summary Consistency
# ---------------------------------------------------------------------------

class TestCoverageSummaryConsistency:
    """
    Feature: competitor-analysis, Property 9: Coverage Summary Consistency

    summary.gap_count == len(coverage_gaps),
    summary.advantage_count == len(coverage_advantages),
    summary.overall_market_share == (neobank_count / (neobank_count + active_competitor_count)) * 100.
    """

    @given(
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_summary_counts_match(self, radius):
        """
        Feature: competitor-analysis, Property 9: Coverage Summary Consistency

        Verify gap_count and advantage_count match list lengths.
        """
        result = query_coverage_analysis(radius_km=radius)

        if "error" in result:
            return

        summary = result["summary"]
        assert summary["gap_count"] == len(result["coverage_gaps"]), (
            f"gap_count mismatch: summary={summary['gap_count']}, "
            f"actual={len(result['coverage_gaps'])}"
        )
        assert summary["advantage_count"] == len(result["coverage_advantages"]), (
            f"advantage_count mismatch: summary={summary['advantage_count']}, "
            f"actual={len(result['coverage_advantages'])}"
        )

    @given(
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_overall_market_share_formula(self, radius):
        """
        Feature: competitor-analysis, Property 9: Coverage Summary Consistency

        overall_market_share == (neobank_count / (neobank_count + active_competitor_count)) * 100.
        """
        result = query_coverage_analysis(radius_km=radius)

        if "error" in result:
            return

        neobank = load_atm_locations()
        competitors = load_competitor_locations()
        active_comp_count = sum(1 for c in competitors if c.get("status") == "active")

        total = len(neobank) + active_comp_count
        if total > 0:
            expected_share = round((len(neobank) / total) * 100, 1)
        else:
            expected_share = 0.0

        assert result["summary"]["overall_market_share"] == expected_share, (
            f"Market share mismatch: got {result['summary']['overall_market_share']}, "
            f"expected {expected_share}"
        )


# ---------------------------------------------------------------------------
# Property 10: Market Share Formula and Range
# Feature: competitor-analysis, Property 10: Market Share Formula and Range
# ---------------------------------------------------------------------------

class TestMarketShareFormulaAndRange:
    """
    Feature: competitor-analysis, Property 10: Market Share Formula and Range

    For any area, market_share = (neobank_count / total_count) * 100,
    and must be in [0, 100].
    """

    @given(
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_market_share_range(self, radius):
        """
        Feature: competitor-analysis, Property 10: Market Share Formula and Range

        Every governorate market share must be in [0, 100].
        """
        result = query_coverage_analysis(radius_km=radius)

        if "error" in result:
            return

        market_share = result.get("market_share", {})
        overall = market_share.get("overall", 0.0)
        assert 0.0 <= overall <= 100.0, (
            f"Overall market share out of range: {overall}"
        )

        for gov, share in market_share.get("by_governorate", {}).items():
            assert 0.0 <= share <= 100.0, (
                f"Market share for {gov} out of range: {share}"
            )

    @given(
        radius=floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100, deadline=None)
    def test_market_share_formula_by_governorate(self, radius):
        """
        Feature: competitor-analysis, Property 10: Market Share Formula and Range

        For each governorate, market_share = (neobank_count / total_count) * 100.
        """
        result = query_coverage_analysis(radius_km=radius)

        if "error" in result:
            return

        neobank = load_atm_locations()
        competitors = load_competitor_locations()
        active_competitors = [c for c in competitors if c.get("status") == "active"]

        # Replicate the governorate assignment logic from query_coverage_analysis
        def _assign_governorate(lat: float, lon: float) -> str:
            if 26.24 <= lat <= 26.30 and 50.60 <= lon <= 50.68:
                return "Muharraq"
            if 26.19 <= lat <= 26.27 and 50.53 <= lon <= 50.62:
                return "Capital"
            if 26.10 <= lat <= 26.24 and 50.44 <= lon <= 50.56:
                return "Northern"
            return "Southern"

        # Build governorate counts
        gov_neobank: dict[str, int] = {}
        gov_competitor: dict[str, int] = {}

        for nb in neobank:
            area = nb.get("area", _assign_governorate(nb["latitude"], nb["longitude"]))
            gov_neobank[area] = gov_neobank.get(area, 0) + 1

        for comp in active_competitors:
            area = comp.get("area", "Unknown")
            gov_competitor[area] = gov_competitor.get(area, 0) + 1

        by_gov = result.get("market_share", {}).get("by_governorate", {})
        for gov, share in by_gov.items():
            nb_count = gov_neobank.get(gov, 0)
            comp_count = gov_competitor.get(gov, 0)
            total = nb_count + comp_count
            if total > 0:
                expected = round((nb_count / total) * 100, 1)
            else:
                expected = 0.0
            assert share == expected, (
                f"Market share for {gov}: got {share}, expected {expected} "
                f"(neobank={nb_count}, competitor={comp_count})"
            )
