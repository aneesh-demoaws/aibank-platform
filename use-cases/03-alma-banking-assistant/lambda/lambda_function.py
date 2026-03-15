"""Alma Banking Assistant — Lambda proxy with session auth + loan A2A routing."""
import json, uuid, time, re, boto3, os

BANKING_ARN = os.environ.get("BANKING_AGENT_ARN", "arn:aws:bedrock-agentcore:eu-west-1:519124228967:runtime/alma_banking_assistant-zxGWis2H4O")
LOAN_AGENT_ARN = os.environ.get("LOAN_AGENT_ARN", "CHANGE_ME")
SESSION_TABLE = os.environ.get("SESSION_TABLE", "aibank-session-routing")
CLUSTER_ARN = "arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking"
SECRET_ARN = "arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ"
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "https://aibank.demoaws.com")

ddb = boto3.resource("dynamodb", region_name="eu-west-1")
session_table = ddb.Table(SESSION_TABLE)
agentcore = boto3.client("bedrock-agentcore", region_name="eu-west-1")
rds = boto3.client("rds-data", region_name="me-south-1")

_customer_cache = {}


def get_customer_info(email):
    if email in _customer_cache:
        return _customer_cache[email]
    resp = rds.execute_statement(
        resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database="corebanking",
        sql="SELECT customer_id, first_name FROM customers WHERE email = :e LIMIT 1",
        parameters=[{"name": "e", "value": {"stringValue": email}}]
    )
    if resp["records"]:
        cid = resp["records"][0][0]["stringValue"]
        fname = resp["records"][0][1]["stringValue"]
        _customer_cache[email] = (cid, fname)
        return cid, fname
    return None, None


def validate_session(event):
    """Extract and validate session from cookie."""
    sid = None
    for c in event.get("cookies", []):
        if c.startswith("aibank_sid="):
            sid = c.split("=", 1)[1]
            break
    if not sid:
        for part in (event.get("headers", {}).get("cookie", "") or "").split(";"):
            p = part.strip()
            if p.startswith("aibank_sid="):
                sid = p.split("=", 1)[1]
                break
    if not sid:
        return None, None

    resp = session_table.get_item(Key={"session_id": sid})
    item = resp.get("Item")
    if not item or item.get("status") != "active":
        return None, None

    now = int(time.time() * 1000)
    if now - item.get("last_active", 0) > item.get("idle_timeout", 900000):
        return None, None

    return item.get("user_email"), sid


def call_banking(prompt, chat_session, customer_id, customer_first_name):
    """Call Alma Banking Assistant (HTTP protocol)."""
    r = agentcore.invoke_agent_runtime(
        agentRuntimeArn=BANKING_ARN,
        runtimeSessionId=chat_session,
        payload=json.dumps({"prompt": prompt, "customer_id": customer_id, "customer_first_name": customer_first_name}),
        qualifier="DEFAULT",
    )
    stream = r.get("response") or r.get("body")
    raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
    parsed = json.loads(raw)
    return parsed.get("answer", raw)


def call_loan_agent(prompt, loan_session_id, customer_id):
    """Call Loan AI Agent (A2A protocol)."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": f"[Customer ID: {customer_id}] {prompt}"}],
                "messageId": uuid.uuid4().hex,
            }
        }
    })
    r = agentcore.invoke_agent_runtime(
        agentRuntimeArn=LOAN_AGENT_ARN,
        runtimeSessionId=loan_session_id,
        payload=payload,
        qualifier="DEFAULT"
    )
    stream = r.get("response") or r.get("body")
    raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
    try:
        parsed = json.loads(raw)
        for artifact in parsed.get("result", {}).get("artifacts", []):
            for part in artifact.get("parts", []):
                if part.get("kind") == "text":
                    return part["text"]
        return raw
    except json.JSONDecodeError:
        return raw


def get_loan_session(chat_session):
    """Check if this chat session has an active loan agent session."""
    resp = session_table.get_item(Key={"session_id": f"loan:{chat_session}"})
    item = resp.get("Item")
    if item:
        return item.get("loan_session_id")
    return None


def set_loan_session(chat_session, loan_session_id):
    session_table.put_item(Item={
        "session_id": f"loan:{chat_session}",
        "loan_session_id": loan_session_id,
        "ttl": int(time.time()) + 3600,
    })


def clear_loan_session(chat_session):
    session_table.delete_item(Key={"session_id": f"loan:{chat_session}"})


def resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": FRONTEND_ORIGIN,
            "Access-Control-Allow-Credentials": "true",
        },
        "body": json.dumps(body),
    }


def handler(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {
            "statusCode": 200,
            "headers": {
                "Access-Control-Allow-Origin": FRONTEND_ORIGIN,
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Allow-Methods": "POST,OPTIONS",
            },
        }

    email, session_id = validate_session(event)
    if not email:
        return resp(401, {"error": "Authentication required. Please log in."})

    customer_id, customer_first_name = get_customer_info(email)
    if not customer_id:
        return resp(403, {"error": "No banking profile found for this account."})

    body = json.loads(event.get("body", "{}"))
    prompt = body.get("message", "Hello")
    chat_session = body.get("session_id", str(uuid.uuid4()))

    try:
        loan_sid = get_loan_session(chat_session)

        if loan_sid:
            # Already in loan flow — route directly to Loan Agent
            answer = call_loan_agent(prompt, loan_sid, customer_id)
            # Check if loan flow is complete (application submitted or user exits)
            if any(s in answer.lower() for s in ["submitted successfully", "application id", "upload your"]):
                clear_loan_session(chat_session)
        else:
            # Route through Alma Banking
            answer = call_banking(prompt, chat_session, customer_id, customer_first_name)

            # Detect if Alma handed off to Loan Agent (SID marker from start_loan_application tool)
            sid_match = re.search(r'\x00SID:([a-f0-9-]+)\x00', answer)
            if sid_match:
                loan_sid = sid_match.group(1)
                set_loan_session(chat_session, loan_sid)
                # Strip the SID marker
                answer = re.sub(r'\x00SID:[a-f0-9-]+\x00', '', answer)

            # Strip [RELAY_VERBATIM] marker
            answer = answer.replace("[RELAY_VERBATIM]", "")

    except Exception as e:
        answer = f"I'm sorry, something went wrong. Please try again. ({str(e)[:100]})"

    answer = re.sub(r"<thinking>[\s\S]*?</thinking>", "", answer).strip()

    # Extract loan upload action markers for frontend
    loan_uploads = re.findall(r'\[ACTION:LOAN_UPLOAD:([\w-]+)\]', answer)
    answer = re.sub(r'\[ACTION:LOAN_UPLOAD:[\w-]+\]', '', answer).strip()

    result = {"answer": answer, "session_id": chat_session, "customer_id": customer_id}
    if loan_uploads:
        result["loan_upload"] = {"applicationId": loan_uploads[0]}

    return resp(200, result)
