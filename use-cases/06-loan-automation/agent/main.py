"""
AI Bank Loan AI Agent — A2A Server on AgentCore Runtime
Multi-turn conversational loan application: eligibility → docs upload → submit.
"""
import os, json, logging, re, uuid, datetime
from decimal import Decimal
import boto3
from botocore.config import Config as BotoConfig
from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from strands.hooks import BeforeInvocationEvent, AfterInvocationEvent
from bedrock_agentcore.memory import MemoryClient
import uvicorn
from fastapi import FastAPI

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

REGION = os.environ.get("AWS_REGION", "eu-west-1")
DB_REGION = os.environ.get("DB_REGION", "me-south-1")
CLUSTER_ARN = os.environ.get("CLUSTER_ARN", "arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking")
SECRET_ARN = os.environ.get("SECRET_ARN", "arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ")
DB_NAME = os.environ.get("DB_NAME", "corebanking")
LOAN_TABLE = os.environ.get("LOAN_TABLE", "aibank-personal-loan")
KYC_TABLE = os.environ.get("KYC_TABLE", "aibank-customer-kyc")
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "aibank-loan-uploads-519124228967")

rds = boto3.client("rds-data", region_name=DB_REGION)
ddb = boto3.resource("dynamodb", region_name=REGION)
ddb_mesouth = boto3.resource("dynamodb", region_name=DB_REGION)
s3 = boto3.client("s3", region_name=REGION, config=BotoConfig(signature_version="s3v4"))

_pending_uploads = {}  # "customer_id:doc_type" -> {"url": ..., "key": ..., "application_id": ...}

CONFIG_TABLE = os.environ.get("CONFIG_TABLE", "aibank-loan-config")

def _load_config():
    """Load product configs and policy from DynamoDB config table. Fails if unavailable."""
    tbl = ddb_mesouth.Table(CONFIG_TABLE)
    products = {}
    policy = {"max_active_loans": 10}
    resp = tbl.query(KeyConditionExpression=boto3.dynamodb.conditions.Key("config_type").eq("product"))
    if not resp.get("Items"):
        raise RuntimeError("No product config found in aibank-loan-config table")
    for item in resp["Items"]:
        products[item["config_id"]] = {
            "min": int(item["min_amount"]), "max": int(item["max_amount"]),
            "min_tenure": int(item["min_tenure"]), "max_tenure": int(item["max_tenure"]),
            "rate": float(item["rate"]), "salary_mult": int(item["salary_multiplier"]),
            "auto": bool(item.get("auto_decision", False))
        }
    resp2 = tbl.get_item(Key={"config_type": "policy", "config_id": "loan_limits"})
    if "Item" in resp2:
        policy["max_active_loans"] = int(resp2["Item"].get("max_active_loans", 10))
    log.info(f"Loaded config: {len(products)} products, max_active_loans={policy['max_active_loans']}")
    return products, policy

PRODUCTS, POLICY = _load_config()

SYSTEM_PROMPT = f"""You are the AI Bank Loan Agent. You guide customers through loan applications step by step.

## PRODUCTS
- **Instant Money**: BHD {PRODUCTS.get('instant_money',{}).get('min',100)}–{PRODUCTS.get('instant_money',{}).get('max',500)}, {PRODUCTS.get('instant_money',{}).get('min_tenure',3)}–{PRODUCTS.get('instant_money',{}).get('max_tenure',12)} months, {PRODUCTS.get('instant_money',{}).get('rate',7.5)}% p.a., auto-approved in minutes
- **Personal Finance**: BHD {PRODUCTS.get('personal',{}).get('min',500)}–{PRODUCTS.get('personal',{}).get('max',20000)}, {PRODUCTS.get('personal',{}).get('min_tenure',6)}–{PRODUCTS.get('personal',{}).get('max_tenure',60)} months, {PRODUCTS.get('personal',{}).get('rate',4.5)}% p.a., reviewed by officer (1-2 days)

## CONVERSATIONAL FLOW — Follow these steps IN ORDER, one per turn:

### Step 1: Collect Details & Check Eligibility
- Extract customer_id from the message (format CUST00000001). Never ask for it.
- Determine loan_type and amount from the message.
- If tenure is NOT specified, ASK the customer for it. Give the valid range (3–12 months for instant_money, 6–60 months for personal). Do NOT assume a default. STOP and wait.
- If purpose is NOT specified, ASK briefly. STOP and wait.
- Once you have loan_type, amount, tenure, AND purpose: call check_loan_eligibility.
- If eligible, call calculate_loan and show the EMI breakdown.
- Ask: "Would you like to proceed? You'll need to upload your salary certificate and bank statement."

### Step 2: Customer Confirms → Request Salary Certificate
- When customer confirms (yes, proceed, go ahead, confirm, etc.):
- Call generate_upload_url with document_type="salary_certificate", application_id="pending", AND include loan_type, amount, tenure_months, purpose from the conversation.
- The tool returns JSON with "application_id". You MUST include the application_id AND the marker in your response.
- Say: "Great! Your application ID is {{application_id}}. Please upload your salary certificate now. [UPLOAD_REQUEST:salary_certificate]"
- STOP here. Wait for next message.

### Step 3: Salary Certificate Uploaded → Request Bank Statement
- When customer says they uploaded or you receive "uploaded salary_certificate":
- Call generate_upload_url with document_type="bank_statement" using the SAME application_id from Step 2. No need to pass loan details again.
- Say: "Salary certificate received! Now please upload your 3-month bank statement for application {{application_id}}. [UPLOAD_REQUEST:bank_statement]"
- STOP here. Wait for next message.

### Step 4: Bank Statement Uploaded → Submit Application
- When customer says they uploaded or you receive "uploaded bank_statement":
- Call submit_loan_application with all collected details.
- Say: "All documents received! Your application {{app_id}} has been submitted."
- For instant_money: "You'll receive a decision within minutes."
- For personal: "A loan officer will review within 1-2 business days."

## RULES
- ONE step per turn. Never skip ahead.
- The customer_id is ALWAYS in the message prefix as [Customer ID: CUSTxxxxxxxx]. Extract it from there. NEVER ask the customer for it.
- Amounts in BHD, 3 decimal places.
- Be concise: 2-3 sentences per response.
- Never fabricate data — only use tool results.
- If eligibility fails, explain why and stop.
- Track state via conversation history — remember what step you're on."""


@tool
def check_loan_eligibility(customer_id: str, loan_type: str, amount: float) -> str:
    """Check if a customer is eligible for a loan.
    Args:
        customer_id: Customer ID (e.g. CUST00000001)
        loan_type: Either 'instant_money' or 'personal'
        amount: Requested loan amount in BHD
    """
    if loan_type not in PRODUCTS:
        return json.dumps({"eligible": False, "reason": "Unknown loan type. Choose: instant_money or personal"})
    p = PRODUCTS[loan_type]
    if amount < p["min"] or amount > p["max"]:
        return json.dumps({"eligible": False, "reason": f"Amount must be BHD {p['min']}–{p['max']} for {loan_type}"})

    try:
        kyc = ddb_mesouth.Table(KYC_TABLE).get_item(Key={"customer_id": customer_id}).get("Item")
        kyc_status = kyc.get("kyc_status", "PENDING") if kyc else "NOT_STARTED"
        if kyc_status != "VERIFIED":
            return json.dumps({"eligible": False, "reason": f"KYC status is {kyc_status}. Identity verification must be completed first."})
    except Exception as e:
        log.error(f"KYC check error: {e}")

    try:
        resp = rds.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="SELECT AVG(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
                "WHERE a.customer_id=:cid AND t.transaction_type='credit' AND t.category_id='CAT014' "
                "AND t.transaction_date>=DATE_SUB(CURDATE(),INTERVAL 3 MONTH)",
            parameters=[{"name": "cid", "value": {"stringValue": customer_id}}])
        rec = resp["records"][0][0]
        avg_salary = float(rec.get("doubleValue", rec.get("stringValue", 0))) if not rec.get("isNull") else 0
    except Exception as e:
        log.error(f"Salary check: {e}")
        avg_salary = 0

    if avg_salary == 0:
        return json.dumps({"eligible": False, "reason": "No salary credits found in the last 3 months."})

    max_loan = avg_salary * p["salary_mult"]
    if amount > max_loan:
        return json.dumps({"eligible": False, "reason": f"Based on avg salary BHD {avg_salary:.3f}, max eligible is BHD {max_loan:.3f}."})

    try:
        from boto3.dynamodb.conditions import Key, Attr
        existing = ddb.Table(LOAN_TABLE).query(
            KeyConditionExpression=Key("customer_id").eq(customer_id),
            FilterExpression=Attr("status").is_in(["PENDING_REVIEW", "APPROVED", "processing", "SUBMITTED"]))
        if len(existing.get("Items", [])) >= POLICY["max_active_loans"]:
            return json.dumps({"eligible": False, "reason": f"You have {len(existing['Items'])} active application(s). Maximum allowed is {POLICY['max_active_loans']}."})
    except Exception as e:
        log.error(f"Loan check: {e}")

    return json.dumps({"eligible": True, "avg_monthly_salary": round(avg_salary, 3),
                        "max_eligible_amount": round(max_loan, 3), "loan_type": loan_type,
                        "auto_decision": p["auto"]})


@tool
def calculate_loan(amount: float, tenure_months: int, loan_type: str) -> str:
    """Calculate EMI, total interest, and total repayment.
    Args:
        amount: Loan amount in BHD
        tenure_months: Tenure in months
        loan_type: Either 'instant_money' or 'personal'
    """
    if loan_type not in PRODUCTS:
        return json.dumps({"error": "Unknown loan type"})
    p = PRODUCTS[loan_type]
    if tenure_months < p["min_tenure"] or tenure_months > p["max_tenure"]:
        return json.dumps({"error": f"Tenure must be {p['min_tenure']}–{p['max_tenure']} months"})
    r = p["rate"] / 100 / 12
    emi = amount * r * (1 + r) ** tenure_months / ((1 + r) ** tenure_months - 1) if r > 0 else amount / tenure_months
    total = emi * tenure_months
    return json.dumps({"monthly_emi": round(emi, 3), "total_repayment": round(total, 3),
                        "total_interest": round(total - amount, 3), "annual_rate": p["rate"],
                        "amount": amount, "tenure_months": tenure_months})


@tool
def generate_upload_url(customer_id: str, application_id: str, document_type: str,
                        loan_type: str = "", amount: float = 0, tenure_months: int = 0, purpose: str = "") -> str:
    """Generate a presigned S3 upload URL for a loan document.
    Args:
        customer_id: Customer ID (e.g. CUST00000001)
        application_id: Loan application ID (e.g. AIB-20260315-XXXX). Use 'pending' if not yet created.
        document_type: Either 'salary_certificate' or 'bank_statement'
        loan_type: Either 'instant_money' or 'personal' (required on first call)
        amount: Loan amount in BHD (required on first call)
        tenure_months: Tenure in months (required on first call)
        purpose: Purpose of the loan (required on first call)
    """
    if application_id == "pending":
        application_id = f"AIB-{datetime.date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    # On first call (salary_certificate), create the DynamoDB record with loan details
    if document_type == "salary_certificate" and loan_type and amount > 0:
        try:
            ddb.Table(LOAN_TABLE).update_item(
                Key={"customer_id": customer_id, "application_id": application_id},
                UpdateExpression="SET loan_type=:lt, amount_bhd=:ab, amount=:a, tenure_months=:tm, "
                                 "#dur=:d, purpose=:p, #s=:st, submitted_at=:sa, channel=:ch",
                ExpressionAttributeNames={"#s": "status", "#dur": "duration"},
                ExpressionAttributeValues={
                    ":lt": loan_type, ":ab": str(amount), ":a": Decimal(str(amount)),
                    ":tm": tenure_months, ":d": tenure_months, ":p": purpose or "General purpose",
                    ":st": "SUBMITTED", ":sa": datetime.datetime.utcnow().isoformat(), ":ch": "alma_assistant"})
            log.info(f"Created loan record {application_id} with details before upload")
        except Exception as e:
            log.error(f"Pre-create loan record error: {e}")

    folder = "salary_certificate" if document_type == "salary_certificate" else "bank_statement"
    filename = "salary-certificate.pdf" if document_type == "salary_certificate" else "bank-statement.pdf"
    key = f"documents/input/{customer_id}/{application_id}/{folder}/{filename}"

    try:
        url = s3.generate_presigned_url("put_object",
            Params={"Bucket": UPLOAD_BUCKET, "Key": key, "ContentType": "application/pdf"}, ExpiresIn=900)
        _pending_uploads[f"{customer_id}:{document_type}"] = {"url": url, "key": key, "application_id": application_id}
        return json.dumps({"success": True, "document_type": document_type, "application_id": application_id,
                           "message": f"Upload URL ready for {document_type}. Include [UPLOAD_REQUEST:{document_type}] in your response."})
    except Exception as e:
        log.error(f"Presigned URL error: {e}")
        return json.dumps({"error": str(e)})


@tool
def submit_loan_application(customer_id: str, application_id: str, loan_type: str, amount: float, tenure_months: int, purpose: str) -> str:
    """Submit the loan application after documents are uploaded.
    Args:
        customer_id: Customer ID
        application_id: Application ID generated during upload URL step
        loan_type: Either 'instant_money' or 'personal'
        amount: Loan amount in BHD
        tenure_months: Tenure in months
        purpose: Purpose of the loan
    """
    try:
        ddb.Table(LOAN_TABLE).update_item(
            Key={"customer_id": customer_id, "application_id": application_id},
            UpdateExpression="SET loan_type=:lt, amount_bhd=:ab, amount=:a, tenure_months=:tm, "
                             "#dur=:d, purpose=:p, documents=:doc, #s=:st, submitted_at=:sa, channel=:ch",
            ExpressionAttributeNames={"#s": "status", "#dur": "duration"},
            ExpressionAttributeValues={
                ":lt": loan_type, ":ab": str(amount), ":a": Decimal(str(amount)),
                ":tm": tenure_months, ":d": tenure_months, ":p": purpose,
                ":doc": {"salary_certificate": "uploaded", "bank_statement": "uploaded"},
                ":st": "SUBMITTED", ":sa": datetime.datetime.utcnow().isoformat(), ":ch": "alma_assistant"})
        p = PRODUCTS.get(loan_type, {})
        # Sync to core banking MySQL
        emi = 0
        r = p["rate"] / 100 / 12
        if r > 0:
            emi = amount * r * (1 + r) ** tenure_months / ((1 + r) ** tenure_months - 1)
        try:
            rds.execute_statement(
                resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
                sql="INSERT INTO loan_applications (application_id, customer_id, loan_type, amount, status, "
                    "monthly_payment, duration, interest, purpose, channel, application_source) "
                    "VALUES (:aid, :cid, :lt, :amt, 'submitted', :emi, :dur, :rate, :purp, 'alma_assistant', 'chat') "
                    "ON DUPLICATE KEY UPDATE status='submitted', updated_at=NOW()",
                parameters=[
                    {"name": "aid", "value": {"stringValue": application_id}},
                    {"name": "cid", "value": {"stringValue": customer_id}},
                    {"name": "lt", "value": {"stringValue": loan_type}},
                    {"name": "amt", "value": {"doubleValue": amount}},
                    {"name": "emi", "value": {"doubleValue": round(emi, 2)}},
                    {"name": "dur", "value": {"longValue": tenure_months}},
                    {"name": "rate", "value": {"doubleValue": p["rate"]}},
                    {"name": "purp", "value": {"stringValue": purpose}}])
        except Exception as e:
            log.error(f"Core banking sync error: {e}")
        return json.dumps({"success": True, "application_id": application_id, "status": "SUBMITTED",
                            "auto_decision": p.get("auto", False)})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@tool
def check_loan_status(customer_id: str) -> str:
    """Check all loan application statuses for a customer.
    Args:
        customer_id: Customer ID (e.g. CUST00000001)
    """
    try:
        from boto3.dynamodb.conditions import Key
        resp = ddb.Table(LOAN_TABLE).query(KeyConditionExpression=Key("customer_id").eq(customer_id))
        loans = [{"application_id": i.get("application_id"), "loan_type": i.get("loan_type"),
                   "amount": str(i.get("amount_bhd", "")), "status": i.get("status"),
                   "submitted_at": i.get("submitted_at")} for i in resp.get("Items", [])]
        loans.sort(key=lambda x: x.get("submitted_at") or "", reverse=True)
        return json.dumps({"loans": loans, "count": len(loans)})
    except Exception as e:
        return json.dumps({"error": str(e)})


runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")
LOAN_MEMORY_ID = os.environ.get("BEDROCK_AGENTCORE_MEMORY_ID", "loan_agent_a2a_mem-p9206F7MkP")
loan_memory_client = MemoryClient(region_name=REGION)

# ── STM Hooks for Loan Agent multi-turn persistence ──
def loan_load_stm(event: BeforeInvocationEvent):
    """Load previous conversation turns from STM before each invocation."""
    # Extract session_id from the user message (passed as [Customer ID: ...] prefix)
    msgs = event.agent.messages
    if not msgs:
        return
    # Get session context from the last user message
    last_user = ""
    for m in msgs:
        if m.get("role") == "user":
            last_user = m.get("content", [{}])[0].get("text", "")
    # Extract customer_id for actor_id
    cid_match = re.search(r'(CUST\d{8})', last_user)
    actor_id = cid_match.group(1) if cid_match else "unknown"
    # Extract session_id from message prefix
    sid_match = re.search(r'Session:\s*([a-f0-9-]+)', last_user)
    session_id = sid_match.group(1) if sid_match else "default"
    if session_id == "default":
        return
    try:
        turns = loan_memory_client.get_last_k_turns(
            memory_id=LOAN_MEMORY_ID, actor_id=actor_id, session_id=session_id, k=5
        )
        turns.reverse()  # Chronological order
        stm_messages = []
        for turn in turns:
            for evt in turn:
                role = evt.get("role", "").lower()
                text = evt.get("content", {}).get("text", "")
                if role in ("user", "assistant") and text:
                    stm_messages.append({"role": role, "content": [{"text": text}]})
        if stm_messages:
            current = list(msgs)
            msgs.clear()
            msgs.extend(stm_messages[-10:] + current)
            log.info(f"Loan STM: loaded {len(stm_messages[-10:])} messages for session {session_id[:20]}")
    except Exception as e:
        log.warning(f"Loan STM load failed: {e}")


def loan_save_stm(event: AfterInvocationEvent):
    """Save conversation turn to STM after each invocation."""
    # Extract session_id and customer_id from messages
    actor_id = "unknown"
    session_id = "default"
    for m in event.agent.messages:
        if m.get("role") == "user":
            text = m.get("content", [{}])[0].get("text", "")
            cid_match = re.search(r'(CUST\d{8})', text)
            sid_match = re.search(r'Session:\s*([a-f0-9-]+)', text)
            if cid_match:
                actor_id = cid_match.group(1)
            if sid_match:
                session_id = sid_match.group(1)
            if actor_id != "unknown" and session_id != "default":
                break
    if session_id == "default":
        return
    try:
        messages_to_save = []
        # First user text message
        for msg in event.agent.messages:
            if msg.get("role") == "user":
                content = msg.get("content", [])
                if not any("toolResult" in c for c in content):
                    text_parts = [c.get("text", "") for c in content if "text" in c]
                    if text_parts:
                        messages_to_save.append((text_parts[0][:500], "USER"))
                        break
        # Last assistant text message
        for msg in reversed(event.agent.messages):
            if msg.get("role") == "assistant":
                content = msg.get("content", [])
                if not any("toolUse" in c for c in content):
                    text_parts = [c.get("text", "") for c in content if "text" in c]
                    if text_parts:
                        messages_to_save.append((text_parts[0][:500], "ASSISTANT"))
                        break
        if messages_to_save:
            loan_memory_client.save_conversation(
                memory_id=LOAN_MEMORY_ID, actor_id=actor_id, session_id=session_id,
                messages=messages_to_save
            )
            log.info(f"Loan STM: saved {len(messages_to_save)} messages")
    except Exception as e:
        log.warning(f"Loan STM save failed: {e}")


strands_agent = Agent(
    name="AI Bank Loan Agent",
    description="Guides customers through loan applications step by step: eligibility check, document uploads (salary certificate and bank statement), and application submission.",
    model=BedrockModel(model_id="eu.anthropic.claude-sonnet-4-20250514-v1:0", region_name=REGION),
    system_prompt=SYSTEM_PROMPT,
    tools=[check_loan_eligibility, calculate_loan, generate_upload_url, submit_loan_application, check_loan_status],
    callback_handler=None,
    trace_attributes={
        "agent.name": "loan_agent_a2a",
        "tags": ["loan", "a2a", "agentcore"],
    },
)
strands_agent.add_hook(loan_load_stm, BeforeInvocationEvent)
strands_agent.add_hook(loan_save_stm, AfterInvocationEvent)

a2a_server = A2AServer(agent=strands_agent, http_url=runtime_url, serve_at_root=True)
app = FastAPI()

@app.get("/ping")
def ping():
    return {"status": "healthy"}

app.mount("/", a2a_server.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
