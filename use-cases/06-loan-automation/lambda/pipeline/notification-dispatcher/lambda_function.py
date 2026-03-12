import json, os, boto3, logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb', region_name='eu-west-1')
ses = boto3.client('ses', region_name='us-east-1')

TABLE = 'aibank-personal-loan'
LOAN_OFFICER_EMAIL = os.environ.get('LOAN_OFFICER_EMAIL', 'loanofficer@demoaws.com')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'noreply@demoaws.com')

def lambda_handler(event, context):
    logger.info(f"notification_dispatcher event: {json.dumps(event, default=str)[:1000]}")

    customer_id = (event.get('processingContext', {}).get('customer_id')
                   or event.get('customer_id', 'UNKNOWN'))
    application_id = (event.get('processingContext', {}).get('application_id')
                      or event.get('application_id', 'UNKNOWN'))

    forced_review = event.get('forcedManualReview', {})
    new_status = 'PENDING_REVIEW'

    try:
        table = dynamodb.Table(TABLE)
        table.update_item(
            Key={'customer_id': customer_id, 'application_id': application_id},
            UpdateExpression='SET #s = :s, updated_at = :t',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={':s': new_status, ':t': datetime.utcnow().isoformat()}
        )
        logger.info(f"Updated {application_id} status to {new_status}")

        amount = event.get('loan_data', {}).get('amount') or \
                 event.get('originalApplicationData', {}).get('amount', 'N/A')
        employer = event.get('customer_data', {}).get('employer_name') or \
                   event.get('originalApplicationData', {}).get('employer_name', 'N/A')
        reason = forced_review.get('reason', 'Manual review required')
        review_url = f"https://aibank.demoaws.com/employee/credit/application-review.html?id={application_id}"
        now = datetime.utcnow().strftime('%d %b %Y, %H:%M UTC')

        subject = f"[AI Bank] Loan Review Required — {application_id}"

        html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:24px 0">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">

  <tr><td style="background:linear-gradient(135deg,#1a3a5c,#0d2137);padding:28px 32px;text-align:center">
    <h1 style="margin:0;color:#fff;font-size:22px;font-weight:600">🏦 AI Bank</h1>
    <p style="margin:6px 0 0;color:#8bb8d9;font-size:13px">Loan Processing &amp; Review Platform</p>
  </td></tr>

  <tr><td style="padding:28px 32px">
    <h2 style="margin:0 0 4px;color:#1a3a5c;font-size:18px">Loan Application Requires Review</h2>
    <p style="margin:0 0 20px;color:#666;font-size:13px">{now}</p>

    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fb;border-radius:6px;border:1px solid #e8ecf1;margin-bottom:20px">
      <tr>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;width:160px;color:#666;font-size:13px">Application ID</td>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;font-weight:600;color:#1a3a5c;font-size:14px">{application_id}</td>
      </tr>
      <tr>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;color:#666;font-size:13px">Customer ID</td>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;font-size:14px;color:#333">{customer_id}</td>
      </tr>
      <tr>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;color:#666;font-size:13px">Loan Amount</td>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;font-weight:600;font-size:14px;color:#333">BHD {amount}</td>
      </tr>
      <tr>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;color:#666;font-size:13px">Employer</td>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;font-size:14px;color:#333">{employer}</td>
      </tr>
      <tr>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1;color:#666;font-size:13px">Status</td>
        <td style="padding:14px 18px;border-bottom:1px solid #e8ecf1">
          <span style="background:#fff3cd;color:#856404;padding:3px 10px;border-radius:10px;font-size:12px;font-weight:600">PENDING REVIEW</span>
        </td>
      </tr>
      <tr>
        <td style="padding:14px 18px;color:#666;font-size:13px">Review Reason</td>
        <td style="padding:14px 18px;font-size:14px;color:#333">{reason}</td>
      </tr>
    </table>

    <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:8px 0 20px">
      <a href="{review_url}" style="display:inline-block;background:#1a3a5c;color:#fff;text-decoration:none;padding:12px 32px;border-radius:6px;font-size:14px;font-weight:600">Review Application →</a>
    </td></tr></table>

    <p style="margin:0;color:#999;font-size:12px;text-align:center">This is an automated notification from AI Bank's loan processing system.<br>Please do not reply to this email.</p>
  </td></tr>

  <tr><td style="background:#f8f9fb;padding:16px 32px;text-align:center;border-top:1px solid #e8ecf1">
    <p style="margin:0;color:#aaa;font-size:11px">© 2026 AI Bank · aibank.demoaws.com</p>
  </td></tr>

</table>
</td></tr></table>
</body></html>"""

        text = (f"Loan Application Requires Review\n\n"
                f"Application ID: {application_id}\nCustomer: {customer_id}\n"
                f"Amount: BHD {amount}\nEmployer: {employer}\n"
                f"Status: {new_status}\nReason: {reason}\n\n"
                f"Review at: {review_url}")

        ses.send_email(
            Source=FROM_EMAIL,
            Destination={'ToAddresses': [LOAN_OFFICER_EMAIL]},
            Message={
                'Subject': {'Data': subject},
                'Body': {'Text': {'Data': text}, 'Html': {'Data': html}}
            }
        )
        logger.info(f"Email sent to {LOAN_OFFICER_EMAIL} for {application_id}")

    except Exception as e:
        logger.error(f"notification_dispatcher error: {str(e)}")
        return {
            'statusCode': 500, 'error': str(e),
            'customer_id': customer_id, 'application_id': application_id,
            'processingContext': event.get('processingContext', {})
        }

    return {
        'statusCode': 200,
        'customer_id': customer_id, 'application_id': application_id,
        'status_updated': new_status, 'notification_sent': True,
        'processingContext': event.get('processingContext', {})
    }
