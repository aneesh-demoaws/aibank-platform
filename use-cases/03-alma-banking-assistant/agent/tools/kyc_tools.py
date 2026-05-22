"""KYC tools — status check and upload URL generation."""
import json
from strands import tool
from config import dynamodb, lambda_client, KYC_TABLE, KYC_PRESIGNED_URL_LAMBDA


@tool
def check_kyc_status(customer_id: str) -> str:
    """Check the current KYC verification status for a customer.

    Args:
        customer_id: The authenticated customer's ID (e.g. CUST00000001).
    """
    try:
        table = dynamodb.Table(KYC_TABLE)
        resp = table.get_item(Key={"customer_id": customer_id})
        item = resp.get("Item")
        if not item:
            return json.dumps({"status": "NOT_STARTED", "message": "No KYC documents submitted yet.",
                               "identity_docs_needed": 2, "address_docs_needed": 1}, default=str)
        return json.dumps({"status": item.get("kyc_status", "PENDING"),
                           "identity_docs_collected": int(item.get("total_id_collected_no", 0)),
                           "identity_docs_verified": int(item.get("total_id_verified_no", 0)),
                           "address_docs_collected": int(item.get("total_address_collected_no", 0)),
                           "address_docs_verified": int(item.get("total_address_verified_no", 0)),
                           "full_name": item.get("full_name", ""),
                           "nationality": item.get("nationality", ""),
                           "verification_details": item.get("verification_details"),
                           "last_updated": item.get("last_updated", "")}, default=str)
    except Exception as e:
        return f"Error checking KYC status: {str(e)}"


@tool
def generate_kyc_upload_url(customer_id: str, document_type: str) -> str:
    """Generate a presigned URL for the customer to upload a KYC document.

    Args:
        customer_id: The authenticated customer's ID.
        document_type: Either "identity" or "address".
    """
    try:
        resp = lambda_client.invoke(FunctionName=KYC_PRESIGNED_URL_LAMBDA,
            Payload=json.dumps({"body": json.dumps({"customer_id": customer_id,
                "documentType": document_type, "fileName": "document.pdf", "fileSize": 5000000})}))
        result = json.loads(resp["Payload"].read())
        body = json.loads(result.get("body", "{}"))
        if result.get("statusCode") != 200:
            return f"Error: {body.get('error', 'Unknown error')}"
        return json.dumps({"uploadUrl": body["uploadUrl"], "key": body["key"],
                           "documentType": body["documentType"], "fileId": body["fileId"],
                           "expiresIn": body["expiresIn"]})
    except Exception as e:
        return f"Error generating upload URL: {str(e)}"
