import os
import boto3
import json
import uuid
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

KB_ID = os.environ.get("ALMA_KB_ID", "CHANGE_ME")
REGION = os.environ.get("COMPUTE_REGION", "eu-west-1")
ONBOARDING_ARN = os.environ.get("ONBOARDING_RUNTIME_ARN", "CHANGE_ME")

SYSTEM_PROMPT = """You are Alma, the friendly AI assistant for AI Bank.

RULES:
1. ALWAYS use search_bank_info tool FIRST for any bank-related question
2. Answer ONLY based on search results — never make up products, rates, or policies
3. If no results found, say "I don't have that specific information. Would you like me to connect you with our team?"
4. Keep responses concise: 2-4 sentences max
5. Be warm and professional like a premium bank concierge
6. NEVER narrate your own actions (do NOT say "I'll search for that", "Let me look that up", "I'll start the onboarding process", etc.)
7. When the start_onboarding tool returns a result starting with [RELAY_VERBATIM], output ONLY the text after [RELAY_VERBATIM] — no additions, no intro, no wrap-up, no changes whatsoever
8. Format responses using markdown: **bold** for key values (rates, amounts, fees), bullet lists for multiple items

ACCOUNT OPENING:
- When a customer wants to OPEN or CREATE a new bank account, use the start_onboarding tool
- This includes phrases like "I want to open an account", "sign up", "create account", "register", "new account"
- Do NOT use start_onboarding for questions ABOUT accounts (e.g. "what types of accounts do you have?" — use search_bank_info for those)

GENERAL KNOWLEDGE:
- You may answer general greetings without the search tool
- For anything about AI Bank — ALWAYS search first"""

# --- Singleton clients (reused across invocations for performance) ---
_kb_client = boto3.client("bedrock-agent-runtime", region_name=REGION)
_agentcore_client = boto3.client("bedrock-agentcore", region_name=REGION)

@tool
def search_bank_info(query: str) -> str:
    """Search the bank's knowledge base for product and service information."""
    response = _kb_client.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 3}}
    )
    results = []
    for r in response.get("retrievalResults", []):
        content = r.get("content", {}).get("text", "")
        if content and r.get("score", 0) > 0.3:
            results.append(content[:1000])
    return "\n---\n".join(results) if results else "No information found."

@tool
def start_onboarding(customer_message: str) -> str:
    """Hand off to the account onboarding agent when a customer wants to open a new bank account.
    Args:
        customer_message: The customer's message about opening an account, including any details they've already provided
    """
    import logging
    logger = logging.getLogger(__name__)
    try:
        onboarding_session_id = str(uuid.uuid4())
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": uuid.uuid4().hex,
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "parts": [{"kind": "text", "text": customer_message}],
                    "messageId": uuid.uuid4().hex,
                }
            }
        })
        response = _agentcore_client.invoke_agent_runtime(
            agentRuntimeArn=ONBOARDING_ARN,
            runtimeSessionId=onboarding_session_id,
            payload=payload,
            qualifier="DEFAULT"
        )
        stream = response.get("response") or response.get("body")
        raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
        try:
            parsed = json.loads(raw)
            for artifact in parsed.get("result", {}).get("artifacts", []):
                for part in artifact.get("parts", []):
                    if part.get("kind") == "text":
                        # Emit session ID as a non-visible prefix Lambda strips before streaming.
                        # \x00SID:<uuid>\x00 — null bytes prevent model from reproducing it.
                        return f"\x00SID:{onboarding_session_id}\x00[RELAY_VERBATIM]{part['text']}"
            return raw
        except json.JSONDecodeError:
            return raw
    except Exception as e:
        logger.error(f"start_onboarding error: {e}", exc_info=True)
        return f"Error connecting to onboarding service: {str(e)}"

# --- Singleton model + per-session agents for conversation memory ---
# Nova 2 Lite: fast, high quality, supports prompt caching (cache_prompt)
_model = BedrockModel(
    model_id="eu.amazon.nova-2-lite-v1:0",
    region_name=REGION,
    temperature=0.3,
    max_tokens=512,
    cache_prompt="default",   # Cache system prompt prefix — reduces latency on repeated calls
)
_sessions = {}       # session_id -> Agent (Strands native conversation memory)
_MAX_SESSIONS = 200  # LRU eviction to prevent unbounded memory growth

app = BedrockAgentCoreApp()

@app.entrypoint
async def invoke(payload):
    user_message = payload.get("prompt", "Hello")
    session_id = payload.get("session_id", "default")

    # Reuse agent per session — Strands keeps conversation history in agent.messages
    if session_id not in _sessions:
        if len(_sessions) >= _MAX_SESSIONS:
            oldest = next(iter(_sessions))
            del _sessions[oldest]
        _sessions[session_id] = Agent(model=_model, system_prompt=SYSTEM_PROMPT, tools=[search_bank_info, start_onboarding])

    agent = _sessions[session_id]

    stream = agent.stream_async(user_message)
    async for event in stream:
        if "data" in event:
            yield {"data": event["data"]}

if __name__ == "__main__":
    app.run()
