"""Alma Banking Assistant — Lambda proxy with session auth + multi-turn loan A2A routing."""
import json, uuid, time, re, boto3, os
from botocore.config import Config as BotoConfig

BANKING_ARN = os.environ.get("BANKING_AGENT_ARN", "arn:aws:bedrock-agentcore:eu-west-1:519124228967:runtime/alma_banking_assistant-zxGWis2H4O")
LOAN_AGENT_ARN = os.environ.get("LOAN_AGENT_ARN", "CHANGE_ME")
SESSION_TABLE = os.environ.get("SESSION_TABLE", "aibank-session-routing")
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "aibank-loan-uploads-519124228967")
CLUSTER_ARN = "arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking"
SECRET_ARN = "arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ"
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "https://aibank.demoaws.com")

ddb = boto3.resource("dynamodb", region_name="eu-west-1")
session_table = ddb.Table(SESSION_TABLE)
loan_table = ddb.Table("aibank-personal-loan")
agentcore = boto3.client("bedrock-agentcore", region_name="eu-west-1")
rds = boto3.client("rds-data", region_name="me-south-1")
s3 = boto3.client("s3", region_name="eu-west-1", config=BotoConfig(signature_version="s3v4"))

_customer_cache = {}


def get_customer_info(email):
    if email in _customer_cache:
        return _customer_cache[email]
    resp = rds.execute_statement(
        resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database="corebanking",
        sql="SELECT customer_id, first_name FROM customers WHERE email = :e LIMIT 1",
        parameters=[{"name": "e", "value": {"stringValue": email}}])
    if resp["records"]:
        cid = resp["records"][0][0]["stringValue"]
        fname = resp["records"][0][1]["stringValue"]
        _customer_cache[email] = (cid, fname)
        return cid, fname
    return None, None


def validate_session(event):
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
    r = agentcore.invoke_agent_runtime(
        agentRuntimeArn=BANKING_ARN, runtimeSessionId=chat_session,
        payload=json.dumps({"prompt": prompt, "customer_id": customer_id, "customer_first_name": customer_first_name, "session_id": chat_session}),
        qualifier="DEFAULT")
    stream = r.get("response") or r.get("body")
    raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
    parsed = json.loads(raw)
    return parsed.get("answer", raw), parsed.get("loan_session_id")


def call_loan_agent(prompt, loan_session_id, customer_id):
    payload = json.dumps({
        "jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send",
        "params": {"message": {"role": "user",
            "parts": [{"kind": "text", "text": f"[Customer ID: {customer_id}] {prompt}"}],
            "messageId": uuid.uuid4().hex}}})
    try:
        r = agentcore.invoke_agent_runtime(
            agentRuntimeArn=LOAN_AGENT_ARN, runtimeSessionId=loan_session_id,
            payload=payload, qualifier="DEFAULT")
        stream = r.get("response") or r.get("body")
        raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
        parsed = json.loads(raw)
        # Check for A2A error response
        if "error" in parsed:
            print(f"LOAN_AGENT_ERROR: {parsed['error']}")
            return None
        result = parsed.get("result", {})
        for artifact in result.get("artifacts", []):
            for part in artifact.get("parts", []):
                if part.get("kind") == "text":
                    return part["text"]
        agent_texts = []
        for msg in result.get("history", []):
            if msg.get("role") == "agent":
                for part in msg.get("parts", []):
                    if part.get("kind") == "text":
                        agent_texts.append(part["text"])
        if agent_texts:
            return "".join(agent_texts)
        return raw
    except Exception as e:
        print(f"LOAN_AGENT_EXCEPTION: {e}")
        return None


def get_loan_session(chat_session):
    resp = session_table.get_item(Key={"session_id": f"loan:{chat_session}"})
    item = resp.get("Item")
    return item.get("loan_session_id") if item else None


def set_loan_session(chat_session, loan_session_id):
    session_table.put_item(Item={
        "session_id": f"loan:{chat_session}",
        "loan_session_id": loan_session_id,
        "ttl": int(time.time()) + 3600})


def clear_loan_session(chat_session):
    session_table.delete_item(Key={"session_id": f"loan:{chat_session}"})


def extract_actions(answer, customer_id, chat_session):
    """Detect upload requests from Loan Agent response and generate presigned URLs."""
    actions = []
    if not answer or len(answer.strip()) < 10:
        return answer or "", actions

    # Get application_id from response text or loan session
    app_id_match = re.search(r'(AIB-\d{8}-[A-Z0-9]{6})', answer)
    loan_meta = session_table.get_item(Key={"session_id": f"loan:{chat_session}"}).get("Item", {})
    app_id = app_id_match.group(1) if app_id_match else loan_meta.get("application_id")
    if not app_id:
        return answer, actions

    # Store app_id in loan session
    if get_loan_session(chat_session):
        try:
            session_table.update_item(
                Key={"session_id": f"loan:{chat_session}"},
                UpdateExpression="SET application_id = :a",
                ExpressionAttributeValues={":a": app_id})
        except Exception:
            pass

    # Detect upload requests by keywords OR markers
    answer_lower = answer.lower()
    doc_type = None
    if '[UPLOAD_REQUEST:salary_certificate]' in answer or \
       ('upload' in answer_lower and 'salary' in answer_lower and 'bank statement' not in answer_lower):
        doc_type = 'salary_certificate'
    elif '[UPLOAD_REQUEST:bank_statement]' in answer or \
         ('upload' in answer_lower and 'bank statement' in answer_lower and 'salary' not in answer_lower):
        doc_type = 'bank_statement'

    if doc_type:
        folder = doc_type
        filename = doc_type.replace("_", "-") + ".pdf"
        key = f"documents/input/{customer_id}/{app_id}/{folder}/{filename}"
        try:
            url = s3.generate_presigned_url("put_object",
                Params={"Bucket": UPLOAD_BUCKET, "Key": key, "ContentType": "application/pdf"}, ExpiresIn=900)
            actions.append({"document_type": doc_type, "upload_url": url, "key": key, "application_id": app_id})
        except Exception as e:
            pass

    clean = re.sub(r'\[UPLOAD_REQUEST:\w+\]', '', answer).strip()
    return clean, actions


def resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": FRONTEND_ORIGIN,
            "Access-Control-Allow-Credentials": "true"},
        "body": json.dumps(body)}


def handler(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method") == "OPTIONS":
        return {"statusCode": 200, "headers": {
            "Access-Control-Allow-Origin": FRONTEND_ORIGIN,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST,OPTIONS"}}

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
            # In active loan flow — route directly to Loan Agent
            answer = call_loan_agent(prompt, loan_sid, customer_id)
            if answer is None:
                # Loan Agent session broken — clear and fall back to Alma
                clear_loan_session(chat_session)
                answer, loan_session_id = call_banking(prompt, chat_session, customer_id, customer_first_name)
                if loan_session_id:
                    set_loan_session(chat_session, loan_session_id)
            else:
                # Check if loan flow should release
                # First check if response contains an app_id (new application being created)
                has_app_in_response = bool(re.search(r'AIB-\d{8}-[A-Z0-9]{6}', answer or ''))
                if not has_app_in_response:
                    loan_meta = session_table.get_item(Key={"session_id": f"loan:{chat_session}"}).get("Item", {})
                    app_id = loan_meta.get("application_id")
                    if not app_id:
                        # No app in response or session — eligibility rejected
                        clear_loan_session(chat_session)
                    elif app_id:
                        try:
                            app = loan_table.get_item(Key={"customer_id": customer_id, "application_id": app_id}).get("Item", {})
                            if app.get("status") in ("processing", "PENDING_REVIEW", "APPROVED", "REJECTED"):
                                clear_loan_session(chat_session)
                        except Exception:
                            pass
        else:
            # Route through Alma Banking
            answer, loan_session_id = call_banking(prompt, chat_session, customer_id, customer_first_name)

            # Alma returns loan_session_id when start_loan_application tool was called
            if loan_session_id:
                set_loan_session(chat_session, loan_session_id)

    except Exception as e:
        answer = f"I'm sorry, something went wrong. Please try again. ({str(e)[:100]})"

    answer = re.sub(r"<thinking>[\s\S]*?</thinking>", "", answer).strip()

    # Extract upload actions
    answer, upload_actions = extract_actions(answer, customer_id, chat_session)

    result = {"answer": answer, "session_id": chat_session, "customer_id": customer_id}
    if upload_actions:
        result["loan_upload"] = upload_actions
    print(f"RESPONSE: loan_upload={bool(upload_actions)}, actions={len(upload_actions)}, answer_preview={answer[:80]}")
    return resp(200, result)
