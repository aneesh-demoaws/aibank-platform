"""
Property-based tests for ATM data integrity and haversine distance calculations.

Uses Hypothesis to verify:
  - Distance symmetry (Property 6)
  - All distances within Bahrain bounds (Property 2)
  - All coordinates within Bahrain geographic boundaries (Requirement 9.5)
"""

import math
import sys
import os

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import floats, tuples

# ---------------------------------------------------------------------------
# Make the data module importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
sys.path.insert(0, DATA_DIR)

from generate_sample_data import haversine, load_atm_locations

# ---------------------------------------------------------------------------
# Bahrain geographic bounds (from agent/config.py)
# ---------------------------------------------------------------------------
BAHRAIN_LAT_MIN = 25.5
BAHRAIN_LAT_MAX = 26.3
BAHRAIN_LON_MIN = 50.4
BAHRAIN_LON_MAX = 50.8
BAHRAIN_MAX_DISTANCE_KM = 60.0
# The bounding box (25.5-26.3°N, 50.4-50.8°E) is larger than Bahrain island.
# Its diagonal spans ~98 km, so arbitrary points within the box can be up to
# ~98 km apart.  The 60 km bound applies to actual ATM locations only.
BAHRAIN_BBOX_MAX_DISTANCE_KM = 100.0

# ---------------------------------------------------------------------------
# Strategies: generate coordinates within Bahrain bounds
# ---------------------------------------------------------------------------
bahrain_lat = floats(min_value=BAHRAIN_LAT_MIN, max_value=BAHRAIN_LAT_MAX, allow_nan=False, allow_infinity=False)
bahrain_lon = floats(min_value=BAHRAIN_LON_MIN, max_value=BAHRAIN_LON_MAX, allow_nan=False, allow_infinity=False)
bahrain_point = tuples(bahrain_lat, bahrain_lon)


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------

class TestHaversineProperties:
    """Property-based tests for haversine distance calculations."""

    @given(point_a=bahrain_point, point_b=bahrain_point)
    @settings(max_examples=500)
    def test_distance_symmetry(self, point_a, point_b):
        """
        **Validates: Requirements 2.3, 9.2** (Property 6)

        Distance from A to B must equal distance from B to A.
        The haversine function must be symmetric for all coordinate pairs.
        """
        lat_a, lon_a = point_a
        lat_b, lon_b = point_b

        d_ab = haversine(lat_a, lon_a, lat_b, lon_b)
        d_ba = haversine(lat_b, lon_b, lat_a, lon_a)

        assert d_ab == pytest.approx(d_ba, abs=1e-10), (
            f"Asymmetric distance: d(A,B)={d_ab} != d(B,A)={d_ba}"
        )

    @given(point_a=bahrain_point, point_b=bahrain_point)
    @settings(max_examples=500)
    def test_distances_within_bahrain_bounds(self, point_a, point_b):
        """
        **Validates: Requirements 9.2, 9.5** (Property 2)

        All distances between points within Bahrain's bounding box must be
        less than 100 km (the bounding box diagonal is ~98 km).
        """
        lat_a, lon_a = point_a
        lat_b, lon_b = point_b

        dist = haversine(lat_a, lon_a, lat_b, lon_b)

        assert dist < BAHRAIN_BBOX_MAX_DISTANCE_KM, (
            f"Distance {dist:.4f} km exceeds Bahrain bounding box max "
            f"({BAHRAIN_BBOX_MAX_DISTANCE_KM} km) between "
            f"({lat_a}, {lon_a}) and ({lat_b}, {lon_b})"
        )

    @given(point_a=bahrain_point, point_b=bahrain_point)
    @settings(max_examples=500)
    def test_distance_non_negative(self, point_a, point_b):
        """
        **Validates: Requirements 9.2** (Property 2)

        Haversine distance must always be non-negative.
        """
        lat_a, lon_a = point_a
        lat_b, lon_b = point_b

        dist = haversine(lat_a, lon_a, lat_b, lon_b)

        assert dist >= 0.0, f"Negative distance: {dist}"

    @given(point=bahrain_point)
    @settings(max_examples=200)
    def test_distance_to_self_is_zero(self, point):
        """
        **Validates: Requirements 9.2** (Property 2)

        Distance from any point to itself must be zero.
        """
        lat, lon = point

        dist = haversine(lat, lon, lat, lon)

        assert dist == pytest.approx(0.0, abs=1e-10), (
            f"Distance to self is not zero: {dist}"
        )


class TestATMLocationCoordinates:
    """Verify all real ATM locations fall within Bahrain boundaries."""

    @pytest.fixture(scope="class")
    def atm_locations(self):
        return load_atm_locations()

    def test_all_coordinates_within_bahrain(self, atm_locations):
        """
        **Validates: Requirements 9.5** (Property 2)

        All 28 ATM coordinates must fall within Bahrain's geographic
        boundaries: 25.5-26.3°N latitude, 50.4-50.8°E longitude.
        """
        for loc in atm_locations:
            lat = loc["latitude"]
            lon = loc["longitude"]
            assert BAHRAIN_LAT_MIN <= lat <= BAHRAIN_LAT_MAX, (
                f"{loc['atm_id']} latitude {lat} outside Bahrain bounds "
                f"[{BAHRAIN_LAT_MIN}, {BAHRAIN_LAT_MAX}]"
            )
            assert BAHRAIN_LON_MIN <= lon <= BAHRAIN_LON_MAX, (
                f"{loc['atm_id']} longitude {lon} outside Bahrain bounds "
                f"[{BAHRAIN_LON_MIN}, {BAHRAIN_LON_MAX}]"
            )

    def test_expected_atm_count(self, atm_locations):
        """
        **Validates: Requirements 9.1**

        The dataset must contain exactly 28 ATM locations.
        """
        assert len(atm_locations) == 28, (
            f"Expected 28 ATMs, found {len(atm_locations)}"
        )

    def test_all_pairwise_distances_within_bounds(self, atm_locations):
        """
        **Validates: Requirements 9.2, 9.5** (Property 2, 6)

        Every pairwise distance between real ATM locations must be
        less than 60 km and symmetric.
        """
        for i, a in enumerate(atm_locations):
            for j, b in enumerate(atm_locations):
                d_ab = haversine(a["latitude"], a["longitude"],
                                 b["latitude"], b["longitude"])
                d_ba = haversine(b["latitude"], b["longitude"],
                                 a["latitude"], a["longitude"])

                assert d_ab < BAHRAIN_MAX_DISTANCE_KM, (
                    f"Distance {a['atm_id']}→{b['atm_id']} = {d_ab:.4f} km "
                    f"exceeds {BAHRAIN_MAX_DISTANCE_KM} km"
                )
                assert d_ab == pytest.approx(d_ba, abs=1e-10), (
                    f"Asymmetric: {a['atm_id']}↔{b['atm_id']}: "
                    f"{d_ab} != {d_ba}"
                )
