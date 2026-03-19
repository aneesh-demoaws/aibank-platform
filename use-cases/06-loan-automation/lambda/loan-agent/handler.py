"""
AI Bank — Loan Agent Lambda Handler
Routes:
  POST /upload-urls  — pre-signed S3 PUT URLs (no Strands)
  POST /apply        — direct loan application → DynamoDB → Step Functions (no Strands)
  GET  /loans        — query DynamoDB by customer_id
  POST /chat         — Alma conversational agent (lazy-loads Strands)

customer_id is always resolved from the Cognito JWT claims (custom:customer_id),
never trusted from the client body.
"""
import json
import logging
import os
import uuid
import datetime
import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

UPLOAD_BUCKET     = os.environ.get("UPLOAD_BUCKET", "aibank-loan-uploads-519124228967")
DYNAMODB_TABLE    = os.environ.get("DYNAMODB_TABLE", "aibank-personal-loan")
LOAN_WORKFLOW_ARN = os.environ.get("LOAN_WORKFLOW_ARN", "")

s3  = boto3.client("s3", region_name="eu-west-1")
ddb = boto3.resource("dynamodb", region_name="eu-west-1")

_agent = None

def get_agent():
    global _agent
    if _agent is None:
        from agent import AIBankLoanAgent
        _agent = AIBankLoanAgent()
    return _agent


def _resolve_customer_id(event):
    """
    Resolve customer_id injected by the session-cookie Lambda authorizer.
    The authorizer validates aibank_sid cookie → DynamoDB → returns customer_id in context.
    """
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    return authorizer.get("customer_id") or authorizer.get("principalId") or "unknown"


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return _cors(200, "")

    path = event.get("path", "")

    if path.endswith("/upload-urls"):
        return handle_upload_urls(event)
    if path.endswith("/apply"):
        return handle_apply(event)
    if path.endswith("/loans"):
        return handle_loans(event)

    # /chat
    try:
        body       = json.loads(event.get("body") or "{}")
        message    = body.get("message", "").strip()
        customer_id = _resolve_customer_id(event)
        session_id  = body.get("sessionId", context.aws_request_id)

        if not message:
            return _cors(400, json.dumps({"error": "message is required"}))

        response = get_agent().process(message, user_id=customer_id, session_id=session_id)
        return _cors(200, json.dumps({"response": response, "sessionId": session_id}))

    except Exception:
        logger.exception("Loan agent error")
        return _cors(500, json.dumps({"error": "Service temporarily unavailable. Please try again."}))


def handle_upload_urls(event):
    try:
        body        = json.loads(event.get("body") or "{}")
        customer_id = _resolve_customer_id(event)
        app_id      = body.get("applicationId", str(uuid.uuid4()))
        files       = body.get("files")

        keys = [f["key"] for f in files] if files else [
            f"documents/input/{customer_id}/{app_id}/salary_certificate/salary-certificate.pdf",
            f"documents/input/{customer_id}/{app_id}/bank_statement/bank-statement.pdf",
        ]
        urls = [
            s3.generate_presigned_url(
                "put_object",
                Params={"Bucket": UPLOAD_BUCKET, "Key": k, "ContentType": "application/pdf"},
                ExpiresIn=900,
            )
            for k in keys
        ]
        return _cors(200, json.dumps({"urls": urls, "applicationId": app_id, "keys": keys}))
    except Exception:
        logger.exception("upload-urls error")
        return _cors(500, json.dumps({"error": "Could not generate upload URLs"}))


def handle_loans(event):
    try:
        customer_id = _resolve_customer_id(event)
        if customer_id == "unknown":
            return _cors(401, json.dumps({"error": "Unauthorized"}))

        table = ddb.Table(DYNAMODB_TABLE)
        resp  = table.query(KeyConditionExpression=Key("customer_id").eq(customer_id))
        loans = [
            {
                "applicationId": i.get("application_id"),
                "loanType":      i.get("loan_type"),
                "amount":        str(i.get("amount_bhd", "")),
                "tenureMonths":  int(i.get("tenure_months", 0)),
                "status":        i.get("status"),
                "submittedAt":   i.get("submitted_at"),
            }
            for i in resp.get("Items", [])
        ]
        loans.sort(key=lambda x: x.get("submittedAt") or "", reverse=True)
        return _cors(200, json.dumps({"loans": loans}))
    except Exception:
        logger.exception("loans error")
        return _cors(500, json.dumps({"error": "Could not retrieve loans"}))


def handle_apply(event):
    try:
        body        = json.loads(event.get("body") or "{}")
        customer_id = _resolve_customer_id(event)
        app_id      = body.get("applicationId") or f"AIB-{datetime.date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        loan_type   = body.get("loanType", "personal")
        amount      = float(body.get("amount", 0))
        months      = int(body.get("tenureMonths", 12))
        purpose     = body.get("purpose", "")
        documents   = body.get("documents", {})

        table = ddb.Table(DYNAMODB_TABLE)
        table.update_item(
            Key={"customer_id": customer_id, "application_id": app_id},
            UpdateExpression="SET loan_type=:lt, amount_bhd=:ab, amount=:a, tenure_months=:tm, "
                             "duration=:d, purpose=:p, documents=:doc, "
                             "#s=:st, submitted_at=:sa, channel=:ch",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":lt": loan_type, ":ab": str(amount), ":a": amount,
                ":tm": months, ":d": months, ":p": purpose, ":doc": documents,
                ":st": "SUBMITTED", ":sa": datetime.datetime.utcnow().isoformat(),
                ":ch": "web_form",
            },
        )

        return _cors(200, json.dumps({
            "applicationId": app_id,
            "status":        "SUBMITTED",
            "message":       f"Application {app_id} submitted. You'll receive an update within 2 business days.",
        }))
    except Exception:
        logger.exception("apply error")
        return _cors(500, json.dumps({"error": "Could not submit application. Please try again."}))


def _cors(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type":                "application/json",
            "Access-Control-Allow-Origin": "https://aibank.demoaws.com",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
        },
        "body": body,
    }
