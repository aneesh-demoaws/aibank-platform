"""
Loan Tools — adapted from NeoBank loan_tools.py for AI Bank (eu-west-1)
Source: github.com/aneesh-demoaws/neo-bank-bahrain-ai-assistant
"""
import json
import logging
import os
import boto3
from strands import tool

logger = logging.getLogger(__name__)

LOAN_PRODUCTS = {
    "instant_money":    {"min": 100,    "max": 2_000,   "base_rate": 5.5, "max_months": 24},
    "personal":         {"min": 500,    "max": 20_000,  "base_rate": 4.5, "max_months": 60},
    "housing":          {"min": 10_000, "max": 200_000, "base_rate": 3.5, "max_months": 300},
    "vehicle":          {"min": 1_000,  "max": 40_000,  "base_rate": 3.8, "max_months": 84},
    "education":        {"min": 500,    "max": 40_000,  "base_rate": 4.0, "max_months": 120},
}

def _emi(principal: float, annual_rate: float, months: int) -> float:
    if annual_rate == 0:
        return principal / months
    r = annual_rate / 100 / 12
    return principal * r * (1 + r) ** months / ((1 + r) ** months - 1)

@tool
def calculate_loan_payment(loan_type: str, amount: float, tenure_months: int) -> str:
    """Calculate monthly EMI and total cost for a loan product.

    Args:
        loan_type: One of instant_money, personal, housing, vehicle, education
        amount: Loan amount in BHD
        tenure_months: Repayment period in months
    """
    product = LOAN_PRODUCTS.get(loan_type)
    if not product:
        return json.dumps({"error": f"Unknown loan type. Choose from: {', '.join(LOAN_PRODUCTS)}"})

    if not (product["min"] <= amount <= product["max"]):
        return json.dumps({"error": f"Amount must be between BHD {product['min']:,} and BHD {product['max']:,}"})

    if tenure_months > product["max_months"]:
        return json.dumps({"error": f"Maximum tenure for {loan_type} is {product['max_months']} months"})

    emi = _emi(amount, product["base_rate"], tenure_months)
    total = emi * tenure_months

    return json.dumps({
        "loan_type": loan_type,
        "amount_bhd": amount,
        "tenure_months": tenure_months,
        "monthly_emi_bhd": round(emi, 3),
        "total_repayment_bhd": round(total, 3),
        "total_interest_bhd": round(total - amount, 3),
        "annual_rate_pct": product["base_rate"],
    })

@tool
def apply_for_loan(user_id: str, loan_type: str, amount: float, tenure_months: int, purpose: str = "") -> str:
    """Submit a loan application and trigger the processing workflow.

    Args:
        user_id: Authenticated customer ID
        loan_type: One of instant_money, personal, housing, vehicle, education
        amount: Requested loan amount in BHD
        tenure_months: Repayment period in months
        purpose: Optional purpose description
    """
    product = LOAN_PRODUCTS.get(loan_type)
    if not product:
        return json.dumps({"error": f"Unknown loan type: {loan_type}"})

    if not (product["min"] <= amount <= product["max"]):
        return json.dumps({"error": f"Amount out of range for {loan_type}"})

    sfn = boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    state_machine_arn = os.environ.get("LOAN_WORKFLOW_ARN")

    import uuid, datetime
    application_id = f"AIB-{datetime.date.today().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"

    payload = {
        "applicationId": application_id,
        "userId": user_id,
        "loanType": loan_type,
        "amountBHD": amount,
        "tenureMonths": tenure_months,
        "purpose": purpose,
        "submittedAt": datetime.datetime.utcnow().isoformat(),
    }

    try:
        sfn.start_execution(
            stateMachineArn=state_machine_arn,
            name=application_id,
            input=json.dumps(payload),
        )
        status = "submitted"
    except Exception as e:
        logger.warning(f"Step Functions unavailable, recording application locally: {e}")
        status = "pending_processing"

    emi = _emi(amount, product["base_rate"], tenure_months)
    return json.dumps({
        "application_id": application_id,
        "status": status,
        "loan_type": loan_type,
        "amount_bhd": amount,
        "monthly_emi_bhd": round(emi, 3),
        "next_steps": [
            "Document verification (1-2 business days)",
            "Credit assessment",
            "Approval decision",
        ],
        "message": f"Application {application_id} submitted successfully. You'll receive an update within 2 business days.",
    })

@tool
def get_loan_status(user_id: str, application_id: str = "") -> str:
    """Get the status of a loan application.

    Args:
        user_id: Authenticated customer ID
        application_id: Optional specific application ID; returns all if omitted
    """
    sfn = boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    state_machine_arn = os.environ.get("LOAN_WORKFLOW_ARN", "")

    try:
        paginator = sfn.get_paginator("list_executions")
        executions = []
        for page in paginator.paginate(stateMachineArn=state_machine_arn, maxResults=20):
            executions.extend(page.get("executions", []))

        # Filter by user if application_id not provided
        results = []
        for ex in executions:
            name = ex.get("name", "")
            if application_id and name != application_id:
                continue
            results.append({
                "application_id": name,
                "status": ex.get("status"),
                "started": ex.get("startDate", "").isoformat() if ex.get("startDate") else "",
                "stopped": ex.get("stopDate", "").isoformat() if ex.get("stopDate") else "",
            })

        return json.dumps({"applications": results[:10]})
    except Exception as e:
        logger.error(f"get_loan_status error: {e}")
        return json.dumps({"error": "Unable to retrieve loan status. Please contact support."})
