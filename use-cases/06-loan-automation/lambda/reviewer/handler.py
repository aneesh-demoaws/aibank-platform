"""
Loan Review API — /auth, /review, /decisions, /loans/pending, /application
Employee auth via session cookie (aibank_sid) or Bearer token.
"""
import json, logging, os, boto3, datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cognito = boto3.client("cognito-idp", region_name=os.environ.get("AWS_REGION", "eu-west-1"))
rds     = boto3.client("rds-data",    region_name=os.environ.get("AWS_REGION", "eu-west-1"))
ddb     = boto3.resource("dynamodb",  region_name="eu-west-1")
sfn     = boto3.client("stepfunctions", region_name="eu-west-1")
sts     = boto3.client("sts")

EMPLOYEE_POOL_ID = os.environ["EMPLOYEE_COGNITO_POOL_ID"]
EMPLOYEE_CLIENT  = os.environ["EMPLOYEE_COGNITO_CLIENT_ID"]
CLUSTER_ARN      = os.environ["AURORA_CLUSTER_ARN"]
SECRET_ARN       = os.environ["AURORA_SECRET_ARN"]
DB_NAME          = os.environ.get("DB_NAME", "aibank")
ALLOWED_ORIGIN   = os.environ.get("ALLOWED_ORIGIN", "https://aibank.demoaws.com")
SESSION_TABLE    = os.environ.get("SESSION_TABLE", "aibank-session-routing")
LOAN_TABLE       = os.environ.get("LOAN_TABLE", "aibank-personal-loan")
SES_ROLE_ARN     = os.environ.get("SES_ROLE_ARN", "arn:aws:iam::225872788412:role/aibank-demo-ses-sender")
SES_REGION       = os.environ.get("SES_REGION", "eu-west-1")
FROM_EMAIL       = os.environ.get("FROM_EMAIL", "noreply@demoaws.com")
CORE_BANKING_CLUSTER_ARN = os.environ.get("CORE_BANKING_CLUSTER_ARN",
                                          "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr")
CORE_BANKING_SECRET_ARN  = os.environ.get("CORE_BANKING_SECRET_ARN",
                                          "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6")


def _ses_client():
    """Assume ct-prod role for SES sending (cross-account)."""
    creds = sts.assume_role(
        RoleArn=SES_ROLE_ARN,
        RoleSessionName="loan-reviewer-decision",
        DurationSeconds=900,
    )["Credentials"]
    return boto3.client(
        "ses", region_name=SES_REGION,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _lookup_customer_email(customer_id):
    try:
        r = boto3.client("rds-data", region_name="eu-west-1").execute_statement(
            resourceArn=CORE_BANKING_CLUSTER_ARN,
            secretArn=CORE_BANKING_SECRET_ARN,
            database="corebanking",
            sql="SELECT email, first_name, last_name FROM customers WHERE customer_id = :c LIMIT 1",
            parameters=[{"name": "c", "value": {"stringValue": customer_id}}],
        )
        for rec in r.get("records", []):
            email = rec[0].get("stringValue")
            name = f"{rec[1].get('stringValue','')} {rec[2].get('stringValue','')}".strip() or customer_id
            return email, name
    except Exception as e:
        logger.warning(f"customer email lookup failed for {customer_id}: {e}")
    return None, customer_id


def _send_customer_officer_decision_email(customer_email, customer_name, application_id,
                                          decision, loan, notes, officer_email):
    approved = decision == "APPROVED"
    subject = f"[AI Bank] Loan {('Approved' if approved else 'Declined')} — {application_id}"
    amount = loan.get("amount")
    tenure = loan.get("tenure_months")
    loan_type = (loan.get("loan_type") or "").replace("_", " ").title() or "Personal"
    # Product rates (match aibank-loan-config 'product' rows)
    rate_by_type = {"instant_money": 7.0, "personal": 5.5}
    rate_pct = rate_by_type.get((loan.get("loan_type") or "").lower(), 7.0)
    # EMI computation
    emi = None
    total = None
    try:
        if approved and amount and tenure:
            P = float(amount); n = int(tenure); r = rate_pct / 100.0 / 12.0
            emi = P / n if r == 0 else P * r * (1 + r) ** n / ((1 + r) ** n - 1)
            total = emi * n
    except Exception:
        emi = total = None

    def _bhd(v):
        try: return f"BHD {float(v):,.3f}"
        except Exception: return "N/A"

    amount_fmt = _bhd(amount) if amount else "N/A"
    emi_fmt = _bhd(emi) if emi else None
    total_fmt = _bhd(total) if total else None

    verdict_bg = "#d1fae5" if approved else "#fee2e2"
    verdict_fg = "#065f46" if approved else "#991b1b"
    verdict_txt = "✓ Approved" if approved else "✗ Declined"
    intro = ("After review, your loan application has been <strong>approved</strong>."
             if approved else
             "After careful review, we're unable to approve your loan application at this time.")

    rows = [
        ("Application ID", f'<span style="font-family:monospace;font-weight:600;color:#1a3a5c">{application_id}</span>'),
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
<table width="600" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden">
  <tr><td style="background:linear-gradient(135deg,#1a3a5c,#0d2137);padding:28px 32px;text-align:center">
    <h1 style="margin:0;color:#fff;font-size:22px">🏦 AI Bank</h1>
    <p style="margin:6px 0 0;color:#8bb8d9;font-size:13px">Loan Decision</p>
  </td></tr>
  <tr><td style="padding:28px 32px">
    <p style="margin:0 0 14px;color:#333;font-size:15px">Dear {customer_name},</p>
    <p style="margin:0 0 16px;color:#333;font-size:14px;line-height:1.6">{intro}</p>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#f8f9fb;border-radius:6px;border:1px solid #e8ecf1;margin:16px 0">
      {rows_html}
    </table>
    {('<p style="margin:8px 0;color:#555;font-size:13px"><strong>Notes from reviewer:</strong> ' + notes + '</p>') if notes else ''}
    <p style="margin:18px 0 0;color:#333;font-size:14px">{('Our team will reach out with disbursement details.' if approved else 'Feel free to speak with Alma in the app for guidance.')}</p>
  </td></tr>
</table></td></tr></table></body></html>"""
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
    if notes: text_lines += ["", f"Notes from reviewer: {notes}"]
    text_lines += ["", "— AI Bank"]
    text = "\n".join(text_lines)
    try:
        _ses_client().send_email(
            Source=FROM_EMAIL,
            Destination={"ToAddresses": [customer_email]},
            Message={"Subject": {"Data": subject},
                     "Body": {"Text": {"Data": text}, "Html": {"Data": html}}},
        )
        logger.info(f"Customer officer-decision email sent to {customer_email}")
        return True
    except Exception as e:
        logger.error(f"Customer email failed: {e}")
        return False


def lambda_handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return _cors(200, "")
    method = event.get("httpMethod", "GET")
    path   = event.get("path", "")

    if path == "/auth"          and method == "POST":  return handle_auth(event)
    if path == "/review"        and method == "GET":   return handle_review(event)
    if path == "/decisions"     and method == "POST":  return handle_decision(event)
    if path == "/loans/pending" and method == "GET":   return handle_pending_loans(event)
    if path == "/loans/all"     and method == "GET":   return handle_all_loans(event)
    if path == "/application"   and method == "GET":   return handle_application_detail(event)
    if path == "/c360/customers" and method == "GET":  return handle_c360_customers(event)
    if path == "/c360/detail"   and method == "GET":   return handle_c360_detail(event)
    return _cors(404, json.dumps({"error": "Not found"}))


# ── Auth (legacy Bearer token login) ──────────────────────────────────────────

def handle_auth(event):
    body  = json.loads(event.get("body") or "{}")
    email = body.get("email", "").strip().lower()
    pwd   = body.get("password", "")
    if not email or not pwd:
        return _cors(400, json.dumps({"error": "email and password required"}))
    try:
        resp = cognito.initiate_auth(
            ClientId=EMPLOYEE_CLIENT,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": pwd},
        )
        tokens   = resp.get("AuthenticationResult", {})
        id_token = tokens.get("IdToken")
        if not id_token:
            raise Exception("No token returned")
        user  = cognito.admin_get_user(UserPoolId=EMPLOYEE_POOL_ID, Username=email)
        attrs = {a["Name"]: a["Value"] for a in user["UserAttributes"]}
        name  = attrs.get("name", email.split("@")[0])
        groups = cognito.admin_list_groups_for_user(
            UserPoolId=EMPLOYEE_POOL_ID, Username=email
        ).get("Groups", [])
        role = groups[0]["GroupName"] if groups else "employee"
        return _cors(200, json.dumps({"token": id_token, "name": name, "role": role}))
    except cognito.exceptions.NotAuthorizedException:
        return _cors(401, json.dumps({"error": "Invalid email or password"}))
    except Exception as e:
        logger.exception("Auth error")
        return _cors(500, json.dumps({"error": str(e)}))


# ── Application detail ─────────────────────────────────────────────────────────

def handle_application_detail(event):
    if not _is_authenticated(event):
        return _cors(401, json.dumps({"message": "Unauthorised"}))
    app_id = (event.get("queryStringParameters") or {}).get("id", "").strip()
    if not app_id:
        return _cors(400, json.dumps({"error": "id query parameter required"}))
    try:
        table  = ddb.Table(LOAN_TABLE)
        result = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("application_id").eq(app_id)
        )
        items = result.get("Items", [])
        if not items:
            return _cors(404, json.dumps({"error": "Application not found"}))
        item = items[0]

        # Enrich with Aurora customer data if KYC fields are sparse
        cid = item.get("customer_id", "")
        if cid.startswith("CUST"):
            try:
                rds_me = boto3.client("rds-data", region_name="eu-west-1")
                row = rds_me.execute_statement(
                    resourceArn="arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr",
                    secretArn="arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6",
                    database="corebanking",
                    sql="SELECT social_name, first_name, last_name, nationality, country, date_of_birth, credit_score, kyc_status FROM customers WHERE customer_id = :c",
                    parameters=[{"name": "c", "value": {"stringValue": cid}}]
                ).get("records", [])
                if row:
                    r = row[0]
                    sv = lambda f: f.get("stringValue") if not f.get("isNull") else None
                    social = sv(r[0])
                    full_name = social if social else f"{sv(r[1]) or ''} {sv(r[2]) or ''}".strip()
                    item["aurora_customer"] = {
                        "full_name": full_name, "nationality": sv(r[3]) or "",
                        "country": sv(r[4]) or "", "date_of_birth": sv(r[5]) or "",
                        "credit_score": sv(r[6]) or "", "kyc_status": sv(r[7]) or "",
                    }
            except Exception as e:
                logger.warning(f"Aurora enrichment failed: {e}")

        return _cors(200, json.dumps(item, default=str))
    except Exception as e:
        logger.exception("Application detail error")
        return _cors(500, json.dumps({"error": str(e)}))


# ── Pending loans from DynamoDB ────────────────────────────────────────────────

def handle_pending_loans(event):
    if not _is_authenticated(event):
        return _cors(401, json.dumps({"message": "Unauthorised"}))
    try:
        table = ddb.Table(LOAN_TABLE)
        result = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("status").eq("PENDING_REVIEW")
        )
        apps = []
        for item in result.get("Items", []):
            apps.append({
                "application_id": item.get("application_id"),
                "customer_id":    item.get("customer_id"),
                "loan_type":      item.get("loan_type"),
                "amount":         str(item.get("amount_bhd") or item.get("amount", "")),
                "tenure_months":  str(item.get("tenure_months") or item.get("duration", "")),
                "status":         item.get("status"),
                "submitted_at":   item.get("submitted_at"),
                "employer_name":  item.get("employer_name", ""),
                "basic_salary":   str(item.get("basic_salary", "")),
                "underwriting":   item.get("loan_underwritting_recommendations", "")[:500] if item.get("loan_underwritting_recommendations") else "",
            })
        apps.sort(key=lambda x: x.get("submitted_at", ""), reverse=True)
        return _cors(200, json.dumps({"applications": apps}))
    except Exception as e:
        logger.exception("Pending loans error")
        return _cors(500, json.dumps({"error": str(e)}))


# ── All loans (for officer visibility) ─────────────────────────────────────────

def handle_all_loans(event):
    if not _is_authenticated(event):
        return _cors(401, json.dumps({"message": "Unauthorised"}))
    try:
        # Query Aurora with customer JOIN for rich data
        result = _sql("""
            SELECT la.application_id, la.customer_id, c.first_name, c.last_name, c.email,
                   la.loan_type, la.amount, la.duration, la.interest, la.monthly_payment,
                   la.status, la.created_at, la.underwriting_score, la.decision_type,
                   la.purpose, la.review_notes, la.reviewer_id, la.disbursement_status,
                   la.disbursement_txn_id
            FROM loan_applications la
            LEFT JOIN customers c ON la.customer_id = c.customer_id
            ORDER BY la.created_at DESC LIMIT 200
        """)
        apps = []
        for r in result.get("records", []):
            apps.append({
                "application_id": _val(r[0]),
                "customer_id": _val(r[1]),
                "first_name": _val(r[2]),
                "last_name": _val(r[3]),
                "email": _val(r[4]),
                "loan_type": _val(r[5]),
                "amount": str(_val(r[6]) or ""),
                "duration": _val(r[7]),
                "interest": str(_val(r[8]) or ""),
                "monthly_payment": str(_val(r[9]) or ""),
                "status": _val(r[10]),
                "created_at": str(_val(r[11]) or ""),
                "underwriting_score": str(_val(r[12]) or ""),
                "decision_type": _val(r[13]),
                "auto_decided": _val(r[13]) in ("auto_approve", "auto_decline"),
                "purpose": _val(r[14]),
                "review_notes": _val(r[15]),
                "reviewer_id": _val(r[16]),
                "disbursement_status": _val(r[17]),
                "disbursement_txn_id": _val(r[18]),
            })
        return _cors(200, json.dumps({"applications": apps}, default=str))
    except Exception as e:
        logger.exception("All loans error")
        return _cors(500, json.dumps({"error": str(e)}))


# ── Review list (Aurora — legacy) ─────────────────────────────────────────────

def handle_review(event):
    if not _is_authenticated(event):
        return _cors(401, json.dumps({"message": "Unauthorised"}))
    try:
        result = _sql("""
            SELECT application_id, user_id, loan_type, amount_bhd, tenure_months,
                   status, submitted_at, purpose, credit_score, monthly_emi_bhd
            FROM loan_applications ORDER BY submitted_at DESC LIMIT 100
        """)
        apps = [{"application_id": _val(r[0]), "user_id": _val(r[1]), "loan_type": _val(r[2]),
                 "amount_bhd": _val(r[3]), "tenure_months": _val(r[4]), "status": _val(r[5]),
                 "submitted_at": _val(r[6]), "purpose": _val(r[7]), "credit_score": _val(r[8]),
                 "monthly_emi_bhd": _val(r[9])} for r in result.get("records", [])]
        return _cors(200, json.dumps({"applications": apps}))
    except Exception as e:
        logger.exception("Review list error")
        return _cors(500, json.dumps({"error": "Failed to load applications"}))


# ── Decision ──────────────────────────────────────────────────────────────────

def handle_decision(event):
    claims = _verify_token(event)
    if not claims and not _is_authenticated(event):
        return _cors(401, json.dumps({"message": "Unauthorised"}))

    body     = json.loads(event.get("body") or "{}")
    app_id   = body.get("application_id", "").strip()
    decision = body.get("decision", "").strip().upper()
    notes    = body.get("notes", "")
    officer  = body.get("officer_email", "loanofficer@demoaws.com")

    if not app_id or decision not in ("APPROVED", "REJECTED"):
        return _cors(400, json.dumps({"error": "application_id and decision (APPROVED|REJECTED) required"}))

    try:
        now = datetime.datetime.utcnow().isoformat()
        table = ddb.Table(LOAN_TABLE)
        result = table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("application_id").eq(app_id)
        )
        items = result.get("Items", [])
        if not items:
            return _cors(404, json.dumps({"error": "Application not found"}))
        item = items[0]
        table.update_item(
            Key={"customer_id": item["customer_id"], "application_id": app_id},
            UpdateExpression="SET #s = :s, updated_at = :t, officer_notes = :n, reviewed_by = :o",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":s": decision, ":t": now, ":n": notes, ":o": officer}
        )
        # ── Unblock the waiting SFN execution ─────────────────────────────
        # notification-dispatcher persisted the task_token on the loan record
        # when Manual_Review_Initiated was entered. If present, unblock the
        # waitForTaskToken state; the SFN will then route via PostDecisionRouter
        # to ApprovalPath / RejectionPath.
        task_token = item.get("review_task_token")
        sfn_unblocked = False
        if task_token:
            try:
                sfn.send_task_success(
                    taskToken=task_token,
                    output=json.dumps({
                        "decision": decision,               # APPROVED | REJECTED
                        "manual_review_result": {
                            "decision": decision,
                            "notes": notes,
                            "officer": officer,
                            "reviewed_at": now,
                        },
                    })
                )
                # Clear the token so the same approval can't replay
                table.update_item(
                    Key={"customer_id": item["customer_id"], "application_id": app_id},
                    UpdateExpression="REMOVE review_task_token",
                )
                sfn_unblocked = True
                logger.info(f"SFN task success sent for {app_id}")
            except Exception as e:
                # Task tokens expire after 24h. Log but don't block the DDB write.
                logger.error(f"SendTaskSuccess failed for {app_id}: {e}")
        else:
            logger.warning(f"No review_task_token found on {app_id}; SFN may already have timed out")

        # ── Notify customer by email ─────────────────────────────────────
        customer_notified = False
        cust_email, cust_name = _lookup_customer_email(item["customer_id"])
        # Build loan details from the DDB item we already have
        try:
            amt = float(item.get("amount")) if item.get("amount") is not None else None
        except Exception:
            amt = None
        try:
            tenure_m = int(item.get("tenure_months") or item.get("duration") or 0) or None
        except Exception:
            tenure_m = None
        loan_details = {
            "amount": amt,
            "tenure_months": tenure_m,
            "loan_type": item.get("loan_type", ""),
            "purpose": item.get("purpose", ""),
        }
        if cust_email and decision in ("APPROVED", "REJECTED"):
            customer_notified = _send_customer_officer_decision_email(
                cust_email, cust_name, app_id, decision, loan_details, notes, officer
            )

        # Sync decision to core banking MySQL
        try:
            rds_data = boto3.client("rds-data", region_name="eu-west-1")
            mysql_status = decision.lower()
            cb_secret = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"
            rds_data.execute_statement(
                resourceArn=os.environ.get("AURORA_CLUSTER_ARN", CLUSTER_ARN),
                secretArn=cb_secret,
                database="corebanking",
                sql="UPDATE loan_applications SET status=:s, reviewed_by=:o, officer_notes=:n, decision_type=:dt, decision_at=NOW(), updated_at=NOW() WHERE application_id=:aid",
                parameters=[
                    {"name": "s", "value": {"stringValue": mysql_status}},
                    {"name": "o", "value": {"stringValue": officer}},
                    {"name": "n", "value": {"stringValue": notes or ""}},
                    {"name": "dt", "value": {"stringValue": ('manual_approve' if decision == 'APPROVED' else 'manual_reject')}},
                    {"name": "aid", "value": {"stringValue": app_id}}])
        except Exception as e:
            logger.error(f"Core banking sync error: {e}")
        return _cors(200, json.dumps({
            "success": True,
            "application_id": app_id,
            "decision": decision,
            "sfn_unblocked": sfn_unblocked,
            "customer_notified": customer_notified,
        }))
    except Exception as e:
        logger.exception("Decision error")
        return _cors(500, json.dumps({"error": "Failed to record decision"}))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_authenticated(event):
    cookies = event.get("headers", {}).get("Cookie", "") or \
              " ".join(event.get("multiValueHeaders", {}).get("Cookie", []))
    for part in cookies.split(";"):
        part = part.strip()
        if part.startswith("aibank_sid="):
            sid  = part[len("aibank_sid="):]
            item = ddb.Table(SESSION_TABLE).get_item(Key={"session_id": sid}).get("Item")
            if item and item.get("status") == "active" and item.get("portal") == "employee":
                return True
    return bool(_verify_token(event))


def _get_user_role(event):
    """Get the authenticated user's role from session."""
    cookies = event.get("headers", {}).get("Cookie", "") or \
              " ".join(event.get("multiValueHeaders", {}).get("Cookie", []))
    for part in cookies.split(";"):
        part = part.strip()
        if part.startswith("aibank_sid="):
            sid = part[len("aibank_sid="):]
            item = ddb.Table(SESSION_TABLE).get_item(Key={"session_id": sid}).get("Item")
            if item and item.get("status") == "active":
                return item.get("role", "employee")
    return None


def _verify_token(event):
    auth = event.get("headers", {}).get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        import base64
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return None


def _val(field):
    if not field:
        return None
    # rds-data returns {"isNull": True} for NULL values — must check before extracting
    if field.get("isNull"):
        return None
    return list(field.values())[0]


def _sql(sql, params=None):
    kwargs = {"resourceArn": CLUSTER_ARN, "secretArn": SECRET_ARN, "database": DB_NAME, "sql": sql}
    if params:
        kwargs["parameters"] = params
    return rds.execute_statement(**kwargs)


def _cors(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Headers": "Content-Type,Authorization,Cookie",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Credentials": "true",
        },
        "body": body,
    }


# ── Customer 360 ──

_rds_me = boto3.client("rds-data", region_name="eu-west-1")
_C360_CLUSTER = "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr"
_C360_SECRET = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"

def _c360_sql(sql, params=None):
    kwargs = {"resourceArn": _C360_CLUSTER, "secretArn": _C360_SECRET, "database": "corebanking",
              "sql": sql, "includeResultMetadata": True}
    if params:
        kwargs["parameters"] = params
    return _rds_me.execute_statement(**kwargs)

def _c360_rows(resp):
    cols = [c["name"] for c in resp["columnMetadata"]]
    rows = []
    for rec in resp["records"]:
        row = {}
        for c, f in zip(cols, rec):
            if "stringValue" in f: row[c] = f["stringValue"]
            elif "longValue" in f: row[c] = f["longValue"]
            elif "doubleValue" in f: row[c] = round(f["doubleValue"], 3)
            elif "booleanValue" in f: row[c] = f["booleanValue"]
            elif "isNull" in f: row[c] = None
            else: row[c] = str(f)
        rows.append(row)
    return rows


def handle_c360_customers(event):
    if not _is_authenticated(event):
        return _cors(401, json.dumps({"message": "Unauthorised"}))
    role = _get_user_role(event)
    if role not in ("relationship-managers", "rm", "admin"):
        return _cors(403, json.dumps({"error": "Access denied. Customer 360 is available to Relationship Managers and Admins only."}))
    try:
        resp = _c360_sql("""
            SELECT customer_id, full_name, email, phone_number, credit_score,
                   CAST(kyc_status AS CHAR) as kyc_status, CAST(risk_category AS CHAR) as risk_category,
                   total_accounts, total_balance, value_segment, spending_segment,
                   transaction_count_90d, credit_rating, days_since_last_transaction, member_since
            FROM customer_360_summary ORDER BY total_balance DESC
        """)
        customers = _c360_rows(resp)
        return _cors(200, json.dumps({"customers": customers, "count": len(customers)}, default=str))
    except Exception as e:
        logger.exception("C360 customers error")
        return _cors(500, json.dumps({"error": str(e)}))


def handle_c360_detail(event):
    if not _is_authenticated(event):
        return _cors(401, json.dumps({"message": "Unauthorised"}))
    role = _get_user_role(event)
    if role not in ("relationship-managers", "rm", "admin"):
        return _cors(403, json.dumps({"error": "Access denied. Customer 360 is available to Relationship Managers and Admins only."}))
    cid = (event.get("queryStringParameters") or {}).get("id", "").strip()
    if not cid:
        return _cors(400, json.dumps({"error": "id required"}))
    try:
        result = {}
        # Profile
        resp = _c360_sql("""
            SELECT s.*, c.nationality, c.date_of_birth, c.address_line1, c.city, c.country,
                   CAST(c.status AS CHAR) as account_status, c.employment_info, c.last_login,
                   c.phone_verified, c.email_verified
            FROM customer_360_summary s JOIN customers c ON s.customer_id = c.customer_id
            WHERE s.customer_id = :cid
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        profiles = _c360_rows(resp)
        if not profiles:
            return _cors(404, json.dumps({"error": "Customer not found"}))
        result["profile"] = profiles[0]

        # Accounts
        resp = _c360_sql("SELECT account_id, CAST(account_type AS CHAR) as account_type, account_number, balance, currency, CAST(status AS CHAR) as status, opening_date FROM accounts WHERE customer_id = :cid ORDER BY balance DESC",
            [{"name": "cid", "value": {"stringValue": cid}}])
        result["accounts"] = _c360_rows(resp)

        # Recent Transactions
        resp = _c360_sql("""
            SELECT t.transaction_date, t.description, t.merchant_name, CAST(t.transaction_type AS CHAR) as transaction_type,
                   t.amount, t.currency, t.balance_after, mc.category_name
            FROM transactions t JOIN accounts a ON t.account_id = a.account_id
            LEFT JOIN merchant_categories mc ON t.category_id = mc.category_id
            WHERE a.customer_id = :cid ORDER BY t.transaction_date DESC LIMIT 20
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        result["recent_transactions"] = _c360_rows(resp)

        # Spending by Category
        resp = _c360_sql("""
            SELECT mc.category_name, COUNT(*) as txn_count, SUM(t.amount) as total_amount
            FROM transactions t JOIN accounts a ON t.account_id = a.account_id
            JOIN merchant_categories mc ON t.category_id = mc.category_id
            WHERE a.customer_id = :cid AND t.transaction_type = 'debit'
              AND t.transaction_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
            GROUP BY mc.category_name ORDER BY total_amount DESC
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        result["spending_by_category"] = _c360_rows(resp)

        # Loans
        resp = _c360_sql("""
            SELECT application_id, CAST(loan_type AS CHAR) as loan_type, amount, CAST(status AS CHAR) as status,
                   monthly_payment, duration, interest, purpose, channel, created_at
            FROM loan_applications WHERE customer_id = :cid ORDER BY created_at DESC
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        result["loans"] = _c360_rows(resp)

        # Goals
        resp = _c360_sql("""
            SELECT goal_id, CAST(goal_type AS CHAR) as goal_type, goal_title, target_amount,
                   current_amount, target_date, CAST(status AS CHAR) as status
            FROM customer_goals WHERE customer_id = :cid ORDER BY target_date
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        result["goals"] = _c360_rows(resp)

        # Metrics
        resp = _c360_sql("""
            SELECT financial_health_score, monthly_income, monthly_expenses, savings_rate,
                   debt_to_income_ratio, engagement_score, transaction_frequency, account_utilization
            FROM customer_360_metrics WHERE customer_id = :cid ORDER BY last_calculated DESC LIMIT 1
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        metrics = _c360_rows(resp)
        result["metrics"] = metrics[0] if metrics else None

        result["next_best_actions"] = []
        result["financial_coach"] = []

        return _cors(200, json.dumps(result, default=str))
    except Exception as e:
        logger.exception("C360 detail error")
        return _cors(500, json.dumps({"error": str(e)}))


