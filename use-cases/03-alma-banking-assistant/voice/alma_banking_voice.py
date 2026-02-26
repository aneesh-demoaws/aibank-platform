"""
Alma Banking Voice Assistant — WebSocket /banking-voice on EC2
Authenticated voice interface using Amazon Nova Sonic.
Runs on port 8091 alongside Alma Public voice (8090).
"""
import asyncio
import json
import logging
import os
import re

import boto3
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from strands import tool
from strands.experimental.bidi import BidiAgent
from strands.experimental.bidi.models import BidiNovaSonicModel
from strands.experimental.bidi.types.events import BidiAudioInputEvent
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig, RetrievalConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Config
SONIC_REGION = os.environ.get("ALMA_SONIC_REGION", "eu-north-1")
VOICE_NAME = os.environ.get("ALMA_VOICE", "matthew")
MEMORY_ID = os.environ.get("BEDROCK_AGENTCORE_MEMORY_ID", "alma_banking_assistant_mem-ijns9pFcc6")
MEMORY_REGION = os.environ.get("MEMORY_REGION", "eu-west-1")
DB_REGION = os.environ.get("DB_REGION", "me-south-1")
CLUSTER_ARN = os.environ.get("CLUSTER_ARN", "arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking")
SECRET_ARN = os.environ.get("SECRET_ARN", "arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ")
DB_NAME = os.environ.get("DB_NAME", "corebanking")

rds = boto3.client("rds-data", region_name=DB_REGION)

SYSTEM_PROMPT = """You are Alma, the AI Banking voice assistant for AI Bank Bahrain.

You have memory of past conversations. Use what you know about the customer to provide better, more personalized responses.

## ABSOLUTE CRITICAL RULE: ZERO TOLERANCE FOR HALLUCINATION
- You MUST NEVER provide ANY financial data unless it comes DIRECTLY from a database query result
- You MUST NEVER make up account numbers, balances, transaction amounts, dates, or merchant names
- If a query fails or returns no data, say so clearly — never guess
- Every single financial detail MUST come from a successful query_customer_data tool call

## VOICE RESPONSE STYLE
- Keep responses concise: 2-3 sentences max for voice
- Be warm and professional, address the customer by name
- ALWAYS complete your sentences fully
- For currency, say "Bahraini Dinars" or "BHD" clearly

LANGUAGE: Always respond in English only. Never switch to Hindi or any other language.

## Database Schema

<accounts_table>
- account_id: Primary key, varchar(20)
- customer_id: Foreign key to customers, varchar(12)
- account_type: enum('savings','current','premium','business')
- account_number: varchar(16)
- balance: decimal(15,3)
- currency: varchar(3), default 'BHD'
- status: enum('ACTIVE','INACTIVE','SUSPENDED','CLOSED')
- opening_date: date
</accounts_table>

<transactions_table>
- transaction_id: Primary key
- account_id: Foreign key to accounts
- transaction_type: enum('credit','debit')
- amount: decimal(12,3)
- currency: varchar(3), default 'BHD'
- description: varchar(255)
- balance_after: decimal(15,3)
- transaction_date: timestamp
- merchant_name: Optional varchar(255)
- category_id: Optional varchar(10)
- mcc_code: Optional varchar(4)
</transactions_table>

<merchant_categories_table>
- category_id: Primary key
- category_name: varchar(100)
Categories: 'Groceries & Food', 'Restaurants & Dining', 'Housing & Utilities', 'Transportation & Travel', 'Entertainment & Recreation', 'Shopping & Retail', 'Healthcare & Medical', 'Education & Learning', 'Financial Services', 'Telecommunications', 'Personal Care & Beauty', 'Government & Legal', 'Charity & Donations', 'Miscellaneous'
</merchant_categories_table>

<customer_goals_table>
- goal_id: Primary key
- customer_id: Foreign key
- goal_type: enum type
- goal_title: varchar
- target_amount, current_amount: decimal
- target_date: date
- status: enum
</customer_goals_table>

## MySQL Query Guidelines
1. Use column names WITHOUT quotes unless they are MySQL reserved words
2. Query only the specific columns needed
3. Limit results to 10 rows unless otherwise specified
4. Use CURDATE() for "today"
5. For merchant searches, ALWAYS use LIKE with wildcards: merchant_name LIKE '%keyword%'
6. Only use columns that exist in the tables above

KEY JOINS: accounts ON customer_id, transactions ON account_id, merchant_categories ON category_id

## SQL Query Examples (Few-Shot Learning)

**Account Information:**
Question: "What is my balance?"
SQL: SELECT account_number, account_type, balance, currency FROM accounts WHERE customer_id = '{customer_id}' AND status = 'ACTIVE'

Question: "What are all my accounts?"
SQL: SELECT account_number, account_type, balance, currency, status FROM accounts WHERE customer_id = '{customer_id}'

**Transaction History:**
Question: "Show me my recent transactions"
SQL: SELECT t.transaction_date, t.description, t.merchant_name, t.amount, t.transaction_type, t.balance_after FROM transactions t JOIN accounts a ON t.account_id = a.account_id WHERE a.customer_id = '{customer_id}' ORDER BY t.transaction_date DESC LIMIT 10

**Spending Analysis:**
Question: "How much did I spend on groceries last month?"
SQL: SELECT SUM(t.amount) as total_spent FROM transactions t JOIN accounts a ON t.account_id = a.account_id JOIN merchant_categories mc ON t.category_id = mc.category_id WHERE a.customer_id = '{customer_id}' AND t.transaction_type = 'debit' AND mc.category_name = 'Groceries & Food' AND t.transaction_date >= DATE_SUB(DATE_SUB(CURDATE(), INTERVAL DAY(CURDATE())-1 DAY), INTERVAL 1 MONTH) AND t.transaction_date < DATE_SUB(CURDATE(), INTERVAL DAY(CURDATE())-1 DAY)

Question: "Where do I spend the most money?"
SQL: SELECT merchant_name, SUM(t.amount) as total_spent FROM transactions t JOIN accounts a ON t.account_id = a.account_id WHERE a.customer_id = '{customer_id}' AND t.transaction_type = 'debit' AND t.merchant_name IS NOT NULL GROUP BY merchant_name ORDER BY total_spent DESC LIMIT 10

Question: "When did I last pay at Lulu?"
SQL: SELECT t.transaction_date, t.merchant_name, t.amount FROM transactions t JOIN accounts a ON t.account_id = a.account_id WHERE a.customer_id = '{customer_id}' AND t.merchant_name LIKE '%lulu%' ORDER BY t.transaction_date DESC LIMIT 1

Question: "Which merchants do I spend the most with?"
SQL: SELECT merchant_name, COUNT(*) as transaction_count, SUM(t.amount) as total_spent, AVG(t.amount) as avg_spent FROM transactions t JOIN accounts a ON t.account_id = a.account_id WHERE a.customer_id = '{customer_id}' AND t.transaction_type = 'debit' AND t.merchant_name IS NOT NULL GROUP BY merchant_name ORDER BY total_spent DESC LIMIT 10

Question: "How much did I earn this month?"
SQL: SELECT SUM(t.amount) as total_earned FROM transactions t JOIN accounts a ON t.account_id = a.account_id WHERE a.customer_id = '{customer_id}' AND t.transaction_type = 'credit' AND t.transaction_date >= DATE_SUB(CURDATE(), INTERVAL DAY(CURDATE())-1 DAY)

Question: "How much did I spend by category this month?"
SQL: SELECT mc.category_name, SUM(t.amount) as total, COUNT(*) as count FROM transactions t JOIN accounts a ON t.account_id = a.account_id JOIN merchant_categories mc ON t.category_id = mc.category_id WHERE a.customer_id = '{customer_id}' AND t.transaction_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01') GROUP BY mc.category_name ORDER BY total DESC

**Savings Goals:**
Question: "What are my savings goals?"
SQL: SELECT goal_title, goal_type, target_amount, current_amount, target_date, status FROM customer_goals WHERE customer_id = '{customer_id}'

## MANDATORY PROCESS FOR FINANCIAL QUERIES:
1. Execute SQL query using query_customer_data tool with EXACT syntax from examples above
2. ONLY provide information from the query result
3. If query returns no data: say "No data found" clearly
4. If query fails: say "I'm having trouble accessing that information"
5. Follow the exact SQL patterns from the examples above"""

# Customer context stored per WebSocket connection
_ws_customer = {}


def _enforce_row_level_security(sql: str, customer_id: str) -> str:
    if not re.match(r'^CUST\d{8}$', customer_id):
        return "SELECT 'INVALID_CUSTOMER_ID' as error"
    scoped = sql
    for orig, repl in [('customer_goals', 'scoped_goals'), ('merchant_categories', 'merchant_categories'),
                       ('transactions', 'scoped_transactions'), ('accounts', 'scoped_accounts'), ('customers', 'scoped_customers')]:
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
  SELECT t.transaction_id, t.account_id, a.customer_id, CAST(t.transaction_type AS CHAR) as transaction_type, t.amount, t.currency, t.description, t.balance_after, t.transaction_date, t.merchant_name, t.category_id, t.mcc_code
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
            return f"ERROR: {kw} not allowed."

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
        return result
    except Exception as e:
        logger.error(f"Query error: {e}")
        return f"Query error: {str(e)}"


def get_customer_id(email: str) -> tuple[str, str]:
    """Look up customer_id and first_name from email."""
    resp = rds.execute_statement(
        resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
        sql="SELECT customer_id, first_name FROM customers WHERE email = :e LIMIT 1",
        parameters=[{"name": "e", "value": {"stringValue": email}}],
        includeResultMetadata=True
    )
    if resp["records"]:
        rec = resp["records"][0]
        return rec[0]["stringValue"], rec[1]["stringValue"]
    return "", ""


app = FastAPI(title="Alma Banking Voice")


@app.get("/health")
async def health():
    return JSONResponse({"status": "healthy", "service": "alma-banking-voice"})


@app.websocket("/banking-voice")
async def banking_voice_chat(websocket: WebSocket):
    await websocket.accept()
    logger.info("Banking voice WebSocket connected")

    # Authenticate from query param (cookie is HttpOnly, cross-domain won't send it)
    email = websocket.query_params.get("token", "")
    if not email:
        await websocket.send_json({"type": "error", "message": "Authentication required. Please log in first."})
        await websocket.close(code=4001)
        return

    customer_id, first_name = get_customer_id(email)
    if not customer_id:
        await websocket.send_json({"type": "error", "message": "Customer not found."})
        await websocket.close(code=4002)
        return

    logger.info(f"Authenticated voice session: {first_name} ({customer_id})")

    # Build personalized system prompt
    personal_prompt = f"{SYSTEM_PROMPT}\n\nCurrent customer: {first_name} (ID: {customer_id}). Always address them as {first_name}."

    model = BidiNovaSonicModel(
        model_id="amazon.nova-2-sonic-v1:0",
        provider_config={
            "audio": {"voice": VOICE_NAME},
            "inference": {"max_tokens": 8192, "temperature": 0.7, "top_p": 0.9},
            "turn_detection": {"endpointingSensitivity": "LOW"}
        },
        client_config={"region": SONIC_REGION}
    )

    # AgentCore Memory — STM + LTM per customer
    session_id = f"voice-{customer_id}-{int(asyncio.get_event_loop().time())}"
    memory_config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID,
        session_id=session_id,
        actor_id=customer_id,
        retrieval=RetrievalConfig(short_term=True, long_term=True),
    )
    session_manager = AgentCoreMemorySessionManager(memory_config, region_name=MEMORY_REGION)

    agent = BidiAgent(model=model, tools=[query_customer_data], system_prompt=personal_prompt, session_manager=session_manager)
    input_queue = asyncio.Queue()
    stop_event = asyncio.Event()

    async def ws_input():
        while not stop_event.is_set():
            try:
                data = await asyncio.wait_for(input_queue.get(), timeout=0.1)
                if data is None:
                    return None
                return data
            except asyncio.TimeoutError:
                continue
        return None

    async def ws_output(event):
        try:
            t = event.get("type", "")
            if t == "bidi_audio_stream":
                await websocket.send_json({"type": "audio", "data": event["audio"]})
            elif t == "bidi_transcript_stream":
                await websocket.send_json({
                    "type": "transcript", "role": event.get("role", ""),
                    "text": event.get("text", ""), "is_final": event.get("is_final", False),
                })
            elif t == "bidi_interruption":
                await websocket.send_json({"type": "interruption"})
            elif t == "bidi_response_complete":
                await websocket.send_json({"type": "response_end"})
            elif t == "bidi_error":
                await websocket.send_json({"type": "error", "message": event.get("message", "")})
        except Exception as e:
            logger.error(f"Output error: {e}")

    async def receive_audio():
        try:
            while not stop_event.is_set():
                msg = await websocket.receive_text()
                data = json.loads(msg)
                if data.get("type") == "audio":
                    await input_queue.put(BidiAudioInputEvent(
                        audio=data["data"], format="pcm", sample_rate=16000, channels=1
                    ))
                elif data.get("type") == "stop":
                    stop_event.set()
                    await input_queue.put(None)
                    break
        except WebSocketDisconnect:
            stop_event.set()
            await input_queue.put(None)
        except Exception as e:
            logger.error(f"Receive error: {e}")
            stop_event.set()
            await input_queue.put(None)

    try:
        recv_task = asyncio.create_task(receive_audio())
        await agent.run(inputs=[ws_input], outputs=[ws_output])
    except Exception as e:
        logger.error(f"Agent error: {e}")
    finally:
        stop_event.set()
        recv_task.cancel()
        try:
            session_manager.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8091)
