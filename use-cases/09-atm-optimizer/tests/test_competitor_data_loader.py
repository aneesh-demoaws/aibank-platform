"""
Property-based tests for competitor data loading round-trip.

Uses Hypothesis to verify:
  - Property 3: Data Loading Round-Trip
"""

import os
import random
import sys

import pytest
from hypothesis import given, settings
from hypothesis.strategies import randoms

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.tools._data_loader import (
    load_atm_locations,
    load_competitor_locations,
)
from data.generate_sample_data import generate_competitor_atm_locations


# ---------------------------------------------------------------------------
# Property 3: Data Loading Round-Trip
# Feature: competitor-analysis, Property 3: Data Loading Round-Trip
# ---------------------------------------------------------------------------

class TestDataLoadingRoundTrip:
    """
    Feature: competitor-analysis, Property 3: Data Loading Round-Trip

    For any generated competitor ATM location record written to CSV,
    loading via load_competitor_locations() must produce a dict with
    equivalent values (float for lat/lon, str for IDs/names).
    """

    @given(rng=randoms())
    @settings(max_examples=100)
    def test_round_trip_field_equivalence(self, rng):
        """
        Feature: competitor-analysis, Property 3: Data Loading Round-Trip

        Generate competitor data with a random seed, write to CSV via
        generate_competitor_atm_locations, then load with
        load_competitor_locations and verify field-by-field equivalence.
        """
        random.seed(rng.randint(0, 2**32 - 1))

        neobank = load_atm_locations()
        generated = generate_competitor_atm_locations(neobank)

        # Clear lru_cache so we reload fresh data from the CSV just written
        load_competitor_locations.cache_clear()
        loaded = load_competitor_locations()

        assert len(loaded) == len(generated), (
            f"Row count mismatch: generated {len(generated)}, loaded {len(loaded)}"
        )

        for gen_rec, load_rec in zip(generated, loaded):
            # String fields must match exactly
            for field in ("competitor_atm_id", "bank_name", "name",
                          "location_type", "area", "status"):
                assert str(gen_rec[field]) == str(load_rec[field]), (
                    f"Field '{field}' mismatch: generated={gen_rec[field]!r}, "
                    f"loaded={load_rec[field]!r}"
                )

            # Float fields must match within tolerance (CSV rounding)
            assert abs(float(gen_rec["latitude"]) - float(load_rec["latitude"])) < 1e-4, (
                f"Latitude mismatch: generated={gen_rec['latitude']}, "
                f"loaded={load_rec['latitude']}"
            )
            assert abs(float(gen_rec["longitude"]) - float(load_rec["longitude"])) < 1e-4, (
                f"Longitude mismatch: generated={gen_rec['longitude']}, "
                f"loaded={load_rec['longitude']}"
            )
