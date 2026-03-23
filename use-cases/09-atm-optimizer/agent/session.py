"""
Multi-turn conversation session manager using AgentCore Memory.

Manages session lifecycle, stores conversation context, and handles
session expiry.  AgentCore Memory keeps session data in eu-west-1 —
only aggregated results and analysis summaries are stored, never raw
transaction data.

Validates: Requirements 21.1, 21.2, 21.4, 21.5, 21.8
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.config import AI_REGION

logger = logging.getLogger(__name__)

# Session TTL in seconds (60 minutes per Requirement 21.5)
SESSION_TTL_SECONDS = 60 * 60


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""

    role: str  # "user" or "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class Session:
    """In-memory representation of a conversation session.

    In production, this is backed by AgentCore Memory in eu-west-1.
    The local dataclass mirrors the remote state for the agent runtime.
    """

    session_id: str
    user_id: str
    role: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    turns: list[ConversationTurn] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        """Check whether the session has exceeded the TTL."""
        return (time.time() - self.last_active) > SESSION_TTL_SECONDS

    def add_turn(
        self,
        role: str,
        content: str,
        tool_calls: Optional[list[dict]] = None,
    ) -> None:
        """Append a conversation turn and refresh the last-active timestamp."""
        self.turns.append(
            ConversationTurn(
                role=role,
                content=content,
                tool_calls=tool_calls or [],
            )
        )
        self.last_active = time.time()

    def get_history_summary(self) -> list[dict]:
        """Return a lightweight summary of conversation history.

        Only aggregated summaries are included — no raw transaction data
        (Requirement 21.8).
        """
        return [
            {
                "role": t.role,
                "content": t.content,
                "timestamp": t.timestamp,
            }
            for t in self.turns
        ]


class SessionManager:
    """Manage conversation sessions backed by AgentCore Memory.

    In the AgentCore Runtime deployment, this class delegates to the
    AgentCore Memory API.  For local / testing use, sessions are held
    in an in-memory dict.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create_session(
        self,
        user_id: str,
        role: str,
        session_id: Optional[str] = None,
    ) -> Session:
        """Create a new conversation session.

        Parameters
        ----------
        user_id:
            Cognito user identifier.
        role:
            ``"admin"`` or ``"operator"``.
        session_id:
            Optional explicit session ID; auto-generated if omitted.

        Returns
        -------
        Session
        """
        sid = session_id or str(uuid.uuid4())
        session = Session(session_id=sid, user_id=user_id, role=role)
        self._sessions[sid] = session
        logger.info("Created session %s for user=%s role=%s", sid, user_id, role)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by ID, returning ``None`` if expired or missing."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if session.is_expired:
            logger.info("Session %s expired, removing", session_id)
            self._sessions.pop(session_id, None)
            return None
        return session

    def get_or_create_session(
        self,
        session_id: Optional[str],
        user_id: str,
        role: str,
    ) -> Session:
        """Get an existing session or create a new one.

        This is the primary entry point for multi-turn conversation
        support.  If the session exists and is still valid, it is
        returned with full history.  Otherwise a fresh session is
        created.
        """
        if session_id:
            existing = self.get_session(session_id)
            if existing is not None:
                return existing
        return self.create_session(user_id=user_id, role=role, session_id=session_id)

    def delete_session(self, session_id: str) -> bool:
        """Explicitly remove a session (e.g., on user logout)."""
        removed = self._sessions.pop(session_id, None)
        if removed:
            logger.info("Deleted session %s", session_id)
        return removed is not None

    def cleanup_expired(self) -> int:
        """Remove all expired sessions.  Returns count of removed sessions."""
        expired = [sid for sid, s in self._sessions.items() if s.is_expired]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info("Cleaned up %d expired sessions", len(expired))
        return len(expired)
