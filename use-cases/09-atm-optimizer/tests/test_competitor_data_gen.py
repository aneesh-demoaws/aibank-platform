"""
Property-based tests for competitor data generation.

Uses Hypothesis to verify:
  - Property 1: Generated Competitor Data Structural Invariants
  - Property 2: Competitor Proximity Distance Consistency
"""

import math
import os
import random
import re
import sys

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import randoms

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools._data_loader import (
    haversine,
    load_atm_locations,
    load_competitor_locations,
    load_competitor_proximity,
)
from data.generate_sample_data import (
    generate_competitor_atm_locations,
    generate_competitor_proximity,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUIRED_COLUMNS = {
    "competitor_atm_id", "bank_name", "name", "latitude", "longitude",
    "location_type", "area", "status",
}
VALID_BANKS = {"Red Bank", "Gold Bank", "Green Bank", "Purple Bank", "Teal Bank"}
VALID_STATUSES = {"active", "planned", "closed"}
VALID_LOCATION_TYPES = {"branch", "mall", "standalone"}
COMP_ID_PATTERN = re.compile(r"^COMP_[A-Z]+_\d{2}$")


# ---------------------------------------------------------------------------
# Property 1: Generated Competitor Data Structural Invariants
# Feature: competitor-analysis, Property 1: Generated Competitor Data Structural Invariants
# ---------------------------------------------------------------------------

class TestCompetitorDataStructuralInvariants:
    """
    Feature: competitor-analysis, Property 1: Generated Competitor Data Structural Invariants

    For any generated competitor ATM record, verify structural constraints
    on columns, ID format, coordinate ranges, and value domains.
    """

    @given(rng=randoms())
    @settings(max_examples=100)
    def test_structural_invariants(self, rng):
        """
        Feature: competitor-analysis, Property 1: Generated Competitor Data Structural Invariants

        For any random seed, generated competitor data must satisfy all
        structural invariants: required columns, ID pattern, coordinate
        bounds, and valid enum values.
        """
        # Seed the global random module so generate_competitor_atm_locations
        # produces different data each run
        random.seed(rng.randint(0, 2**32 - 1))

        neobank = load_atm_locations()
        competitors = generate_competitor_atm_locations(neobank)

        assert len(competitors) > 0, "No competitor records generated"

        seen_ids = set()
        for comp in competitors:
            # All required columns present
            assert REQUIRED_COLUMNS.issubset(comp.keys()), (
                f"Missing columns: {REQUIRED_COLUMNS - comp.keys()}"
            )

            # competitor_atm_id matches pattern COMP_{BANK}_{SEQ}
            assert COMP_ID_PATTERN.match(comp["competitor_atm_id"]), (
                f"ID does not match pattern: {comp['competitor_atm_id']}"
            )

            # Latitude in [25.5, 26.3]
            assert 25.5 <= comp["latitude"] <= 26.3, (
                f"Latitude out of range: {comp['latitude']}"
            )

            # Longitude in [50.4, 50.8]
            assert 50.4 <= comp["longitude"] <= 50.8, (
                f"Longitude out of range: {comp['longitude']}"
            )

            # Status in valid set
            assert comp["status"] in VALID_STATUSES, (
                f"Invalid status: {comp['status']}"
            )

            # Bank name in valid set
            assert comp["bank_name"] in VALID_BANKS, (
                f"Invalid bank_name: {comp['bank_name']}"
            )

            # Location type in valid set
            assert comp["location_type"] in VALID_LOCATION_TYPES, (
                f"Invalid location_type: {comp['location_type']}"
            )

            # Collect IDs for uniqueness check
            seen_ids.add(comp["competitor_atm_id"])

        # All competitor_atm_ids are unique
        assert len(seen_ids) == len(competitors), (
            f"Duplicate IDs found: {len(competitors)} records but {len(seen_ids)} unique IDs"
        )


# ---------------------------------------------------------------------------
# Property 2: Competitor Proximity Distance Consistency
# Feature: competitor-analysis, Property 2: Competitor Proximity Distance Consistency
# ---------------------------------------------------------------------------

class TestCompetitorProximityDistanceConsistency:
    """
    Feature: competitor-analysis, Property 2: Competitor Proximity Distance Consistency

    For any pair in generated proximity data, the stored distance_km must
    equal haversine(neobank_lat, neobank_lon, comp_lat, comp_lon) within
    0.001 km tolerance.
    """

    @given(rng=randoms())
    @settings(max_examples=100)
    def test_proximity_distances_match_haversine(self, rng):
        """
        Feature: competitor-analysis, Property 2: Competitor Proximity Distance Consistency

        Generate competitor data with a random seed, produce proximity CSV,
        then verify each stored distance matches haversine computation.
        """
        random.seed(rng.randint(0, 2**32 - 1))

        neobank = load_atm_locations()
        competitors = generate_competitor_atm_locations(neobank)
        generate_competitor_proximity(neobank, competitors)

        # Clear lru_cache so we reload fresh data
        load_competitor_proximity.cache_clear()
        load_competitor_locations.cache_clear()

        proximity = load_competitor_proximity()
        assert len(proximity) > 0, "No proximity records loaded"

        # Build lookup maps
        neo_map = {a["atm_id"]: a for a in neobank}
        comp_map = {c["competitor_atm_id"]: c for c in competitors}

        for pair in proximity:
            neo_id = pair["neobank_atm_id"]
            comp_id = pair["competitor_atm_id"]
            stored_dist = pair["distance_km"]

            assert neo_id in neo_map, f"Unknown neobank ATM: {neo_id}"
            assert comp_id in comp_map, f"Unknown competitor ATM: {comp_id}"

            neo = neo_map[neo_id]
            comp = comp_map[comp_id]

            expected_dist = haversine(
                neo["latitude"], neo["longitude"],
                comp["latitude"], comp["longitude"],
            )

            assert abs(stored_dist - expected_dist) < 0.001, (
                f"Distance mismatch for {neo_id}-{comp_id}: "
                f"stored={stored_dist}, expected={expected_dist:.4f}"
            )
