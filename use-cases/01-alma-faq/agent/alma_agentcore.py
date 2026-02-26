import os
import boto3
import json
import re
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
4. Keep responses concise: 2-4 sentences
5. Be warm and professional like a premium bank concierge

ACCOUNT OPENING:
- When a customer wants to OPEN or CREATE a new bank account, use the start_onboarding tool
- This includes phrases like "I want to open an account", "sign up", "create account", "register", "new account"
- Do NOT use start_onboarding for questions ABOUT accounts (e.g. "what types of accounts do you have?" — use search_bank_info for those)

GENERAL KNOWLEDGE:
- You may answer general greetings without the search tool
- For anything about AI Bank — ALWAYS search first"""

@tool
def search_bank_info(query: str) -> str:
    """Search the bank's knowledge base for product and service information."""
    client = boto3.client("bedrock-agent-runtime", region_name=REGION)
    response = client.retrieve(
        knowledgeBaseId=KB_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 5}}
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
        client = boto3.client("bedrock-agentcore", region_name=REGION)
        # A2A JSON-RPC payload
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
        logger.info(f"Calling onboarding A2A: {ONBOARDING_ARN}")
        response = client.invoke_agent_runtime(
            agentRuntimeArn=ONBOARDING_ARN,
            runtimeSessionId=str(uuid.uuid4()),
            payload=payload,
            qualifier="DEFAULT"
        )
        stream = response.get("response") or response.get("body")
        raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
        logger.info(f"A2A response: {raw[:500]}")
        try:
            parsed = json.loads(raw)
            result = parsed.get("result", {})
            artifacts = result.get("artifacts", [])
            for artifact in artifacts:
                for part in artifact.get("parts", []):
                    if part.get("kind") == "text":
                        return part["text"]
            return raw
        except json.JSONDecodeError:
            return raw
    except Exception as e:
        logger.error(f"start_onboarding error: {e}", exc_info=True)
        return f"Error connecting to onboarding service: {str(e)}"

app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload):
    user_message = payload.get("prompt", "Hello")
    model = BedrockModel(model_id="eu.amazon.nova-lite-v1:0", region_name=REGION)
    agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=[search_bank_info, start_onboarding])
    result = agent(user_message)
    answer = re.sub(r"<thinking>[\s\S]*?</thinking>", "", str(result)).strip()
    return {"answer": answer}

if __name__ == "__main__":
    app.run()
