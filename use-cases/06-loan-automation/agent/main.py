"""
AI Bank Loan AI Agent — A2A Server on AgentCore Runtime
Called by Alma Banking Assistant via invoke_agent_runtime (A2A protocol).
"""
import os, json, logging, re, uuid, datetime
from decimal import Decimal
import boto3
from botocore.config import Config as BotoConfig
from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
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

PRODUCTS = {
    "instant_money": {"min": 100, "max": 500, "min_tenure": 3, "max_tenure": 12, "rate": 7.5, "salary_mult": 20, "auto": True},
    "personal": {"min": 500, "max": 20000, "min_tenure": 6, "max_tenure": 60, "rate": 4.5, "salary_mult": 40, "auto": False},
}

SYSTEM_PROMPT = """You are the AI Bank Loan Agent. You help customers apply for loans.

## PRODUCTS
- **Instant Money**: BHD 100–500, 3–12 months, 7.5% p.a., auto-approved in minutes
- **Personal Finance**: BHD 500–20,000, 6–60 months, 4.5% p.a., reviewed by officer (1-2 days)

## WORKFLOW — Execute ALL steps in a SINGLE response, do NOT wait between steps:
1. Extract customer_id from the message (format: CUST00000001). Never ask for it.
2. Determine loan type and amount from the customer's message. If tenure is missing, use default (6 months for instant_money, 12 months for personal). If purpose is missing, use "General".
3. Call check_loan_eligibility immediately.
4. If eligible, call calculate_loan immediately.
5. Call submit_loan_application immediately — the customer already expressed intent, do NOT ask for confirmation.
6. Return ONE response with: eligibility result, EMI breakdown, application ID, and upload instructions.
7. Include [ACTION:LOAN_UPLOAD:{application_id}] in your response.

CRITICAL: Complete the ENTIRE flow (eligibility → calculate → submit) in ONE turn. Never ask "shall I proceed?" or "would you like to confirm?" — the customer's request IS the confirmation.

## RULES
- Amounts in BHD (Bahraini Dinar), 3 decimal places
- Be concise: 2-3 sentences per response
- Never fabricate eligibility or financial data — only use tool results
- If eligibility fails, explain why and suggest alternatives"""


@tool
def check_loan_eligibility(customer_id: str, loan_type: str, amount: float) -> str:
    """Check if a customer is eligible for a loan based on salary, existing loans, and KYC.
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

    # KYC check
    try:
        kyc = ddb_mesouth.Table(KYC_TABLE).get_item(Key={"customer_id": customer_id}).get("Item")
        kyc_status = kyc.get("kyc_status", "PENDING") if kyc else "NOT_STARTED"
        if kyc_status != "VERIFIED":
            return json.dumps({"eligible": False, "reason": f"KYC status is {kyc_status}. Identity verification must be completed first."})
    except Exception as e:
        log.error(f"KYC check error: {e}")

    # Salary check
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

    # Existing loans check
    try:
        from boto3.dynamodb.conditions import Key, Attr
        existing = ddb.Table(LOAN_TABLE).query(
            KeyConditionExpression=Key("customer_id").eq(customer_id),
            FilterExpression=Attr("status").is_in(["PENDING_REVIEW", "APPROVED", "processing", "SUBMITTED"]))
        if existing.get("Items"):
            return json.dumps({"eligible": False, "reason": f"You have {len(existing['Items'])} active application(s). Please wait for completion."})
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
def submit_loan_application(customer_id: str, loan_type: str, amount: float, tenure_months: int, purpose: str) -> str:
    """Submit a loan application. Creates DynamoDB record and returns presigned upload URLs.
    Args:
        customer_id: Customer ID (e.g. CUST00000001)
        loan_type: Either 'instant_money' or 'personal'
        amount: Loan amount in BHD
        tenure_months: Tenure in months
        purpose: Purpose of the loan
    """
    app_id = f"AIB-{datetime.date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    try:
        ddb.Table(LOAN_TABLE).update_item(
            Key={"customer_id": customer_id, "application_id": app_id},
            UpdateExpression="SET loan_type=:lt, amount_bhd=:ab, amount=:a, tenure_months=:tm, "
                             "#dur=:d, purpose=:p, #s=:st, submitted_at=:sa, channel=:ch",
            ExpressionAttributeNames={"#s": "status", "#dur": "duration"},
            ExpressionAttributeValues={
                ":lt": loan_type, ":ab": str(amount), ":a": Decimal(str(amount)),
                ":tm": tenure_months, ":d": tenure_months, ":p": purpose,
                ":st": "SUBMITTED", ":sa": datetime.datetime.utcnow().isoformat(), ":ch": "alma_assistant"})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})

    sal_key = f"documents/input/{customer_id}/{app_id}/salary_certificate/salary-certificate.pdf"
    bs_key = f"documents/input/{customer_id}/{app_id}/bank_statement/bank-statement.pdf"
    try:
        sal_url = s3.generate_presigned_url("put_object", Params={"Bucket": UPLOAD_BUCKET, "Key": sal_key, "ContentType": "application/pdf"}, ExpiresIn=900)
        bs_url = s3.generate_presigned_url("put_object", Params={"Bucket": UPLOAD_BUCKET, "Key": bs_key, "ContentType": "application/pdf"}, ExpiresIn=900)
    except Exception as e:
        log.error(f"Presigned URL error: {e}")
        sal_url = bs_url = ""

    return json.dumps({"success": True, "application_id": app_id, "status": "SUBMITTED",
                        "upload_urls": {"salary_certificate": {"url": sal_url, "key": sal_key},
                                        "bank_statement": {"url": bs_url, "key": bs_key}},
                        "auto_decision": PRODUCTS.get(loan_type, {}).get("auto", False),
                        "next_step": "Upload salary certificate and 3-month bank statement."})


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

strands_agent = Agent(
    name="AI Bank Loan Agent",
    description="Handles loan applications for AI Bank. Checks eligibility, calculates EMI, submits applications, and provides upload URLs for required documents.",
    model=BedrockModel(model_id="eu.anthropic.claude-3-haiku-20240307-v1:0", region_name=REGION),
    system_prompt=SYSTEM_PROMPT,
    tools=[check_loan_eligibility, calculate_loan, submit_loan_application, check_loan_status],
    callback_handler=None,
)

a2a_server = A2AServer(agent=strands_agent, http_url=runtime_url, serve_at_root=True)
app = FastAPI()

@app.get("/ping")
def ping():
    return {"status": "healthy"}

app.mount("/", a2a_server.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
