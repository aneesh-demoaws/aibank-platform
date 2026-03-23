"""
AgentCore Runtime entry point for the NeoBank ATM Profitability Optimizer.

Uses the BedrockAgentCoreApp SDK for deployment via the starter toolkit.
Integrates AgentCore Memory for conversation persistence (STM + LTM).

Data flow:
  AgentCore Runtime (eu-west-1) -> Strands Agent + AgentCore Memory
  -> MCP Tools -> Athena (me-south-1) -> S3 (me-south-1)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from bedrock_agentcore.runtime import BedrockAgentCoreApp

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODEL_ID = os.environ.get("ATM_MODEL_ID", "eu.anthropic.claude-sonnet-4-20250514-v1:0")
MODEL_REGION = os.environ.get("ATM_MODEL_REGION", "eu-west-1")
MODEL_MAX_TOKENS = int(os.environ.get("ATM_MODEL_MAX_TOKENS", "4096"))
MODEL_TEMPERATURE = float(os.environ.get("ATM_MODEL_TEMPERATURE", "0.1"))
MEMORY_ID = os.environ.get("ATM_MEMORY_ID", "")
MEMORY_REGION = os.environ.get("ATM_MEMORY_REGION", "eu-west-1")

# AgentCore Gateway endpoint — provides MCP tools via Lambda
# REQUIRED: Set ATM_GATEWAY_URL env var to your AgentCore Gateway MCP endpoint
# Example: https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
GATEWAY_URL = os.environ.get("ATM_GATEWAY_URL", os.environ.get("AGENTCORE_GATEWAY_URL", ""))
GATEWAY_REGION = os.environ.get("ATM_GATEWAY_REGION", "eu-west-1")

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------
BASE_PROMPT = """\
You are the {bank} ATM Profitability Optimizer Agent. You help retail banking \
analysts understand ATM network performance across Bahrain.

When answering questions:
1. Identify the relevant ATM(s) from the user's query.
2. Use the appropriate MCP tools to gather data.
3. Analyze the data and provide clear insights.
4. Include specific numbers (BHD amounts, percentages).
5. Provide actionable recommendations when appropriate.

For What-If scenarios:
- Calculate revenue loss based on historical daily averages.
- Model traffic redistribution using inverse-distance weighting.
- Identify ATMs that may exceed capacity.
- Recommend mitigation actions.

Always respond in a professional, concise manner suitable for \
banking executives. Use BHD (Bahraini Dinar) for all currency values.
"""

ADMIN_SECTION = """
Your capabilities (Admin role — full access):
You have access to ALL analysis tools:
- query_atm_data: Query ATM transaction summaries by date range.
- query_branch_proximity: Find nearby ATMs/branches within a radius.
- query_revenue_data: Revenue metrics with period aggregation.
- query_maintenance_costs: Maintenance cost history and breakdowns.
- query_cash_levels: Current cash levels and 7-day forecasts.
- calculate_impact_analysis: Revenue impact and traffic reallocation for downtime scenarios.
- detect_anomalies: Identify ATMs with unusual performance patterns.
- profitability_ranking: Rank ATMs by net revenue.
"""

COMPETITOR_ADMIN_SECTION = """
Competitor Analysis capabilities (Admin — full access):
- query_competitor_analysis: Competition Index scores for {bank} ATMs. Shows how much competitive pressure each ATM faces. USE THIS TOOL for any question about competition index, competitive pressure, or nearby competitors.
- query_coverage_analysis: Coverage gaps (areas where competitors have ATMs but {bank} doesn't), coverage advantages, and market share by governorate.
- simulate_competitor_scenario: Model the impact of a competitor opening or closing an ATM near {bank} locations. Shows projected revenue changes.
- recommend_atm_placement: Optimal locations for new {bank} ATMs based on coverage gaps and competitor density.

IMPORTANT: When the user asks about competition, competitors, competition index, \
competitive pressure, or nearby competitor banks, you MUST use query_competitor_analysis \
or query_coverage_analysis — NOT query_atm_data or query_branch_proximity. \
The query_branch_proximity tool only finds other {bank} ATMs and branches, \
it does NOT return competitor bank data.
"""

COMPETITOR_OPERATOR_SECTION = """
Competitor Analysis capabilities (Operator — read-only):
- query_competitor_analysis: Competition Index scores for {bank} ATMs.
- query_coverage_analysis: Coverage gaps, advantages, and market share by governorate.

You do NOT have access to simulate_competitor_scenario or recommend_atm_placement.
If the user asks for simulations or placement recommendations, explain that Admin
privileges are required.

IMPORTANT: When the user asks about competition, competitors, competition index, \
competitive pressure, or nearby competitor banks, you MUST use query_competitor_analysis \
or query_coverage_analysis — NOT query_atm_data or query_branch_proximity.
"""

OPERATOR_SECTION = """
Your capabilities (Operator role — basic access):
You have access to basic query tools only:
- query_atm_data: Query ATM transaction summaries by date range.
- query_branch_proximity: Find nearby ATMs/branches within a radius.
- query_revenue_data: Revenue metrics with period aggregation.

If the user asks for maintenance costs, cash levels, impact analysis, \
anomaly detection, or profitability ranking, explain that Admin privileges \
are required.
"""

GUIDELINES = """
Data Availability:
- The dataset covers August 2025 through January 2026 (6 months).
- When a user asks about maintenance costs, transactions, or any date-ranged \
query without specifying dates, default to start_date='2025-08-01' and \
end_date='2026-01-31'.
- There is NO data before August 2025. Querying earlier dates will return \
zero results.

Guidelines:
- Present monetary values in BHD with three decimal places.
- When comparing ATMs, use tables or structured lists.
- If a tool returns an error, explain in plain language.
- Use memory of past conversations to provide personalized responses.
- When referencing ATMs, use their full name alongside the ATM ID.
"""


def _build_system_prompt(role: str, bank_name: str | None = None) -> str:
    from agent.bank_alias import get_bank_alias
    bank = bank_name or get_bank_alias()
    section = ADMIN_SECTION if role == "admin" else OPERATOR_SECTION
    competitor_section = COMPETITOR_ADMIN_SECTION if role == "admin" else COMPETITOR_OPERATOR_SECTION
    prompt = BASE_PROMPT + section + competitor_section + GUIDELINES
    return prompt.replace("{bank}", bank)


# ---------------------------------------------------------------------------
# Memory integration
# ---------------------------------------------------------------------------

def _create_session_manager(session_id: str, actor_id: str):
    """Create AgentCore Memory session manager if MEMORY_ID is set."""
    if not MEMORY_ID:
        return None

    try:
        from bedrock_agentcore.memory.integrations.strands.config import (
            AgentCoreMemoryConfig,
            RetrievalConfig,
        )
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )

        config = AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=session_id,
            actor_id=actor_id,
            retrieval_config={
                "/preferences/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.7),
                "/summaries/{actorId}/{sessionId}": RetrievalConfig(top_k=3, relevance_score=0.5),
                "/facts/{actorId}": RetrievalConfig(top_k=5, relevance_score=0.5),
            },
        )
        sm = AgentCoreMemorySessionManager(
            agentcore_memory_config=config,
            region_name=MEMORY_REGION,
        )
        logger.info("AgentCore Memory enabled: memory=%s actor=%s", MEMORY_ID, actor_id)
        return sm
    except Exception as e:
        logger.warning("Failed to init AgentCore Memory, proceeding without: %s", e)
        return None


def _extract_response_text(result) -> str:
    """Extract text from a Strands Agent result."""
    message = result.message
    if isinstance(message, dict):
        content = message.get("content", [])
        if isinstance(content, list):
            return "".join(
                block["text"] for block in content
                if isinstance(block, dict) and "text" in block
            )
        if isinstance(content, str):
            return content
    if isinstance(message, str):
        return message
    return str(message)


# ---------------------------------------------------------------------------
# Callback handler — captures reasoning + tool calls + tool results
# ---------------------------------------------------------------------------

class TraceCallbackHandler:
    """Strands callback handler that captures the full agent execution trace.

    For gateway tools, the model streams everything as text (reasoning +
    ``<invoke>`` XML tags).  Standard ``current_tool_use`` events do NOT
    fire for gateway tools.  So we:

    1. Buffer ALL streamed ``data`` text.
    2. Also capture ``current_tool_use`` / ``message`` events (for non-
       gateway tools that do fire them).
    3. In ``finalize()``, post-process the buffered text to split it into
       reasoning segments and tool-call segments by parsing ``<invoke>``
       tags as delimiters.
    """

    def __init__(self):
        self.trace: list[dict] = []
        self._text_buf: list[str] = []
        self._seen_tools: set[str] = set()
        self._has_native_tool_events = False

    def __call__(self, **kwargs):
        # Buffer all streamed text (reasoning + invoke tags)
        if "data" in kwargs:
            self._text_buf.append(kwargs["data"])

        # Native tool events (MCP gateway tools fire these)
        if "current_tool_use" in kwargs:
            tu = kwargs["current_tool_use"]
            tool_id = tu.get("toolUseId", "")
            tool_name = tu.get("name", "")
            if tool_name and tool_id and tool_id not in self._seen_tools:
                self._has_native_tool_events = True
                self._seen_tools.add(tool_id)
                tool_input = tu.get("input", {})
                self.trace.append({
                    "step": "tool_call",
                    "tool": tool_name,
                    "input": tool_input if isinstance(tool_input, dict) else {},
                })

        # Tool results from message events
        if "message" in kwargs:
            msg = kwargs["message"]
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and "toolResult" in block:
                            self._has_native_tool_events = True
                            tr = block["toolResult"]
                            result_content = tr.get("content", [])
                            result_text = ""
                            if isinstance(result_content, list):
                                result_text = " ".join(
                                    r.get("text", "") for r in result_content
                                    if isinstance(r, dict) and "text" in r
                                )
                            self.trace.append({
                                "step": "tool_result",
                                "status": tr.get("status", "success"),
                                "output": result_text[:500],  # Truncate for trace
                            })

    def finalize(self):
        """Post-process buffered text into structured trace entries."""
        full_text = "".join(self._text_buf)
        if not full_text.strip():
            return

        # Parse <invoke> tags to split reasoning from tool calls
        invoke_re = re.compile(
            r'<invoke\s+name="([^"]+)">\s*(.*?)</invoke>',
            re.DOTALL,
        )
        param_re = re.compile(
            r'<parameter\s+name="([^"]+)">(.*?)</parameter>',
            re.DOTALL,
        )

        matches = list(invoke_re.finditer(full_text))

        if not matches:
            # No tool calls — entire text is reasoning
            self.trace.append({"step": "reasoning", "text": full_text.strip()})
            return

        last_end = 0
        for match in matches:
            # Reasoning text before this tool call
            reasoning = full_text[last_end:match.start()].strip()
            if reasoning:
                self.trace.append({"step": "reasoning", "text": reasoning})

            # Tool call
            tool_name = match.group(1)
            params = {}
            for pm in param_re.finditer(match.group(2)):
                pval = pm.group(2).strip()
                try:
                    params[pm.group(1)] = json.loads(pval)
                except (json.JSONDecodeError, TypeError):
                    params[pm.group(1)] = pval
            self.trace.append({
                "step": "tool_call",
                "tool": tool_name,
                "input": params,
            })

            # Check for tool result text between </invoke> and next
            # <invoke> or end — gateway returns results inline
            last_end = match.end()

        # Trailing reasoning after last tool call (the final answer)
        trailing = full_text[last_end:].strip()
        if trailing:
            self.trace.append({"step": "reasoning", "text": trailing})


# ---------------------------------------------------------------------------
# BedrockAgentCoreApp
# ---------------------------------------------------------------------------

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    """Process a user query through the Strands Agent with AgentCore Memory."""
    # Parse input
    input_data = payload.get("input", payload)
    prompt = input_data.get("prompt", "")
    if not prompt:
        return {"error": "No prompt provided in input."}

    if not GATEWAY_URL:
        return {"error": "ATM_GATEWAY_URL environment variable is not set. Configure your AgentCore Gateway endpoint."}

    role = input_data.get("user_role", "operator")
    session_id = input_data.get("session_id", f"atm-{uuid.uuid4().hex}")
    actor_id = input_data.get("actor_id", "anonymous")

    logger.info("Invocation: role=%s session=%s actor=%s len=%d", role, session_id, actor_id, len(prompt))

    # Build model
    model = BedrockModel(
        model_id=MODEL_ID,
        region_name=MODEL_REGION,
        max_tokens=MODEL_MAX_TOKENS,
        temperature=MODEL_TEMPERATURE,
    )

    # Build memory session manager
    session_manager = _create_session_manager(session_id, actor_id)

    # Callback handler captures reasoning + tool calls + tool results
    trace_handler = TraceCallbackHandler()

    trace = []
    timing = {}

    # Create MCP client connected to AgentCore Gateway (IAM SigV4 auth)
    mcp_client = MCPClient(lambda: aws_iam_streamablehttp_client(
        endpoint=GATEWAY_URL,
        aws_region=GATEWAY_REGION,
        aws_service="bedrock-agentcore",
    ))

    try:
        with mcp_client:
            tools = mcp_client.list_tools_sync()
            logger.info("Gateway tools discovered: %d tools — %s",
                        len(tools), [getattr(t, 'name', t.get('name', '?') if isinstance(t, dict) else '?') for t in tools])

            agent = Agent(
                model=model,
                system_prompt=_build_system_prompt(role),
                tools=tools,
                session_manager=session_manager,
                callback_handler=trace_handler,
            )
            t0 = time.time()
            result = agent(prompt)
            total_time = time.time() - t0

        # Finalize the trace handler (flush trailing reasoning)
        trace_handler.finalize()

        response_text = _extract_response_text(result)

        # Use the callback handler trace (has reasoning + tool calls + results)
        trace = trace_handler.trace

        logger.info("Callback trace: %d items (%d reasoning, %d tool_call, %d tool_result)",
                     len(trace),
                     sum(1 for t in trace if t.get("step") == "reasoning"),
                     sum(1 for t in trace if t.get("step") == "tool_call"),
                     sum(1 for t in trace if t.get("step") == "tool_result"))

        # Strip <invoke> tags from displayed response for clean business output
        invoke_pattern = re.compile(
            r'<invoke\s+name="[^"]+">.*?</invoke>',
            re.DOTALL,
        )
        response_text = invoke_pattern.sub("", response_text).strip()

        # Extract metrics
        metrics = result.metrics.get_summary() if hasattr(result, "metrics") else {}
        timing = {
            "total_seconds": round(total_time, 2),
            "cycles": metrics.get("total_cycles", 0),
            "duration": round(metrics.get("total_duration", 0), 2),
        }
    finally:
        if session_manager is not None:
            try:
                session_manager.close()
            except Exception as e:
                logger.warning("Error closing session manager: %s", e)

    # AgentCore Runtime only passes through a subset of response fields.
    # Embed trace/timing as a JSON metadata block inside the response text
    # so the frontend can extract it.
    metadata = {
        "trace": trace,
        "timing": timing,
        "model": "Claude Sonnet 4",
    }
    metadata_block = f"\n<!--AGENT_META:{json.dumps(metadata)}:AGENT_META-->"

    return {
        "response": response_text + metadata_block,
        "session_id": session_id,
        "role": role,
        "memory_enabled": bool(MEMORY_ID),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    app.run()
