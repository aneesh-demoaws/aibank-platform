"""
Trade Finance AI Agent — Async Lambda Proxy
POST /chat → starts agent call async, returns requestId
GET /status?id=xxx → polls for result
Secure: employee session cookie auth, role-restricted to RM/admin.
"""
import json, logging, os, uuid, time, threading
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Config ──
AGENT_ARN = os.environ.get("TF_AGENT_ARN", "arn:aws:bedrock-agentcore:eu-west-1:519124228967:runtime/trade_finance_agent-c8Al0REGd1")
SESSION_TABLE = os.environ.get("SESSION_TABLE", "aibank-session-routing")
RESULTS_TABLE = os.environ.get("RESULTS_TABLE", "aibank-tf-async-results")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://aibank.demoaws.com")
ALLOWED_ROLES = ("rm", "relationship-managers", "admin")

ddb = boto3.resource("dynamodb", region_name="eu-west-1")
agentcore = boto3.client("bedrock-agentcore", region_name="eu-west-1")
session_table = ddb.Table(SESSION_TABLE)
results_table = ddb.Table(RESULTS_TABLE)


def _cors(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": body if isinstance(body, str) else json.dumps(body, default=str),
    }


def _get_session(event):
    """Validate employee session cookie."""
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
    return None, None


def lambda_handler(event, context):
    method = event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method", "GET")
    if method == "OPTIONS":
        return _cors(200, "{}")

    path = event.get("path") or event.get("rawPath", "")

    if method == "POST" and "chat" in path:
        return handle_chat(event)
    elif method == "GET" and "status" in path:
        return handle_status(event)
    return _cors(404, {"error": "Not found"})


def handle_chat(event):
    """Start async agent call, return requestId immediately."""
    email, role = _get_session(event)
    if not email:
        return _cors(401, {"error": "Authentication required."})
    if role not in ALLOWED_ROLES:
        return _cors(403, {"error": "Access denied."})

    body = json.loads(event.get("body", "{}"))
    prompt = body.get("prompt", "Hello")
    session_id = body.get("session_id", f"tf-{uuid.uuid4()}")
    actor_id = body.get("actor_id", email.split("@")[0])
    request_id = str(uuid.uuid4())

    # Store pending request
    results_table.put_item(Item={
        "request_id": request_id,
        "status": "processing",
        "prompt": prompt[:200],
        "created_at": int(time.time()),
        "ttl": int(time.time()) + 300,  # 5 min TTL
    })

    # Start agent call in background thread
    t = threading.Thread(target=_invoke_agent, args=(request_id, prompt, session_id, actor_id))
    t.daemon = True
    t.start()

    return _cors(200, {"request_id": request_id, "status": "processing"})


def _invoke_agent(request_id, prompt, session_id, actor_id):
    """Background: call agent and store result."""
    try:
        response = agentcore.invoke_agent_runtime(
            agentRuntimeArn=AGENT_ARN,
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": prompt, "session_id": session_id, "actor_id": actor_id}),
            qualifier="DEFAULT")
        stream = response.get("response") or response.get("body")
        raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
        parsed = json.loads(raw)

        results_table.update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET #s = :s, #r = :r, completed_at = :t",
            ExpressionAttributeNames={"#s": "status", "#r": "result"},
            ExpressionAttributeValues={
                ":s": "complete",
                ":r": json.dumps(parsed, default=str),
                ":t": int(time.time()),
            })
    except Exception as e:
        logger.error(f"Agent error: {e}")
        results_table.update_item(
            Key={"request_id": request_id},
            UpdateExpression="SET #s = :s, #r = :r",
            ExpressionAttributeNames={"#s": "status", "#r": "result"},
            ExpressionAttributeValues={
                ":s": "error",
                ":r": json.dumps({"error": str(e)}),
            })


def handle_status(event):
    """Poll for async result."""
    email, role = _get_session(event)
    if not email:
        return _cors(401, {"error": "Authentication required."})

    qs = event.get("queryStringParameters") or {}
    request_id = qs.get("id", "")
    if not request_id:
        return _cors(400, {"error": "id required"})

    item = results_table.get_item(Key={"request_id": request_id}).get("Item")
    if not item:
        return _cors(404, {"error": "Request not found"})

    status = item.get("status", "processing")
    if status == "processing":
        return _cors(200, {"status": "processing", "request_id": request_id})
    elif status == "complete":
        result = json.loads(item.get("result", "{}"))
        return _cors(200, {"status": "complete", **result})
    else:
        result = json.loads(item.get("result", "{}"))
        return _cors(200, {"status": "error", **result})
