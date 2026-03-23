"""
Cognito authentication integration for the Streamlit frontend.

Handles user login via AWS Cognito User Pool (me-south-1), JWT token
management, session timeout enforcement, and role extraction from
Cognito group claims.

Validates: Requirements 10.1-10.3, 17.8, 23.9
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional

import boto3
import streamlit as st
from botocore.exceptions import ClientError

from frontend.config import (
    COGNITO_ADMIN_GROUP,
    COGNITO_APP_CLIENT_ID,
    COGNITO_OPERATOR_GROUP,
    COGNITO_REGION,
    COGNITO_USER_POOL_ID,
    SESSION_TIMEOUT_MINUTES,
    TOKEN_EXPIRY_MINUTES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cognito client (lazy-initialised)
# ---------------------------------------------------------------------------
_cognito_client = None


def _get_cognito_client():
    """Return a cached Cognito Identity Provider client."""
    global _cognito_client
    if _cognito_client is None:
        _cognito_client = boto3.client(
            "cognito-idp", region_name=COGNITO_REGION
        )
    return _cognito_client


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _decode_jwt_payload(token: str) -> dict[str, Any]:
    """Decode the payload section of a JWT without signature verification.

    Signature verification is handled server-side by AgentCore Identity.
    Here we only need the claims for UI-level role decisions.
    """
    try:
        payload_b64 = token.split(".")[1]
        # Add padding if needed
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes)
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}


def _extract_role_from_token(id_token: str) -> str:
    """Extract the user role from Cognito group claims in the ID token.

    Returns ``"admin"`` if the user belongs to the admin group,
    ``"operator"`` otherwise.
    """
    claims = _decode_jwt_payload(id_token)
    groups = claims.get("cognito:groups", [])
    if COGNITO_ADMIN_GROUP in groups:
        return "admin"
    return "operator"


def _is_token_expired(id_token: str) -> bool:
    """Check whether the JWT has passed its ``exp`` claim."""
    claims = _decode_jwt_payload(id_token)
    exp = claims.get("exp", 0)
    return time.time() >= exp


# ---------------------------------------------------------------------------
# Session state helpers
# ---------------------------------------------------------------------------

def _init_session_state() -> None:
    """Ensure all auth-related keys exist in Streamlit session state."""
    defaults = {
        "authenticated": False,
        "id_token": None,
        "access_token": None,
        "refresh_token": None,
        "user_role": None,
        "username": None,
        "last_activity": None,
        "auth_error": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _update_activity() -> None:
    """Refresh the last-activity timestamp (for session timeout)."""
    st.session_state["last_activity"] = time.time()


def is_session_timed_out() -> bool:
    """Return True if the session has been inactive beyond the timeout."""
    last = st.session_state.get("last_activity")
    if last is None:
        return False
    elapsed_minutes = (time.time() - last) / 60
    return elapsed_minutes >= SESSION_TIMEOUT_MINUTES


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def authenticate(username: str, password: str) -> bool:
    """Authenticate a user against Cognito and store tokens in session state.

    Returns ``True`` on success, ``False`` on failure (error stored in
    ``st.session_state["auth_error"]``).
    """
    _init_session_state()
    client = _get_cognito_client()

    try:
        response = client.initiate_auth(
            ClientId=COGNITO_APP_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
            },
        )

        result = response.get("AuthenticationResult", {})
        id_token = result.get("IdToken", "")
        access_token = result.get("AccessToken", "")
        refresh_token = result.get("RefreshToken", "")

        if not id_token:
            st.session_state["auth_error"] = "Authentication failed — no token received."
            return False

        role = _extract_role_from_token(id_token)

        st.session_state.update(
            {
                "authenticated": True,
                "id_token": id_token,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "user_role": role,
                "username": username,
                "auth_error": None,
            }
        )
        _update_activity()
        logger.info("User %s authenticated with role=%s", username, role)
        return True

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code == "NotAuthorizedException":
            msg = "Invalid username or password."
        elif error_code == "UserNotFoundException":
            msg = "Invalid username or password."
        elif error_code == "UserNotConfirmedException":
            msg = "Account not confirmed. Please check your email."
        else:
            msg = "Authentication failed. Please try again."
            logger.error("Cognito auth error: %s", exc)

        st.session_state["auth_error"] = msg
        return False
    except Exception as exc:
        logger.error("Unexpected auth error: %s", exc)
        st.session_state["auth_error"] = "An unexpected error occurred."
        return False


def refresh_tokens() -> bool:
    """Attempt to refresh the session using the Cognito refresh token.

    Returns ``True`` on success.
    """
    refresh_token = st.session_state.get("refresh_token")
    if not refresh_token:
        return False

    client = _get_cognito_client()
    try:
        response = client.initiate_auth(
            ClientId=COGNITO_APP_CLIENT_ID,
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_token},
        )
        result = response.get("AuthenticationResult", {})
        id_token = result.get("IdToken", "")
        access_token = result.get("AccessToken", "")

        if not id_token:
            return False

        st.session_state["id_token"] = id_token
        st.session_state["access_token"] = access_token
        st.session_state["user_role"] = _extract_role_from_token(id_token)
        _update_activity()
        return True
    except (ClientError, Exception) as exc:
        logger.warning("Token refresh failed: %s", exc)
        return False


def ensure_authenticated() -> bool:
    """Gate check — returns True if the user has a valid, active session.

    Handles token expiry and session timeout transparently.
    """
    _init_session_state()

    if not st.session_state.get("authenticated"):
        return False

    # Session inactivity timeout (Requirement 17.8)
    if is_session_timed_out():
        logout()
        st.session_state["auth_error"] = "Session timed out due to inactivity."
        return False

    # Token expiry check (Requirement 23.9)
    id_token = st.session_state.get("id_token", "")
    if id_token and _is_token_expired(id_token):
        if not refresh_tokens():
            logout()
            st.session_state["auth_error"] = "Session expired. Please log in again."
            return False

    _update_activity()
    return True


def logout() -> None:
    """Clear all authentication state."""
    keys = [
        "authenticated",
        "id_token",
        "access_token",
        "refresh_token",
        "user_role",
        "username",
        "last_activity",
    ]
    for key in keys:
        st.session_state[key] = None
    st.session_state["authenticated"] = False
    logger.info("User logged out")


def get_current_role() -> Optional[str]:
    """Return the current user's role or None if not authenticated."""
    if st.session_state.get("authenticated"):
        return st.session_state.get("user_role")
    return None


def get_id_token() -> Optional[str]:
    """Return the current ID token for forwarding to AgentCore Gateway."""
    if st.session_state.get("authenticated"):
        return st.session_state.get("id_token")
    return None


def has_feature_access(feature: str) -> bool:
    """Check whether the current user's role grants access to *feature*.

    Admin users have access to all features. Operator users only see
    features listed in ``OPERATOR_FEATURES``.
    """
    from frontend.config import ADMIN_FEATURES, OPERATOR_FEATURES

    role = get_current_role()
    if role == "admin":
        return True
    if role == "operator":
        return feature in OPERATOR_FEATURES
    return False


def render_login_form() -> None:
    """Render the Streamlit login form matching Bank ABC standardized UI."""
    _init_session_state()

    # Hide sidebar and header on login page
    st.markdown("""<style>
        [data-testid="stSidebar"] {display: none}
        [data-testid="stHeader"] {display: none}
    </style>""", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        st.markdown(
            "<div style='text-align:center;padding-top:60px'>",
            unsafe_allow_html=True,
        )
        st.markdown("# 🏧 NeoBank ATM Optimizer")
        st.markdown("##### AI-Powered ATM Profitability Analysis — Powered by AWS")
        st.divider()

        with st.form("login_form"):
            username = st.text_input("Email", placeholder="admin@demoaws.com")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In", use_container_width=True)

        if submitted:
            if username and password:
                with st.spinner("Authenticating…"):
                    authenticate(username, password)
            else:
                st.session_state["auth_error"] = (
                    "Please enter both username and password."
                )

        error = st.session_state.get("auth_error")
        if error:
            st.error(error)

        st.markdown("</div>", unsafe_allow_html=True)

        # Footer — attribution
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown(
            '<div style="text-align:center;padding:16px 0;'
            'border-top:1px solid rgba(128,128,128,0.3)">'
            '<span style="color:#8896a8;font-size:12px">'
            "Designed &amp; Developed by</span><br>"
            '<span style="color:#232f3e;font-size:14px;font-weight:600">'
            "Aneesh Mohan</span><br>"
            '<span style="color:#ff9900;font-size:12px">'
            "Senior Solutions Architect &middot; Amazon Web Services"
            "</span></div>",
            unsafe_allow_html=True,
        )
