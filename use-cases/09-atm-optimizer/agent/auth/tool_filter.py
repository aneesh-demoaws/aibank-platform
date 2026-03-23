"""
Tool-filtering middleware for the Strands agent.

Wraps MCP tool calls so that the user's role is checked *before* the
underlying tool executes.  If the role lacks permission, the call is
short-circuited with an access-denied message — the real tool is never
invoked.

Validates: Requirements 10.7, 10.8
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

from agent.auth.role_manager import (
    get_access_denied_response,
    is_tool_permitted,
)

logger = logging.getLogger(__name__)


def role_gate(role: str) -> Callable:
    """Decorator factory that gates a tool function behind a role check.

    Usage::

        @role_gate(user_role)
        def query_maintenance_costs(atm_id, start_date, end_date):
            ...

    Parameters
    ----------
    role:
        The current user's role (``"admin"`` or ``"operator"``).

    Returns
    -------
    Callable
        A decorator that wraps the tool function with an authorisation
        check.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            tool_name = func.__name__
            if not is_tool_permitted(role, tool_name):
                logger.warning(
                    "Access denied: role=%s attempted tool=%s",
                    role,
                    tool_name,
                )
                return {"error": get_access_denied_response(tool_name)}
            return func(*args, **kwargs)

        return wrapper

    return decorator


def filter_tools_for_role(
    tools: list[Callable],
    role: str,
) -> list[Callable]:
    """Return a filtered copy of *tools* permitted for *role*.

    Each tool in the returned list is wrapped with :func:`role_gate` so
    that even if the agent somehow selects a tool outside the filtered
    set, the call will still be denied at execution time (defence in
    depth).

    Parameters
    ----------
    tools:
        Full list of MCP tool callables.  Each callable must expose its
        tool name via ``__name__``.
    role:
        The current user's role.

    Returns
    -------
    list[Callable]
        Only the tools the role is allowed to use, each wrapped with a
        role gate.
    """
    permitted: list[Callable] = []
    for tool in tools:
        if is_tool_permitted(role, tool.__name__):
            permitted.append(role_gate(role)(tool))
        else:
            logger.debug(
                "Filtering out tool=%s for role=%s",
                tool.__name__,
                role,
            )
    return permitted
