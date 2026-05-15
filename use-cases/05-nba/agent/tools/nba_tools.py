"""NBA Tools — MCP tools for the NBA specialist agents within Alma's graph.

Tools are split into:
  - Life-Event Detection (used by life_event_agent node)
  - NBA Real-Time Generation (used by nba_realtime_agent node)
  - NBA Read (used by nba_query_agent node for "show my recommendations" etc.)
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from strands import tool

from config import rds, CLUSTER_ARN, SECRET_ARN, DB_NAME, REGION

logger = logging.getLogger(__name__)

NOVA_MODEL = "eu.amazon.nova-2-lite-v1:0"


def _sql(sql, params=None):
    kwargs = dict(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME, sql=sql)
    if params:
        kwargs['parameters'] = params
    return rds.execute_statement(**kwargs).get('records', [])


def _val(cell):
    if cell.get('isNull'):
        return None
    return list(cell.values())[0]


# ═══════════════════════════════════════════════════════════════════════════════
# NBA READ TOOLS (for nba_query_agent — answers "show my recommendations" etc.)
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def list_customer_nbas(customer_id: str) -> str:
    """List the customer's active Next Best Action recommendations.

    Args:
        customer_id: The customer ID (e.g. CUST20250100)

    Returns:
        JSON array of active NBAs with title, category, priority, reasoning.
    """
    rows = _sql(
        "SELECT action_id, title, category, priority, reasoning, confidence "
        "FROM next_best_actions WHERE customer_id=:cid AND status='active' "
        "ORDER BY priority DESC LIMIT 8",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    nbas = []
    for r in rows:
        nbas.append({
            'action_id': _val(r[0]),
            'title': _val(r[1]),
            'category': _val(r[2]),
            'priority': _val(r[3]),
            'reasoning': (_val(r[4]) or '')[:200],
            'confidence': _val(r[5]),
        })
    return json.dumps(nbas, indent=2)


@tool
def get_financial_health_score(customer_id: str) -> str:
    """Get the customer's Financial Health Score with subscores and explanation.

    Args:
        customer_id: The customer ID

    Returns:
        JSON with score, band, 6 subscores, and AI explanation.
    """
    rows = _sql(
        "SELECT score, band, subscore_debt, subscore_savings, subscore_spending, "
        "subscore_income, subscore_credit, subscore_behavior, explanation "
        "FROM customer_financial_health WHERE customer_id=:cid",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    if not rows:
        return json.dumps({"error": "FHS not yet computed for this customer"})
    r = rows[0]
    return json.dumps({
        'score': _val(r[0]), 'band': _val(r[1]),
        'subscores': {
            'debt': _val(r[2]), 'savings': _val(r[3]), 'spending': _val(r[4]),
            'income': _val(r[5]), 'credit': _val(r[6]), 'behavior': _val(r[7]),
        },
        'explanation': (_val(r[8]) or '')[:300],
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# LIFE-EVENT DETECTION TOOL (used by life_event_agent)
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def persist_life_event(customer_id: str, event_type: str, confidence: float, attributes: str) -> str:
    """Persist a detected life event to the database and emit to EventBridge.

    Args:
        customer_id: The customer ID
        event_type: One of: travel, new_baby, job_change, income_change, marriage, relocation
        confidence: Detection confidence (0.0-1.0)
        attributes: JSON string of extracted attributes (dates, destinations, etc.)

    Returns:
        JSON with event_id and status.
    """
    import boto3
    events_client = boto3.client('events', region_name=REGION)

    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    attrs = json.loads(attributes) if isinstance(attributes, str) else attributes

    _sql(
        "INSERT INTO customer_life_events (event_id, customer_id, event_type, "
        "detection_source, confidence, attributes, status) "
        "VALUES (:eid, :cid, :etype, 'app_chat', :conf, :attrs, 'active')",
        [
            {'name': 'eid', 'value': {'stringValue': event_id}},
            {'name': 'cid', 'value': {'stringValue': customer_id}},
            {'name': 'etype', 'value': {'stringValue': event_type}},
            {'name': 'conf', 'value': {'doubleValue': confidence}},
            {'name': 'attrs', 'value': {'stringValue': json.dumps(attrs)}},
        ]
    )

    # Emit to EventBridge for async consumers
    events_client.put_events(Entries=[{
        'Source': 'aibank.nba',
        'DetailType': 'life_event.detected',
        'Detail': json.dumps({
            'event_id': event_id,
            'customer_id': customer_id,
            'event_type': event_type,
            'confidence': confidence,
            'attributes': attrs,
            'source_channel': 'app_chat',
        })
    }])

    return json.dumps({'event_id': event_id, 'status': 'persisted_and_emitted'})


# ═══════════════════════════════════════════════════════════════════════════════
# NBA REAL-TIME GENERATION TOOL (used by nba_realtime_agent)
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def get_customer_context_for_nba(customer_id: str) -> str:
    """Get customer financial context for NBA generation.

    Args:
        customer_id: The customer ID

    Returns:
        JSON with balance, income, spend, FHS, household size.
    """
    rows = _sql(
        "SELECT c.first_name, "
        "(SELECT SUM(balance) FROM accounts WHERE customer_id=:cid AND status='ACTIVE') as balance, "
        "(SELECT AVG(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        " WHERE a.customer_id=:cid AND t.transaction_type='credit' AND t.amount > 400 "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 90 DAY)) as income, "
        "(SELECT SUM(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        " WHERE a.customer_id=:cid AND t.transaction_type='debit' "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 30 DAY)) as spend "
        "FROM customers c WHERE c.customer_id=:cid",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    if not rows:
        return json.dumps({"error": "Customer not found"})

    r = rows[0]
    # FHS
    fhs_rows = _sql(
        "SELECT score, band FROM customer_financial_health WHERE customer_id=:cid",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    fhs = {'score': _val(fhs_rows[0][0]), 'band': _val(fhs_rows[0][1])} if fhs_rows else {}

    return json.dumps({
        'first_name': _val(r[0]),
        'total_balance_bhd': round(float(_val(r[1]) or 0), 3),
        'monthly_income_bhd': round(float(_val(r[2]) or 0), 3),
        'monthly_spend_bhd': round(float(_val(r[3]) or 0), 3),
        'fhs': fhs,
    }, indent=2)


@tool
def get_nba_templates() -> str:
    """Get available NBA templates for the real-time agent to select from.

    Returns:
        JSON array of active templates with id, name, category, priority.
    """
    rows = _sql(
        "SELECT template_id, template_name, category, default_priority "
        "FROM nba_templates WHERE status='active' ORDER BY default_priority DESC"
    )
    templates = [{'id': _val(r[0]), 'name': _val(r[1]), 'category': _val(r[2]),
                  'priority': _val(r[3])} for r in rows]
    return json.dumps(templates, indent=2)


@tool
def persist_realtime_nba(customer_id: str, template_id: str, category: str,
                         title: str, reasoning: str, priority: int,
                         confidence: float, entity_ref: str) -> str:
    """Persist a real-time generated NBA to the database with deduplication.

    DEDUP RULE: One active NBA per (customer_id, template_id, entity_ref).
    - Same customer + same template + same entity_ref → REFRESH (update reasoning)
    - Same customer + same template + different entity_ref → CREATE NEW (different trip/event)
    - entity_ref examples: "london_2026-06-15", "dubai_next_week", "baby_2026-10"

    Also sets expires_at based on the event type:
    - Travel: departure date + 1 day
    - Baby: expected month + 1 month
    - Other: 30 days from now

    Args:
        customer_id: The customer ID
        template_id: The selected template ID
        category: NBA category (opportunity, wellness, security, etc.)
        title: Customer-facing title
        reasoning: AI-generated reasoning text (grounded in data)
        priority: Priority score 0-100
        confidence: Confidence score 0.0-1.0
        entity_ref: Dedup key — identifies the specific event (e.g. "london_2026-06-15", "baby_2026-10")

    Returns:
        JSON with action_id and status (created or refreshed).
    """
    # Sanitize entity_ref
    if not entity_ref or entity_ref in ('True', 'False', 'true', 'false', 'None', 'null'):
        entity_ref = template_id

    # DEDUP: one active NBA per (customer_id, template_id) — always refresh, never duplicate
    existing = _sql(
        "SELECT action_id FROM next_best_actions "
        "WHERE customer_id=:cid AND template_id=:tid AND status='active' LIMIT 1",
        [{'name': 'cid', 'value': {'stringValue': customer_id}},
         {'name': 'tid', 'value': {'stringValue': template_id}}]
    )

    # CTA based on template
    if 'travel_insurance' in template_id:
        cta = '{"label":"Purchase Now","action":"alma","prompt":"I was recommended travel insurance for my upcoming trip. I would like to purchase it now."}'
    elif 'fixed_deposit' in template_id or 'goal_saver' in template_id:
        cta = '{"label":"Set it up","action":"alma","prompt":"I want to set up ' + title.replace('"', '') + '"}'
    elif category == 'security':
        cta = '{"label":"Enable Now","action":"alma","prompt":"Enable this for me"}'
    else:
        cta = '{"label":"Learn More","action":"alma","prompt":"Tell me more about ' + title.replace('"', '') + '"}'
    # (cta_map replaced with template-specific logic above)

    # Map template to product_type for actioned badge matching
    _template_product_map = {
        'opportunity.travel_insurance_on_trip': 'travel_insurance_international',
        'opportunity.goal_saver_for_child': 'goal_saver',
        'opportunity.fixed_deposit': 'fixed_deposit',
        'opportunity.home_loan_prequalification': None,
        'wellness.salary_day_allocation': 'salary_allocation',
        'security.enable_large_txn_alerts': None,
    }
    product_type = _template_product_map.get(template_id, None)

    # Expiry: based on entity_ref date if parseable, otherwise 30 days
    # entity_ref format: "dubai_2026-05-20" or "baby_2026-10" or "dubai_next_week"
    import re as _re
    date_match = _re.search(r'(\d{4}-\d{2}-\d{2})', entity_ref)
    month_match = _re.search(r'(\d{4}-\d{2})$', entity_ref)
    if date_match:
        # Specific date found → expire end of that day
        expires_interval = f"INTERVAL 0 DAY"  # placeholder, overridden below
        expires_sql_override = f"'{date_match.group(1)} 23:59:59'"
    elif month_match:
        # Month found (e.g. baby_2026-10) → expire end of that month + 1 month
        expires_interval = f"INTERVAL 0 DAY"
        expires_sql_override = f"'{month_match.group(1)}-28 23:59:59' + INTERVAL 1 MONTH"
    else:
        # No date → 14 days for travel, 30 days for others
        expires_interval = "INTERVAL 14 DAY" if "travel" in template_id.lower() else "INTERVAL 30 DAY"
        expires_sql_override = None

    if existing and _val(existing[0][0]):
        # REFRESH existing (same event mentioned again)
        action_id = _val(existing[0][0])
        _sql(
            "UPDATE next_best_actions SET title=:title, reasoning=:reason, "
            "priority=:pri, confidence=:conf, cta_primary=:cta, "
            "generated_at=NOW(), model_version=:model "
            "WHERE action_id=:aid",
            [
                {'name': 'title', 'value': {'stringValue': title}},
                {'name': 'reason', 'value': {'stringValue': reasoning}},
                {'name': 'pri', 'value': {'longValue': priority}},
                {'name': 'conf', 'value': {'doubleValue': confidence}},
                {'name': 'cta', 'value': {'stringValue': cta}},
                {'name': 'model', 'value': {'stringValue': NOVA_MODEL}},
                {'name': 'aid', 'value': {'stringValue': action_id}},
            ]
        )
        status = "refreshed"
    else:
        # CREATE new NBA
        action_id = f"nba_rt_{customer_id[-4:]}_{uuid.uuid4().hex[:8]}"
        _sql(
            "INSERT IGNORE INTO next_best_actions (action_id, customer_id, template_id, category, product_type, "
            "priority, confidence, title, reasoning, metrics, cta_primary, source, "
            "source_detail, model_version, related_entity_id, generated_at, expires_at) VALUES "
            "(:aid, :cid, :tid, :cat, :ptype, :pri, :conf, :title, :reason, '[]', :cta, "
            "'agent', 'alma_graph_nba_realtime_v1', :model, :eref, NOW(), "
            + (expires_sql_override if expires_sql_override else f"DATE_ADD(NOW(), {expires_interval})") + ")",
            [
                {'name': 'aid', 'value': {'stringValue': action_id}},
                {'name': 'cid', 'value': {'stringValue': customer_id}},
                {'name': 'tid', 'value': {'stringValue': template_id}},
                {'name': 'cat', 'value': {'stringValue': category}},
                {'name': 'ptype', 'value': {'stringValue': product_type} if product_type else {'isNull': True}},
                {'name': 'pri', 'value': {'longValue': priority}},
                {'name': 'conf', 'value': {'doubleValue': confidence}},
                {'name': 'title', 'value': {'stringValue': title}},
                {'name': 'reason', 'value': {'stringValue': reasoning}},
                {'name': 'cta', 'value': {'stringValue': cta}},
                {'name': 'model', 'value': {'stringValue': NOVA_MODEL}},
                {'name': 'eref', 'value': {'stringValue': entity_ref}},
            ]
        )
        # Check if INSERT succeeded or was silently ignored (duplicate)
        check = _sql("SELECT action_id FROM next_best_actions WHERE action_id=:aid", [{'name':'aid','value':{'stringValue':action_id}}])
        if not check:
            # INSERT IGNORE skipped — find existing
            dup = _sql("SELECT action_id FROM next_best_actions WHERE customer_id=:cid AND template_id=:tid AND status='active' LIMIT 1", [{'name':'cid','value':{'stringValue':customer_id}},{'name':'tid','value':{'stringValue':template_id}}])
            action_id = _val(dup[0][0]) if dup else action_id
            status = "refreshed"
        else:
            status = "created"

    # Audit (wrapped in try/except to not break the main flow)
    try:
        _sql(
            "INSERT INTO agent_invocations (invocation_id, agent_id, agent_version, customer_id, "
            "trigger_type, trigger_ref, model_id, outcome, created_at) VALUES "
            "(:iid, 'nba_realtime_agent', 'v1', :cid, 'event', :aid, :model, 'success', NOW())",
            [
                {'name': 'iid', 'value': {'stringValue': str(uuid.uuid4())}},
                {'name': 'cid', 'value': {'stringValue': customer_id}},
                {'name': 'aid', 'value': {'stringValue': action_id}},
                {'name': 'model', 'value': {'stringValue': NOVA_MODEL}},
            ]
        )
    except Exception:
        pass  # audit failure must not break NBA persistence

    return json.dumps({'action_id': action_id, 'status': status, 'title': title})


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSACTION EXECUTION TOOL (purchases, used by NBA agent when customer says "yes")
# ═══════════════════════════════════════════════════════════════════════════════

@tool
def execute_purchase(customer_id: str, product_type: str, details: str) -> str:
    """Execute a product purchase — looks up price from catalog, debits account, creates product.

    Call this ONLY when the customer explicitly confirms they want to buy/activate something.
    This creates a REAL transaction (debit from their account).
    Price is looked up automatically from the product_catalog table.

    Available product_types:
    - travel_insurance_regional (BHD 8) — GCC travel
    - travel_insurance_international (BHD 12) — international travel
    - goal_saver (free) — savings sub-account
    - fixed_deposit (min BHD 500) — term deposit
    - credit_card_classic (free first year)
    - credit_card_gold (BHD 25/year)
    - credit_card_signature (BHD 75/year)
    - life_insurance_basic (BHD 15/month)
    - salary_allocation (free) — automated salary split strategy
    - bnpl_split_pay (free)

    Args:
        customer_id: The customer ID
        product_type: Must be one of the types above
        details: JSON string with product-specific details (e.g. {"destination":"London","departure":"2026-05-17"})

    Returns:
        JSON with receipt_id, transaction_id, amount, new_balance on success.
        Error message on failure.
    """
    import boto3 as _b3

    # Lookup price from product_catalog
    rows = _sql(
        "SELECT product_name, price_bhd FROM product_catalog WHERE product_type=:pt AND status='active'",
        [{'name': 'pt', 'value': {'stringValue': product_type}}]
    )
    if not rows:
        return json.dumps({'success': False, 'error': f'Product type "{product_type}" not found in catalog'})

    product_name = _val(rows[0][0])
    price = float(_val(rows[0][1]) or 0)

    # Invoke transaction module
    _lambda = _b3.client('lambda', region_name='eu-west-1')
    payload = json.dumps({
        'action': 'purchase',
        'customer_id': customer_id,
        'product_type': product_type,
        'product_name': product_name,
        'amount': price,
        'details': json.loads(details) if isinstance(details, str) else details,
    })

    resp = _lambda.invoke(
        FunctionName='aibank-transaction-module',
        InvocationType='RequestResponse',
        Payload=payload.encode()
    )
    result = json.loads(resp['Payload'].read())
    return json.dumps(result, indent=2)
