"""
Chat interface component for the ATM Profitability Optimizer.

Sends queries to the Strands Agent running on AgentCore Runtime via
boto3 invoke_agent_runtime API. Supports message history and
session persistence via AgentCore Memory.

Validates: Requirements 1.1, 8.2, 8.3, 11.1, 11.4
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Optional

import boto3
import streamlit as st
from botocore.exceptions import ClientError

from frontend.auth import get_id_token, get_current_role
from frontend.config import AGENTCORE_RUNTIME_ARN, AGENTCORE_RUNTIME_REGION

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metadata extraction from agent response
# ---------------------------------------------------------------------------
_META_PATTERN = re.compile(r"\n?<!--AGENT_META:(.*?):AGENT_META-->", re.DOTALL)
_INVOKE_PATTERN = re.compile(r"<invoke\s+name=\"[^\"]+\">.*?</invoke>", re.DOTALL)


def _extract_metadata(response_text: str) -> tuple[str, dict]:
    """Extract embedded metadata block from agent response text.

    The AgentCore Runtime only passes through a subset of response fields,
    so the backend embeds trace/timing as an HTML comment in the response.
    """
    match = _META_PATTERN.search(response_text)
    if not match:
        return response_text, {}
    try:
        metadata = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError):
        metadata = {}
    clean_text = response_text[:match.start()] + response_text[match.end():]
    # Strip any residual <invoke> XML tags from displayed text
    clean_text = _INVOKE_PATTERN.sub("", clean_text)
    return clean_text.rstrip(), metadata

# ---------------------------------------------------------------------------
# Boto3 client (lazy-initialised)
# ---------------------------------------------------------------------------
_agentcore_client = None


def _get_agentcore_client():
    global _agentcore_client
    if _agentcore_client is None:
        _agentcore_client = boto3.client(
            "bedrock-agentcore", region_name=AGENTCORE_RUNTIME_REGION
        )
    return _agentcore_client


# ---------------------------------------------------------------------------
# Session-state helpers
# ---------------------------------------------------------------------------

def _init_chat_state() -> None:
    """Ensure chat-related keys exist in session state."""
    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    if "session_id" not in st.session_state:
        # AgentCore requires 33+ char session IDs
        st.session_state["session_id"] = f"atm-{uuid.uuid4().hex}"
    if "last_response_data" not in st.session_state:
        st.session_state["last_response_data"] = None


# ---------------------------------------------------------------------------
# Agent invocation via AgentCore Runtime
# ---------------------------------------------------------------------------

def _send_query(query: str) -> dict[str, Any]:
    """Send a query to the Strands Agent on AgentCore Runtime.

    Uses boto3 invoke_agent_runtime which handles SigV4 auth automatically.
    Reads response as chunks (matching Bank ABC pattern).
    """
    if not AGENTCORE_RUNTIME_ARN:
        return {
            "response": "Agent runtime is not configured. Please set ATM_AGENTCORE_RUNTIME_ARN.",
            "error": True,
        }

    role = get_current_role() or "operator"
    session_id = st.session_state.get("session_id", f"atm-{uuid.uuid4().hex}")
    actor_id = st.session_state.get("username", "anonymous")

    payload = json.dumps({
        "input": {
            "prompt": query,
            "user_role": role,
            "session_id": session_id,
            "actor_id": actor_id,
        }
    })

    try:
        client = _get_agentcore_client()
        t0 = time.time()
        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
            runtimeSessionId=session_id,
            payload=payload,
            qualifier="DEFAULT",
        )

        # Read response — handle both streaming chunks and .read()
        resp_obj = response.get("response", response)
        chunks = []
        try:
            for chunk in resp_obj:
                if isinstance(chunk, bytes):
                    chunks.append(chunk.decode("utf-8"))
                elif isinstance(chunk, str):
                    chunks.append(chunk)
        except TypeError:
            # Not iterable — try .read()
            if hasattr(resp_obj, "read"):
                chunks.append(resp_obj.read().decode("utf-8") if isinstance(resp_obj.read(), bytes) else str(resp_obj.read()))
            else:
                chunks.append(str(resp_obj))

        wall_time = round(time.time() - t0, 2)
        raw = "".join(chunks)
        logger.info("Raw response length: %d chars, first 200: %s", len(raw), raw[:200])

        parsed = json.loads(raw)
        logger.info("Parsed response keys: %s", list(parsed.keys()))

        # AgentCore wraps in "output" — unwrap if present
        data = parsed.get("output", parsed)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                data = {"response": data}

        logger.info("Data keys after unwrap: %s", list(data.keys()) if isinstance(data, dict) else type(data))

        # Add wall_time to data
        if isinstance(data, dict):
            data["wall_time"] = wall_time

        # Persist session ID
        if isinstance(data, dict) and "session_id" in data:
            st.session_state["session_id"] = data["session_id"]

        st.session_state["last_response_data"] = data

        response_text = data.get("response", "No response received.") if isinstance(data, dict) else str(data)

        # Extract embedded metadata (trace/timing) from response text
        # AgentCore Runtime strips extra fields, so backend embeds them in response
        clean_text, metadata = _extract_metadata(response_text)

        # Use metadata trace/timing if available, fall back to top-level data fields
        trace = metadata.get("trace", []) or (data.get("trace", []) if isinstance(data, dict) else [])
        timing = metadata.get("timing", {}) or (data.get("timing", {}) if isinstance(data, dict) else {})
        model_name = metadata.get("model", "") or (data.get("model", "Unknown") if isinstance(data, dict) else "Unknown")

        # If timing doesn't have total_seconds, use wall_time
        if timing and "total_seconds" not in timing:
            timing["total_seconds"] = wall_time
        elif not timing:
            timing = {"total_seconds": wall_time}

        logger.info("Trace items: %d, Timing: %s, Model: %s", len(trace), timing, model_name)

        return {
            "response": clean_text,
            "error": False,
            "memory_enabled": data.get("memory_enabled", False) if isinstance(data, dict) else False,
            "trace": trace,
            "timing": timing,
            "model": model_name,
        }

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        logger.error("AgentCore invocation error %s: %s", error_code, exc)

        if error_code in ("AccessDeniedException", "UnauthorizedException"):
            msg = "You do not have permission to access the agent. Please contact your administrator."
        elif error_code == "ThrottlingException":
            msg = "Too many requests. Please wait a moment and try again."
        elif error_code == "ServiceUnavailableException":
            msg = "The agent service is temporarily unavailable. Please try again later."
        else:
            msg = "An error occurred while processing your request."

        return {"response": msg, "error": True}

    except Exception as exc:
        logger.error("Unexpected agent error: %s", exc)
        return {
            "response": "An unexpected error occurred. Please try again.",
            "error": True,
        }


# ---------------------------------------------------------------------------
# Trace / execution details renderer
# ---------------------------------------------------------------------------

# Map short tool names to icons for ATM tools
_TOOL_ICONS = {
    "query_atm_data": "📍",
    "query_branch_proximity": "🏦",
    "query_revenue_data": "💰",
    "query_maintenance_costs": "🔧",
    "query_cash_levels": "💵",
    "calculate_impact_analysis": "📊",
    "detect_anomalies": "🔍",
    "profitability_ranking": "🏆",
}


def _short_tool_name(full_name: str) -> str:
    """Strip gateway prefix from tool names like 'ATM-Tools___query_atm_data'."""
    if "___" in full_name:
        return full_name.split("___", 1)[1]
    return full_name


def render_trace(data: dict) -> None:
    """Render agent execution details in a collapsible expander."""
    trace = data.get("trace", [])
    timing = data.get("timing", {})
    wall = timing.get("total_seconds", 0) or data.get("wall_time", 0)
    model = data.get("model", "Unknown")

    if not trace and not wall:
        return

    with st.expander("🔍 Agent Execution Details", expanded=False):
        cols = st.columns(4)
        cols[0].metric("Total Time", f"{wall}s")
        cols[1].metric("Agent Cycles", timing.get("cycles", "—"))
        cols[2].metric("Model", model)
        tool_calls = sum(1 for t in trace if t.get("step") == "tool_call")
        cols[3].metric("Tool Calls", tool_calls)

        st.divider()

        step_num = 0
        reasoning_count = 0
        total_reasoning = sum(1 for t in trace if t.get("step") == "reasoning")
        for item in trace:
            step_type = item.get("step", "")

            if step_type == "reasoning":
                reasoning_count += 1
                reasoning_text = item.get("text", "")
                if reasoning_text:
                    # Last reasoning block is the final answer — skip it
                    # (it's already shown in the main chat)
                    if reasoning_count == total_reasoning and step_num > 0:
                        st.markdown("🎯 **Final Analysis**")
                        # Show just a preview — full answer is in the chat
                        preview = reasoning_text[:300]
                        if len(reasoning_text) > 300:
                            preview += "…"
                        st.caption(preview)
                    else:
                        st.markdown("🤔 **Agent Reasoning**")
                        st.info(reasoning_text[:2000])
                    st.markdown("")

            elif step_type == "tool_call":
                step_num += 1
                full_tool = item.get("tool", "")
                short = _short_tool_name(full_tool)
                icon = _TOOL_ICONS.get(short, "🔧")
                inp = item.get("input", {})

                st.markdown(f"**Step {step_num}: {icon} `{short}`**")
                if inp:
                    st.json(inp)

            elif step_type == "tool_result":
                output = item.get("output", "")
                status = item.get("status", "success")
                if status == "error":
                    st.error(f"❌ Error: {output[:300]}")
                else:
                    if output:
                        try:
                            result_data = json.loads(output)
                            if "row_count" in result_data:
                                st.success(f"✅ Returned {result_data['row_count']} rows")
                            elif "error" in result_data:
                                st.warning(f"⚠️ {result_data['error']}")
                            else:
                                st.success("✅ Result:")
                                st.json(result_data)
                        except (json.JSONDecodeError, TypeError):
                            st.success(f"✅ {output[:500]}")
                    else:
                        st.success("✅ Done")
                st.markdown("---")

        st.markdown("**🔗 Data Flow**")
        st.code(
            "User → Streamlit (me-south-1) → AgentCore Runtime (eu-west-1)\n"
            "  → Strands Agent → AgentCore Gateway (eu-west-1)\n"
            "  → Lambda MCP Server (eu-west-1) → Athena (me-south-1)\n"
            "  → S3 Parquet (me-south-1)",
            language="text",
        )


# ---------------------------------------------------------------------------
# Public component
# ---------------------------------------------------------------------------

def render_chat() -> None:
    """Render the chat interface with message history.

    The chat input is rendered at the page level (in app.py) so it stays
    pinned to the bottom.  This function only renders the message history
    and processes any pending queries.

    Uses a two-phase approach to avoid the new question appearing above
    old output during the agent call:
      1. Append user message to history + set processing flag + rerun
      2. On next render, history shows the user message in order,
         then we call the agent and append the response.
    """
    _init_chat_state()

    st.subheader("💬 Ask the ATM Optimizer")

    # Check if we need to process a pending query (from sidebar buttons, FAQs, or chat_input)
    pending = st.session_state.pop("pending_query", None)
    if pending:
        # Phase 1: append user message and set processing flag, then rerun
        st.session_state["messages"].append({"role": "user", "content": pending})
        st.session_state["_processing_query"] = pending
        st.rerun()

    # Render message history
    for msg in st.session_state["messages"]:
        role = msg["role"]
        with st.chat_message(role):
            st.markdown(msg["content"])
            if role == "assistant" and "trace_data" in msg:
                render_trace(msg["trace_data"])

    # Phase 2: if we have a query to process, call the agent now
    processing = st.session_state.pop("_processing_query", None)
    if processing:
        with st.chat_message("assistant"):
            with st.spinner("Analyzing…"):
                result = _send_query(processing)

            response_text = result.get("response", "No response received.")
            is_error = result.get("error", False)

            if is_error:
                st.error(response_text)
            else:
                st.markdown(response_text)

                if result.get("memory_enabled"):
                    st.caption("🧠 Memory enabled — context persists across conversations")

                render_trace(result)

        msg_data = {"role": "assistant", "content": response_text}
        if not is_error and (result.get("trace") or result.get("timing")):
            msg_data["trace_data"] = {
                "trace": result.get("trace", []),
                "timing": result.get("timing", {}),
                "model": result.get("model", "Unknown"),
            }
        st.session_state["messages"].append(msg_data)


def _process_prompt(prompt: str) -> None:
    """Process a user prompt by queuing it for the two-phase render cycle."""
    st.session_state["pending_query"] = prompt
    st.rerun()


def clear_chat() -> None:
    """Reset the chat history and start a new session."""
    st.session_state["messages"] = []
    st.session_state["session_id"] = f"atm-{uuid.uuid4().hex}"
    st.session_state["last_response_data"] = None
