"""
Reusable Athena query client for the ATM Profitability Optimizer.

Features:
  - VPC endpoint configuration for private network access
  - In-memory result caching with configurable TTL
  - Retry logic with exponential backoff for transient failures
  - Data ingestion validation for required fields and types

All queries target me-south-1 (Bahrain) region.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

# Allow imports from project root
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agent.config import (
    ATHENA_DATABASE,
    ATHENA_OUTPUT_LOCATION,
    ATHENA_QUERY_TIMEOUT_SECONDS,
    DATA_REGION,
    MCP_CACHE_TTL_SECONDS,
    MCP_MAX_RETRIES,
    MCP_RETRY_BACKOFF_BASE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data validation schemas — required fields and types per table
# ---------------------------------------------------------------------------

TABLE_SCHEMAS: dict[str, dict[str, type]] = {
    "atm_transactions": {
        "transaction_id": str,
        "atm_id": str,
        "timestamp": str,
        "transaction_type": str,
        "amount": Decimal,
        "fee": Decimal,
    },
    "atm_locations": {
        "atm_id": str,
        "name": str,
        "latitude": float,
        "longitude": float,
        "location_type": str,
        "branch_id": str,
        "daily_capacity": int,
        "status": str,
    },
    "branch_locations": {
        "branch_id": str,
        "name": str,
        "latitude": float,
        "longitude": float,
        "atm_count": int,
        "avg_daily_footfall": int,
    },
    "atm_proximity": {
        "source_atm_id": str,
        "target_atm_id": str,
        "distance_km": float,
        "is_same_branch": bool,
    },
    "maintenance_costs": {
        "atm_id": str,
        "date": str,
        "maintenance_type": str,
        "cost": Decimal,
        "downtime_hours": float,
    },
    "cash_levels": {
        "atm_id": str,
        "date": str,
        "opening_balance": Decimal,
        "closing_balance": Decimal,
        "total_withdrawals": Decimal,
        "replenishment_amount": Decimal,
        "replenishment_cost": Decimal,
    },
}

# Valid enum values for constrained fields
VALID_TRANSACTION_TYPES = {"withdrawal", "deposit", "balance_inquiry"}
VALID_LOCATION_TYPES = {"mall", "branch", "hospital", "standalone", "airport"}
VALID_MAINTENANCE_TYPES = {"preventive", "corrective", "emergency"}
VALID_ATM_STATUSES = {"active", "maintenance", "offline"}

# Bahrain geographic bounds
BAHRAIN_LAT_MIN = 25.5
BAHRAIN_LAT_MAX = 26.3
BAHRAIN_LON_MIN = 50.4
BAHRAIN_LON_MAX = 50.8


# ---------------------------------------------------------------------------
# Data validation
# ---------------------------------------------------------------------------

class ValidationError(Exception):
    """Raised when a data record fails validation."""

    def __init__(self, table: str, field: str, message: str):
        self.table = table
        self.field = field
        self.message = message
        super().__init__(f"[{table}.{field}] {message}")


def validate_record(table_name: str, record: dict[str, Any]) -> list[ValidationError]:
    """
    Validate a single data record against the table schema.

    Returns a list of ValidationError instances (empty if valid).
    Checks:
      1. All required fields are present
      2. No field value is None or empty string
      3. Numeric fields are parseable and non-negative where applicable
      4. Enum fields contain valid values
      5. Geographic coordinates are within Bahrain bounds
    """
    schema = TABLE_SCHEMAS.get(table_name)
    if schema is None:
        return [ValidationError(table_name, "", f"Unknown table: {table_name}")]

    errors: list[ValidationError] = []

    # 1. Check required fields are present
    for field in schema:
        if field not in record:
            errors.append(ValidationError(table_name, field, "Missing required field"))

    # If fields are missing, skip further checks on those fields
    present_fields = set(record.keys()) & set(schema.keys())

    for field in present_fields:
        value = record[field]
        expected_type = schema[field]

        # 2. Check for None / empty
        if value is None:
            errors.append(ValidationError(table_name, field, "Value is None"))
            continue
        if isinstance(value, str) and value.strip() == "":
            errors.append(ValidationError(table_name, field, "Value is empty string"))
            continue

        # 3. Type coercion / validation
        try:
            if expected_type is Decimal:
                coerced = Decimal(str(value))
                if coerced < 0:
                    errors.append(ValidationError(
                        table_name, field, f"Negative value not allowed: {value}"
                    ))
            elif expected_type is float:
                coerced = float(value)
                if field == "distance_km" and coerced < 0:
                    errors.append(ValidationError(
                        table_name, field, f"Negative distance: {value}"
                    ))
                if field == "downtime_hours" and coerced < 0:
                    errors.append(ValidationError(
                        table_name, field, f"Negative downtime: {value}"
                    ))
            elif expected_type is int:
                coerced = int(value)
                if coerced < 0:
                    errors.append(ValidationError(
                        table_name, field, f"Negative integer not allowed: {value}"
                    ))
            elif expected_type is bool:
                if isinstance(value, str) and value.lower() not in ("true", "false"):
                    errors.append(ValidationError(
                        table_name, field, f"Invalid boolean: {value}"
                    ))
        except (ValueError, TypeError, InvalidOperation):
            errors.append(ValidationError(
                table_name, field, f"Cannot convert '{value}' to {expected_type.__name__}"
            ))
            continue

    # 4. Enum validation
    if table_name == "atm_transactions" and "transaction_type" in present_fields:
        val = record["transaction_type"]
        if isinstance(val, str) and val not in VALID_TRANSACTION_TYPES:
            errors.append(ValidationError(
                table_name, "transaction_type",
                f"Invalid transaction type: '{val}'. Must be one of {VALID_TRANSACTION_TYPES}"
            ))

    if table_name == "atm_locations" and "location_type" in present_fields:
        val = record["location_type"]
        if isinstance(val, str) and val not in VALID_LOCATION_TYPES:
            errors.append(ValidationError(
                table_name, "location_type",
                f"Invalid location type: '{val}'. Must be one of {VALID_LOCATION_TYPES}"
            ))

    if table_name == "atm_locations" and "status" in present_fields:
        val = record["status"]
        if isinstance(val, str) and val not in VALID_ATM_STATUSES:
            errors.append(ValidationError(
                table_name, "status",
                f"Invalid status: '{val}'. Must be one of {VALID_ATM_STATUSES}"
            ))

    if table_name == "maintenance_costs" and "maintenance_type" in present_fields:
        val = record["maintenance_type"]
        if isinstance(val, str) and val not in VALID_MAINTENANCE_TYPES:
            errors.append(ValidationError(
                table_name, "maintenance_type",
                f"Invalid maintenance type: '{val}'. Must be one of {VALID_MAINTENANCE_TYPES}"
            ))

    # 5. Geographic bounds
    if table_name in ("atm_locations", "branch_locations"):
        for coord_field, lo, hi in [
            ("latitude", BAHRAIN_LAT_MIN, BAHRAIN_LAT_MAX),
            ("longitude", BAHRAIN_LON_MIN, BAHRAIN_LON_MAX),
        ]:
            if coord_field in present_fields:
                try:
                    val = float(record[coord_field])
                    if not (lo <= val <= hi):
                        errors.append(ValidationError(
                            table_name, coord_field,
                            f"Value {val} outside Bahrain bounds [{lo}, {hi}]"
                        ))
                except (ValueError, TypeError):
                    pass  # Already caught above

    return errors


# ---------------------------------------------------------------------------
# Result cache
# ---------------------------------------------------------------------------

class _CacheEntry:
    """Single cached query result with expiry."""

    __slots__ = ("result", "expires_at")

    def __init__(self, result: list[dict], ttl_seconds: int):
        self.result = result
        self.expires_at = time.monotonic() + ttl_seconds


class QueryCache:
    """Simple in-memory cache keyed by query string."""

    def __init__(self, ttl_seconds: int = MCP_CACHE_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> list[dict] | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            del self._store[key]
            return None
        return entry.result

    def put(self, key: str, result: list[dict]) -> None:
        self._store[key] = _CacheEntry(result, self._ttl)

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._store.clear()
        else:
            self._store.pop(key, None)

    @property
    def size(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Athena client
# ---------------------------------------------------------------------------

class AthenaClient:
    """
    Reusable Athena query client.

    - Targets me-south-1 with optional VPC endpoint URL
    - Caches results in memory with configurable TTL
    - Retries transient failures with exponential backoff
    """

    def __init__(
        self,
        database: str = ATHENA_DATABASE,
        output_location: str = ATHENA_OUTPUT_LOCATION,
        region: str = DATA_REGION,
        cache_ttl: int = MCP_CACHE_TTL_SECONDS,
        max_retries: int = MCP_MAX_RETRIES,
        backoff_base: int = MCP_RETRY_BACKOFF_BASE,
        endpoint_url: str | None = None,
        workgroup: str | None = None,
    ):
        self.database = database
        self.output_location = output_location
        self.workgroup = workgroup or os.environ.get("ATM_ATHENA_WORKGROUP", "atm-optimizer")
        self.region = region
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.cache = QueryCache(ttl_seconds=cache_ttl)

        # VPC endpoint configuration
        boto_config = BotoConfig(
            region_name=region,
            retries={"max_attempts": 0},  # We handle retries ourselves
        )
        client_kwargs: dict[str, Any] = {"config": boto_config}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        else:
            # Check environment for VPC endpoint
            vpc_endpoint = os.environ.get("ATM_ATHENA_VPC_ENDPOINT")
            if vpc_endpoint:
                client_kwargs["endpoint_url"] = vpc_endpoint

        self._client = boto3.client("athena", **client_kwargs)

    def execute_query(
        self,
        query: str,
        use_cache: bool = True,
        timeout: int = ATHENA_QUERY_TIMEOUT_SECONDS,
    ) -> list[dict]:
        """
        Execute an Athena SQL query and return results as list of dicts.

        Uses caching and retry with exponential backoff.
        """
        # Check cache first
        if use_cache:
            cached = self.cache.get(query)
            if cached is not None:
                logger.debug("Cache hit for query: %s…", query[:80])
                return cached

        # Execute with retries
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                result = self._run_query(query, timeout)
                if use_cache:
                    self.cache.put(query, result)
                return result
            except ClientError as exc:
                error_code = exc.response["Error"]["Code"]
                # Retry on transient errors only
                if error_code in (
                    "ThrottlingException",
                    "TooManyRequestsException",
                    "InternalServerException",
                    "ServiceUnavailableException",
                ):
                    last_error = exc
                    if attempt < self.max_retries:
                        wait = self.backoff_base ** attempt
                        logger.warning(
                            "Transient error (attempt %d/%d): %s. Retrying in %ds…",
                            attempt + 1, self.max_retries + 1, error_code, wait,
                        )
                        time.sleep(wait)
                        continue
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self.max_retries:
                    wait = self.backoff_base ** attempt
                    logger.warning(
                        "Error (attempt %d/%d): %s. Retrying in %ds…",
                        attempt + 1, self.max_retries + 1, exc, wait,
                    )
                    time.sleep(wait)
                    continue
                raise

        raise last_error  # type: ignore[misc]

    def _run_query(self, query: str, timeout: int) -> list[dict]:
        """Start a query, poll for completion, and parse results."""
        start_params: dict[str, Any] = {
            "QueryString": query,
            "QueryExecutionContext": {"Database": self.database},
            "WorkGroup": self.workgroup,
        }
        # When using a workgroup with EnforceWorkGroupConfiguration=true,
        # the workgroup's ResultConfiguration takes precedence. We still
        # pass OutputLocation as a fallback for workgroups that don't enforce.
        if self.output_location:
            start_params["ResultConfiguration"] = {
                "OutputLocation": self.output_location,
            }
        response = self._client.start_query_execution(**start_params)
        query_id = response["QueryExecutionId"]

        # Poll for completion
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = self._client.get_query_execution(QueryExecutionId=query_id)
            state = status["QueryExecution"]["Status"]["State"]

            if state == "SUCCEEDED":
                return self._fetch_results(query_id)
            if state in ("FAILED", "CANCELLED"):
                reason = status["QueryExecution"]["Status"].get(
                    "StateChangeReason", "Unknown error"
                )
                raise RuntimeError(f"Athena query {state}: {reason}")

            time.sleep(1)

        raise TimeoutError(
            f"Athena query timed out after {timeout}s (query_id={query_id})"
        )

    def _fetch_results(self, query_id: str) -> list[dict]:
        """Fetch all result rows for a completed query."""
        rows: list[dict] = []
        paginator = self._client.get_paginator("get_query_results")

        for page in paginator.paginate(QueryExecutionId=query_id):
            result_set = page["ResultSet"]
            columns = [
                col["Name"]
                for col in result_set["ResultSetMetadata"]["ColumnInfo"]
            ]

            for i, row in enumerate(result_set["Rows"]):
                # Skip header row on first page
                if i == 0 and not rows:
                    continue
                values = [
                    datum.get("VarCharValue") for datum in row["Data"]
                ]
                rows.append(dict(zip(columns, values)))

        return rows
