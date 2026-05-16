"""Notification dispatcher Lambda.

Two flows:

  1. AUTO-DECISION (instant money, after underwriting agent):
       event.auto_decision == True, event.decision in {APPROVE, REJECT, ...}
     - Writes AUTO_APPROVED / AUTO_REJECTED to DDB status.
     - Sends CUSTOMER a final decision email (via cross-account SES in ct-prod).

  2. MANUAL REVIEW (personal loans, forced review):
       event.auto_decision missing/False, event.task_token present
     - Writes PENDING_REVIEW to DDB.
     - Persists review_task_token + review_trigger_at on the loan record so the
       officer portal's /decisions handler can call SendTaskSuccess to unblock
       the waiting SFN (Manual_Review_Initiated waitForTaskToken state).
     - Emails the loan officer with a review link.
"""
import json, os, boto3, logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb', region_name='eu-west-1')
sts = boto3.client('sts')

TABLE = 'aibank-personal-loan'
CUSTOMERS_TABLE = os.environ.get('CUSTOMERS_TABLE', 'aibank-customers')
LOAN_OFFICER_EMAIL = os.environ.get('LOAN_OFFICER_EMAIL', 'loanofficer@demoaws.com')
FROM_EMAIL = os.environ.get('FROM_EMAIL', 'noreply@demoaws.com')
SES_ROLE_ARN = os.environ.get('SES_ROLE_ARN', 'arn:aws:iam::225872788412:role/aibank-demo-ses-sender')
SES_REGION = os.environ.get('SES_REGION', 'eu-west-1')

# Aurora lookup for customer email
CLUSTER_ARN = os.environ.get('AURORA_CLUSTER_ARN', 'arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr')
AURORA_SECRET_ARN = os.environ.get('AURORA_SECRET_ARN', 'arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6')
rds = boto3.client('rds-data', region_name='eu-west-1')


def _ses_client():
    """Assume the ct-prod SES sender role and return a configured SES client."""
    creds = sts.assume_role(
        RoleArn=SES_ROLE_ARN,
        RoleSessionName='notification-dispatcher',
        DurationSeconds=900,
    )['Credentials']
    return boto3.client(
        'ses', region_name=SES_REGION,
        aws_access_key_id=creds['AccessKeyId'],
        aws_secret_access_key=creds['SecretAccessKey'],
        aws_session_token=creds['SessionToken'],
    )


def _lookup_customer_email(customer_id):
    try:
        resp = rds.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=AURORA_SECRET_ARN,
            database='corebanking',
            sql="SELECT email, first_name, last_name FROM customers WHERE customer_id = :c LIMIT 1",
            parameters=[{"name": "c", "value": {"stringValue": customer_id}}])
        for rec in resp.get('records', []):
            email = rec[0].get('stringValue')
            first = rec[1].get('stringValue', '')
            last = rec[2].get('stringValue', '')
            return email, f"{first} {last}".strip() or customer_id
    except Exception as e:
        logger.warning(f"customer email lookup failed for {customer_id}: {e}")
    return None, customer_id


def _lookup_loan_details(customer_id, application_id):
    """Fetch authoritative loan details from DDB (amount / tenure / loan_type).
    Source of truth — avoids dependency on whatever payload the SFN sends."""
    try:
        resp = dynamodb.Table(TABLE).get_item(
            Key={'customer_id': customer_id, 'application_id': application_id}
        )
        item = resp.get('Item') or {}
        # Decimal / int unification
        def num(v):
            try: return float(v)
            except Exception: return None
        amount = num(item.get('amount'))
        tenure = item.get('tenure_months') or item.get('duration')
        tenure = int(tenure) if tenure is not None else None
        return {
            'amount': amount,
            'tenure_months': tenure,
            'loan_type': item.get('loan_type', ''),
            'purpose': item.get('purpose', ''),
        }
    except Exception as e:
        logger.warning(f"loan lookup failed for {application_id}: {e}")
        return {}


def _compute_emi(amount, tenure_months, annual_rate_pct):
    try:
        P = float(amount)
        n = int(tenure_months)
        r = float(annual_rate_pct) / 100.0 / 12.0
        if r == 0:
            return P / n
        return P * r * (1 + r) ** n / ((1 + r) ** n - 1)
    except Exception:
        return None


def _loan_rate(loan_type):
    # matches aibank-loan-config 'product' rows
    return {'instant_money': 7.0, 'personal': 5.5}.get((loan_type or '').lower(), 7.0)


def _fmt_bhd(v):
    if v is None:
        return "N/A"
    try:
        return f"BHD {float(v):,.3f}"
    except Exception:
        return f"BHD {v}"


def _map_decision(raw):
    """Map LLM decision strings to canonical DDB status values."""
    if not raw:
        return None
    u = str(raw).strip().upper()
    if u in ('APPROVE', 'APPROVED', 'AUTO_APPROVED', 'AUTO_APPROVE'):
        return 'AUTO_APPROVED'
    if u in ('REJECT', 'REJECTED', 'AUTO_REJECTED', 'AUTO_REJECT', 'DECLINE', 'DECLINED'):
        return 'AUTO_REJECTED'
    if u in ('MANUAL_REVIEW_REQUIRED', 'MANUAL'):
        return 'PENDING_REVIEW'
    return None


def _get_current_status(customer_id, application_id):
    """Read current status from DDB — used for idempotency checks."""
    try:
        resp = dynamodb.Table(TABLE).get_item(
            Key={'customer_id': customer_id, 'application_id': application_id},
            ConsistentRead=True,
        )
        return (resp.get('Item') or {}).get('status', '')
    except Exception as e:
        logger.warning(f"status lookup failed: {e}")
        return ''


_TERMINAL_STATUSES = {
    'APPROVED', 'REJECTED',
    'APPROVED_AND_NOTIFIED', 'REJECTED_AND_NOTIFIED',
    'AUTO_APPROVED', 'AUTO_REJECTED',
}


def _update_status(application_id, customer_id, new_status, decision_reason=None, task_token=None):
    """Update DDB status AND Aurora status + decision_type in one call.

    DDB: keeps the AUTO_APPROVED / AUTO_REJECTED / PENDING_REVIEW status (uppercase)
    Aurora: maps to lowercase ENUM values + sets decision_type for traceability
    """
    # === DDB update (existing behavior) ===
    table = dynamodb.Table(TABLE)
    sets = ['#s = :s', 'updated_at = :t']
    names = {'#s': 'status'}
    vals = {':s': new_status, ':t': datetime.utcnow().isoformat()}
    if decision_reason:
        sets.append('decision_reason = :r')
        vals[':r'] = str(decision_reason)[:1000]
    if task_token:
        sets.append('review_task_token = :tk')
        sets.append('review_triggered_at = :tt')
        vals[':tk'] = task_token
        vals[':tt'] = datetime.utcnow().isoformat()
    table.update_item(
        Key={'customer_id': customer_id, 'application_id': application_id},
        UpdateExpression='SET ' + ', '.join(sets),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=vals,
    )

    # === Aurora update — map to ENUM and set decision_type ===
    aurora_status_map = {
        'AUTO_APPROVED': ('approved', 'auto_approve'),
        'AUTO_REJECTED': ('rejected', 'auto_decline'),
        'APPROVED': ('approved', 'manual_approve'),
        'REJECTED': ('rejected', 'manual_reject'),
        'PENDING_REVIEW': ('manual_review', None),
        'PROCESSING': ('processing', None),
    }
    aurora_status, aurora_dec_type = aurora_status_map.get(new_status, (None, None))
    if aurora_status:
        try:
            sql_parts = ["status = :s", "updated_at = NOW()"]
            params = [
                {'name': 's', 'value': {'stringValue': aurora_status}},
                {'name': 'aid', 'value': {'stringValue': application_id}},
            ]
            if aurora_dec_type:
                sql_parts.append("decision_type = :dt")
                sql_parts.append("decision_at = NOW()")
                params.append({'name': 'dt', 'value': {'stringValue': aurora_dec_type}})
            if decision_reason:
                sql_parts.append("decision_reason = :r")
                params.append({'name': 'r', 'value': {'stringValue': str(decision_reason)[:1000]}})
            rds.execute_statement(
                resourceArn=CLUSTER_ARN, secretArn=AURORA_SECRET_ARN, database='corebanking',
                sql=f"UPDATE loan_applications SET {', '.join(sql_parts)} WHERE application_id = :aid",
                parameters=params,
            )
        except Exception as e:
            logger.warning(f"Aurora status update failed for {application_id}: {e}")

    logger.info(f"Updated {application_id} status={new_status} aurora={aurora_status} dec_type={aurora_dec_type}"
                + (" task_token_saved=True" if task_token else ""))


def _send_customer_decision_email(customer_email, customer_name, application_id, status, reason, loan):
    approved = status == 'AUTO_APPROVED'
    subject = f"[AI Bank] Loan {('Approved' if approved else 'Declined')} — {application_id}"
    amount = loan.get('amount')
    tenure = loan.get('tenure_months')
    loan_type = (loan.get('loan_type') or '').replace('_', ' ').title() or 'Personal'
    emi = _compute_emi(amount, tenure, _loan_rate(loan.get('loan_type'))) if (approved and amount and tenure) else None
    total = emi * tenure if emi and tenure else None
    amount_fmt = _fmt_bhd(amount)
    emi_fmt = _fmt_bhd(emi) if emi else None
    total_fmt = _fmt_bhd(total) if total else None
    verdict_bg = '#d1fae5' if approved else '#fee2e2'
    verdict_fg = '#065f46' if approved else '#991b1b'
    verdict_txt = '✓ Approved' if approved else '✗ Declined'
    body_intro = ("We're pleased to inform you that your loan application has been <strong>approved</strong>."
                  if approved else
                  "After careful review, we're unable to approve your loan application at this time.")

    # Build details rows conditionally
    rows = [
        ("Application ID", f"<span style=\"font-family:monospace;font-weight:600;color:#1a3a5c\">{application_id}</span>"),
        ("Loan Type", loan_type),
        ("Amount", f"<strong>{amount_fmt}</strong>"),
    ]
    if tenure:
        rows.append(("Tenure", f"{tenure} months"))
    if approved and emi_fmt:
        rows.append(("Monthly EMI", f"<strong>{emi_fmt}</strong>"))
        if total_fmt:
            rows.append(("Total Repayment", total_fmt))
    rows.append(("Decision",
                 f'<span style="background:{verdict_bg};color:{verdict_fg};padding:4px 12px;border-radius:10px;font-size:13px;font-weight:700">{verdict_txt}</span>'))

    rows_html = "".join(
        f'<tr><td style="padding:12px 16px;border-bottom:1px solid #e8ecf1;color:#666;font-size:13px;width:160px">{k}</td>'
        f'<td style="padding:12px 16px;border-bottom:1px solid #e8ecf1;color:#333;font-size:14px">{v}</td></tr>'
        for k, v in rows
    )

    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:24px 0"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08)">
  <tr><td style="background:linear-gradient(135deg,#1a3a5c,#0d2137);padding:28px 32px;text-align:center">
    <h1 style="margin:0;color:#fff;font-size:22px">🏦 AI Bank</h1>
    <p style="margin:6px 0 0;color:#8bb8d9;font-size:13px">Loan Decision</p>
  </td></tr>
  <tr><td style="padding:28px 32px">
    <p style="margin:0 0 14px;color:#333;font-size:15px">Dear {customer_name},</p>
    <p style="margin:0 0 16px;color:#333;font-size:14px;line-height:1.6">{body_intro}</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fb;border-radius:6px;border:1px solid #e8ecf1;margin:16px 0">
      {rows_html}
    </table>
    {('<p style="margin:8px 0;color:#555;font-size:13px"><strong>Reason:</strong> ' + reason + '</p>') if reason else ''}
    {('<p style="margin:16px 0 0;color:#333;font-size:14px">Our team will reach out shortly with disbursement details. No further action is required from you.</p>' if approved else '<p style="margin:16px 0 0;color:#333;font-size:14px">You may reapply after addressing the factors above. Feel free to speak with Alma in the app for guidance.</p>')}
    <p style="margin:24px 0 0;color:#999;font-size:12px;text-align:center">Questions? Reply to this email or chat with Alma in the AI Bank app.</p>
  </td></tr>
  <tr><td style="background:#f8f9fb;padding:16px 32px;text-align:center;border-top:1px solid #e8ecf1">
    <p style="margin:0;color:#aaa;font-size:11px">© 2026 AI Bank · aibank.demoaws.com</p>
  </td></tr>
</table></td></tr></table></body></html>"""

    # Plain-text version
    text_lines = [
        f"Dear {customer_name},",
        "",
        ("Your loan application has been APPROVED." if approved else "Your loan application has been declined."),
        "",
        f"Application ID: {application_id}",
        f"Loan Type: {loan_type}",
        f"Amount: {amount_fmt}",
    ]
    if tenure: text_lines.append(f"Tenure: {tenure} months")
    if approved and emi_fmt: text_lines.append(f"Monthly EMI: {emi_fmt}")
    if approved and total_fmt: text_lines.append(f"Total Repayment: {total_fmt}")
    text_lines.append(f"Decision: {verdict_txt}")
    if reason: text_lines += ["", f"Reason: {reason}"]
    text_lines += ["", "— AI Bank"]
    text = "\n".join(text_lines)

    try:
        _ses_client().send_email(
            Source=FROM_EMAIL,
            Destination={'ToAddresses': [customer_email]},
            Message={'Subject': {'Data': subject},
                     'Body': {'Text': {'Data': text}, 'Html': {'Data': html}}},
        )
        logger.info(f"Customer decision email sent to {customer_email} for {application_id}")
        return True
    except Exception as e:
        logger.error(f"Customer email send failed for {customer_email}: {e}")
        return False


def _send_officer_review_email(application_id, customer_id, amount, employer, reason,
                               tenure=None, loan_type=None, purpose=None,
                               agent_verdict=None, agent_reason=None):
    review_url = f"https://aibank.demoaws.com/employee/credit/application-review.html?id={application_id}"
    now = datetime.utcnow().strftime('%d %b %Y, %H:%M UTC')
    subject = f"[AI Bank] Loan Review Required — {application_id}"
    amount_fmt = _fmt_bhd(amount) if amount else "N/A"
    loan_type_fmt = (str(loan_type or "").replace("_", " ").title()) or "N/A"

    # Build detail rows (only show fields we actually have)
    rows = [
        ("Application ID", f'<span style="font-family:monospace;font-weight:600;color:#1a3a5c">{application_id}</span>'),
        ("Customer ID", customer_id),
        ("Loan Type", loan_type_fmt),
        ("Amount", f"<strong>{amount_fmt}</strong>"),
    ]
    if tenure:
        rows.append(("Tenure", f"{tenure} months"))
    if purpose:
        rows.append(("Purpose", str(purpose)))
    rows.append(("Employer", str(employer) if employer else "N/A"))
    if agent_verdict:
        vb = '#d1fae5' if 'APPROVE' in str(agent_verdict).upper() else (
              '#fee2e2' if 'REJECT' in str(agent_verdict).upper() else '#fef3c7')
        vf = '#065f46' if 'APPROVE' in str(agent_verdict).upper() else (
              '#991b1b' if 'REJECT' in str(agent_verdict).upper() else '#92400e')
        rows.append(("AI Agent Recommendation",
                     f'<span style="background:{vb};color:{vf};padding:3px 10px;border-radius:10px;font-size:12px;font-weight:700">{agent_verdict}</span>'))
    rows.append(("Status", '<span style="background:#fff3cd;color:#856404;padding:3px 10px;border-radius:10px;font-size:12px;font-weight:600">PENDING REVIEW</span>'))
    rows.append(("Review Reason", str(reason or "Manual review required")))
    if agent_reason:
        rows.append(("Agent Notes", str(agent_reason)[:400]))

    rows_html = "".join(
        f'<tr><td style="padding:12px 16px;border-bottom:1px solid #e8ecf1;color:#666;font-size:13px;width:180px">{k}</td>'
        f'<td style="padding:12px 16px;border-bottom:1px solid #e8ecf1;color:#333;font-size:14px">{v}</td></tr>'
        for k, v in rows
    )

    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f4f6f9;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:24px 0"><tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden">
  <tr><td style="background:linear-gradient(135deg,#1a3a5c,#0d2137);padding:28px 32px;text-align:center">
    <h1 style="margin:0;color:#fff;font-size:22px">🏦 AI Bank</h1>
    <p style="margin:6px 0 0;color:#8bb8d9;font-size:13px">Loan Review Required</p>
  </td></tr>
  <tr><td style="padding:28px 32px">
    <h2 style="margin:0 0 4px;color:#1a3a5c;font-size:18px">Manual Review Needed</h2>
    <p style="margin:0 0 18px;color:#666;font-size:13px">{now}</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fb;border-radius:6px;border:1px solid #e8ecf1;margin-bottom:18px">
      {rows_html}
    </table>
    <p align="center" style="margin:8px 0"><a href="{review_url}" style="display:inline-block;background:#1a3a5c;color:#fff;text-decoration:none;padding:12px 32px;border-radius:6px;font-weight:600">Review Application →</a></p>
  </td></tr>
</table></td></tr></table></body></html>"""
    text_lines = [
        "Loan review required",
        f"Application: {application_id}",
        f"Customer:    {customer_id}",
        f"Loan Type:   {loan_type_fmt}",
        f"Amount:      {amount_fmt}",
    ]
    if tenure: text_lines.append(f"Tenure:      {tenure} months")
    if purpose: text_lines.append(f"Purpose:     {purpose}")
    text_lines.append(f"Employer:    {employer or 'N/A'}")
    if agent_verdict: text_lines.append(f"Agent:       {agent_verdict}")
    text_lines += [f"Reason:      {reason or 'Manual review required'}", f"URL: {review_url}"]
    text = "\n".join(text_lines)
    try:
        _ses_client().send_email(
            Source=FROM_EMAIL,
            Destination={'ToAddresses': [LOAN_OFFICER_EMAIL]},
            Message={'Subject': {'Data': subject},
                     'Body': {'Text': {'Data': text}, 'Html': {'Data': html}}},
        )
        logger.info(f"Officer review email sent to {LOAN_OFFICER_EMAIL} for {application_id}")
        return True
    except Exception as e:
        logger.error(f"Officer email send failed: {e}")
        return False


def lambda_handler(event, context):
    logger.info(f"notification_dispatcher event: {json.dumps(event, default=str)[:1500]}")

    customer_id = (event.get('processingContext', {}).get('customer_id')
                   or event.get('customer_id', 'UNKNOWN'))
    application_id = (event.get('processingContext', {}).get('application_id')
                      or event.get('application_id', 'UNKNOWN'))

    is_auto = bool(event.get('auto_decision'))
    raw_decision = event.get('decision') or \
                   event.get('underwriting_decision', {}).get('decision') or ''
    decision_reason = event.get('decision_reason') or \
                      event.get('underwriting_decision', {}).get('reason') or ''
    task_token = event.get('task_token')
    terms_summary = event.get('terms_summary')  # present when called by SendApprovalNotification

    # ── Branch 0: IDEMPOTENCY GUARD ──
    # If called without auto_decision and without task_token, this is almost
    # certainly SendApprovalNotification or SendRejectionNotification firing
    # AFTER the officer already made a decision (handled by aibank-loan-reviewer).
    # The status is already terminal — do NOT clobber it or re-send officer emails.
    if not is_auto and not task_token:
        current = _get_current_status(customer_id, application_id)
        if current in _TERMINAL_STATUSES:
            # Optional: bump APPROVED → APPROVED_AND_NOTIFIED (and REJECTED → REJECTED_AND_NOTIFIED)
            # only when the SFN has formally reached the notification step.
            final_status = current
            if current == 'APPROVED':
                final_status = 'APPROVED_AND_NOTIFIED'
            elif current == 'REJECTED':
                final_status = 'REJECTED_AND_NOTIFIED'
            if final_status != current:
                try:
                    _update_status(application_id, customer_id, final_status)
                except Exception as e:
                    logger.warning(f"post-decision status bump failed: {e}")
            logger.info(
                f"Idempotent call for {application_id}: status already {current}, "
                f"final={final_status} — skipping officer email and status rewrite"
            )
            return {
                'statusCode': 200,
                'customer_id': customer_id, 'application_id': application_id,
                'status_updated': final_status,
                'decision_source': 'post_decision_notification',
                'idempotent_noop': True,
                'terms_attached': bool(terms_summary),
                'processingContext': event.get('processingContext', {}),
            }

    # ── Branch 1: AUTO-DECISION (instant money) ──
    if is_auto:
        mapped = _map_decision(raw_decision) or 'PENDING_REVIEW'
        try:
            _update_status(application_id, customer_id, mapped, decision_reason)
        except Exception as e:
            logger.error(f"auto status write failed: {e}")
            return {'statusCode': 500, 'error': str(e),
                    'customer_id': customer_id, 'application_id': application_id,
                    'processingContext': event.get('processingContext', {})}

        # Customer email — fetch authoritative loan details from DDB
        sent = False
        email, name = _lookup_customer_email(customer_id)
        if email and mapped in ('AUTO_APPROVED', 'AUTO_REJECTED'):
            loan = _lookup_loan_details(customer_id, application_id)
            sent = _send_customer_decision_email(email, name, application_id, mapped,
                                                 decision_reason, loan)
        return {
            'statusCode': 200,
            'customer_id': customer_id, 'application_id': application_id,
            'status_updated': mapped, 'decision_source': 'underwriting_agent',
            'raw_decision': raw_decision, 'decision_reason': decision_reason,
            'auto_decided': True, 'customer_notified': sent,
            'processingContext': event.get('processingContext', {}),
        }

    # ── Branch 2: MANUAL REVIEW ──
    forced_review = event.get('forcedManualReview', {})
    new_status = 'PENDING_REVIEW'
    try:
        _update_status(application_id, customer_id, new_status,
                       decision_reason or 'Manual review required',
                       task_token=task_token)

        # Pull authoritative loan details from DDB — SFN payload for manual
        # review doesn't include loan_data/customer_data, so we look them up.
        loan = _lookup_loan_details(customer_id, application_id)

        # Also read employer_name + agent verdict from the record for richer email
        try:
            full = dynamodb.Table(TABLE).get_item(
                Key={'customer_id': customer_id, 'application_id': application_id}
            ).get('Item') or {}
            employer = full.get('employer_name') or 'N/A'
            agent_verdict = full.get('underwriting_decision', {}).get('decision') if isinstance(full.get('underwriting_decision'), dict) else None
            # Also allow pulling verdict from the decision_reason hint passed in
        except Exception:
            full = {}
            employer = 'N/A'
            agent_verdict = None

        reason = forced_review.get('reason') or decision_reason or 'Manual review required'

        sent = _send_officer_review_email(
            application_id,
            customer_id,
            loan.get('amount'),
            employer,
            reason,
            tenure=loan.get('tenure_months'),
            loan_type=loan.get('loan_type'),
            purpose=loan.get('purpose'),
            agent_verdict=agent_verdict,
            agent_reason=decision_reason,
        )
    except Exception as e:
        logger.error(f"notification_dispatcher manual branch error: {e}")
        return {'statusCode': 500, 'error': str(e),
                'customer_id': customer_id, 'application_id': application_id,
                'processingContext': event.get('processingContext', {})}

    return {
        'statusCode': 200,
        'customer_id': customer_id, 'application_id': application_id,
        'status_updated': new_status, 'decision_source': 'manual_review',
        'auto_decided': False, 'notification_sent': sent,
        'task_token_persisted': bool(task_token),
        'processingContext': event.get('processingContext', {}),
    }
