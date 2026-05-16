"""Loan Terms Calculator Lambda.

Runs after the officer approves a personal loan (SFN ApprovalPath).
Computes the authoritative loan terms (rate, EMI, total interest, total
repayment), persists them to the loan record, and returns a structured
`terms_summary` + human-readable `loan_agreement` for downstream use by
SendApprovalNotification.

All numbers are sourced from the DDB loan record — never from the SFN
payload — so this Lambda cannot be fooled into generating wrong terms
by stale or mis-shaped input.
"""
import json
import logging
import boto3
from decimal import Decimal
from datetime import datetime, date

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb", region_name="eu-west-1")
TABLE = "aibank-personal-loan"

# Fallback product rates (matches aibank-loan-config 'product' rows)
_PRODUCT_RATES = {"instant_money": 7.0, "personal": 5.5}


def _num(v, default=0.0):
    try:
        if isinstance(v, Decimal):
            return float(v)
        if v is None or (isinstance(v, str) and not v.strip()):
            return default
        return float(v)
    except Exception:
        return default


def _compute_emi(amount, tenure_months, annual_rate_pct):
    P = _num(amount)
    n = int(_num(tenure_months))
    r = _num(annual_rate_pct) / 100.0 / 12.0
    if P <= 0 or n <= 0:
        return 0.0
    if r == 0:
        return P / n
    return P * r * (1 + r) ** n / ((1 + r) ** n - 1)


def _load_loan(customer_id, application_id):
    resp = dynamodb.Table(TABLE).get_item(
        Key={"customer_id": customer_id, "application_id": application_id},
        ConsistentRead=True,
    )
    return resp.get("Item") or {}


def _save_terms(customer_id, application_id, terms):
    """Persist generated terms to DDB so portals can read them."""
    try:
        dynamodb.Table(TABLE).update_item(
            Key={"customer_id": customer_id, "application_id": application_id},
            UpdateExpression="SET loan_terms = :t, terms_generated_at = :now",
            ExpressionAttributeValues={
                ":t": {k: (Decimal(str(v)) if isinstance(v, float) else v) for k, v in terms.items()},
                ":now": datetime.utcnow().isoformat(),
            },
        )
    except Exception as e:
        logger.warning(f"Failed to persist terms: {e}")


def _build_agreement(customer_name, terms):
    today = date.today().strftime("%d %B %Y")
    return (
        f"LOAN AGREEMENT — AI Bank\n"
        f"Application: {terms['application_id']}\n"
        f"Date: {today}\n\n"
        f"Borrower: {customer_name or terms['customer_id']}\n"
        f"Approved Loan Amount: BHD {terms['approved_amount']:,.3f}\n"
        f"Tenure: {terms['tenure_months']} months\n"
        f"Interest Rate: {terms['annual_rate_pct']:.2f}% p.a.\n"
        f"Monthly EMI: BHD {terms['monthly_emi']:,.3f}\n"
        f"Total Interest: BHD {terms['total_interest']:,.3f}\n"
        f"Total Repayment: BHD {terms['total_repayment']:,.3f}\n"
        f"First EMI due: 1 month from disbursement.\n\n"
        f"Early settlement penalty: {terms.get('early_settlement_penalty_pct', 2.0):.1f}% of "
        f"outstanding principal.\n"
        f"Processing fee: BHD {terms.get('processing_fee', 50):,.3f}.\n"
    )


def lambda_handler(event, context):
    logger.info(f"loan_terms_calculator event: {json.dumps(event, default=str)[:800]}")
    try:
        customer_id = (event.get("customer_id")
                       or event.get("processingContext", {}).get("customer_id"))
        application_id = (event.get("application_id")
                          or event.get("processingContext", {}).get("application_id"))
        customer_name = event.get("customer_name") or ""

        if not customer_id or not application_id:
            raise ValueError("customer_id and application_id are required")

        item = _load_loan(customer_id, application_id)
        if not item:
            raise ValueError(f"Loan record {application_id} not found")

        # Authoritative source: the DDB record
        loan_type = (item.get("loan_type") or "").lower()
        amount = _num(item.get("amount"))
        tenure_months = int(_num(item.get("tenure_months") or item.get("duration")))
        rate_pct = _PRODUCT_RATES.get(loan_type, 5.5)

        if amount <= 0 or tenure_months <= 0:
            raise ValueError(f"Invalid loan amount/tenure: amount={amount} tenure={tenure_months}")

        emi = _compute_emi(amount, tenure_months, rate_pct)
        total_repayment = emi * tenure_months
        total_interest = total_repayment - amount

        # Read segment-specific pricing if the segment_config step wrote it
        seg_config = (item.get("customer_profile") or {}).get("segment_configuration") or {}
        pricing = seg_config.get("pricing_parameters") or {}

        terms_summary = {
            "application_id": application_id,
            "customer_id": customer_id,
            "loan_type": loan_type,
            "approved_amount": round(amount, 3),
            "tenure_months": tenure_months,
            "annual_rate_pct": rate_pct,
            "monthly_emi": round(emi, 3),
            "total_interest": round(total_interest, 3),
            "total_repayment": round(total_repayment, 3),
            "processing_fee": _num(pricing.get("processing_fee"), 50.0),
            "early_settlement_penalty_pct": _num(pricing.get("early_settlement_penalty"), 2.0),
            "generated_at": datetime.utcnow().isoformat(),
            "currency": "BHD",
        }

        loan_agreement = _build_agreement(customer_name, terms_summary)

        _save_terms(customer_id, application_id, terms_summary)

        logger.info(
            f"Terms generated for {application_id}: amount=BHD {amount:,.3f} "
            f"tenure={tenure_months} rate={rate_pct}% EMI=BHD {emi:,.3f}"
        )

        return {
            "statusCode": 200,
            "customer_id": customer_id,
            "application_id": application_id,
            "terms_summary": terms_summary,
            "loan_agreement": loan_agreement,
            "processingContext": event.get("processingContext", {}),
            "executionContext": event.get("executionContext", {}),
        }

    except Exception as e:
        logger.error(f"loan_terms_calculator failed: {e}", exc_info=True)
        return {
            "statusCode": 500,
            "error": str(e),
            "customer_id": event.get("customer_id"),
            "application_id": event.get("application_id"),
            "processingContext": event.get("processingContext", {}),
        }
