"""
Centralized bank alias management via AWS SSM Parameter Store.

Both the Streamlit frontend (me-south-1) and AgentCore Runtime (eu-west-1)
read the bank alias from the same SSM parameter, ensuring consistency.

The admin Settings tab writes to SSM; all components read from it.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# SSM parameter path and region
SSM_PARAM_NAME = "/atm-optimizer/bank-alias"
SSM_REGION = "me-south-1"  # Parameter lives where the data is

# Cache to avoid hitting SSM on every request
_cache: dict[str, tuple[str, float]] = {}
_CACHE_TTL_SECONDS = 60

# Default if SSM parameter doesn't exist yet
_DEFAULT_BANK_NAME = "Blue Bank"

# All banks in the Bahrain ecosystem (for UI dropdown)
# Fictional colour-based names matched to real bank logo colours:
#   BBK         → Blue Bank   (blue brand identity)
#   NBB         → Red Bank    (orange/red logo)
#   AUB         → Gold Bank   (gold & turquoise brand)
#   BisB        → Green Bank  (green Islamic banking identity)
#   Khaleeji    → Purple Bank (2024 rebrand, purple/magenta)
#   Al Salam    → Teal Bank   (teal/dark blue logo)
AVAILABLE_BANKS = ["Blue Bank", "Red Bank", "Gold Bank", "Green Bank", "Purple Bank", "Teal Bank"]

_ssm_client = None


def _get_ssm_client():
    """Return cached SSM client for me-south-1."""
    global _ssm_client
    if _ssm_client is None:
        _ssm_client = boto3.client("ssm", region_name=SSM_REGION)
    return _ssm_client


def get_bank_alias() -> str:
    """Read the current bank alias from SSM Parameter Store (cached).

    Returns the cached value if within TTL, otherwise fetches from SSM.
    Falls back to _DEFAULT_BANK_NAME if SSM is unavailable.
    """
    now = time.time()
    cached = _cache.get("bank_alias")
    if cached and (now - cached[1]) < _CACHE_TTL_SECONDS:
        return cached[0]

    try:
        client = _get_ssm_client()
        resp = client.get_parameter(Name=SSM_PARAM_NAME)
        value = resp["Parameter"]["Value"].strip()
        _cache["bank_alias"] = (value, now)
        return value
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ParameterNotFound":
            logger.info("SSM parameter %s not found, creating with default '%s'", SSM_PARAM_NAME, _DEFAULT_BANK_NAME)
            set_bank_alias(_DEFAULT_BANK_NAME)
            return _DEFAULT_BANK_NAME
        logger.warning("SSM read error (%s): %s", code, e)
        return _cache.get("bank_alias", (_DEFAULT_BANK_NAME, 0))[0]
    except Exception as e:
        logger.warning("SSM unavailable, using cached/default: %s", e)
        return _cache.get("bank_alias", (_DEFAULT_BANK_NAME, 0))[0]


def set_bank_alias(bank_name: str) -> bool:
    """Write the bank alias to SSM Parameter Store.

    Called by the admin Settings tab when the dropdown changes.
    Returns True on success, False on failure.
    """
    try:
        client = _get_ssm_client()
        client.put_parameter(
            Name=SSM_PARAM_NAME,
            Value=bank_name,
            Type="String",
            Overwrite=True,
            Description="Bank display name alias for ATM Optimizer demos",
        )
        _cache["bank_alias"] = (bank_name, time.time())
        logger.info("Bank alias updated to '%s' in SSM", bank_name)
        return True
    except Exception as e:
        logger.error("Failed to write bank alias to SSM: %s", e)
        return False


def get_excluded_banks() -> list[str]:
    """Return list of banks to exclude from competitor queries.

    The excluded bank is always the currently selected alias.
    """
    return [get_bank_alias()]
