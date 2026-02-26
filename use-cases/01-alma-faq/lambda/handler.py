import os
import json, uuid, boto3, re, time

ALMA_ARN = os.environ.get("ALMA_RUNTIME_ARN", "CHANGE_ME")
ONBOARDING_ARN = os.environ.get("ONBOARDING_RUNTIME_ARN", "CHANGE_ME")
TABLE = os.environ.get("SESSION_TABLE", "aibank-session-routing")
TTL_HOURS = 1

ddb = boto3.resource("dynamodb", region_name=os.environ.get("COMPUTE_REGION", "eu-west-1"))
table = ddb.Table(TABLE)
agentcore = boto3.client("bedrock-agentcore", region_name=os.environ.get("COMPUTE_REGION", "eu-west-1"))


def call_alma(prompt, session_id):
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=ALMA_ARN,
        runtimeSessionId=session_id,
        payload=json.dumps({"prompt": prompt}),
        qualifier="DEFAULT"
    )
    stream = resp.get("response") or resp.get("body")
    raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
    parsed = json.loads(raw)
    return parsed.get("answer", raw)


def call_onboarding(prompt, onboarding_session_id):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": prompt}],
                "messageId": uuid.uuid4().hex,
            }
        }
    })
    resp = agentcore.invoke_agent_runtime(
        agentRuntimeArn=ONBOARDING_ARN,
        runtimeSessionId=onboarding_session_id,
        payload=payload,
        qualifier="DEFAULT"
    )
    stream = resp.get("response") or resp.get("body")
    raw = stream.read().decode("utf-8") if hasattr(stream, "read") else str(stream)
    parsed = json.loads(raw)
    # Extract text from A2A response
    for artifact in parsed.get("result", {}).get("artifacts", []):
        for part in artifact.get("parts", []):
            if part.get("kind") == "text":
                return part["text"]
    return raw


def get_session_route(session_id):
    resp = table.get_item(Key={"session_id": session_id})
    item = resp.get("Item")
    if item:
        return item.get("onboarding_session_id")
    return None


def set_onboarding(session_id, onboarding_session_id):
    table.put_item(Item={
        "session_id": session_id,
        "onboarding_session_id": onboarding_session_id,
        "ttl": int(time.time()) + TTL_HOURS * 3600,
    })


def clear_onboarding(session_id):
    table.delete_item(Key={"session_id": session_id})


def handler(event, context):
    body = json.loads(event.get("body", "{}"))
    prompt = body.get("message", "Hello")
    session_id = body.get("session_id", str(uuid.uuid4()))

    try:
        onboarding_sid = get_session_route(session_id)

        if onboarding_sid:
            # Already in onboarding flow — route directly
            answer = call_onboarding(prompt, onboarding_sid)
            # Check if onboarding is complete
            if "Account created" in answer or "welcome email" in answer.lower():
                clear_onboarding(session_id)
        else:
            # Route through Alma
            answer = call_alma(prompt, session_id)
            # Detect if Alma handed off to onboarding (response asks for personal info)
            onboarding_signals = ["first name", "date of birth", "email address", "phone number", "nationality"]
            if sum(1 for s in onboarding_signals if s in answer.lower()) >= 3:
                # Alma used the onboarding tool — seed a dedicated onboarding session
                onboarding_sid = str(uuid.uuid4())
                call_onboarding(prompt, onboarding_sid)  # seed with original message for context
                set_onboarding(session_id, onboarding_sid)

    except Exception as e:
        answer = f"I'm sorry, something went wrong. Please try again. ({str(e)[:100]})"

    answer = re.sub(r"<thinking>[\s\S]*?</thinking>", "", answer).strip()
    answer = re.sub(r"</?response>", "", answer).strip()

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps({"answer": answer, "session_id": session_id})
    }
