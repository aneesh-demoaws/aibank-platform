"""Loan tools — eligibility, calculation, upload, submission."""
import json, uuid, datetime
from decimal import Decimal
from strands import tool
from botocore.config import Config as BotoConfig
import boto3
from config import rds, dynamodb, s3, CLUSTER_ARN, SECRET_ARN, DB_NAME, LOAN_TABLE, UPLOAD_BUCKET, KYC_TABLE, REGION

# Load loan product config at import time
_ddb = dynamodb
_config_table = _ddb.Table("aibank-loan-config")

def _load_products():
    from boto3.dynamodb.conditions import Key
    resp = _config_table.query(KeyConditionExpression=Key("config_type").eq("product"))
    products = {}
    for item in resp.get("Items", []):
        products[item["config_id"]] = {
            "min": int(item["min_amount"]), "max": int(item["max_amount"]),
            "min_tenure": int(item["min_tenure"]), "max_tenure": int(item["max_tenure"]),
            "rate": float(item["rate"]), "salary_mult": int(item["salary_multiplier"]),
            "auto": bool(item.get("auto_decision", False))
        }
    return products

try:
    PRODUCTS = _load_products()
except:
    PRODUCTS = {"instant_money": {"min": 100, "max": 2000, "min_tenure": 1, "max_tenure": 12, "rate": 7.0, "salary_mult": 2, "auto": True},
                "personal": {"min": 1000, "max": 25000, "min_tenure": 6, "max_tenure": 60, "rate": 5.5, "salary_mult": 20, "auto": False}}


@tool
def check_loan_application_status(customer_id: str, application_id: str = "") -> str:
    """Look up the status of a customer's loan application(s).

    Returns the current status, key details, and five-Cs workflow execution state
    if available. Queries DynamoDB (authoritative) and enriches with a summary
    suitable for speaking back to the customer.

    Args:
        customer_id: The authenticated customer's ID (e.g. CUST20250100). Required.
        application_id: Optional specific application ID (e.g. AIB-20260511-7641F1).
                        If omitted, returns the customer's most-recent application.
    """
    import re
    if not re.match(r"^CUST\d{6,10}$", customer_id or ""):
        return json.dumps({"success": False, "error": "Invalid customer_id."})

    table = _ddb.Table(LOAN_TABLE)

    try:
        if application_id:
            resp = table.get_item(Key={"customer_id": customer_id, "application_id": application_id})
            item = resp.get("Item")
            if not item:
                return json.dumps({
                    "success": False,
                    "error": f"No application {application_id} found for this customer.",
                })
            items = [item]
        else:
            from boto3.dynamodb.conditions import Key
            resp = table.query(
                KeyConditionExpression=Key("customer_id").eq(customer_id),
                ScanIndexForward=False,  # newest first by sort key
                Limit=5,
            )
            items = resp.get("Items", [])
            if not items:
                return json.dumps({
                    "success": False,
                    "error": "No loan applications found for this customer.",
                })
    except Exception as e:
        return json.dumps({"success": False, "error": f"Lookup failed: {str(e)[:200]}"})

    def _summarise(it):
        # Strip out large/sensitive blobs — return only what's safe to speak back.
        return {
            "application_id": it.get("application_id"),
            "status": it.get("status", "UNKNOWN"),
            "loan_type": it.get("loan_type"),
            "amount_bhd": float(it.get("amount", 0)) if it.get("amount") is not None else None,
            "tenure_months": int(it.get("tenure_months", 0)) if it.get("tenure_months") is not None else None,
            "purpose": it.get("purpose"),
            "submitted_at": it.get("submitted_at"),
            "updated_at": it.get("updated_at"),
            "channel": it.get("channel"),
            "documents": it.get("documents"),
            "customer_segment": it.get("customer_segment"),
            "five_cs_execution_arn": it.get("five_cs_execution_arn"),
            "five_cs_triggered_at": it.get("five_cs_triggered_at"),
        }

    if application_id:
        return json.dumps({"success": True, "application": _summarise(items[0])}, default=str)

    return json.dumps({
        "success": True,
        "count": len(items),
        "applications": [_summarise(i) for i in items],
    }, default=str)


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

    # Check KYC
    kyc = _ddb.Table(KYC_TABLE).get_item(Key={"customer_id": customer_id}).get("Item")
    kyc_status = kyc.get("kyc_status", "PENDING") if kyc else "NOT_STARTED"
    if kyc_status != "VERIFIED":
        return json.dumps({"eligible": False, "reason": f"KYC status is {kyc_status}. Must be VERIFIED first."})

    # Check salary
    try:
        resp = rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="SELECT AVG(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
                "WHERE a.customer_id=:cid AND t.transaction_type='credit' AND t.category_id='CAT014' "
                "AND t.transaction_date>=DATE_SUB(CURDATE(),INTERVAL 3 MONTH)",
            parameters=[{"name": "cid", "value": {"stringValue": customer_id}}])
        rec = resp["records"][0][0]
        avg_salary = float(rec.get("doubleValue", rec.get("stringValue", 0))) if not rec.get("isNull") else 0
    except:
        avg_salary = 0

    if avg_salary == 0:
        return json.dumps({"eligible": False, "reason": "No salary credits found in the last 3 months."})

    max_loan = avg_salary * p["salary_mult"]
    if amount > max_loan:
        return json.dumps({"eligible": False, "reason": f"Max eligible is BHD {max_loan:.3f} based on salary."})

    return json.dumps({"eligible": True, "avg_monthly_salary": round(avg_salary, 3),
                       "max_eligible_amount": round(max_loan, 3), "loan_type": loan_type, "auto_decision": p["auto"]})


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
                       "total_interest": round(total - amount, 3), "annual_rate": p["rate"]})


@tool
def generate_loan_upload_url(customer_id: str, application_id: str, document_type: str,
                             loan_type: str = "", amount: float = 0, tenure_months: int = 0, purpose: str = "") -> str:
    """Generate a presigned S3 upload URL for a loan document.

    Args:
        customer_id: Customer ID
        application_id: Use 'pending' if not yet created.
        document_type: Either 'salary_certificate' or 'bank_statement'
        loan_type: 'instant_money' or 'personal' (required on first call)
        amount: Loan amount in BHD (required on first call)
        tenure_months: Tenure in months (required on first call)
        purpose: Purpose of the loan (required on first call)
    """
    if application_id == "pending":
        application_id = f"AIB-{datetime.date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    # Create DynamoDB record on first call
    if document_type == "salary_certificate" and loan_type and amount > 0:
        try:
            _ddb.Table(LOAN_TABLE).update_item(
                Key={"customer_id": customer_id, "application_id": application_id},
                # `duration` is a DynamoDB reserved keyword — alias it via #dur.
                # Writing both `tenure_months` (canonical) AND `duration` (legacy
                # alias consumed by the loan-processing pipeline's stream mapper).
                UpdateExpression=(
                    "SET loan_type=:lt, amount=:a, tenure_months=:tm, #dur=:tm, "
                    "purpose=:p, #s=:st, submitted_at=:sa, channel=:ch"
                ),
                ExpressionAttributeNames={"#s": "status", "#dur": "duration"},
                ExpressionAttributeValues={":lt": loan_type, ":a": Decimal(str(amount)),
                    ":tm": tenure_months, ":p": purpose or "General", ":st": "SUBMITTED",
                    ":sa": datetime.datetime.utcnow().isoformat(), ":ch": "alma_assistant"})
        except Exception as e:
            # Don't swallow silently — surface upstream so we don't fly blind again.
            import logging
            logging.getLogger(__name__).error(f"generate_loan_upload_url DDB write failed: {e}")

    folder = document_type
    filename = document_type.replace("_", "-") + ".pdf"
    key = f"documents/input/{customer_id}/{application_id}/{folder}/{filename}"
    try:
        url = s3.generate_presigned_url("put_object",
            Params={"Bucket": UPLOAD_BUCKET, "Key": key, "ContentType": "application/pdf"}, ExpiresIn=900)
        # Match the shape of generate_kyc_upload_url so the frontend/widget that
        # renders KYC uploads can render loan uploads the same way.
        # NOTE: the agent must NEVER paste `uploadUrl` into the chat. The frontend
        # widget triggers off the `[UPLOAD_REQUEST:<document_type>]` marker and
        # handles upload UX itself. The URL is kept here for backend traceability.
        return json.dumps({
            "success": True,
            "uploadUrl": url,
            "key": key,
            "documentType": document_type,
            "application_id": application_id,
            "expiresIn": 900,
            "message": (
                f"Upload widget ready. Reply with the marker "
                f"[UPLOAD_REQUEST:{document_type}] and a short instruction — "
                f"do NOT paste the raw URL in the visible reply."
            ),
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


@tool
def submit_loan_application(customer_id: str, application_id: str, loan_type: str, amount: float, tenure_months: int, purpose: str) -> str:
    """Submit the loan application after documents are uploaded.

    Args:
        customer_id: Customer ID
        application_id: Application ID from upload step
        loan_type: 'instant_money' or 'personal'
        amount: Loan amount in BHD
        tenure_months: Tenure in months
        purpose: Purpose of the loan
    """
    try:
        _ddb.Table(LOAN_TABLE).update_item(
            Key={"customer_id": customer_id, "application_id": application_id},
            # `duration` is a DynamoDB reserved keyword — alias it via #dur.
            # Writing both `tenure_months` (canonical) AND `duration` (legacy
            # alias consumed by the loan-processing pipeline's stream mapper).
            UpdateExpression=(
                "SET loan_type=:lt, amount=:a, tenure_months=:tm, #dur=:tm, "
                "purpose=:p, documents=:doc, #s=:st, submitted_at=:sa"
            ),
            ExpressionAttributeNames={"#s": "status", "#dur": "duration"},
            ExpressionAttributeValues={":lt": loan_type, ":a": Decimal(str(amount)), ":tm": tenure_months,
                ":p": purpose, ":doc": {"salary_certificate": "uploaded", "bank_statement": "uploaded"},
                ":st": "SUBMITTED", ":sa": datetime.datetime.utcnow().isoformat()})

        # Sync to Aurora
        p = PRODUCTS.get(loan_type, {})
        r_rate = p.get("rate", 7.0) / 100 / 12
        emi = amount * r_rate * (1 + r_rate) ** tenure_months / ((1 + r_rate) ** tenure_months - 1) if r_rate > 0 else amount / tenure_months
        try:
            rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
                sql="INSERT INTO loan_applications (application_id, customer_id, loan_type, amount, status, monthly_payment, duration, interest, purpose) "
                    "VALUES (:aid,:cid,:lt,:amt,'submitted',:emi,:dur,:rate,:purp) ON DUPLICATE KEY UPDATE status='submitted'",
                parameters=[{"name":"aid","value":{"stringValue":application_id}},{"name":"cid","value":{"stringValue":customer_id}},
                    {"name":"lt","value":{"stringValue":loan_type}},{"name":"amt","value":{"doubleValue":amount}},
                    {"name":"emi","value":{"doubleValue":round(emi,2)}},{"name":"dur","value":{"longValue":tenure_months}},
                    {"name":"rate","value":{"doubleValue":p.get("rate",7.0)}},{"name":"purp","value":{"stringValue":purpose}}])
        except:
            pass
        return json.dumps({"success": True, "application_id": application_id, "status": "SUBMITTED", "auto_decision": p.get("auto", False)})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
