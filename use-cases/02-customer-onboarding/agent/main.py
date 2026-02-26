import boto3
import json
import logging
import os
import random
import re
import string
from datetime import datetime, date
from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
import uvicorn
from fastapi import FastAPI

# GCC country codes and their expected local number lengths
GCC_CODES = {
    "973": 8,   # Bahrain
    "966": 9,   # Saudi Arabia
    "971": 9,   # UAE
    "968": 8,   # Oman
    "974": 8,   # Qatar
    "965": 8,   # Kuwait
}


def _normalize_phone(phone: str) -> str:
    """Normalize phone to E.164 format. Supports all GCC countries."""
    digits = re.sub(r'[^\d]', '', phone.strip().lstrip('+'))
    # Already has country code: 973XXXXXXXX, 966XXXXXXXXX, etc.
    for code, length in GCC_CODES.items():
        if digits.startswith(code) and len(digits) == len(code) + length:
            return f"+{digits}"
    # Starts with 00 (international): 00973XXXXXXXX
    if digits.startswith('00'):
        stripped = digits[2:]
        for code, length in GCC_CODES.items():
            if stripped.startswith(code) and len(stripped) == len(code) + length:
                return f"+{stripped}"
    # Local Bahrain number (8 digits starting with 3)
    if len(digits) == 8 and digits[0] in ('3', '1', '6', '7'):
        return f"+973{digits}"
    # Local Saudi (9 digits starting with 5)
    if len(digits) == 9 and digits[0] == '5':
        return f"+966{digits}"
    # Local UAE (9 digits starting with 5)
    if len(digits) == 9 and digits[0] in ('5', '4'):
        return f"+971{digits}"
    # Can't normalize — return with + prefix if it looks valid
    if len(digits) >= 10:
        return f"+{digits}"
    return phone.strip()


def _validate_phone(phone: str) -> bool:
    """Validate E.164 phone number for GCC countries."""
    for code, length in GCC_CODES.items():
        if re.match(rf'^\+{code}\d{{{length}}}$', phone):
            return True
    return False


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

REGION = "eu-west-1"
runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")

COGNITO_REGION = "me-south-1"
COGNITO_POOL_ID = os.environ.get("COGNITO_POOL_ID", "CHANGE_ME")
CLUSTER_ARN = os.environ.get("CLUSTER_ARN", "CHANGE_ME")
SECRET_ARN = os.environ.get("DB_SECRET_ARN", "CHANGE_ME")
SES_SECRET_ARN = os.environ.get("SES_SECRET_ARN", "CHANGE_ME")
DB_NAME = os.environ.get("AURORA_DB_NAME", "corebanking")

SYSTEM_PROMPT = """You are the AI Bank Customer Onboarding Agent. Your ONLY job is to open new bank accounts.

## REQUIRED INFORMATION:
1. Account type (Savings, Premium, or Business)
2. First name
3. Last name
4. Date of birth (YYYY-MM-DD) — must be 18+
5. Email address
6. Phone number (with country code, e.g. +973XXXXXXXX)
7. Nationality

## WORKFLOW:
- If information is missing, ask for the missing items ONE at a time.
- If the customer provides ALL information at once, proceed directly.
- Once all info is collected:
  1. Use validate_age tool to confirm 18+.
  2. Use send_otp tool to send a verification code to their email.
  3. Ask the customer for the 6-digit code they received.
  4. Use verify_otp tool to verify the code.
  5. ONLY after successful OTP verification, use create_customer_account tool.
  6. Share the customer ID, account ID, and let them know a welcome email with login credentials has been sent.

## IMPORTANT:
- If the customer already provides a verification code in their message, use verify_otp FIRST (do NOT send a new OTP).
- NEVER create the account before email is verified via OTP.
- If age < 18, REJECT immediately.
- If OTP verification fails, let them request a new code.
- Be warm, professional, and concise.
- Do NOT make up information."""


def _get_ses_client():
    sm = boto3.client("secretsmanager", region_name="eu-west-1")
    creds = json.loads(sm.get_secret_value(SecretId=SES_SECRET_ARN)["SecretString"])
    return boto3.client("ses", region_name=creds["region"],
                        aws_access_key_id=creds["access_key_id"],
                        aws_secret_access_key=creds["secret_access_key"]), creds["sender"]


def _get_rds():
    return boto3.client("rds-data", region_name=COGNITO_REGION)


def _check_otp_exists(email):
    """Check if an OTP has been sent (pending or verified) for this email."""
    resp = _get_rds().execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
        sql="SELECT COUNT(*) FROM otp_codes WHERE user_identifier = :em AND otp_type = 'account_creation'",
        parameters=[{"name": "em", "value": {"stringValue": email}}])
    return int(resp["records"][0][0].get("longValue", 0)) > 0


def _check_otp_verified(email):
    """Check if email has a verified OTP record."""
    resp = _get_rds().execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
        sql="SELECT COUNT(*) FROM otp_codes WHERE user_identifier = :em AND otp_type = 'account_creation' AND verified = 1",
        parameters=[{"name": "em", "value": {"stringValue": email}}])
    return int(resp["records"][0][0].get("longValue", 0)) > 0


def _next_customer_id():
    resp = _get_rds().execute_statement(
        resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
        sql="SELECT MAX(customer_id) as max_id FROM customers"
    )
    row = resp["records"][0][0]
    if row.get("isNull"):
        return "CUST00000002"
    num = int(row["stringValue"].replace("CUST", "")) + 1
    return f"CUST{num:08d}"


@tool
def validate_age(date_of_birth: str) -> str:
    """Validate that the customer is 18 or older.
    Args:
        date_of_birth: Date of birth in YYYY-MM-DD format
    """
    try:
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        today = date.today()
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 18:
            return json.dumps({"valid": False, "age": age, "error": f"Customer is {age} years old. Minimum age is 18."})
        return json.dumps({"valid": True, "age": age})
    except ValueError:
        return json.dumps({"valid": False, "error": "Invalid date format. Use YYYY-MM-DD."})


@tool
def send_otp(email: str) -> str:
    """Send a 6-digit OTP verification code to the customer's email.
    Args:
        email: Customer email address
    """
    try:
        code = ''.join(random.choices(string.digits, k=6))
        otp_id = f"OTP-{datetime.now().strftime('%Y%m%d%H%M%S')}-{''.join(random.choices(string.ascii_uppercase, k=4))}"
        rds = _get_rds()
        # Clean up old codes for this email
        rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="DELETE FROM otp_codes WHERE user_identifier = :em AND otp_type = 'account_creation'",
            parameters=[{"name": "em", "value": {"stringValue": email}}])
        # Store new code (5 min expiry)
        rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="""INSERT INTO otp_codes (otp_id, user_identifier, otp_code, otp_type, delivery_method, expires_at, verified, verification_attempts, max_attempts, created_at)
                   VALUES (:oid, :em, :code, 'account_creation', 'email', DATE_ADD(NOW(), INTERVAL 5 MINUTE), 0, 0, 3, NOW())""",
            parameters=[
                {"name": "oid", "value": {"stringValue": otp_id}},
                {"name": "em", "value": {"stringValue": email}},
                {"name": "code", "value": {"stringValue": code}},
            ])
        # Send via SES
        ses, sender = _get_ses_client()
        ses.send_email(
            Source=f"AI Bank <{sender}>",
            Destination={"ToAddresses": [email]},
            Message={
                "Subject": {"Data": "AI Bank - Email Verification Code"},
                "Body": {"Html": {"Data": f"""
                    <div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:20px">
                        <h2 style="color:#1a365d">AI Bank Email Verification</h2>
                        <p>Your verification code is:</p>
                        <div style="background:#f0f4f8;padding:20px;text-align:center;border-radius:8px;margin:20px 0">
                            <span style="font-size:32px;font-weight:bold;letter-spacing:8px;color:#1a365d">{code}</span>
                        </div>
                        <p>This code expires in 5 minutes.</p>
                        <p style="color:#666;font-size:12px">If you didn't request this, please ignore this email.</p>
                    </div>"""}}
            }
        )
        return json.dumps({"success": True, "message": f"Verification code sent to {email}. It expires in 5 minutes."})
    except Exception as e:
        log.error(f"OTP send failed: {e}")
        return json.dumps({"success": False, "error": str(e)})


@tool
def verify_otp(email: str, code: str) -> str:
    """Verify the OTP code the customer received via email.
    Args:
        email: Customer email address
        code: The 6-digit verification code
    """
    try:
        # PRECONDITION: OTP must have been sent first
        if not _check_otp_exists(email):
            return json.dumps({"verified": False, "error": "PRECONDITION FAILED: No OTP has been sent to this email. Use send_otp first."})
        rds = _get_rds()
        resp = rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="SELECT otp_id, otp_code, verification_attempts, max_attempts FROM otp_codes WHERE user_identifier = :em AND otp_type = 'account_creation' AND expires_at > NOW() AND verified = 0 ORDER BY created_at DESC LIMIT 1",
            parameters=[{"name": "em", "value": {"stringValue": email}}])
        if not resp["records"]:
            return json.dumps({"verified": False, "error": "No valid OTP found. It may have expired. Please request a new code."})
        row = resp["records"][0]
        otp_id = row[0]["stringValue"]
        stored = row[1]["stringValue"]
        attempts = int(row[2].get("longValue", row[2].get("stringValue", 0)))
        max_att = int(row[3].get("longValue", row[3].get("stringValue", 3)))
        if attempts >= max_att:
            return json.dumps({"verified": False, "error": "Maximum attempts exceeded. Please request a new code."})
        # Increment attempts
        rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="UPDATE otp_codes SET verification_attempts = verification_attempts + 1 WHERE otp_id = :oid",
            parameters=[{"name": "oid", "value": {"stringValue": otp_id}}])
        if stored != code.strip():
            remaining = max_att - attempts - 1
            return json.dumps({"verified": False, "error": f"Invalid code. {remaining} attempt(s) remaining."})
        # Mark verified
        rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="UPDATE otp_codes SET verified = 1, verified_at = NOW() WHERE otp_id = :oid",
            parameters=[{"name": "oid", "value": {"stringValue": otp_id}}])
        return json.dumps({"verified": True, "message": "Email verified successfully!"})
    except Exception as e:
        return json.dumps({"verified": False, "error": str(e)})


@tool
def create_customer_account(first_name: str, last_name: str, email: str, phone_number: str,
                            date_of_birth: str, nationality: str, account_type: str) -> str:
    """Create a new customer account in Cognito and core banking. ONLY call this AFTER email is verified via OTP.
    Args:
        first_name: Customer first name
        last_name: Customer last name
        email: Customer email address (must be OTP-verified)
        phone_number: Phone with country code e.g. +97333001234
        date_of_birth: YYYY-MM-DD format
        nationality: Customer nationality
        account_type: savings, premium, or business
    """
    try:
        # PRECONDITION: Email must be OTP-verified before account creation
        if not _check_otp_verified(email):
            return json.dumps({"success": False, "error": "PRECONDITION FAILED: Email not verified. The customer must complete OTP verification (send_otp → verify_otp) before account creation."})

        # Normalize and validate phone number
        phone_number = _normalize_phone(phone_number)
        if not _validate_phone(phone_number):
            return json.dumps({"success": False, "error": f"Invalid phone number: {phone_number}. Must be a valid GCC number with country code (e.g. +97338175284, +966501234567, +971501234567)."})

        dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
        age = date.today().year - dob.year - ((date.today().month, date.today().day) < (dob.month, dob.day))
        if age < 18:
            return json.dumps({"success": False, "error": f"Customer is {age}. Minimum age is 18."})

        customer_id = _next_customer_id()
        temp_pw = f"AiBank@{datetime.now().strftime('%Y%m%d')}"
        cognito = boto3.client("cognito-idp", region_name=COGNITO_REGION)

        cognito.admin_create_user(
            UserPoolId=COGNITO_POOL_ID, Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
                {"Name": "given_name", "Value": first_name},
                {"Name": "family_name", "Value": last_name},
                {"Name": "phone_number", "Value": phone_number},
                {"Name": "birthdate", "Value": date_of_birth},
                {"Name": "custom:customer_id", "Value": customer_id},
            ],
            TemporaryPassword=temp_pw, MessageAction="SUPPRESS",
        )
        cognito.admin_set_user_password(
            UserPoolId=COGNITO_POOL_ID, Username=email, Password=temp_pw, Permanent=True,
        )
        user = cognito.admin_get_user(UserPoolId=COGNITO_POOL_ID, Username=email)
        cognito_sub = next(a["Value"] for a in user["UserAttributes"] if a["Name"] == "sub")

        rds = _get_rds()
        acct_type = account_type.lower()
        acct_prefix = "SAV" if acct_type == "savings" else "PRM" if acct_type == "premium" else "BUS"
        acct_num = customer_id.replace("CUST", "")
        account_id = f"ACC{acct_prefix}{acct_num}"

        rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="""INSERT INTO customers (customer_id, cognito_user_id, first_name, last_name, email, phone_number,
                    date_of_birth, nationality, credit_score, created_at)
                    VALUES (:cid, :cog, :fn, :ln, :em, :ph, :dob, :nat, 700, NOW())""",
            parameters=[
                {"name": "cid", "value": {"stringValue": customer_id}},
                {"name": "cog", "value": {"stringValue": cognito_sub}},
                {"name": "fn", "value": {"stringValue": first_name}},
                {"name": "ln", "value": {"stringValue": last_name}},
                {"name": "em", "value": {"stringValue": email}},
                {"name": "ph", "value": {"stringValue": phone_number}},
                {"name": "dob", "value": {"stringValue": date_of_birth}},
                {"name": "nat", "value": {"stringValue": nationality}},
            ])
        rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="""INSERT INTO accounts (account_id, customer_id, account_type, account_number, balance, currency, status, opening_date, created_at)
                    VALUES (:aid, :cid, :atype, :anum, 0.00, 'BHD', 'ACTIVE', CURDATE(), NOW())""",
            parameters=[
                {"name": "aid", "value": {"stringValue": account_id}},
                {"name": "cid", "value": {"stringValue": customer_id}},
                {"name": "atype", "value": {"stringValue": acct_type}},
                {"name": "anum", "value": {"stringValue": f"10{acct_num}"}},
            ])

        # Send welcome email
        try:
            ses, sender = _get_ses_client()
            ses.send_email(
                Source=f"AI Bank <{sender}>",
                Destination={"ToAddresses": [email]},
                Message={
                    "Subject": {"Data": "Welcome to AI Bank! 🎉"},
                    "Body": {"Html": {"Data": f"""
                        <div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:30px;background:#fff">
                            <div style="text-align:center;padding:20px;background:linear-gradient(135deg,#1a365d,#2563eb);border-radius:12px 12px 0 0">
                                <h1 style="color:#fff;margin:0">Welcome to AI Bank!</h1>
                            </div>
                            <div style="padding:30px;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 12px 12px">
                                <p>Dear {first_name} {last_name},</p>
                                <p>Congratulations! Your AI Bank account has been successfully created. Here are your details:</p>
                                <table style="width:100%;border-collapse:collapse;margin:20px 0">
                                    <tr style="background:#f0f4f8"><td style="padding:12px;font-weight:bold;border:1px solid #e2e8f0">Customer ID</td><td style="padding:12px;border:1px solid #e2e8f0">{customer_id}</td></tr>
                                    <tr><td style="padding:12px;font-weight:bold;border:1px solid #e2e8f0">Account ID</td><td style="padding:12px;border:1px solid #e2e8f0">{account_id}</td></tr>
                                    <tr style="background:#f0f4f8"><td style="padding:12px;font-weight:bold;border:1px solid #e2e8f0">Account Type</td><td style="padding:12px;border:1px solid #e2e8f0">{acct_type.title()}</td></tr>
                                    <tr><td style="padding:12px;font-weight:bold;border:1px solid #e2e8f0">Account Number</td><td style="padding:12px;border:1px solid #e2e8f0">10{acct_num}</td></tr>
                                </table>
                                <div style="background:#fef3c7;padding:15px;border-radius:8px;margin:20px 0;border-left:4px solid #f59e0b">
                                    <p style="margin:0;font-weight:bold">🔐 Your Temporary Login Credentials</p>
                                    <p style="margin:8px 0 0">Email: <strong>{email}</strong></p>
                                    <p style="margin:4px 0 0">Password: <strong>{temp_pw}</strong></p>
                                    <p style="margin:8px 0 0;font-size:12px;color:#92400e">Please change your password after your first login.</p>
                                </div>
                                <p>You can now log in to AI Bank Internet Banking to manage your account.</p>
                                <p style="color:#666;font-size:12px;margin-top:30px;border-top:1px solid #e2e8f0;padding-top:15px">
                                    This is an automated message from AI Bank. Please do not reply to this email.
                                </p>
                            </div>
                        </div>"""}}
                }
            )
        except Exception as e:
            log.error(f"Welcome email failed: {e}")

        return json.dumps({
            "success": True, "customer_id": customer_id, "account_id": account_id,
            "account_number": f"10{acct_num}", "temp_password": temp_pw,
            "message": f"Account created! Customer ID: {customer_id}, Account: {account_id}. A welcome email with login credentials has been sent to {email}."
        })
    except Exception as e:
        if "UsernameExistsException" in str(type(e).__name__):
            return json.dumps({"success": False, "error": "An account with this email already exists."})
        log.error(f"Account creation failed: {e}")
        return json.dumps({"success": False, "error": str(e)})


TOOLS = [validate_age, send_otp, verify_otp, create_customer_account]

strands_agent = Agent(
    name="Customer Onboarding Agent",
    description="Opens new bank accounts for AI Bank customers. Collects personal info, verifies email via OTP, and creates Cognito + core banking records.",
    model=BedrockModel(model_id="eu.amazon.nova-lite-v1:0", region_name=REGION),
    system_prompt=SYSTEM_PROMPT,
    tools=TOOLS,
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
