"""NBA Personal Reasoning Agent.

Tier 3 reasoning: takes a candidate NBA (template + customer context) and produces
grounded, explainable reasoning text + metrics + refined CTA.

Models:
  - Nova 2 Lite (amazon.nova-2-lite-v1:0) for bulk NBAs (top-2, top-3)
  - Claude Sonnet 4 for flagship NBA (top-1) — set via event.use_flagship=true

Input event:
{
  "customer_id": "CUST20250100",
  "template_id": "wellness.prevent_debit_failure",
  "candidate_context": { ... template-specific data ... },
  "use_flagship": false
}

Output:
{
  "statusCode": 200,
  "reasoning": "Your EWA bill BHD 45 is due Sunday...",
  "title": "Prevent Bill Bounce",
  "metrics": [{"label": "Late fee avoided", "value": "BHD 5.000"}],
  "cta_primary": {"label": "Move BHD 50", "action": "execute", ...},
  "confidence": 0.93,
  "model_used": "amazon.nova-2-lite-v1:0"
}
"""
import json, logging, os, boto3
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock = boto3.client('bedrock-runtime', region_name='eu-west-1')
rds = boto3.client('rds-data', region_name='eu-west-1')

CLUSTER = os.environ.get('AURORA_CLUSTER_ARN',
    'arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr')
SECRET = os.environ.get('AURORA_SECRET_ARN',
    'arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6')
DB = 'corebanking'

NOVA_MODEL = 'eu.amazon.nova-pro-v1:0'
SONNET_MODEL = 'eu.amazon.nova-pro-v1:0'


def _sql(sql, params=None):
    kwargs = dict(resourceArn=CLUSTER, secretArn=SECRET, database=DB, sql=sql)
    if params:
        kwargs['parameters'] = params
    return rds.execute_statement(**kwargs).get('records', [])


def _get_customer_context(customer_id):
    """Fetch customer features for prompt grounding."""
    # Basic profile
    rows = _sql(
        "SELECT first_name, nationality, credit_score FROM customers WHERE customer_id=:cid",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    profile = {}
    if rows:
        profile = {
            'first_name': list(rows[0][0].values())[0],
            'nationality': list(rows[0][1].values())[0] if not rows[0][1].get('isNull') else 'Unknown',
            'credit_score': list(rows[0][2].values())[0] if not rows[0][2].get('isNull') else None,
        }

    # Account balances
    rows = _sql(
        "SELECT account_type, balance FROM accounts WHERE customer_id=:cid AND status='ACTIVE'",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    accounts = [{'type': list(r[0].values())[0], 'balance': float(list(r[1].values())[0])} for r in rows]
    total_balance = sum(a['balance'] for a in accounts)

    # Monthly income (from salary credits last 60 days)
    rows = _sql(
        "SELECT AVG(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        "WHERE a.customer_id=:cid AND t.transaction_type='credit' AND t.description='Salary Credit' "
        "AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 60 DAY)",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    monthly_income = float(list(rows[0][0].values())[0]) if rows and not rows[0][0].get('isNull') else 0

    # Monthly spend (last 30 days)
    rows = _sql(
        "SELECT SUM(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        "WHERE a.customer_id=:cid AND t.transaction_type='debit' "
        "AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 30 DAY)",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    monthly_spend = float(list(rows[0][0].values())[0]) if rows and not rows[0][0].get('isNull') else 0

    # Active goals
    rows = _sql(
        "SELECT goal_title, target_amount, current_amount FROM customer_goals "
        "WHERE customer_id=:cid AND status='active'",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    goals = [{'name': list(r[0].values())[0],
              'target': float(list(r[1].values())[0]),
              'current': float(list(r[2].values())[0])} for r in rows]

    # Household (via joint accounts)
    rows = _sql(
        "SELECT DISTINCT ah2.customer_id FROM account_holders ah1 "
        "JOIN account_holders ah2 ON ah1.account_id=ah2.account_id "
        "WHERE ah1.customer_id=:cid AND ah2.customer_id<>:cid "
        "AND ah1.removed_at IS NULL AND ah2.removed_at IS NULL "
        "AND ah1.role IN ('primary','joint') AND ah2.role IN ('primary','joint')",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    household_size = len(rows) + 1  # include self

    # Fetch graph context from Neptune Analytics
    graph_ctx = {}
    try:
        graph_resp = lambda_client.invoke(
            FunctionName='aibank-nba-graph-context',
            InvocationType='RequestResponse',
            Payload=json.dumps({'customer_id': customer_id, 'context_types': ['household', 'peer_similarity', 'community']}).encode()
        )
        graph_ctx = json.loads(graph_resp['Payload'].read())
        if graph_ctx.get('statusCode') != 200:
            graph_ctx = {}
    except Exception as e:
        logger.warning(f"Graph context fetch failed: {e}")

    return {
        'customer_id': customer_id,
        'first_name': profile.get('first_name', 'Customer'),
        'nationality': profile.get('nationality', 'Unknown'),
        'credit_score': profile.get('credit_score'),
        'total_balance_bhd': round(total_balance, 3),
        'monthly_income_bhd': round(monthly_income, 3),
        'monthly_spend_bhd': round(monthly_spend, 3),
        'savings_rate_pct': round((monthly_income - monthly_spend) / monthly_income * 100, 1) if monthly_income > 0 else 0,
        'accounts': accounts,
        'goals': goals,
        'household_size': household_size,
        'peer_similarity': graph_ctx.get('peer_similarity', {}),
        'community': graph_ctx.get('community', {}),
    }


def _get_template(template_id):
    rows = _sql(
        "SELECT template_name, category, reasoning_prompt, cta_template, default_priority, default_confidence "
        "FROM nba_templates WHERE template_id=:tid",
        [{'name': 'tid', 'value': {'stringValue': template_id}}]
    )
    if not rows:
        return None
    r = rows[0]
    return {
        'name': list(r[0].values())[0],
        'category': list(r[1].values())[0],
        'reasoning_prompt': list(r[2].values())[0] if not r[2].get('isNull') else '',
        'cta_template': json.loads(list(r[3].values())[0]) if not r[3].get('isNull') else {},
        'priority': list(r[4].values())[0],
        'confidence': float(list(r[5].values())[0]),
    }


def _invoke_bedrock(system_prompt, user_prompt, model_id, max_tokens=500):
    """Call Bedrock Converse API."""
    resp = bedrock.converse(
        modelId=model_id,
        system=[{'text': system_prompt}],
        messages=[{'role': 'user', 'content': [{'text': user_prompt}]}],
        inferenceConfig={'maxTokens': max_tokens, 'temperature': 0.3}
    )
    return resp['output']['message']['content'][0]['text']


def handler(event, context):
    logger.info(f"Personal Reasoning Agent invoked: {json.dumps(event, default=str)[:500]}")

    customer_id = event.get('customer_id')
    template_id = event.get('template_id')
    candidate_context = event.get('candidate_context', {})
    use_flagship = event.get('use_flagship', False)

    if not customer_id or not template_id:
        return {'statusCode': 400, 'error': 'customer_id and template_id required'}

    # Fetch context
    ctx = _get_customer_context(customer_id)
    template = _get_template(template_id)
    if not template:
        return {'statusCode': 404, 'error': f'Template {template_id} not found'}

    # Build prompt
    model_id = SONNET_MODEL if use_flagship else NOVA_MODEL

    system_prompt = (
        "You are an AI banking recommendation agent for AI Bank (Bahrain). "
        "You produce short, grounded, explainable recommendations. "
        "RULES (MANDATORY — never skip):\n"
        "1. Every number MUST come from the customer context below. Never invent.\n"
        "2. PEER INSIGHT (CRITICAL): If 'peer_insight' is provided in the context, you MUST include it VERBATIM as one of your sentences. Copy it exactly — do not rephrase, do not omit. This is the most important sentence in your output.\n"
        "3. If community data shows shared merchants, mention 1-2 merchant names.\n"
        "4. If household size > 1, reference the household.\n"
        "5. Keep total output to 2-4 sentences. The peer_insight sentence counts as one.\n"
        "- Never invent figures. If data is missing, say so.\n"
        "- Keep reasoning to 2-4 sentences max.\n"
        "- Be warm but professional. Use the customer's first name.\n"
        "- Currency is always BHD with 3 decimal places.\n"
        "- Output ONLY a JSON object with keys: title, reasoning, metrics (array of {label,value}), confidence (0-1).\n"
        "- Do NOT include markdown, code fences, or explanation outside the JSON."
    )

    # Extract FHS for prominent display in prompt
    fhs = ctx.get('fhs', {})
    fhs_section = ""
    if fhs.get('score'):
        fhs_section = (
            f"\nFINANCIAL HEALTH SCORE: {fhs['score']}/100 ({fhs.get('band','?')})\n"
            f"  Subscores: debt={fhs.get('subscores',{}).get('debt','?')}, "
            f"savings={fhs.get('subscores',{}).get('savings','?')}, "
            f"spending={fhs.get('subscores',{}).get('spending','?')}, "
            f"income={fhs.get('subscores',{}).get('income','?')}, "
            f"credit={fhs.get('subscores',{}).get('credit','?')}, "
            f"behavior={fhs.get('subscores',{}).get('behavior','?')}\n"
            f"  Weakest area: {min(fhs.get('subscores',{}).items(), key=lambda x: x[1])[0] if fhs.get('subscores') else '?'}\n"
        )

    user_prompt = (
        f"TEMPLATE: {template['name']} (category: {template['category']})\n"
        f"TEMPLATE GUIDANCE: {template['reasoning_prompt']}\n\n"
        f"CUSTOMER CONTEXT:\n{json.dumps(ctx, indent=2)}\n\n"
        f"{fhs_section}\n"
        f"CANDIDATE CONTEXT (template-specific data):\n{json.dumps(candidate_context, indent=2)}\n\n"
        f"Generate the NBA JSON now."
    )

    try:
        raw_output = _invoke_bedrock(system_prompt, user_prompt, model_id)
        # Parse JSON from response (strip any markdown fences)
        cleaned = raw_output.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.split('\n', 1)[1].rsplit('```', 1)[0]
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning(f"Failed to parse agent output as JSON: {raw_output[:200]}")
        # Fallback: use raw text as reasoning
        result = {
            'title': template['name'],
            'reasoning': raw_output[:500],
            'metrics': [],
            'confidence': template['confidence'],
        }
    except Exception as e:
        logger.error(f"Bedrock invocation failed: {e}")
        return {'statusCode': 500, 'error': str(e)}


    # Audit log
    try:
        import uuid as _uuid
        _sql(
            "INSERT INTO agent_invocations (invocation_id, agent_id, agent_version, customer_id, "
            "trigger_type, tokens_in, tokens_out, model_id, outcome, created_at) VALUES "
            "(:iid, 'personal_reasoning_agent', 'v1', :cid, 'api', 0, 0, :model, 'success', NOW())",
            [{'name':'iid','value':{'stringValue':str(_uuid.uuid4())}},
             {'name':'cid','value':{'stringValue':customer_id}},
             {'name':'model','value':{'stringValue':model_id}}]
        )
    except:
        pass

    return {
        'statusCode': 200,
        'customer_id': customer_id,
        'template_id': template_id,
        'title': result.get('title', template['name']),
        'reasoning': result.get('reasoning', ''),
        'metrics': result.get('metrics', []),
        'confidence': result.get('confidence', template['confidence']),
        'cta_primary': template['cta_template'],
        'model_used': model_id,
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }
