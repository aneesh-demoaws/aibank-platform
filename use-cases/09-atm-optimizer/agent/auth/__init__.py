# ATM Profitability Optimizer - Agent Auth Package

from agent.auth.role_manager import (
    ACCESS_DENIED_MESSAGE,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    extract_role_from_claims,
    get_access_denied_response,
    get_permitted_tools,
    is_tool_permitted,
)
from agent.auth.tool_filter import filter_tools_for_role, role_gate

__all__ = [
    "ACCESS_DENIED_MESSAGE",
    "ROLE_ADMIN",
    "ROLE_OPERATOR",
    "extract_role_from_claims",
    "filter_tools_for_role",
    "get_access_denied_response",
    "get_permitted_tools",
    "is_tool_permitted",
    "role_gate",
]
