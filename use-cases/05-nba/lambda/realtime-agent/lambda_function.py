"""NBA Real-Time Agent — Fully agentic, event-triggered NBA generation.

Triggered by EventBridge events (salary_credit, life_event, txn_failed, etc.).
Decides whether to generate an NBA, selects template, generates reasoning,
and writes to next_best_actions — all in one LLM call with tool access.

This is the WOW path — <10 seconds from event to customer seeing the NBA.
"""
import json, logging, os, uuid, boto3
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock = boto3.client('bedrock-runtime', region_name='eu-west-1')
rds = boto3.client('rds-data', region_name='eu-west-1')
lambda_client = boto3.client('lambda', region_name='eu-west-1')

CLUSTER = "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr"
SECRET = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"
DB = "corebanking"
NOVA_MODEL = "eu.amazon.nova-2-lite-v1:0"


def _sql(sql, params=None):
    kwargs = dict(resourceArn=CLUSTER, secretArn=SECRET, database=DB, sql=sql)
    if params:
        kwargs['parameters'] = params
    return rds.execute_statement(**kwargs).get('records', [])


def _val(cell):
    if cell.get('isNull'):
        return None
    return list(cell.values())[0]


def get_context(customer_id):
    """Get customer + graph context for the agent."""
    # Aurora context
    rows = _sql(
        "SELECT c.first_name, c.nationality, "
        "(SELECT SUM(balance) FROM accounts WHERE customer_id=:cid AND status='ACTIVE') as balance, "
        "(SELECT AVG(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        " WHERE a.customer_id=:cid AND t.transaction_type='credit' AND t.description='Salary Credit' "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 60 DAY)) as income, "
        "(SELECT SUM(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        " WHERE a.customer_id=:cid AND t.transaction_type='debit' "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 30 DAY)) as spend "
        "FROM customers c WHERE c.customer_id=:cid",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    if not rows:
        return None

    r = rows[0]
    ctx = {
        'first_name': _val(r[0]) or 'Customer',
        'nationality': _val(r[1]) or 'Unknown',
        'total_balance_bhd': round(float(_val(r[2]) or 0), 3),
        'monthly_income_bhd': round(float(_val(r[3]) or 0), 3),
        'monthly_spend_bhd': round(float(_val(r[4]) or 0), 3),
    }

    # Graph context
    try:
        resp = lambda_client.invoke(
            FunctionName='aibank-nba-graph-context',
            InvocationType='RequestResponse',
            Payload=json.dumps({'customer_id': customer_id, 'context_types': ['household', 'peer_similarity', 'community']}).encode()
        )
        graph = json.loads(resp['Payload'].read())
        if graph.get('statusCode') == 200:
            ctx['household'] = graph.get('household', {})
            ctx['peer_similarity'] = graph.get('peer_similarity', {})
            ctx['community'] = graph.get('community', {})
    except Exception as e:
        logger.warning(f"Graph context failed: {e}")

    return ctx


def get_templates():
    """Get active templates as context for the agent."""
    rows = _sql("SELECT template_id, template_name, category, default_priority, reasoning_prompt, cta_template "
                "FROM nba_templates WHERE status='active' ORDER BY default_priority DESC")
    return [{'id': _val(r[0]), 'name': _val(r[1]), 'category': _val(r[2]),
             'priority': _val(r[3]), 'guidance': _val(r[4]),
             'cta': _val(r[5])} for r in rows]


def invoke_agent(event_type, event_data, customer_context, templates):
    """Single LLM call — agent decides + reasons in one shot."""
    system = (
        "You are an NBA Real-Time Agent for AI Bank. An event just occurred for a customer. "
        "Your job: decide if this event warrants a personalised recommendation, and if so, generate it.\n\n"
        "RULES:\n"
        "- Not every event deserves an NBA. If the event is routine and the customer already has relevant NBAs, output generate=false.\n"
        "- If you generate, pick the BEST matching template from the available list.\n"
        "- Ground ALL numbers in the customer context. Never invent figures.\n"
        "- Reference peer/community data if available and relevant.\n"
        "- Keep reasoning to 2-4 sentences. Be warm, use first name.\n"
        "- Output ONLY valid JSON with keys: generate (bool), template_id (str), title (str), "
        "reasoning (str), metrics (array of {label,value}), priority (int 0-100), category (str), confidence (float 0-1).\n"
        "- If generate=false, only include {generate: false, reason: '...'}."
    )

    user = (
        f"EVENT: {event_type}\n"
        f"EVENT DATA: {json.dumps(event_data)}\n\n"
        f"CUSTOMER CONTEXT:\n{json.dumps(customer_context, indent=2)}\n\n"
        f"AVAILABLE TEMPLATES:\n{json.dumps(templates, indent=2)}\n\n"
        f"Decide: should we generate an NBA for this event? If yes, produce the full NBA JSON."
    )

    resp = bedrock.converse(
        modelId=NOVA_MODEL,
        system=[{'text': system}],
        messages=[{'role': 'user', 'content': [{'text': user}]}],
        inferenceConfig={'maxTokens': 600, 'temperature': 0.3}
    )
    raw = resp['output']['message']['content'][0]['text'].strip()

    # Parse JSON (strip markdown fences if present)
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1].rsplit('```', 1)[0]
    return json.loads(raw)


def persist_nba(customer_id, result, templates):
    """Write the generated NBA to Aurora."""
    template_id = result.get('template_id', 'realtime.generic')
    action_id = f"nba_rt_{customer_id[-4:]}_{uuid.uuid4().hex[:8]}"

    # Find CTA from template
    cta = '{}'
    for t in templates:
        if t['id'] == template_id:
            cta = t.get('cta', '{}')
            break

    _sql(
        "INSERT INTO next_best_actions (action_id, customer_id, template_id, category, "
        "priority, confidence, title, reasoning, metrics, cta_primary, source, "
        "source_detail, model_version, generated_at) VALUES "
        "(:aid, :cid, :tid, :cat, :pri, :conf, :title, :reason, :metrics, :cta, "
        "'agent', 'nba_realtime_agent_v1', :model, NOW())",
        [
            {'name': 'aid', 'value': {'stringValue': action_id}},
            {'name': 'cid', 'value': {'stringValue': customer_id}},
            {'name': 'tid', 'value': {'stringValue': template_id}},
            {'name': 'cat', 'value': {'stringValue': result.get('category', 'wellness')}},
            {'name': 'pri', 'value': {'longValue': int(result.get('priority', 90))}},
            {'name': 'conf', 'value': {'doubleValue': float(result.get('confidence', 0.85))}},
            {'name': 'title', 'value': {'stringValue': result.get('title', 'Recommendation')}},
            {'name': 'reason', 'value': {'stringValue': result.get('reasoning', '')}},
            {'name': 'metrics', 'value': {'stringValue': json.dumps(result.get('metrics', []))}},
            {'name': 'cta', 'value': {'stringValue': cta}},
            {'name': 'model', 'value': {'stringValue': NOVA_MODEL}},
        ]
    )
    return action_id


def handler(event, context):
    """Handle EventBridge event or direct invocation."""
    # Extract event details
    detail = event.get('detail', event)
    event_type = event.get('detail-type', detail.get('event_type', 'unknown'))
    customer_id = detail.get('customer_id')

    if not customer_id:
        return {'statusCode': 400, 'error': 'customer_id required in event detail'}

    logger.info(f"Real-Time Agent: {event_type} for {customer_id}")

    # Get context
    ctx = get_context(customer_id)
    if not ctx:
        return {'statusCode': 404, 'error': f'Customer {customer_id} not found'}

    templates = get_templates()

    # Agent decides + reasons
    try:
        result = invoke_agent(event_type, detail, ctx, templates)
    except json.JSONDecodeError as e:
        logger.error(f"Agent output not valid JSON: {e}")
        return {'statusCode': 500, 'error': 'Agent produced invalid JSON'}
    except Exception as e:
        logger.error(f"Agent invocation failed: {e}")
        return {'statusCode': 500, 'error': str(e)}

    # If agent decided not to generate
    if not result.get('generate', True):
        logger.info(f"Agent decided: no NBA needed. Reason: {result.get('reason', '?')}")
        return {'statusCode': 200, 'generated': False, 'reason': result.get('reason')}

    # Persist the NBA
    action_id = persist_nba(customer_id, result, templates)
    logger.info(f"Real-Time NBA generated: {action_id} [{result.get('category')}] {result.get('title')}")


    # Audit log
    try:
        _sql(
            "INSERT INTO agent_invocations (invocation_id, agent_id, agent_version, customer_id, "
            "trigger_type, trigger_ref, model_id, outcome, created_at) VALUES "
            "(:iid, 'nba_realtime_agent', 'v1', :cid, 'event', :evt, :model, :outcome, NOW())",
            [{'name':'iid','value':{'stringValue':str(uuid.uuid4())}},
             {'name':'cid','value':{'stringValue':customer_id}},
             {'name':'evt','value':{'stringValue':event_type}},
             {'name':'model','value':{'stringValue':NOVA_MODEL}},
             {'name':'outcome','value':{'stringValue':'success' if result.get('generate') else 'skipped'}}]
        )
    except:
        pass

    return {
        'statusCode': 200,
        'generated': True,
        'action_id': action_id,
        'title': result.get('title'),
        'category': result.get('category'),
        'priority': result.get('priority'),
    }
