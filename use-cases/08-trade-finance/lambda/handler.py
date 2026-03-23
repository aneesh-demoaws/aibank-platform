"""
Trade Finance AI Agent — Lambda Proxy
Authenticates employee sessions, proxies chat to AgentCore Trade Finance Agent.
Modular: independent of other use cases (loan, C360, etc.)
"""
import json, logging, os, uuid
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ──
AGENT_ARN = os.environ.get("TF_AGENT_ARN", "arn:aws:bedrock-agentcore:eu-west-1:519124228967:runtime/trade_finance_agent-c8Al0REGd1")
SESSION_TABLE = os.environ.get("SESSION_TABLE", "aibank-session-routing")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://aibank.demoaws.com")
ALLOWED_ROLES = ("rm", "relationship-managers", "admin")

ddb = boto3.resource("dynamodb", region_name="eu-west-1")
agentcore = boto3.client("bedrock-agentcore", region_name="eu-west-1")
session_table = ddb.Table(SESSION_TABLE)


def _cors(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": body if isinstance(body, str) else json.dumps(body, default=str),
    }


def _get_session(event):
    """Validate employee session cookie and return (email, role) or (None, None)."""
    # HTTP API v2 puts cookies in a separate array
    cookies = "; ".join(event.get("cookies", []))
    if not cookies:
        cookies = event.get("headers", {}).get("cookie", "") or \
                  event.get("headers", {}).get("Cookie", "") or \
                  " ".join(event.get("multiValueHeaders", {}).get("Cookie", []))
    for part in cookies.split(";"):
        part = part.strip()
        if part.startswith("aibank_sid="):
            sid = part[len("aibank_sid="):]
            item = session_table.get_item(Key={"session_id": sid}).get("Item")
            if item and item.get("status") == "active" and item.get("portal") == "employee":
                return item.get("user_email", ""), item.get("role", "employee")
    # Fallback: check x-session-id header (cross-domain requests via Function URL)
    sid = (event.get("headers") or {}).get("x-session-id", "")
    if sid:
        item = session_table.get_item(Key={"session_id": sid}).get("Item")
        if item and item.get("status") == "active" and item.get("portal") == "employee":
            return item.get("user_email", ""), item.get("role", "employee")
    return None, None


def lambda_handler(event, context):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method == "OPTIONS":
        return _cors(200, "{}")

    path = event.get("path") or event.get("rawPath", "")

    if path.endswith("/chat") or path == "/":
        return handle_chat(event)
    return _cors(404, {"error": "Not found"})


def handle_chat(event):
    email, role = _get_session(event)
    if not email:
        return _cors(401, {"error": "Authentication required. Please log in."})
    if role not in ALLOWED_ROLES:
        return _cors(403, {"error": "Access denied. Trade Finance is available to Relationship Managers and Admins only."})

    try:
        body = json.loads(event.get("body", "{}"))
        prompt = body.get("prompt", "Hello")
        session_id = body.get("session_id", f"tf-{uuid.uuid4()}")
        actor_id = body.get("actor_id", email.split("@")[0])

        # Retry on 502 (agent container health check race condition)
        last_error = None
        for attempt in range(3):
            try:
                response = agentcore.invoke_agent_runtime(
                    agentRuntimeArn=AGENT_ARN,
                    runtimeSessionId=session_id,
                    payload=json.dumps({"prompt": prompt, "session_id": session_id, "actor_id": actor_id}),
                    qualifier="DEFAULT")
                stream = response.get("response") or response.get("body")
                raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
                parsed = json.loads(raw)
                return _cors(200, parsed)
            except Exception as e:
                last_error = e
                if "502" in str(e) and attempt < 2:
                    import time
                    time.sleep(2)
                    continue
                raise
        return _cors(500, {"error": f"Agent error after retries: {str(last_error)}"})
    except Exception as e:
        logger.exception("Trade finance chat error")
        return _cors(500, {"error": f"Agent error: {str(e)}"})
