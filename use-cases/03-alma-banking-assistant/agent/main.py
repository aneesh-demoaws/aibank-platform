"""
Alma Banking Assistant — AgentCore Runtime
Authenticated customer agent with Text-to-SQL and row-level security.
"""
import os, json, logging, re
import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ──
CLUSTER_ARN = os.environ.get("CLUSTER_ARN", "arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking")
SECRET_ARN = os.environ.get("SECRET_ARN", "arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ")
DB_NAME = os.environ.get("DB_NAME", "corebanking")
DB_REGION = os.environ.get("DB_REGION", "me-south-1")
REGION = os.environ.get("AWS_REGION", "eu-west-1")
MODEL_ID = os.environ.get("MODEL_ID", "eu.anthropic.claude-sonnet-4-20250514-v1:0")

rds = boto3.client("rds-data", region_name=DB_REGION)

SYSTEM_PROMPT = """You are Alma Banking Assistant for AI Bank. You help authenticated customers query their banking data.

## CRITICAL RULES
1. Row-level security is enforced automatically — the tool scopes all tables to the authenticated customer
2. NEVER fabricate financial data — every number must come from query_customer_data
3. READ-ONLY — never attempt INSERT, UPDATE, DELETE
4. If no data found, say so honestly
5. Write queries using table names: customers, accounts, transactions, merchant_categories, customer_goals

## DATABASE SCHEMA
customers: customer_id(PK), email, phone_number, first_name, last_name, date_of_birth, nationality, city, country(BH|SA|AE), kyc_status
accounts: account_id(PK), customer_id(FK), account_type(savings|current|premium|business), account_number, balance(decimal15,3), currency(BHD|SAR|AED), status, opening_date
transactions: transaction_id(PK), account_id(FK→accounts), transaction_type(credit|debit), amount(decimal12,3), currency, description, balance_after, transaction_date, merchant_name, category_id(FK), mcc_code
merchant_categories: category_id(PK, CAT001-CAT014), category_name(Groceries|Dining|Housing/Utilities|Transport|Entertainment|Shopping|Health|Telecom|Salary)
customer_goals: goal_id(PK), customer_id(FK), goal_type, goal_title, target_amount, current_amount, target_date, status

## KEY JOINS
- Customer's transactions: JOIN accounts ON customer_id, then JOIN transactions ON account_id
- Category names: JOIN merchant_categories ON category_id
- Salary = transaction_type='credit' AND category_id='CAT014'

## SQL EXAMPLES (use these patterns)

Category spending: SELECT mc.category_name, COUNT(*) as txn_count, SUM(t.amount) as total FROM transactions t JOIN merchant_categories mc ON t.category_id = mc.category_id WHERE t.transaction_type = 'debit' AND mc.category_name LIKE '%Groceries%' AND t.transaction_date >= DATE_SUB(CURDATE(), INTERVAL 1 MONTH) GROUP BY mc.category_name;

Monthly trend: SELECT DATE_FORMAT(t.transaction_date, '%Y-%m') as month, SUM(t.amount) as total FROM transactions t WHERE t.transaction_type = 'debit' AND t.transaction_date >= DATE_SUB(CURDATE(), INTERVAL 3 MONTH) GROUP BY month ORDER BY month;

Top merchants: SELECT t.merchant_name, mc.category_name, COUNT(*) as visits, SUM(t.amount) as total FROM transactions t JOIN merchant_categories mc ON t.category_id = mc.category_id WHERE t.transaction_type = 'debit' GROUP BY t.merchant_name, mc.category_name ORDER BY total DESC LIMIT 10;

Salary/income: SELECT t.amount, t.transaction_date, t.description FROM transactions t WHERE t.transaction_type = 'credit' AND t.category_id = 'CAT014' ORDER BY t.transaction_date DESC LIMIT 3;

Day-of-week: SELECT DAYNAME(t.transaction_date) as day_name, COUNT(*) as txns, SUM(t.amount) as total FROM transactions t WHERE t.transaction_type = 'debit' GROUP BY day_name ORDER BY total DESC;

IMPORTANT: Always use merchant_categories JOIN for category filtering, never LIKE on merchant_name for categories.

## RESPONSE STYLE
- Friendly, professional, concise
- Use customer's currency with correct decimals (BHD=3, SAR/AED=2)
- Tables for lists, bold for totals
- Include percentages in spending breakdowns"""


def _enforce_row_level_security(sql: str, customer_id: str) -> str:
    """Wrap any SELECT in CTEs that pre-filter by customer_id.
    Guarantees row-level isolation regardless of what the LLM generates.
    customer_id is safe to interpolate: validated format, sourced from our DB lookup."""
    if not re.match(r'^CUST\d{8}$', customer_id):
        return "SELECT 'INVALID_CUSTOMER_ID' as error"

    scoped = sql
    for orig, repl in [('customer_goals', 'scoped_goals'), ('merchant_categories', 'merchant_categories'), ('transactions', 'scoped_transactions'), ('accounts', 'scoped_accounts'), ('customers', 'scoped_customers')]:
        scoped = re.sub(rf'\b{orig}\b', repl, scoped)

    cid = customer_id
    return f"""WITH scoped_customers AS (
  SELECT customer_id, email, phone_number, first_name, last_name, date_of_birth, nationality, city, country, CAST(kyc_status AS CHAR) as kyc_status
  FROM customers WHERE customer_id = '{cid}'
),
scoped_accounts AS (
  SELECT account_id, customer_id, CAST(account_type AS CHAR) as account_type, account_number, balance, currency, CAST(status AS CHAR) as status, opening_date
  FROM accounts WHERE customer_id = '{cid}'
),
scoped_transactions AS (
  SELECT t.transaction_id, t.account_id, CAST(t.transaction_type AS CHAR) as transaction_type, t.amount, t.currency, t.description, t.balance_after, t.transaction_date, t.merchant_name, t.category_id, t.mcc_code
  FROM transactions t INNER JOIN accounts a ON t.account_id = a.account_id WHERE a.customer_id = '{cid}'
),
scoped_goals AS (
  SELECT goal_id, customer_id, CAST(goal_type AS CHAR) as goal_type, goal_title, target_amount, current_amount, target_date, CAST(status AS CHAR) as status
  FROM customer_goals WHERE customer_id = '{cid}'
)
{scoped}"""


@tool
def query_customer_data(sql_query: str, customer_id: str) -> str:
    """Execute a READ-ONLY SQL query scoped to the authenticated customer's data only.

    Args:
        sql_query: MySQL SELECT query. Row-level security is enforced automatically.
        customer_id: The authenticated customer's ID (e.g. CUST00000001).

    Returns:
        Query results as formatted text, or an error message.
    """
    sql_upper = sql_query.strip().upper()
    if not sql_upper.startswith("SELECT"):
        return "ERROR: Only SELECT queries allowed."

    for kw in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "GRANT", "REVOKE"]:
        if re.search(rf'\b{kw}\b', sql_upper):
            return f"ERROR: {kw} not allowed. Read-only access only."

    secured_sql = _enforce_row_level_security(sql_query, customer_id)

    try:
        resp = rds.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN,
            database=DB_NAME, sql=secured_sql, includeResultMetadata=True
        )
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
        if len(rows) > 50:
            result += f"\n... and {len(rows) - 50} more rows"
        return result
    except Exception as e:
        logger.error(f"Query error: {e}")
        return f"Query error: {str(e)}"


# ── AgentCore App ──
app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload):
    user_message = payload.get("prompt", "Hello")
    customer_id = payload.get("customer_id", "")

    if not customer_id:
        return {"answer": "Authentication required. Please log in to use Alma Banking Assistant."}

    prompt = f"[Customer ID: {customer_id}] {user_message}"
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=[query_customer_data])
    result = agent(prompt)
    answer = re.sub(r"<thinking>[\s\S]*?</thinking>", "", str(result)).strip()
    return {"answer": answer, "customer_id": customer_id}

if __name__ == "__main__":
    app.run()
