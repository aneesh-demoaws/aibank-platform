"""
AgentCore Memory configuration for ATM Profitability Optimizer.

Sets up:
- Session memory with 60-minute TTL for conversation context
- Long-term memory for persisting user preferences across sessions

Validates: Requirement 21 (AgentCore Memory)
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SESSION_TTL_MINUTES = 60
LONG_TERM_STORAGE_DAYS = 30


@dataclass(frozen=True)
class SessionMemoryConfig:
    """Session memory keeps conversation context alive for a single session.

    The TTL is set to 60 minutes to match the Cognito token validity
    window, ensuring memory expires when the user's session ends.
    """

    ttl_minutes: int = SESSION_TTL_MINUTES
    memory_type: str = "SESSION_SUMMARY"
    max_tokens: int = 4096  # Max context tokens retained per session

    @property
    def ttl_seconds(self) -> int:
        return self.ttl_minutes * 60

    def to_dict(self) -> dict:
        return {
            "type": self.memory_type,
            "ttlSeconds": self.ttl_seconds,
            "maxTokens": self.max_tokens,
        }


@dataclass(frozen=True)
class LongTermMemoryConfig:
    """Long-term memory persists user preferences across sessions.

    Stores items such as preferred ATM groupings, default date ranges,
    and frequently queried ATM IDs so the agent can personalise
    responses over time.
    """

    storage_days: int = LONG_TERM_STORAGE_DAYS
    memory_type: str = "SEMANTIC"
    max_entries_per_user: int = 100

    # Categories of preferences the agent can store
    preference_categories: tuple[str, ...] = (
        "preferred_atms",
        "default_date_range",
        "report_format",
        "alert_thresholds",
    )

    def to_dict(self) -> dict:
        return {
            "type": self.memory_type,
            "storageDays": self.storage_days,
            "maxEntriesPerUser": self.max_entries_per_user,
            "preferenceCategories": list(self.preference_categories),
        }


@dataclass(frozen=True)
class MemoryConfig:
    """Top-level AgentCore Memory configuration."""

    session: SessionMemoryConfig = field(default_factory=SessionMemoryConfig)
    long_term: LongTermMemoryConfig = field(default_factory=LongTermMemoryConfig)

    def to_dict(self) -> dict:
        """Full memory configuration for AgentCore deployment."""
        return {
            "enabledMemoryTypes": [
                self.session.memory_type,
                self.long_term.memory_type,
            ],
            "session": self.session.to_dict(),
            "longTerm": self.long_term.to_dict(),
        }
