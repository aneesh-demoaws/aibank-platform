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

EMPLOYEE_POOL_ID = os.environ["EMPLOYEE_COGNITO_POOL_ID"]
EMPLOYEE_CLIENT  = os.environ["EMPLOYEE_COGNITO_CLIENT_ID"]
CLUSTER_ARN      = os.environ["AURORA_CLUSTER_ARN"]
SECRET_ARN       = os.environ["AURORA_SECRET_ARN"]
DB_NAME          = os.environ.get("DB_NAME", "aibank")
ALLOWED_ORIGIN   = os.environ.get("ALLOWED_ORIGIN", "https://aibank.demoaws.com")
SESSION_TABLE    = os.environ.get("SESSION_TABLE", "aibank-session-routing")
LOAN_TABLE       = os.environ.get("LOAN_TABLE", "aibank-personal-loan")


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
                rds_me = boto3.client("rds-data", region_name="me-south-1")
                row = rds_me.execute_statement(
                    resourceArn="arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking",
                    secretArn="arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ",
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
        table = ddb.Table(LOAN_TABLE)
        result = table.scan()
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
                "auto_decided":   item.get("auto_decided", False),
                "decision_reason": item.get("decision_reason", ""),
            })
        apps.sort(key=lambda x: x.get("submitted_at") or "", reverse=True)
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
        # Sync decision to core banking MySQL
        try:
            rds_data = boto3.client("rds-data", region_name="me-south-1")
            mysql_status = decision.lower()
            cb_secret = "arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ"
            rds_data.execute_statement(
                resourceArn=os.environ.get("AURORA_CLUSTER_ARN", CLUSTER_ARN),
                secretArn=cb_secret,
                database="corebanking",
                sql="UPDATE loan_applications SET status=:s, reviewed_by=:o, officer_notes=:n, updated_at=NOW() WHERE application_id=:aid",
                parameters=[
                    {"name": "s", "value": {"stringValue": mysql_status}},
                    {"name": "o", "value": {"stringValue": officer}},
                    {"name": "n", "value": {"stringValue": notes or ""}},
                    {"name": "aid", "value": {"stringValue": app_id}}])
        except Exception as e:
            logger.error(f"Core banking sync error: {e}")
        return _cors(200, json.dumps({"success": True, "application_id": app_id, "decision": decision}))
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

_rds_me = boto3.client("rds-data", region_name="me-south-1")
_C360_CLUSTER = "arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking"
_C360_SECRET = "arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ"

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
