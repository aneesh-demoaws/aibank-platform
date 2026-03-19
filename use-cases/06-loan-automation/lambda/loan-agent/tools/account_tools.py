"""
Account Tools — adapted from NeoBank account_tools.py for AI Bank (eu-west-1)
"""
import json
import logging
import os
import boto3
from strands import tool

logger = logging.getLogger(__name__)

def _rds_execute(sql: str, params: list = None):
    rds = boto3.client("rds-data", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
    kwargs = {
        "resourceArn": os.environ["AURORA_CLUSTER_ARN"],
        "secretArn": os.environ["AURORA_SECRET_ARN"],
        "database": os.environ.get("DB_NAME", "aibank"),
        "sql": sql,
    }
    if params:
        kwargs["parameters"] = params
    return rds.execute_statement(**kwargs)

@tool
def get_account_balance(user_id: str, account_type: str = "all") -> str:
    """Get account balance(s) for a customer.

    Args:
        user_id: Authenticated customer ID
        account_type: Filter by account type (current, savings, all)
    """
    try:
        where = "WHERE customer_id = :uid"
        params = [{"name": "uid", "value": {"stringValue": user_id}}]
        if account_type != "all":
            where += " AND account_type = :atype"
            params.append({"name": "atype", "value": {"stringValue": account_type}})

        result = _rds_execute(
            f"SELECT account_number, account_type, balance, currency FROM accounts {where}",
            params,
        )
        accounts = [
            {
                "account_number": r[0]["stringValue"],
                "type": r[1]["stringValue"],
                "balance": float(r[2]["stringValue"]),
                "currency": r[3]["stringValue"],
            }
            for r in result.get("records", [])
        ]
        return json.dumps({"accounts": accounts})
    except Exception as e:
        logger.error(f"get_account_balance error: {e}")
        return json.dumps({"error": "Unable to retrieve balance."})

@tool
def get_account_details(user_id: str) -> str:
    """Get full account details for a customer.

    Args:
        user_id: Authenticated customer ID
    """
    try:
        result = _rds_execute(
            "SELECT account_number, account_type, balance, currency, opened_date, status FROM accounts WHERE customer_id = :uid",
            [{"name": "uid", "value": {"stringValue": user_id}}],
        )
        accounts = [
            {
                "account_number": r[0]["stringValue"],
                "type": r[1]["stringValue"],
                "balance": float(r[2]["stringValue"]),
                "currency": r[3]["stringValue"],
                "opened_date": r[4]["stringValue"],
                "status": r[5]["stringValue"],
            }
            for r in result.get("records", [])
        ]
        return json.dumps({"accounts": accounts})
    except Exception as e:
        logger.error(f"get_account_details error: {e}")
        return json.dumps({"error": "Unable to retrieve account details."})
