"""AI Bank — Auto Decision Processor for Instant Money loans.
Updates DynamoDB status and emails customer the outcome."""
import json, os, boto3, logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb', region_name='eu-west-1')
ses = boto3.client('ses', region_name='us-east-1')
rds_data = boto3.client('rds-data', region_name='me-south-1')

TABLE = 'aibank-personal-loan'
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'noreply@demoaws.com')
CLUSTER_ARN = os.environ.get('AURORA_CLUSTER_ARN', 'arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking')
SECRET_ARN = os.environ.get('AURORA_SECRET_ARN', 'arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ')


def lambda_handler(event, context):
    logger.info(f"auto_decision event: {json.dumps(event, default=str)[:800]}")

    customer_id = event.get('processingContext', {}).get('customer_id', 'UNKNOWN')
    application_id = event.get('processingContext', {}).get('application_id', 'UNKNOWN')
    decision = event.get('decision', 'REJECTED')
    reason = event.get('reason', '')
    amount = event.get('loan_data', {}).get('amount', 0)

    now = datetime.utcnow().isoformat()
    table = dynamodb.Table(TABLE)

    try:
        # Update DynamoDB
        table.update_item(
            Key={'customer_id': customer_id, 'application_id': application_id},
            UpdateExpression='SET #s = :s, updated_at = :t, auto_decided = :ad, decision_reason = :r',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={
                ':s': decision, ':t': now, ':ad': True, ':r': reason
            }
        )

        # Get customer email from Aurora
        email = _get_customer_email(customer_id)
        if email:
            _send_email(email, customer_id, application_id, decision, amount, reason)

        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'decision': decision,
            'email_sent': bool(email),
            'processingContext': event.get('processingContext', {})
        }
    except Exception as e:
        logger.error(f"auto_decision error: {e}")
        return {'statusCode': 500, 'error': str(e),
                'customer_id': customer_id, 'application_id': application_id,
                'processingContext': event.get('processingContext', {})}


def _get_customer_email(customer_id):
    try:
        resp = rds_data.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database='corebanking',
            sql='SELECT email FROM customers WHERE customer_id = :c',
            parameters=[{'name': 'c', 'value': {'stringValue': customer_id}}]
        )
        rows = resp.get('records', [])
        return rows[0][0].get('stringValue') if rows else None
    except Exception as e:
        logger.warning(f"Email lookup failed: {e}")
        return None


def _send_email(to_email, customer_id, app_id, decision, amount, reason):
    is_approved = decision == 'APPROVED'
    color = '#27ae60' if is_approved else '#e74c3c'
    icon = '✅' if is_approved else '❌'
    title = 'Loan Approved!' if is_approved else 'Loan Application Update'
    status_text = 'APPROVED' if is_approved else 'NOT APPROVED'
    body_text = (f"Your Instant Money application for BHD {amount} has been approved and will be disbursed shortly."
                 if is_approved else
                 f"After careful review, we're unable to approve your application at this time.\n\nReason: {reason}")

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:24px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
  <tr><td style="background:linear-gradient(135deg,#1a3a5c,#0d2137);padding:28px 32px;text-align:center">
    <h1 style="margin:0;color:#fff;font-size:22px">🏦 AI Bank</h1>
    <p style="margin:6px 0 0;color:#8bb8d9;font-size:13px">Instant Money</p>
  </td></tr>
  <tr><td style="padding:28px 32px;text-align:center">
    <div style="font-size:48px;margin-bottom:12px">{icon}</div>
    <h2 style="margin:0 0 8px;color:#1a3a5c;font-size:20px">{title}</h2>
    <p style="margin:0 0 24px;color:#666;font-size:14px">Application {app_id}</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fb;border-radius:6px;border:1px solid #e8ecf1;margin-bottom:20px;text-align:left">
      <tr>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;color:#666;font-size:13px">Amount</td>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;font-weight:600;font-size:14px">BHD {amount}</td>
      </tr>
      <tr>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;color:#666;font-size:13px">Decision</td>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1">
          <span style="background:{color};color:#fff;padding:3px 12px;border-radius:10px;font-size:12px;font-weight:600">{status_text}</span>
        </td>
      </tr>
    </table>
    <p style="color:#333;font-size:14px;line-height:1.6;text-align:left">{body_text}</p>
  </td></tr>
  <tr><td style="background:#f8f9fb;padding:16px 32px;text-align:center;border-top:1px solid #e8ecf1">
    <p style="margin:0;color:#aaa;font-size:11px">© 2026 AI Bank · aibank.demoaws.com</p>
  </td></tr>
</table></td></tr></table></body></html>"""

    ses.send_email(
        Source=FROM_EMAIL,
        Destination={'ToAddresses': [to_email]},
        Message={
            'Subject': {'Data': f"[AI Bank] {title} — {app_id}"},
            'Body': {'Text': {'Data': body_text}, 'Html': {'Data': html}}
        }
    )
    logger.info(f"Customer email sent to {to_email} for {app_id}: {decision}")
