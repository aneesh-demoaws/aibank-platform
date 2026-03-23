"""
Customer 360 API — Employee portal backend
GET /c360/customers — List all customers with summary metrics
GET /c360/detail?id=CUST00000001 — Full 360 view for a customer
"""
import json, logging, os, boto3, datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

rds = boto3.client("rds-data", region_name="me-south-1")
CLUSTER_ARN = os.environ.get("AURORA_CLUSTER_ARN", "arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking")
SECRET_ARN = os.environ.get("AURORA_SECRET_ARN", "arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ")
DB_NAME = os.environ.get("DB_NAME", "corebanking")
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://aibank.demoaws.com")


def _sql(sql, params=None):
    kwargs = {"resourceArn": CLUSTER_ARN, "secretArn": SECRET_ARN, "database": DB_NAME,
              "sql": sql, "includeResultMetadata": True}
    if params:
        kwargs["parameters"] = params
    return rds.execute_statement(**kwargs)


def _rows(resp):
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


def _cors(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


def lambda_handler(event, context):
    method = event.get("httpMethod", event.get("requestContext", {}).get("http", {}).get("method", "GET"))
    if method == "OPTIONS":
        return _cors(200, {})

    path = event.get("path", event.get("requestContext", {}).get("http", {}).get("path", ""))

    if path.endswith("/c360/customers"):
        return handle_customers(event)
    elif path.endswith("/c360/detail"):
        return handle_detail(event)
    return _cors(404, {"error": "Not found"})


def handle_customers(event):
    """List all customers with C360 summary."""
    try:
        resp = _sql("""
            SELECT customer_id, full_name, email, phone_number, credit_score, 
                   CAST(kyc_status AS CHAR) as kyc_status, CAST(risk_category AS CHAR) as risk_category,
                   total_accounts, total_balance, value_segment, spending_segment,
                   transaction_count_90d, credit_rating, days_since_last_transaction,
                   member_since
            FROM customer_360_summary
            ORDER BY total_balance DESC
        """)
        customers = _rows(resp)
        return _cors(200, {"customers": customers, "count": len(customers)})
    except Exception as e:
        logger.exception("Customer list error")
        return _cors(500, {"error": str(e)})


def handle_detail(event):
    """Full 360 view for a single customer."""
    qs = event.get("queryStringParameters") or {}
    cid = qs.get("id", "").strip()
    if not cid:
        return _cors(400, {"error": "id query parameter required"})

    try:
        result = {}

        # 1. Profile + C360 Summary
        resp = _sql("""
            SELECT s.*, c.nationality, c.date_of_birth, c.address_line1, c.city, c.country,
                   CAST(c.status AS CHAR) as account_status, c.employment_info, c.last_login,
                   c.phone_verified, c.email_verified
            FROM customer_360_summary s
            JOIN customers c ON s.customer_id = c.customer_id
            WHERE s.customer_id = :cid
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        profiles = _rows(resp)
        if not profiles:
            return _cors(404, {"error": "Customer not found"})
        result["profile"] = profiles[0]

        # 2. Accounts
        resp = _sql("""
            SELECT account_id, CAST(account_type AS CHAR) as account_type, account_number, 
                   balance, currency, CAST(status AS CHAR) as status, opening_date
            FROM accounts WHERE customer_id = :cid ORDER BY balance DESC
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        result["accounts"] = _rows(resp)

        # 3. Recent Transactions (last 20)
        resp = _sql("""
            SELECT t.transaction_id, t.transaction_date, t.description, t.merchant_name,
                   CAST(t.transaction_type AS CHAR) as transaction_type, t.amount, t.currency,
                   t.balance_after, mc.category_name
            FROM transactions t
            JOIN accounts a ON t.account_id = a.account_id
            LEFT JOIN merchant_categories mc ON t.category_id = mc.category_id
            WHERE a.customer_id = :cid
            ORDER BY t.transaction_date DESC LIMIT 20
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        result["recent_transactions"] = _rows(resp)

        # 4. Spending by Category (90 days)
        resp = _sql("""
            SELECT mc.category_name, COUNT(*) as txn_count, SUM(t.amount) as total_amount
            FROM transactions t
            JOIN accounts a ON t.account_id = a.account_id
            JOIN merchant_categories mc ON t.category_id = mc.category_id
            WHERE a.customer_id = :cid AND t.transaction_type = 'debit'
              AND t.transaction_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
            GROUP BY mc.category_name ORDER BY total_amount DESC
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        result["spending_by_category"] = _rows(resp)

        # 5. Loan Applications
        resp = _sql("""
            SELECT application_id, CAST(loan_type AS CHAR) as loan_type, amount, 
                   CAST(status AS CHAR) as status, monthly_payment, duration, interest,
                   purpose, channel, created_at
            FROM loan_applications WHERE customer_id = :cid ORDER BY created_at DESC
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        result["loans"] = _rows(resp)

        # 6. Customer Goals
        resp = _sql("""
            SELECT goal_id, CAST(goal_type AS CHAR) as goal_type, goal_title, 
                   target_amount, current_amount, target_date, CAST(status AS CHAR) as status
            FROM customer_goals WHERE customer_id = :cid ORDER BY target_date
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        result["goals"] = _rows(resp)

        # 7. Financial Metrics
        resp = _sql("""
            SELECT financial_health_score, monthly_income, monthly_expenses, savings_rate,
                   debt_to_income_ratio, engagement_score, transaction_frequency, 
                   account_utilization, last_calculated
            FROM customer_360_metrics WHERE customer_id = :cid
            ORDER BY last_calculated DESC LIMIT 1
        """, [{"name": "cid", "value": {"stringValue": cid}}])
        metrics = _rows(resp)
        result["metrics"] = metrics[0] if metrics else None

        # 8. Placeholder: Next Best Action (future use case)
        result["next_best_actions"] = []

        # 9. Placeholder: Financial Coach Insights (future use case)
        result["financial_coach"] = []

        return _cors(200, result)
    except Exception as e:
        logger.exception("Customer detail error")
        return _cors(500, {"error": str(e)})
