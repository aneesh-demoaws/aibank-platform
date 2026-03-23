"""
Role-Based Access Control for ATM Profitability Optimizer.

Maps Cognito user groups to permitted MCP tools, enforcing that Operator
users can only access basic query tools while Admin users have full access.

Validates: Requirements 10.5, 10.6, 10.7, 10.8
"""

from __future__ import annotations

from typing import Optional

from agent.config import ADMIN_TOOLS, OPERATOR_TOOLS

# Supported roles (matching Cognito group names)
ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"

_ROLE_TOOL_MAP: dict[str, list[str]] = {
    ROLE_ADMIN: ADMIN_TOOLS,
    ROLE_OPERATOR: OPERATOR_TOOLS,
}

ACCESS_DENIED_MESSAGE = (
    "Access denied: this feature requires Admin privileges. "
    "Your current Operator role only permits basic ATM queries "
    "(query_atm_data, query_branch_proximity, query_revenue_data). "
    "Please contact your administrator to request elevated access."
)


def get_permitted_tools(role: str) -> list[str]:
    """Return the list of MCP tool names permitted for *role*.

    Parameters
    ----------
    role:
        User role string extracted from the Cognito ``cognito:groups``
        JWT claim.  Expected values are ``"admin"`` or ``"operator"``.

    Returns
    -------
    list[str]
        Tool names the role is allowed to invoke.  An unrecognised role
        is treated as having **no** permissions (empty list).
    """
    return list(_ROLE_TOOL_MAP.get(role, []))


def is_tool_permitted(role: str, tool_name: str) -> bool:
    """Check whether *role* is allowed to invoke *tool_name*.

    Parameters
    ----------
    role:
        User role (``"admin"`` or ``"operator"``).
    tool_name:
        Name of the MCP tool to check.

    Returns
    -------
    bool
        ``True`` if the tool is in the role's permitted set.
    """
    return tool_name in _ROLE_TOOL_MAP.get(role, [])


def get_access_denied_response(tool_name: str) -> str:
    """Return a user-friendly denial message for *tool_name*.

    Parameters
    ----------
    tool_name:
        The MCP tool the Operator attempted to use.

    Returns
    -------
    str
        A message explaining the restriction and suggesting next steps.
    """
    return (
        f"Access denied: the tool '{tool_name}' requires Admin privileges. "
        "Your current Operator role only permits basic ATM queries "
        "(query_atm_data, query_branch_proximity, query_revenue_data). "
        "Please contact your administrator to request elevated access."
    )


def extract_role_from_claims(claims: dict) -> str:
    """Extract the user role from decoded Cognito JWT claims.

    The function inspects the ``cognito:groups`` claim and returns the
    highest-privilege role found.  If the user belongs to both groups,
    ``"admin"`` takes precedence.

    Parameters
    ----------
    claims:
        Decoded JWT payload dictionary.

    Returns
    -------
    str
        ``"admin"``, ``"operator"``, or ``"unknown"`` if no recognised
        group is present.
    """
    groups: list[str] = claims.get("cognito:groups", [])
    if ROLE_ADMIN in groups:
        return ROLE_ADMIN
    if ROLE_OPERATOR in groups:
        return ROLE_OPERATOR
    return "unknown"
