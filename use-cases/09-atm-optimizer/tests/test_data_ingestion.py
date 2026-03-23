"""
Property-based tests for data ingestion validation.

**Validates: Requirements 12.4, 12.5**

Uses Hypothesis to verify:
  - Data ingestion validates all required fields are present
  - Invalid records (missing fields, wrong types, out-of-range values) are rejected
  - Valid records pass validation with zero errors
"""

import os
import sys
from decimal import Decimal

import pytest
from hypothesis import given, settings, assume
from hypothesis.strategies import (
    booleans,
    composite,
    decimals,
    fixed_dictionaries,
    floats,
    from_regex,
    integers,
    just,
    none,
    one_of,
    sampled_from,
    text,
)

# ---------------------------------------------------------------------------
# Make project modules importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from mcp_server.athena_client import (
    TABLE_SCHEMAS,
    VALID_ATM_STATUSES,
    VALID_LOCATION_TYPES,
    VALID_MAINTENANCE_TYPES,
    VALID_TRANSACTION_TYPES,
    ValidationError,
    validate_record,
)


# ---------------------------------------------------------------------------
# Strategies for generating valid records
# ---------------------------------------------------------------------------

# Bahrain geographic bounds
BAHRAIN_LAT = floats(min_value=25.5, max_value=26.3, allow_nan=False, allow_infinity=False)
BAHRAIN_LON = floats(min_value=50.4, max_value=50.8, allow_nan=False, allow_infinity=False)

# Non-empty identifier strings
atm_id_st = from_regex(r"ATM_[A-Z]{3,10}_\d{2}", fullmatch=True)
branch_id_st = from_regex(r"BR_[A-Z]{3,10}_\d{2}", fullmatch=True)
name_st = text(min_size=1, max_size=50).filter(lambda s: s.strip() != "")
date_st = from_regex(r"2025-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])", fullmatch=True)
timestamp_st = from_regex(
    r"2025-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])T([01]\d|2[0-3]):[0-5]\d:[0-5]\d",
    fullmatch=True,
)
positive_decimal = decimals(
    min_value=Decimal("0.000"), max_value=Decimal("9999.999"),
    allow_nan=False, allow_infinity=False, places=3,
)
positive_large_decimal = decimals(
    min_value=Decimal("0.000"), max_value=Decimal("999999.999"),
    allow_nan=False, allow_infinity=False, places=3,
)
positive_float = floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
positive_int = integers(min_value=0, max_value=10000)
transaction_id_st = from_regex(r"[a-f0-9]{16}", fullmatch=True)


@composite
def valid_atm_transaction(draw):
    return {
        "transaction_id": draw(transaction_id_st),
        "atm_id": draw(atm_id_st),
        "timestamp": draw(timestamp_st),
        "transaction_type": draw(sampled_from(sorted(VALID_TRANSACTION_TYPES))),
        "amount": draw(positive_decimal),
        "fee": draw(positive_decimal),
    }


@composite
def valid_atm_location(draw):
    return {
        "atm_id": draw(atm_id_st),
        "name": draw(name_st),
        "latitude": draw(BAHRAIN_LAT),
        "longitude": draw(BAHRAIN_LON),
        "location_type": draw(sampled_from(sorted(VALID_LOCATION_TYPES))),
        "branch_id": draw(branch_id_st),
        "daily_capacity": draw(positive_int),
        "status": draw(sampled_from(sorted(VALID_ATM_STATUSES))),
    }


@composite
def valid_branch_location(draw):
    return {
        "branch_id": draw(branch_id_st),
        "name": draw(name_st),
        "latitude": draw(BAHRAIN_LAT),
        "longitude": draw(BAHRAIN_LON),
        "atm_count": draw(positive_int),
        "avg_daily_footfall": draw(positive_int),
    }


@composite
def valid_atm_proximity(draw):
    return {
        "source_atm_id": draw(atm_id_st),
        "target_atm_id": draw(atm_id_st),
        "distance_km": draw(positive_float),
        "is_same_branch": draw(booleans()),
    }


@composite
def valid_maintenance_cost(draw):
    return {
        "atm_id": draw(atm_id_st),
        "date": draw(date_st),
        "maintenance_type": draw(sampled_from(sorted(VALID_MAINTENANCE_TYPES))),
        "cost": draw(positive_decimal),
        "downtime_hours": draw(positive_float),
    }


@composite
def valid_cash_level(draw):
    return {
        "atm_id": draw(atm_id_st),
        "date": draw(date_st),
        "opening_balance": draw(positive_large_decimal),
        "closing_balance": draw(positive_large_decimal),
        "total_withdrawals": draw(positive_large_decimal),
        "replenishment_amount": draw(positive_large_decimal),
        "replenishment_cost": draw(positive_decimal),
    }


# Map table names to their valid record strategies
VALID_RECORD_STRATEGIES = {
    "atm_transactions": valid_atm_transaction(),
    "atm_locations": valid_atm_location(),
    "branch_locations": valid_branch_location(),
    "atm_proximity": valid_atm_proximity(),
    "maintenance_costs": valid_maintenance_cost(),
    "cash_levels": valid_cash_level(),
}


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------

class TestValidRecordsAccepted:
    """Valid records must pass validation with zero errors."""

    @given(record=valid_atm_transaction())
    @settings(max_examples=200)
    def test_valid_atm_transaction_accepted(self, record):
        """
        **Validates: Requirements 12.4**

        A well-formed atm_transactions record with all required fields,
        valid types, and valid enum values must produce zero validation errors.
        """
        errors = validate_record("atm_transactions", record)
        assert errors == [], f"Valid record rejected: {[str(e) for e in errors]}"

    @given(record=valid_atm_location())
    @settings(max_examples=200)
    def test_valid_atm_location_accepted(self, record):
        """
        **Validates: Requirements 12.4**

        A well-formed atm_locations record must produce zero validation errors.
        """
        errors = validate_record("atm_locations", record)
        assert errors == [], f"Valid record rejected: {[str(e) for e in errors]}"

    @given(record=valid_branch_location())
    @settings(max_examples=200)
    def test_valid_branch_location_accepted(self, record):
        """
        **Validates: Requirements 12.4**

        A well-formed branch_locations record must produce zero validation errors.
        """
        errors = validate_record("branch_locations", record)
        assert errors == [], f"Valid record rejected: {[str(e) for e in errors]}"

    @given(record=valid_atm_proximity())
    @settings(max_examples=200)
    def test_valid_atm_proximity_accepted(self, record):
        """
        **Validates: Requirements 12.4**

        A well-formed atm_proximity record must produce zero validation errors.
        """
        errors = validate_record("atm_proximity", record)
        assert errors == [], f"Valid record rejected: {[str(e) for e in errors]}"

    @given(record=valid_maintenance_cost())
    @settings(max_examples=200)
    def test_valid_maintenance_cost_accepted(self, record):
        """
        **Validates: Requirements 12.4**

        A well-formed maintenance_costs record must produce zero validation errors.
        """
        errors = validate_record("maintenance_costs", record)
        assert errors == [], f"Valid record rejected: {[str(e) for e in errors]}"

    @given(record=valid_cash_level())
    @settings(max_examples=200)
    def test_valid_cash_level_accepted(self, record):
        """
        **Validates: Requirements 12.4**

        A well-formed cash_levels record must produce zero validation errors.
        """
        errors = validate_record("cash_levels", record)
        assert errors == [], f"Valid record rejected: {[str(e) for e in errors]}"


class TestMissingFieldsRejected:
    """Records with missing required fields must be rejected."""

    @given(record=valid_atm_transaction())
    @settings(max_examples=200)
    def test_missing_field_produces_error(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        Removing any single required field from a valid atm_transactions record
        must produce at least one validation error mentioning the missing field.
        """
        for field in list(record.keys()):
            incomplete = {k: v for k, v in record.items() if k != field}
            errors = validate_record("atm_transactions", incomplete)
            assert len(errors) >= 1, (
                f"Missing field '{field}' was not detected"
            )
            missing_errors = [e for e in errors if e.field == field]
            assert len(missing_errors) >= 1, (
                f"No error specifically for missing field '{field}'"
            )

    @given(record=valid_cash_level())
    @settings(max_examples=200)
    def test_missing_cash_level_field_produces_error(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        Removing any single required field from a valid cash_levels record
        must produce at least one validation error.
        """
        for field in list(record.keys()):
            incomplete = {k: v for k, v in record.items() if k != field}
            errors = validate_record("cash_levels", incomplete)
            assert len(errors) >= 1, (
                f"Missing field '{field}' was not detected"
            )


class TestInvalidValuesRejected:
    """Records with invalid values must be rejected."""

    @given(record=valid_atm_transaction())
    @settings(max_examples=200)
    def test_invalid_transaction_type_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        A transaction record with an invalid transaction_type must be rejected.
        """
        record["transaction_type"] = "invalid_type"
        errors = validate_record("atm_transactions", record)
        type_errors = [e for e in errors if e.field == "transaction_type"]
        assert len(type_errors) >= 1, "Invalid transaction_type was not rejected"

    @given(record=valid_atm_transaction())
    @settings(max_examples=200)
    def test_negative_amount_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        A transaction record with a negative amount must be rejected.
        """
        record["amount"] = Decimal("-10.000")
        errors = validate_record("atm_transactions", record)
        amount_errors = [e for e in errors if e.field == "amount"]
        assert len(amount_errors) >= 1, "Negative amount was not rejected"

    @given(record=valid_atm_transaction())
    @settings(max_examples=200)
    def test_negative_fee_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        A transaction record with a negative fee must be rejected.
        """
        record["fee"] = Decimal("-1.000")
        errors = validate_record("atm_transactions", record)
        fee_errors = [e for e in errors if e.field == "fee"]
        assert len(fee_errors) >= 1, "Negative fee was not rejected"

    @given(record=valid_atm_location())
    @settings(max_examples=200)
    def test_out_of_bounds_latitude_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        An ATM location with latitude outside Bahrain bounds must be rejected.
        """
        record["latitude"] = 40.0  # Outside Bahrain
        errors = validate_record("atm_locations", record)
        lat_errors = [e for e in errors if e.field == "latitude"]
        assert len(lat_errors) >= 1, "Out-of-bounds latitude was not rejected"

    @given(record=valid_atm_location())
    @settings(max_examples=200)
    def test_out_of_bounds_longitude_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        An ATM location with longitude outside Bahrain bounds must be rejected.
        """
        record["longitude"] = 10.0  # Outside Bahrain
        errors = validate_record("atm_locations", record)
        lon_errors = [e for e in errors if e.field == "longitude"]
        assert len(lon_errors) >= 1, "Out-of-bounds longitude was not rejected"

    @given(record=valid_atm_location())
    @settings(max_examples=200)
    def test_invalid_location_type_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        An ATM location with an invalid location_type must be rejected.
        """
        record["location_type"] = "underwater"
        errors = validate_record("atm_locations", record)
        type_errors = [e for e in errors if e.field == "location_type"]
        assert len(type_errors) >= 1, "Invalid location_type was not rejected"

    @given(record=valid_atm_location())
    @settings(max_examples=200)
    def test_invalid_status_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        An ATM location with an invalid status must be rejected.
        """
        record["status"] = "destroyed"
        errors = validate_record("atm_locations", record)
        status_errors = [e for e in errors if e.field == "status"]
        assert len(status_errors) >= 1, "Invalid status was not rejected"

    @given(record=valid_maintenance_cost())
    @settings(max_examples=200)
    def test_invalid_maintenance_type_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        A maintenance record with an invalid maintenance_type must be rejected.
        """
        record["maintenance_type"] = "magic"
        errors = validate_record("maintenance_costs", record)
        type_errors = [e for e in errors if e.field == "maintenance_type"]
        assert len(type_errors) >= 1, "Invalid maintenance_type was not rejected"

    @given(record=valid_atm_transaction())
    @settings(max_examples=200)
    def test_none_value_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        Setting any field to None must produce a validation error.
        """
        for field in list(record.keys()):
            broken = dict(record)
            broken[field] = None
            errors = validate_record("atm_transactions", broken)
            none_errors = [e for e in errors if e.field == field]
            assert len(none_errors) >= 1, (
                f"None value for '{field}' was not rejected"
            )

    @given(record=valid_atm_transaction())
    @settings(max_examples=200)
    def test_empty_string_rejected(self, record):
        """
        **Validates: Requirements 12.4, 12.5**

        Setting any string field to empty must produce a validation error.
        """
        string_fields = ["transaction_id", "atm_id", "timestamp", "transaction_type"]
        for field in string_fields:
            broken = dict(record)
            broken[field] = ""
            errors = validate_record("atm_transactions", broken)
            empty_errors = [e for e in errors if e.field == field]
            assert len(empty_errors) >= 1, (
                f"Empty string for '{field}' was not rejected"
            )


class TestUnknownTableRejected:
    """Validation against an unknown table name must produce an error."""

    def test_unknown_table_returns_error(self):
        """
        **Validates: Requirements 12.5**

        Attempting to validate a record against a non-existent table
        must return an error.
        """
        errors = validate_record("nonexistent_table", {"foo": "bar"})
        assert len(errors) == 1
        assert "Unknown table" in errors[0].message
