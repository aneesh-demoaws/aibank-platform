# ATM Profitability Optimizer - Agent Package
#
# Lazy imports to avoid loading strands/bedrock dependencies
# when only tool modules are needed (e.g., Lambda MCP handler).


def create_agent(*args, **kwargs):
    from agent.agent import create_agent as _create_agent
    return _create_agent(*args, **kwargs)


def create_agent_from_claims(*args, **kwargs):
    from agent.agent import create_agent_from_claims as _create
    return _create(*args, **kwargs)


def _get_session():
    from agent.session import Session
    return Session


def _get_session_manager():
    from agent.session import SessionManager
    return SessionManager


__all__ = [
    "create_agent",
    "create_agent_from_claims",
]
