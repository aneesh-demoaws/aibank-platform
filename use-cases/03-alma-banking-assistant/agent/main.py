"""AI Bank Graph Agent — Multi-agent with Router + Specialist Nodes.
Deployed on AgentCore Runtime using Strands Graph pattern.

Multi-turn persistence strategy:
- AgentCore Memory (STM_ONLY) stores (USER, ASSISTANT) turns per (actor_id, session_id).
- Before each invocation we load the last K turns and inject them into the router prompt
  so the router can disambiguate short follow-ups (e.g. "yes" after a KYC upload prompt).
- After each invocation we persist the new (USER, ASSISTANT) pair.
- active_flow is also round-tripped via the payload so the router can pin the route
  mid-flow (loan application, KYC upload) without needing to re-reason from history.
"""
import os
import json
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent import GraphBuilder
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient

from config import REGION, ROUTER_MODEL, SPECIALIST_MODEL, MEMORY_ID, s3, UPLOAD_BUCKET
from tools.banking_tools import query_customer_data
from tools.kyc_tools import check_kyc_status, generate_kyc_upload_url
from tools.loan_tools import (
    check_loan_eligibility,
    calculate_loan,
    generate_loan_upload_url,
    submit_loan_application,
    check_loan_application_status,
)
from tools.nba_tools import (
    list_customer_nbas,
    get_financial_health_score,
    persist_life_event,
    get_customer_context_for_nba,
    get_nba_templates,
    persist_realtime_nba,
    execute_purchase,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Lazy singleton to avoid import-time network/boto init and to let retries
# recover if the first construction ever fails.
_memory_client: Optional[MemoryClient] = None


def _get_memory_client() -> Optional[MemoryClient]:
    """Return a cached MemoryClient, creating it on first use. Returns None on failure."""
    global _memory_client
    if _memory_client is not None:
        return _memory_client
    if not MEMORY_ID:
        logger.warning("MEMORY_ID is empty — STM disabled")
        return None
    try:
        _memory_client = MemoryClient(region_name=REGION)
        logger.info(f"MemoryClient initialized (region={REGION}, memory_id={MEMORY_ID})")
        return _memory_client
    except Exception as e:
        logger.error(f"MemoryClient init failed: {e}")
        return None



# ── Deterministic Persistence Node (no LLM) ──

# ── Node Definitions ──

router_agent = Agent(
    name="router",
    model=BedrockModel(model_id=ROUTER_MODEL, region_name=REGION),
    system_prompt="""You are a routing classifier for AI Bank. Given a customer message, optional [active_flow], and optional [Recent conversation], output EXACTLY one word:
- "loan"    — loan application, loan status, upload of loan documents, or any follow-up inside an active loan flow
- "kyc"     — KYC / identity verification / document upload for verification / follow-up inside an active KYC flow
- "banking" — balance, transactions, spending, transfers, goals, account info
- "next_best_action" — life events (travel, baby, marriage, job change, relocation), recommendations, "for you", financial health score, or any mention of trips/flights/expecting/pregnant/new job/promotion
- "faq"     — general product/rate/fee/bank-info questions

CRITICAL ROUTING RULES — apply strictly in order, first match wins:

1. EXPLICIT NEW INTENT beats everything. If the current message clearly expresses a new intent, classify by that intent and IGNORE active_flow and recent conversation:
   - Mentions "loan", "borrow", "EMI", or a loan amount ("200 BD", "BHD 1000") → "loan"
   - Mentions "KYC", "verify", "verification", "identity document", "passport", "CPR upload" → "kyc"
   - Mentions "balance", "transaction", "spending", "transfer", "goal", "statement", "account" → "banking"
   - Mentions travel (flight, trip, booked, airport, airline, holiday), baby (expecting, pregnant, baby, newborn), job (new job, promotion, resigned, salary increase), marriage (engaged, wedding), recommendations, "for you", financial health → "next_best_action"
   - General product/rate/fee/info question with no personal data → "faq"

2. Otherwise, if the current message is a short confirmation/denial/number (e.g. "yes", "no", "ok", "proceed", "sure", "1000", "12 months") then use active_flow + history to decide:
   - [active_flow: loan] OR recent conversation was about loan/EMI/salary certificate/bank statement → "loan"
   - [active_flow: kyc]  OR recent conversation was about KYC/identity documents/passport/CPR → "kyc"

3. Otherwise, if [active_flow: loan] → "loan"; if [active_flow: kyc] → "kyc".

4. Otherwise classify by the current message alone. Plain greetings like "hi", "hello", "hey" → "faq".

Output only the single lowercase word. No explanation, no punctuation.""",
    tools=[],
)

banking_agent = Agent(
    name="banking",
    model=BedrockModel(model_id=SPECIALIST_MODEL, region_name=REGION),
    system_prompt="""You are Alma Banking Assistant for AI Bank. You help customers query their banking data.

RULES:
1. Row-level security is enforced automatically by the tool
2. NEVER fabricate financial data — every number must come from query_customer_data
3. READ-ONLY — never attempt INSERT, UPDATE, DELETE
4. Write queries using the tables below
5. DATE LABELLING — Never write a month, quarter, or year string from prior knowledge or training data. Always derive date labels from the data:
   - The user prompt includes a [Today: YYYY-MM-DD (Weekday, Month YYYY)] block. This is the authoritative current date. When the SQL filtered by NOW()-relative expressions (e.g. "this month", "current month", DATE_FORMAT(NOW(), '%Y-%m-01'), CURDATE()), the heading MUST use the month/year from the [Today: ...] block.
   - If the SQL returned explicit transaction_date values, derive labels from MIN/MAX of those rows.
   - When in doubt, include the actual date range from the data (e.g. "May 2026" or "1–22 May 2026") rather than guessing.
   - Never default to "January", "January 2025", "January 2026", or any other hard-coded month. The current month is whatever [Today: ...] says it is.
6. PARTIAL-MONTH AWARENESS — If the current month from [Today: ...] appears in your trend or summary results, treat it as in-progress, not a closed period:
   - Label it explicitly as in-progress, e.g. "May 2026 (in progress, day X of Y)" where X = today's day-of-month and Y = total days in that month from [Today: ...].
   - Do NOT compute "X% reduction", "trending down significantly", "down 100%", or similar conclusions when one of the months in the comparison is the in-progress current month. Lower numbers there usually just mean fewer days have elapsed, not a real behavioural change.
   - If the customer explicitly asks for a trend that includes the current month, qualify the conclusion: "May is only N of M days complete, so this isn't directly comparable to closed months. At the current pace it would project to ~Z BHD."
   - Pro-rating is acceptable when called out: "60.890 BHD over 22 days projects to ~85.8 BHD for the full month of May", but only as a clearly-labelled projection, never as a stated fact.
   - Never describe an in-progress month as a "100% reduction" or "no spending" — describe it neutrally ("X transactions so far this month totalling Y BHD").

SCHEMA:
customers: customer_id, email, first_name, last_name, nationality, city, country, kyc_status
accounts: account_id, customer_id, account_type, account_number, balance, currency, status
transactions: transaction_id, account_id, transaction_type(credit|debit), amount, description, merchant_name, category_id, transaction_date
merchant_categories: category_id(CAT001-CAT014), category_name(Groceries|Dining|Entertainment|Transport|Shopping|Housing/Utilities|Health|Education|Salary|Telecom)
customer_goals: goal_id, customer_id, goal_type, goal_title, target_amount, current_amount, target_date, status
loan_applications: application_id, customer_id, loan_type, amount, status(approved|pending|rejected), monthly_payment, duration, interest, purpose
customer_financial_health: customer_id, score(0-100), band(excellent|good|fair|weak|critical), subscore_debt, subscore_savings, subscore_spending, subscore_income, subscore_credit, subscore_behavior, peer_percentile, trend_30d, calculated_at
customer_products: product_id, customer_id, product_type, product_name, amount_bhd, status(active|expired|cancelled), purchased_at, expires_at, source_nba_id
product_catalog: product_type, product_name, category, price_bhd, description, status(active|inactive)
customer_life_events: event_id, customer_id, event_type(travel|baby|job_change|marriage|relocation), detected_at, confidence, attributes(JSON), status(active|expired)
next_best_actions: action_id, customer_id, template_id, category(opportunity|wellness|security|engagement), priority, title, reasoning, status(active|dismissed|converted|expired), generated_at, expires_at
execution_audit: execution_id, action_id, customer_id, action_template, status(completed|failed|rolled_back), receipt_id, started_at

KEY: Salary = transaction_type='credit' AND category_id='CAT014'
CURRENCY: BHD (3 decimals)
JOIN: transactions → accounts ON account_id; accounts → customers ON customer_id

FEW-SHOT EXAMPLES:
Q: "What is my financial health score?"
SQL: SELECT score, band, subscore_debt, subscore_savings, subscore_spending, subscore_income, subscore_credit, subscore_behavior, peer_percentile FROM customer_financial_health WHERE customer_id = :cid

Q: "What products do I have?"
SQL: SELECT product_name, product_type, amount_bhd, status, purchased_at, expires_at FROM customer_products WHERE customer_id = :cid AND status = 'active'

Q: "Show my recommendations"
SQL: SELECT title, category, priority, reasoning, generated_at FROM next_best_actions WHERE customer_id = :cid AND status = 'active' AND (expires_at IS NULL OR expires_at > NOW()) ORDER BY priority DESC

Q: "What is my loan status?"
SQL: SELECT loan_type, amount, status, monthly_payment, duration, interest, purpose FROM loan_applications WHERE customer_id = :cid ORDER BY application_id DESC LIMIT 5

Q: "What did I buy recently?"
SQL: SELECT product_name, amount_bhd, purchased_at, receipt_id FROM customer_products cp JOIN execution_audit ea ON cp.source_nba_id = ea.action_id WHERE cp.customer_id = :cid ORDER BY purchased_at DESC LIMIT 5

Q: "Any life events detected?"
SQL: SELECT event_type, detected_at, attributes, status FROM customer_life_events WHERE customer_id = :cid ORDER BY detected_at DESC LIMIT 5

Q: "How much did I spend on dining this month?"
SQL: SELECT SUM(t.amount) as total FROM transactions t JOIN accounts a ON t.account_id = a.account_id WHERE a.customer_id = :cid AND t.transaction_type = 'debit' AND t.category_id = 'CAT002' AND t.transaction_date >= DATE_FORMAT(NOW(), '%Y-%m-01')""",
    tools=[query_customer_data],
)

kyc_agent = Agent(
    name="kyc",
    model=BedrockModel(model_id=SPECIALIST_MODEL, region_name=REGION),
    system_prompt="""You are the KYC verification assistant for AI Bank.

WORKFLOW:
1. ALWAYS call check_kyc_status FIRST
2. Based on status:
   - NOT_STARTED/PENDING: Ask if they want to upload. Need 2 identity + 1 address docs.
   - PROCESSING: Tell them docs are being analyzed.
   - VERIFIED: Congratulate them.
   - REJECTED: Explain mismatch, offer re-upload.
3. Only call generate_kyc_upload_url if customer explicitly confirms upload.
4. Include [KYC_UPLOAD] marker when providing upload form.

Supported docs: Passport, CPR, Driving License (identity); Driving License, Utility Bill (address).""",
    tools=[check_kyc_status, generate_kyc_upload_url],
)

loan_agent = Agent(
    name="loan",
    model=BedrockModel(model_id=SPECIALIST_MODEL, region_name=REGION),
    system_prompt="""You are the AI Bank Loan Agent. Guide customers through loan applications step by step.

PRODUCTS:
- Instant Money: BHD 100–2000, 1–12 months, 7.0% p.a., auto-approved
- Personal Finance: BHD 1000–25000, 6–60 months, 5.5% p.a., officer review

FLOW (one step per turn):
1. Collect: loan_type, amount, tenure, purpose. If missing, ASK (don't assume).
2. Call check_loan_eligibility. **If the tool returns `eligible: false`, STOP — explain the `reason` to the customer and do NOT proceed to calculate_loan, upload, or submit. Never override a negative eligibility result.** If eligible, call calculate_loan and show EMI.
3. Ask: "Would you like to proceed? You'll need a salary certificate and last 3 months of bank statements."
4. On confirm: call generate_loan_upload_url with document_type="salary_certificate".
   **UI CONTRACT — strict.** The frontend renders an in-chat document uploader widget whenever it sees a marker of the form `[UPLOAD_REQUEST:<document_type>]`. Your visible reply for this step MUST:
     - include the marker `[UPLOAD_REQUEST:salary_certificate]` on its own line,
     - include a short friendly instruction (e.g. "Please upload your salary certificate using the uploader below."),
     - include the application ID for reference,
     - **NEVER include the raw `uploadUrl`, `key`, presigned-URL strings, S3 paths, or any https:// link in the visible reply.** The URL is for the backend only.
   If the tool returns `success: false`, apologise, explain the error, and stop.
5. On customer signalling the salary_certificate is uploaded: call generate_loan_upload_url with document_type="bank_statement".
   Same UI CONTRACT applies — emit `[UPLOAD_REQUEST:bank_statement]`, short instruction (mention "last 3 months of bank statements"), application ID on its own line, and NO raw URL. **The application_id line is REQUIRED on every upload-request reply so the frontend can't confuse it with a previous loan.**
6. On customer signalling the bank_statement is uploaded: call submit_loan_application and confirm the submission.

RULES:
- ONE step per turn. Never skip ahead.
- Customer_id is in the message prefix [Customer ID: CUSTxxxxxxxx]. Extract it.
- Amounts in BHD, 3 decimal places.
- If eligibility fails, explain why and stop.
- Never paste presigned URLs, S3 object keys, or temporary credentials into the chat.
- If the customer asks about the STATUS of an existing application (e.g. "what's the status of my loan", "has my loan been approved yet", "check application AIB-…"), call check_loan_application_status FIRST. Prefer the specific application_id if the customer mentions one; otherwise call with customer_id only to get the most recent. Summarise the response in plain English — status, amount, tenure, purpose, submitted/updated dates.""",
    tools=[check_loan_eligibility, calculate_loan, generate_loan_upload_url, submit_loan_application, check_loan_application_status],
)

faq_agent = Agent(
    name="faq",
    model=BedrockModel(model_id=ROUTER_MODEL, region_name=REGION),
    system_prompt="""You are AI Bank's FAQ assistant. Answer general banking questions:
- Products: Savings, Current, Premium accounts; Instant Money loans (7% p.a.); Personal Finance (5.5% p.a.)
- Rates: Savings 3.5%, Personal Loan 5.5%, Instant Money 7.0%
- Limits: Instant Money BHD 100-2000, Personal BHD 1000-25000
- KYC: Required for loans. Need 2 ID docs + 1 address doc.
- Channels: Online banking, mobile app, branches across Bahrain
Be concise and helpful. If the question needs account-specific data, suggest they ask about their balance or transactions.""",
    tools=[],
)

# ── NBA Agents (Next Best Action — life events, recommendations, FHS) ──

nba_agent = Agent(
    name="nba",
    model=BedrockModel(model_id="eu.amazon.nova-pro-v1:0", region_name=REGION),
    system_prompt="""You are the NBA (Next Best Action) agent for AI Bank.

FIRST: CLASSIFY THE MESSAGE — pick exactly ONE scenario:
- If the message mentions ANY of: travel, trip, flight, booked, Dubai, London, baby, expecting, pregnant, new job, promotion, resigned, married, engaged, wedding, moving, relocation → SCENARIO 1 (LIFE EVENT)
- If the message asks "what are my recommendations", "for you", "financial health", "my score" → SCENARIO 2 (QUERY)
- Default to SCENARIO 1 if unsure.

═══════════════════════════════════════════════════════════
SCENARIO 1 — LIFE EVENT (this is the most common case):
═══════════════════════════════════════════════════════════
You MUST call these tools IN THIS EXACT ORDER. Do NOT skip any step.
Do NOT call list_customer_nbas. Do NOT show existing batch NBAs.

Step 1: persist_life_event (record the event)
Step 2: get_customer_context_for_nba (get their financial data)
Step 3: get_nba_templates (get available templates)
Step 4: persist_realtime_nba (save the ONE best recommendation for this event)
         - entity_ref format: "destination_YYYY-MM-DD" for travel, "baby_YYYY-MM" for baby
         - Pick the SINGLE most relevant template for this specific event
         - Do NOT persist multiple NBAs — just the ONE best match
Step 5: Respond to the customer (ONLY after step 4 succeeds)
         - Acknowledge the event warmly
         - Mention the ONE recommendation you just created
         - Cite 1-2 numbers from their context (balance, FHS)
         - End with a purchase offer: "Would you like me to set up [product] for BHD [price]?"
         - Keep it to 3-4 sentences max

CRITICAL: Your response should mention ONLY the one recommendation you persisted.
Do NOT list other batch recommendations. The customer sees those on their For You page.

═══════════════════════════════════════════════════════════
SCENARIO 2 — QUERY (only when explicitly asked):
═══════════════════════════════════════════════════════════
1. Call list_customer_nbas
2. Call get_financial_health_score
3. Summarize conversationally

PURCHASE RULE (CRITICAL — money leaves the customer's account):
- NEVER call execute_purchase unless the previous assistant message was a confirmation question.
- ANY message about buying/purchasing/setting up a product → ALWAYS respond with confirmation first:
  "I can set up [product] for BHD [price]. This will be debited from your account. Shall I proceed?"
- Only call execute_purchase AFTER you have already asked the confirmation question AND
  the customer's CURRENT message is a short reply (under 4 words): "yes", "sure", "go ahead", "proceed", "do it", "ok", "confirm"
- If the customer's message is 4+ words → it is NEVER a confirmation. Always ask first.
- NEVER skip the confirmation step. Even if the customer says "purchase it now" — ask first.
- One purchase per confirmation. Never batch multiple purchases.

RULES:
- Ground all numbers in tool output. Never invent figures.
- Be warm, use first name, culturally appropriate (MENAT region).
- Extract customer_id from the [Customer ID: ...] prefix in the message.""",
    tools=[
        list_customer_nbas,
        get_financial_health_score,
        persist_life_event,
        get_customer_context_for_nba,
        get_nba_templates,
        persist_realtime_nba,
        execute_purchase,
    ],
)


# ── Graph Construction ──

def build_graph():
    """Build the conditional routing graph."""
    builder = GraphBuilder()
    builder.add_node(router_agent, "router")
    builder.add_node(banking_agent, "banking")
    builder.add_node(kyc_agent, "kyc")
    builder.add_node(loan_agent, "loan")
    builder.add_node(faq_agent, "faq")
    builder.add_node(nba_agent, "next_best_action")

    def _router_output(state) -> str:
        res = state.results.get("router")
        return str(res.result).strip().lower() if res else ""

    builder.add_edge("router", "banking", condition=lambda s: "banking" in _router_output(s))
    builder.add_edge("router", "kyc",     condition=lambda s: "kyc"     in _router_output(s))
    builder.add_edge("router", "loan",    condition=lambda s: "loan"    in _router_output(s))
    builder.add_edge("router", "faq",     condition=lambda s: "faq"     in _router_output(s))
    builder.add_edge("router", "next_best_action", condition=lambda s: "next_best_action" in _router_output(s))

    builder.set_entry_point("router")
    # Note: GraphBuilder in strands-agents 1.x does not expose set_execution_timeout;
    # AgentCore Runtime enforces the overall invocation timeout.

    return builder.build()


graph = build_graph()


# ── STM helpers ──

def _load_history(customer_id: str, session_id: str, k: int = 3) -> str:
    """Load last K turns from AgentCore Memory, return as a formatted string (oldest→newest)."""
    client = _get_memory_client()
    if client is None:
        return ""
    try:
        turns = client.get_last_k_turns(
            memory_id=MEMORY_ID,
            actor_id=customer_id,
            session_id=session_id,
            k=k,
        )
        # get_last_k_turns returns newest-first; we want oldest-first in the prompt.
        turns = list(reversed(turns or []))
        lines = []
        for turn in turns:
            for evt in turn:
                role = (evt.get("role") or "").lower()
                text = (evt.get("content") or {}).get("text", "")
                if role in ("user", "assistant") and text:
                    lines.append(f"{role}: {text[:200]}")
        if not lines:
            logger.info(f"STM: no prior turns for actor={customer_id} session={session_id}")
            return ""
        history = "\n".join(lines[-(k * 2):])  # at most k*2 lines
        logger.info(f"STM: loaded {len(lines)} lines for actor={customer_id} session={session_id}")
        return history
    except Exception as e:
        logger.warning(f"STM load failed (actor={customer_id} session={session_id}): {e}")
        return ""


def _save_turn(customer_id: str, session_id: str, user_msg: str, assistant_msg: str) -> None:
    client = _get_memory_client()
    if client is None:
        return
    try:
        resp = client.save_conversation(
            memory_id=MEMORY_ID,
            actor_id=customer_id,
            session_id=session_id,
            messages=[(user_msg[:500], "USER"), (assistant_msg[:500], "ASSISTANT")],
        )
        event_id = (resp or {}).get("eventId") or (resp or {}).get("event", {}).get("eventId", "")
        logger.info(f"STM: saved turn for actor={customer_id} session={session_id} event={event_id}")
    except Exception as e:
        logger.warning(f"STM save failed (actor={customer_id} session={session_id}): {e}")


# ── Loan upload widget plumbing ──
# Contract with the frontend (banking/dashboard.html):
#   response JSON may include `loan_upload: [{document_type, upload_url}]`.
#   The widget renders an in-chat file picker keyed off those entries and
#   PUTs the file directly to `upload_url` (an S3 presigned URL).
# We build these server-side (in the entrypoint, not in the tool) by:
#   1. Scanning the final answer for `[UPLOAD_REQUEST:<document_type>]` markers.
#   2. Finding the application id from the answer or recent STM history.
#   3. Regenerating a fresh presigned URL using the same S3 key scheme the
#      `generate_loan_upload_url` tool uses (so the key the tool already wrote
#      to its DynamoDB record matches what the widget uploads to).
# The marker still appears in `answer`; the frontend strips it if desired.

_UPLOAD_MARKER_RE = re.compile(r"\[UPLOAD_REQUEST:([a-zA-Z0-9_]+)\]")
_APPLICATION_ID_RE = re.compile(r"AIB-\d{8}-[A-Z0-9]+")


def _find_application_id(answer: str, history_context: str) -> str:
    """Locate the CURRENT loan application ID.

    Priority order:
      1. Answer (most authoritative — this is what the agent is saying *now*).
         Use the LAST match in case the agent accidentally references an older app.
      2. History context — use the LAST (most recent) match, not the first,
         because STM accumulates across loans for the same session.

    Returns the empty string if no application_id can be confidently derived.
    """
    # 1. Current answer wins — prefer the LAST match if multiple appear
    if answer:
        matches = _APPLICATION_ID_RE.findall(answer)
        if matches:
            return matches[-1]
    # 2. History fallback — LAST match (most recent turn), not the first
    if history_context:
        matches = _APPLICATION_ID_RE.findall(history_context)
        if matches:
            return matches[-1]
    return ""


def _build_loan_uploads(answer: str, history_context: str, customer_id: str) -> list:
    """Produce the `loan_upload` array for the response, one entry per marker."""
    uploads = []
    markers = _UPLOAD_MARKER_RE.findall(answer or "")
    if not markers:
        return uploads
    application_id = _find_application_id(answer, history_context)
    if not application_id:
        logger.warning(
            "Upload marker(s) %s present but no application_id could be found "
            "in answer or history — cannot build loan_upload widget payload",
            markers,
        )
        return uploads
    for document_type in markers:
        filename = document_type.replace("_", "-") + ".pdf"
        key = f"documents/input/{customer_id}/{application_id}/{document_type}/{filename}"
        try:
            url = s3.generate_presigned_url(
                "put_object",
                Params={"Bucket": UPLOAD_BUCKET, "Key": key, "ContentType": "application/pdf"},
                ExpiresIn=900,
            )
            uploads.append({
                "document_type": document_type,
                "upload_url": url,
                "application_id": application_id,
            })
        except Exception as e:
            logger.warning(f"Failed to build presigned URL for {document_type}: {e}")
    return uploads


# ── AgentCore App ──

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    user_message = payload.get("prompt", "Hello")
    customer_id = payload.get("customer_id", "")
    customer_first_name = payload.get("customer_first_name", "")
    session_id = payload.get("session_id", "default")
    active_flow = payload.get("active_flow", "")

    if not customer_id:
        return {"answer": "Authentication required. Please log in."}

    logger.info(
        f"invoke: customer={customer_id} session={session_id} "
        f"active_flow={active_flow!r} msg={user_message[:80]!r}"
    )

    # ── Load conversation history from STM ──
    history_context = _load_history(customer_id, session_id, k=3)

    # Build the prompt for the router / graph.
    # Order matters: metadata first, then conversation history, then the user message
    # at the very end so it's the most salient token block for the model.
    # [Today: ...] gives the model a concrete date anchor so it cannot default to a
    # training-prior month label (e.g. "January 2025") for "this month" queries.
    # The "day X of Y" suffix lets specialist agents reason about partial months.
    _now = datetime.now(ZoneInfo("Asia/Bahrain"))
    # Total days in the current month (works for any month/year incl. leap years)
    if _now.month == 12:
        _next_month_first = _now.replace(year=_now.year + 1, month=1, day=1)
    else:
        _next_month_first = _now.replace(month=_now.month + 1, day=1)
    _days_in_month = (_next_month_first - _now.replace(day=1)).days
    today_str = _now.strftime("%Y-%m-%d (%A, %B %Y") + f" — day {_now.day} of {_days_in_month})"
    parts = [
        f"[Customer ID: {customer_id}]",
        f"[Today: {today_str}]",
    ]
    if active_flow:
        parts.append(f"[active_flow: {active_flow}]")
    if history_context:
        parts.append(f"[Recent conversation:\n{history_context}\n]")
    parts.append(f"User: {user_message}")
    prompt = "\n".join(parts)

    # Shared state passed to every node (tools read customer_id from here).
    shared_state = {
        "customer_id": customer_id,
        "customer_first_name": customer_first_name,
        "session_id": session_id,
        "active_flow": active_flow,
    }

    try:
        result = graph(prompt, invocation_state=shared_state)

        # Extract the answer from the last completed specialist node.
        # node.result is a NodeResult wrapper; the real AgentResult(s) are
        # accessible via get_agent_results(). We grab the text from the last
        # assistant message.
        answer = ""
        for node in reversed(result.execution_order):
            if node.node_id == "router" or not node.result:
                continue
            try:
                agent_results = node.result.get_agent_results()
            except Exception:
                agent_results = []
            if not agent_results:
                # Fall back to the raw .result attr if the node wraps a non-Agent result.
                inner = getattr(node.result, "result", None)
                if inner is not None:
                    agent_results = [inner]
            for agent_result in agent_results:
                msg = getattr(agent_result, "message", None)
                if isinstance(msg, dict) and "content" in msg:
                    for block in msg["content"]:
                        if isinstance(block, dict) and "text" in block and block["text"]:
                            answer = block["text"]
                            break
                elif msg is not None and hasattr(msg, "content"):
                    for block in msg.content:
                        if hasattr(block, "text") and block.text:
                            answer = block.text
                            break
                if answer:
                    break
            if answer:
                break

        if not answer:
            answer = "I'm here to help! What would you like to know about your accounts?"



        # Strip any reasoning leakage.
        answer = re.sub(r"<thinking>[\s\S]*?</thinking>", "", answer).strip()

        # ── Decide the active_flow for the next turn ──
        router_result = ""
        if result.results.get("router"):
            router_result = str(result.results["router"].result).strip().lower()

        answer_lc = answer.lower()

        new_active_flow = ""
        if "loan" in router_result:
            # Stay in loan until terminal outcome.
            if "submitted successfully" in answer_lc or "not eligible" in answer_lc:
                new_active_flow = ""
            else:
                new_active_flow = "loan"
        elif "kyc" in router_result:
            # Clear the flow whenever KYC is effectively done:
            # - explicit status confirmations (VERIFIED / already verified / fully verified / complete)
            # - processing state (docs submitted, nothing more for the user to do)
            # Otherwise stay in KYC.
            kyc_done_markers = (
                "status: verified",
                "status: ✅ verified",
                "status is verified",
                "already verified",
                "fully verified",
                "is complete",
                "verification is complete",
                "kyc status: processing",
                "status: processing",
            )
            if any(m in answer_lc for m in kyc_done_markers):
                new_active_flow = ""
            else:
                new_active_flow = "kyc"

        logger.info(
            f"invoke: route={router_result!r} new_active_flow={new_active_flow!r} "
            f"answer_preview={answer[:80]!r}"
        )

        # Build loan upload widget payload (if any `[UPLOAD_REQUEST:...]` markers).
        loan_upload = _build_loan_uploads(answer, history_context, customer_id)
        if loan_upload:
            logger.info(
                f"invoke: producing loan_upload={[u['document_type'] for u in loan_upload]} "
                f"application_id={loan_upload[0].get('application_id')}"
            )

        # ── Save to STM ──
        _save_turn(customer_id, session_id, user_message, answer)

        response = {
            "answer": answer,
            "customer_id": customer_id,
            "active_flow": new_active_flow,
        }
        if loan_upload:
            response["loan_upload"] = loan_upload
        return response

    except Exception as e:
        logger.error(f"Graph execution error: {e}", exc_info=True)
        return {
            "answer": "I'm sorry, something went wrong. Please try again.",
            "customer_id": customer_id,
        }


if __name__ == "__main__":
    app.run()
