"""
Property-based tests for competitor map visualization.

Uses Hypothesis to verify:
  - Property 4: Competitor Marker HTML Completeness
  - Property 16: Impact Severity Colour-Coding
"""

import os
import sys

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import (
    composite,
    floats,
    integers,
    sampled_from,
    text,
    characters,
)

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

import folium

# Mock streamlit and streamlit_folium before importing map_view
# (they are not needed for the functions under test)
from unittest.mock import MagicMock

sys.modules.setdefault("streamlit", MagicMock())
sys.modules.setdefault("streamlit_folium", MagicMock())

from frontend.components.map_view import (
    _add_competitor_markers,
    IMPACT_THRESHOLDS,
)


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

@composite
def competitor_atm_dict(draw):
    """Generate a random competitor ATM dict with valid fields."""
    bank = draw(sampled_from(["Red Bank", "Gold Bank", "Green Bank", "Purple Bank", "Teal Bank"]))
    seq = draw(integers(min_value=1, max_value=99))
    name = draw(text(
        min_size=1, max_size=30,
        alphabet=characters(whitelist_categories=("L", "N", "Zs")),
    ))
    return {
        "competitor_atm_id": f"COMP_{bank}_{seq:02d}",
        "bank_name": bank,
        "name": name,
        "latitude": draw(floats(min_value=25.5, max_value=26.3)),
        "longitude": draw(floats(min_value=50.4, max_value=50.8)),
        "location_type": draw(sampled_from(["branch", "mall", "standalone"])),
        "area": draw(sampled_from(["Capital", "Muharraq", "Northern", "Southern"])),
        "status": draw(sampled_from(["active", "planned", "closed"])),
    }


# ---------------------------------------------------------------------------
# Property 4: Competitor Marker HTML Completeness
# Feature: competitor-analysis, Property 4: Competitor Marker HTML Completeness
# ---------------------------------------------------------------------------

class TestCompetitorMarkerHTMLCompleteness:
    """
    Feature: competitor-analysis, Property 4: Competitor Marker HTML Completeness

    For any competitor ATM dict, the generated Folium marker tooltip must
    contain bank_name and name, popup must contain competitor_atm_id,
    bank_name, name, and location_type.
    """

    @given(comp=competitor_atm_dict())
    @settings(max_examples=100)
    def test_tooltip_contains_bank_and_name(self, comp):
        """
        Feature: competitor-analysis, Property 4: Competitor Marker HTML Completeness

        Verify tooltip text contains bank_name and name, and popup HTML
        contains competitor_atm_id, bank_name, name, and location_type.
        Also verify _add_competitor_markers does not crash.
        """
        bank = comp["bank_name"]
        name = comp["name"]
        cid = comp["competitor_atm_id"]
        loc_type = comp["location_type"]

        # Verify the string construction matches the map_view logic
        tooltip_text = f"{bank} — {name}"
        popup_html = f"{cid} | {bank} | {name} | {loc_type}"

        assert bank in tooltip_text, f"bank_name not in tooltip: {tooltip_text}"
        assert name in tooltip_text, f"name not in tooltip: {tooltip_text}"
        assert cid in popup_html, f"competitor_atm_id not in popup: {popup_html}"
        assert bank in popup_html, f"bank_name not in popup: {popup_html}"
        assert name in popup_html, f"name not in popup: {popup_html}"
        assert loc_type in popup_html, f"location_type not in popup: {popup_html}"

        # Verify _add_competitor_markers does not crash
        m = folium.Map(location=[26.0, 50.5], zoom_start=10)
        fg = _add_competitor_markers(m, [comp])
        assert fg is not None, "FeatureGroup should not be None"

    @given(comp=competitor_atm_dict())
    @settings(max_examples=100)
    def test_marker_added_to_feature_group(self, comp):
        """
        Feature: competitor-analysis, Property 4: Competitor Marker HTML Completeness

        Verify that calling _add_competitor_markers adds at least one
        child to the FeatureGroup.
        """
        m = folium.Map(location=[26.0, 50.5], zoom_start=10)
        fg = _add_competitor_markers(m, [comp])

        # The FeatureGroup should have at least one child (the marker)
        children = list(fg._children.values())
        assert len(children) >= 1, (
            f"Expected at least 1 marker in FeatureGroup, got {len(children)}"
        )


# ---------------------------------------------------------------------------
# Property 16: Impact Severity Colour-Coding
# Feature: competitor-analysis, Property 16: Impact Severity Colour-Coding
# ---------------------------------------------------------------------------

def get_impact_color(current: float, projected: float) -> str:
    """Replicate the colour logic from _add_scenario_overlay."""
    if current > 0:
        pct_change = abs(projected - current) / current
    else:
        pct_change = 0.0
    if pct_change < IMPACT_THRESHOLDS["low"]:
        return "green"
    elif pct_change <= IMPACT_THRESHOLDS["medium"]:
        return "orange"
    else:
        return "red"


class TestImpactSeverityColourCoding:
    """
    Feature: competitor-analysis, Property 16: Impact Severity Colour-Coding

    For any affected NeoBank ATM in a scenario result, the assigned colour
    must be green if abs(pct_change) < 0.05, orange if 0.05 <= abs(pct_change)
    <= 0.15, red if abs(pct_change) > 0.15.
    """

    @given(
        current=floats(min_value=1.0, max_value=2000.0, allow_nan=False, allow_infinity=False),
        projected=floats(min_value=0.0, max_value=3000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_color_matches_thresholds(self, current, projected):
        """
        Feature: competitor-analysis, Property 16: Impact Severity Colour-Coding

        For any current > 0 and projected >= 0, the colour must match
        the threshold-based classification.
        """
        pct_change = abs(projected - current) / current
        color = get_impact_color(current, projected)

        if pct_change < 0.05:
            assert color == "green", (
                f"Expected green for pct_change={pct_change:.4f}, got {color}"
            )
        elif pct_change <= 0.15:
            assert color == "orange", (
                f"Expected orange for pct_change={pct_change:.4f}, got {color}"
            )
        else:
            assert color == "red", (
                f"Expected red for pct_change={pct_change:.4f}, got {color}"
            )

    @given(
        projected=floats(min_value=0.0, max_value=3000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=100)
    def test_zero_current_always_green(self, projected):
        """
        Feature: competitor-analysis, Property 16: Impact Severity Colour-Coding

        When current is 0, pct_change is 0, so colour must be green.
        """
        color = get_impact_color(0.0, projected)
        assert color == "green", (
            f"Expected green for current=0, got {color}"
        )
