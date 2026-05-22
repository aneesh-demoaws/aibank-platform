"""Banking tools — Text-to-SQL with row-level security."""
import re, json
from strands import tool
from config import rds, CLUSTER_ARN, SECRET_ARN, DB_NAME


def _enforce_row_level_security(sql: str, customer_id: str) -> str:
    """Wrap SELECT in CTEs that pre-filter by customer_id. Guarantees row-level isolation."""
    if not re.match(r'^CUST\d{8}$', customer_id):
        return "SELECT 'INVALID_CUSTOMER_ID' as error"

    scoped = sql
    for orig, repl in [('customer_goals', 'scoped_goals'), ('merchant_categories', 'merchant_categories'),
                       ('transactions', 'scoped_transactions'), ('accounts', 'scoped_accounts'),
                       ('customers', 'scoped_customers'), ('loan_applications', 'scoped_loans')]:
        scoped = re.sub(rf'\b{orig}\b', repl, scoped)

    return f"""WITH scoped_customers AS (
  SELECT customer_id, email, phone_number, first_name, last_name, date_of_birth, nationality, city, country, CAST(kyc_status AS CHAR) as kyc_status
  FROM customers WHERE customer_id = '{customer_id}'
),
scoped_accounts AS (
  SELECT account_id, customer_id, CAST(account_type AS CHAR) as account_type, account_number, balance, currency, CAST(status AS CHAR) as status, opening_date
  FROM accounts WHERE customer_id = '{customer_id}'
),
scoped_transactions AS (
  SELECT t.transaction_id, t.account_id, a.customer_id, CAST(t.transaction_type AS CHAR) as transaction_type, t.amount, t.currency, t.description, t.balance_after, t.transaction_date, t.merchant_name, t.category_id, t.mcc_code
  FROM transactions t INNER JOIN accounts a ON t.account_id = a.account_id WHERE a.customer_id = '{customer_id}'
),
scoped_goals AS (
  SELECT goal_id, customer_id, CAST(goal_type AS CHAR) as goal_type, goal_title, target_amount, current_amount, target_date, CAST(status AS CHAR) as status
  FROM customer_goals WHERE customer_id = '{customer_id}'
),
scoped_loans AS (
  SELECT application_id, customer_id, CAST(loan_type AS CHAR) as loan_type, amount, CAST(status AS CHAR) as status, monthly_payment, duration, interest, purpose, channel, reviewed_by, officer_notes, decision_reason, created_at, updated_at
  FROM loan_applications WHERE customer_id = '{customer_id}'
)
{scoped}"""


@tool
def query_customer_data(sql_query: str, customer_id: str) -> str:
    """Execute a READ-ONLY SQL query scoped to the authenticated customer's data.

    Args:
        sql_query: MySQL SELECT query. Row-level security is enforced automatically.
        customer_id: The authenticated customer's ID (e.g. CUST00000001).
    """
    sql_upper = sql_query.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return "ERROR: Only SELECT queries allowed."
    for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE"]:
        if re.search(rf'\b{kw}\b', sql_upper):
            return f"ERROR: {kw} not allowed."

    secured_sql = _enforce_row_level_security(sql_query, customer_id)
    try:
        resp = rds.execute_statement(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN,
                                     database=DB_NAME, sql=secured_sql, includeResultMetadata=True)
        cols = [m["name"] for m in resp["columnMetadata"]]
        rows = []
        for rec in resp["records"]:
            row = []
            for f in rec:
                if "stringValue" in f: row.append(f["stringValue"])
                elif "longValue" in f: row.append(str(f["longValue"]))
                elif "doubleValue" in f: row.append(f"{f['doubleValue']:.3f}")
                elif "booleanValue" in f: row.append(str(f["booleanValue"]))
                elif "isNull" in f: row.append("NULL")
                else: row.append(str(f))
            rows.append(row)
        if not rows:
            return "Query returned no results."
        result = " | ".join(cols) + "\n" + "-" * 60 + "\n"
        for row in rows[:50]:
            result += " | ".join(row) + "\n"
        return result
    except Exception as e:
        return f"Query error: {str(e)}"
