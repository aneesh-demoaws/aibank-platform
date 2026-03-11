"""
Auth Tools — adapted from NeoBank authentication_tools.py for AI Bank (eu-west-1)
"""
import json
import logging
import os
import boto3
from strands import tool

logger = logging.getLogger(__name__)

@tool
def authenticate_user(user_id: str) -> str:
    """Authenticate a user and build their banking context.

    Args:
        user_id: Cognito sub / customer ID
    """
    try:
        cognito = boto3.client("cognito-idp", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        resp = cognito.admin_get_user(
            UserPoolId=os.environ["COGNITO_USER_POOL_ID"],
            Username=user_id,
        )
        attrs = {a["Name"]: a["Value"] for a in resp.get("UserAttributes", [])}
        return json.dumps({
            "authenticated": True,
            "user_id": user_id,
            "name": attrs.get("name", ""),
            "email": attrs.get("email", ""),
            "kyc_status": attrs.get("custom:kyc_status", "pending"),
        })
    except Exception as e:
        logger.error(f"authenticate_user error: {e}")
        return json.dumps({"authenticated": False, "error": "Authentication failed."})

@tool
def get_user_profile(user_id: str) -> str:
    """Get full user profile including KYC and loan summary.

    Args:
        user_id: Authenticated customer ID
    """
    try:
        cognito = boto3.client("cognito-idp", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
        resp = cognito.admin_get_user(
            UserPoolId=os.environ["COGNITO_USER_POOL_ID"],
            Username=user_id,
        )
        attrs = {a["Name"]: a["Value"] for a in resp.get("UserAttributes", [])}
        return json.dumps({
            "user_id": user_id,
            "name": attrs.get("name", ""),
            "email": attrs.get("email", ""),
            "phone": attrs.get("phone_number", ""),
            "kyc_status": attrs.get("custom:kyc_status", "pending"),
            "member_since": attrs.get("custom:member_since", ""),
        })
    except Exception as e:
        logger.error(f"get_user_profile error: {e}")
        return json.dumps({"error": "Unable to retrieve profile."})
