"""NBA Batch Generator Lambda.

Runs every 6h (EventBridge cron). For each customer:
1. Gathers context from Aurora + Neptune
2. Evaluates 8 templates deterministically
3. Selects top-3 candidates
4. Calls Personal Reasoning Agent for each (LLM)
5. Writes results to next_best_actions

This is the batch path — deterministic selection + agentic reasoning.
"""
import json, logging, os, uuid, boto3
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

rds = boto3.client('rds-data', region_name='eu-west-1')
lambda_client = boto3.client('lambda', region_name='eu-west-1')

CLUSTER = os.environ.get('AURORA_CLUSTER_ARN',
    'arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr')
SECRET = os.environ.get('AURORA_SECRET_ARN',
    'arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6')
DB = 'corebanking'
REASONING_AGENT = os.environ.get('REASONING_AGENT_FUNCTION', 'aibank-nba-personal-reasoning')


def _sql(sql, params=None):
    kwargs = dict(resourceArn=CLUSTER, secretArn=SECRET, database=DB, sql=sql)
    if params:
        kwargs['parameters'] = params
    return rds.execute_statement(**kwargs).get('records', [])


def _val(cell):
    if cell.get('isNull'):
        return None
    return list(cell.values())[0]


def get_customer_context(customer_id):
    """Gather customer data from Aurora."""
    rows = _sql(
        "SELECT c.first_name, c.nationality, c.credit_score, "
        "(SELECT SUM(balance) FROM accounts WHERE customer_id=:cid AND status='ACTIVE') as total_balance, "
        "(SELECT AVG(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        " WHERE a.customer_id=:cid AND t.transaction_type='credit' AND t.amount > 400 "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 90 DAY)) as monthly_income, "
        "(SELECT SUM(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        " WHERE a.customer_id=:cid AND t.transaction_type='debit' "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 30 DAY)) as monthly_spend "
        "FROM customers c WHERE c.customer_id=:cid",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    if not rows:
        return None

    r = rows[0]
    balance = float(_val(r[3]) or 0)
    income = float(_val(r[4]) or 0)
    spend = float(_val(r[5]) or 0)

    # Check existing active NBAs (to avoid duplicates)
    existing = _sql(
        "SELECT template_id FROM next_best_actions WHERE customer_id=:cid AND status='active'",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    existing_templates = {_val(r[0]) for r in existing}

    # Check goals
    goals = _sql(
        "SELECT goal_type FROM customer_goals WHERE customer_id=:cid AND status='active'",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    active_goal_types = {_val(r[0]) for r in goals}

    # FHS score (for gating + reasoning)
    fhs = _sql(
        "SELECT score, band, subscore_debt, subscore_savings, subscore_spending, "
        "subscore_income, subscore_credit, subscore_behavior "
        "FROM customer_financial_health WHERE customer_id=:cid",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    fhs_data = {}
    if fhs:
        fhs_data = {
            'score': int(_val(fhs[0][0]) or 0),
            'band': _val(fhs[0][1]) or 'unknown',
            'subscores': {
                'debt': int(_val(fhs[0][2]) or 50),
                'savings': int(_val(fhs[0][3]) or 50),
                'spending': int(_val(fhs[0][4]) or 50),
                'income': int(_val(fhs[0][5]) or 50),
                'credit': int(_val(fhs[0][6]) or 50),
                'behavior': int(_val(fhs[0][7]) or 50),
            }
        }

    # Household size
    hh = _sql(
        "SELECT COUNT(DISTINCT ah2.customer_id) FROM account_holders ah1 "
        "JOIN account_holders ah2 ON ah1.account_id=ah2.account_id "
        "WHERE ah1.customer_id=:cid AND ah2.customer_id<>:cid "
        "AND ah1.removed_at IS NULL AND ah2.removed_at IS NULL",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    household_size = int(_val(hh[0][0]) or 0) + 1

    # Active NBA suppressions (V015 nba_suppressions)
    # Batch generator honours customer-set suppressions with full scope support:
    #   scope_type='all'       → suppress every NBA
    #   scope_type='category'  → suppress that whole category
    #   scope_type='template'  → suppress that specific template_id
    # Expired suppressions (expires_at < NOW()) are silently ignored.
    supp_rows = _sql(
        "SELECT scope_type, scope_value FROM nba_suppressions "
        "WHERE customer_id=:cid AND status='suppressed' "
        "AND (expires_at IS NULL OR expires_at > NOW())",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    )
    suppressed_all = False
    suppressed_categories = set()
    suppressed_templates = set()
    for sr in supp_rows:
        st = _val(sr[0])
        sv = _val(sr[1])
        if st == 'all':
            suppressed_all = True
        elif st == 'category':
            suppressed_categories.add(sv)
        elif st == 'template':
            suppressed_templates.add(sv)

    return {
        'customer_id': customer_id,
        'first_name': _val(r[0]) or 'Customer',
        'nationality': _val(r[1]) or 'Unknown',
        'credit_score': int(_val(r[2]) or 0),
        'total_balance_bhd': round(balance, 3),
        'monthly_income_bhd': round(income, 3),
        'monthly_spend_bhd': round(spend, 3),
        'savings_rate_pct': round((income - spend) / income * 100, 1) if income > 0 else 0,
        'household_size': household_size,
        'existing_templates': list(existing_templates),
        'fhs': fhs_data,
        'active_goal_types': list(active_goal_types),
        'suppressed_all': suppressed_all,
        'suppressed_categories': list(suppressed_categories),
        'suppressed_templates': list(suppressed_templates),
    }


def get_templates():
    """Load all active templates."""
    rows = _sql("SELECT template_id, template_name, category, eligibility_rules, "
                "default_priority, default_confidence, reasoning_prompt, cta_template "
                "FROM nba_templates WHERE status='active' ORDER BY default_priority DESC")
    templates = []
    for r in rows:
        rules_json = _val(r[3])
        templates.append({
            'template_id': _val(r[0]),
            'name': _val(r[1]),
            'category': _val(r[2]),
            'rules': json.loads(rules_json) if rules_json else {},
            'priority': int(_val(r[4]) or 50),
            'confidence': float(_val(r[5]) or 0.8),
            'cta_template': json.loads(_val(r[7]) or '{}'),
        })
    return templates


def evaluate_eligibility(ctx, template):
    """Deterministic rule evaluation. Returns True if template should fire."""
    rules = template['rules']
    tid = template['template_id']

    # Skip if already active for this customer
    if tid in ctx['existing_templates']:
        return False

    # Customer-set suppressions (V015 nba_suppressions) — honoured first
    if ctx.get('suppressed_all'):
        return False
    if template['category'] in ctx.get('suppressed_categories', []):
        return False
    if tid in ctx.get('suppressed_templates', []):
        return False

    # FHS-based gating
    fhs = ctx.get('fhs', {})
    fhs_score = fhs.get('score', 50)
    fhs_band = fhs.get('band', 'unknown')

    # Suppress lending/opportunity NBAs for financially stressed customers
    if template['category'] == 'opportunity' and fhs_score < 55:
        return False  # Don't push products on weak/critical customers

    # Template-specific FHS gate
    if 'min_fhs' in rules:
        if fhs_score < rules['min_fhs']:
            return False

    # Check specific rules
    if 'requires_life_event' in rules:
        return False  # Life-event templates are real-time only

    if 'trigger' in rules and rules['trigger'] in ('cashflow_scanner', 'txn.salary_credit'):
        return False  # Event-triggered templates are real-time only

    if 'max_fhs_subscore_savings' in rules:
        # Would need FHS — skip if not computed yet (MVP: always pass)
        pass

    if 'min_fhs' in rules:
        # Skip if credit score is a rough proxy and below threshold
        if ctx['credit_score'] < rules['min_fhs'] * 10:  # rough mapping
            return False

    if 'min_income_bhd' in rules:
        if ctx['monthly_income_bhd'] < rules['min_income_bhd']:
            return False

    if 'min_balance_bhd' in rules:
        if ctx['total_balance_bhd'] < rules.get('min_balance_bhd', 0):
            return False

    if 'exclude_if_has_goal' in rules:
        if rules['exclude_if_has_goal'] in ctx['active_goal_types']:
            return False

    if 'min_recurring_subscriptions' in rules:
        # Simplified: check if customer has enough transaction variety
        pass  # Always pass at MVP — Neptune merchant data enriches later

    return True


def select_top_candidates(candidates, max_per_category=3, top_n=6):
    """Rank and select top-N with category balancing."""
    # FHS priority boosting: if a subscore is critically low (<40),
    # boost related template priority by +20
    for c in candidates:
        fhs = ctx.get('fhs', {}).get('subscores', {}) if 'ctx' in dir() else {}
        if c['category'] == 'wellness' and fhs.get('savings', 50) < 40:
            c['priority'] = min(100, c['priority'] + 20)
        if c['category'] == 'security' and fhs.get('behavior', 50) < 40:
            c['priority'] = min(100, c['priority'] + 15)

    candidates.sort(key=lambda x: x['priority'], reverse=True)
    selected = []
    category_counts = {}
    for c in candidates:
        cat = c['category']
        if category_counts.get(cat, 0) >= max_per_category:
            continue
        selected.append(c)
        category_counts[cat] = category_counts.get(cat, 0) + 1
        if len(selected) >= top_n:
            break
    return selected


def _get_template_peer_stat(template_id, graph_context, customer_id=''):
    """Read pre-computed peer stats from Neptune Customer node properties.
    
    These are materialized daily by the Neptune Enrichment Pipeline:
    - peer_pct_home_loan: % of similar peers with home loan
    - peer_pct_products: % of community with any product
    - peer_avg_merchants: avg merchant count in community
    - peer_pct_goals: % of community with savings goals
    - peer_shared_merchants_count: customers sharing payment channels
    """
    import boto3 as _b3, json as _j
    neptune = _b3.client('neptune-graph', region_name='eu-west-1')
    GRAPH_ID = 'g-ruhyz8aj39'
    community_size = graph_context.get('community', {}).get('community_size', 0)
    peer_count = graph_context.get('peer_similarity', {}).get('count', 0)

    try:
        # Read all materialized peer stats in one query
        r = neptune.execute_query(graphIdentifier=GRAPH_ID, queryString=f"""
            MATCH (c:Customer {{`~id`:'{customer_id}'}})
            RETURN c.peer_count as peer_count,
                   c.peer_pct_home_loan as pct_home_loan,
                   c.peer_pct_products as pct_products,
                   c.peer_avg_merchants as avg_merchants,
                   c.peer_pct_goals as pct_goals,
                   c.peer_pct_high_balance as pct_high_balance,
                   c.peer_pct_approved_loans as pct_approved_loans,
                   c.peer_avg_credit_score as avg_credit_score,
                   c.community_avg_fhs as community_avg_fhs,
                   c.community_id as community_id
        """, language='OPEN_CYPHER')
        data = _j.loads(r['payload'].read()).get('results', [{}])[0]

        actual_peer_count = int(data.get('peer_count', 0) or 0)
        pct_loan = data.get('pct_home_loan', 0) or 0
        pct_products = data.get('pct_products', 0) or 0
        avg_merchants = data.get('avg_merchants', 0) or 0
        pct_goals = data.get('pct_goals', 0) or 0
        pct_high_bal = data.get('pct_high_balance', 0) or 0

        if 'home_loan' in template_id:
            # Use peer_pct_approved_loans (actual approved applications) for more accurate insight
            pct_approved = data.get('pct_approved_loans', pct_loan) or pct_loan
            return f"Among {actual_peer_count} customers with similar spending and income patterns, {pct_approved}% have been approved for home loans."

        elif 'fixed_deposit' in template_id:
            return f"Among {actual_peer_count} customers with similar financial profiles, {pct_high_bal}% also have significant idle balances that could benefit from a fixed deposit."

        elif 'subscription' in template_id:
            return f"Among {actual_peer_count} customers with similar spending patterns, the average transacts with {avg_merchants} merchants — reviewing recurring charges helps optimize spend."

        elif 'large_txn_alerts' in template_id or 'enable_large' in template_id:
            return f"Among {actual_peer_count} customers with similar profiles, {pct_high_bal}% have balances above BHD 5,000 and benefit from instant transaction alerts."

        elif 'travel_insurance' in template_id:
            return f"Among {actual_peer_count} customers with similar spending patterns, {pct_products}% have purchased protection products."

        elif 'goal_saver' in template_id:
            return f"Among {actual_peer_count} customers with similar financial profiles, {pct_goals}% have set up savings goals for their family."

    except Exception as e:
        logger.warning(f"Neptune peer stat read failed for {template_id}: {e}")

    return f"Among customers in your spending community of {community_size}."


def generate_reasoning(customer_id, template, ctx):
    """Call Personal Reasoning Agent to generate grounded reasoning with peer insights."""
    # Fetch behavioural signals from pattern scanner
    signal_rows = rds.execute_statement(
        resourceArn=CLUSTER, secretArn=SECRET, database="corebanking",
        sql="SELECT signal_type, confidence, attributes FROM customer_signals "
            "WHERE customer_id=:cid AND consumed_at IS NULL AND (expires_at IS NULL OR expires_at > NOW())",
        parameters=[{'name':'cid','value':{'stringValue':customer_id}}]
    ).get('records', [])
    signals = [{'type': r[0].get('stringValue',''), 'confidence': float(r[1].get('doubleValue', 0) or 0),
                'attributes': json.loads(r[2].get('stringValue','{}'))} for r in signal_rows]

    # Get graph context (peer stats, community data) from Neptune
    graph_context = {}
    try:
        graph_resp = lambda_client.invoke(
            FunctionName='aibank-nba-graph-context',
            InvocationType='RequestResponse',
            Payload=json.dumps({
                'customer_id': customer_id,
                'context_types': ['household', 'peer_similarity', 'community']
            }).encode()
        )
        graph_result = json.loads(graph_resp['Payload'].read())

        if graph_result.get('statusCode') == 200:
            graph_context = {
                'community': graph_result.get('community', {}),
                'peer_similarity': graph_result.get('peer_similarity', {}),
                'household': graph_result.get('household', {}),
            }
            # Template-specific peer stats from Neptune (computed per-template in generate_reasoning)
            pass
    except Exception:
        pass

    payload = {
        'customer_id': customer_id,
        'template_id': template['template_id'],
        'candidate_context': {
            'source': 'batch_generator',
            'household_size': ctx['household_size'],
            'total_balance_bhd': ctx['total_balance_bhd'],
            'monthly_income_bhd': ctx['monthly_income_bhd'],
            'monthly_spend_bhd': ctx['monthly_spend_bhd'],
            'credit_score': ctx['credit_score'],
            'fhs': ctx.get('fhs', {}),
            'graph_context': graph_context,
            'signals': signals,
            'peer_insight': _get_template_peer_stat(template['template_id'], graph_context, customer_id),
        },
        'use_flagship': template['template_id'] == 'opportunity.home_loan_prequalification',  # Nova Pro for home loan
    }
    try:
        resp = lambda_client.invoke(
            FunctionName=REASONING_AGENT,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload).encode()
        )
        result = json.loads(resp['Payload'].read())
        if result.get('statusCode') == 200:
            return result
    except Exception as e:
        logger.warning(f"Reasoning agent failed for {customer_id}/{template['template_id']}: {e}")
    
    # Fallback: use template name as reasoning
    return {
        'title': template['name'],
        'reasoning': f"Based on your financial profile, this action could benefit you.",
        'metrics': [],
        'confidence': template['confidence'],
    }


def persist_nba(customer_id, template, reasoning):
    """Write NBA to next_best_actions table."""
    action_id = f"nba_{customer_id[-4:]}_{template['template_id'].split('.')[-1]}_{uuid.uuid4().hex[:6]}"
    
    _sql(
        "INSERT INTO next_best_actions (action_id, customer_id, template_id, category, "
        "priority, confidence, title, reasoning, metrics, cta_primary, source, "
        "source_detail, model_version, generated_at) VALUES "
        "(:aid, :cid, :tid, :cat, :pri, :conf, :title, :reason, :metrics, :cta, "
        "'rule', 'batch_generator_v1', :model, NOW()) "
        "ON DUPLICATE KEY UPDATE reasoning=:reason, metrics=:metrics, confidence=:conf, "
        "generated_at=NOW(), priority=:pri",
        [
            {'name': 'aid', 'value': {'stringValue': action_id}},
            {'name': 'cid', 'value': {'stringValue': customer_id}},
            {'name': 'tid', 'value': {'stringValue': template['template_id']}},
            {'name': 'cat', 'value': {'stringValue': template['category']}},
            {'name': 'pri', 'value': {'longValue': template['priority']}},
            {'name': 'conf', 'value': {'doubleValue': float(reasoning.get('confidence', 0.8))}},
            {'name': 'title', 'value': {'stringValue': reasoning.get('title', template['name'])}},
            {'name': 'reason', 'value': {'stringValue': reasoning.get('reasoning', '')}},
            {'name': 'metrics', 'value': {'stringValue': json.dumps(reasoning.get('metrics', []))}},
            {'name': 'cta', 'value': {'stringValue': json.dumps(template['cta_template'])}},
            {'name': 'model', 'value': {'stringValue': reasoning.get('model_used', 'batch_fallback')}},
        ]
    )
    return action_id


def process_customer(customer_id, templates):
    """Full pipeline for one customer."""
    ctx = get_customer_context(customer_id)
    if not ctx:
        return 0

    # Evaluate all templates
    candidates = [t for t in templates if evaluate_eligibility(ctx, t)]
    if not candidates:
        return 0

    # Select top-3
    top = select_top_candidates(candidates)

    # Generate reasoning + persist for each
    count = 0
    for template in top:
        reasoning = generate_reasoning(customer_id, template, ctx)
        persist_nba(customer_id, template, reasoning)
        count += 1

    return count


def handler(event, context):
    """Main handler — process customers, list them, or report pipeline status."""
    action = event.get('action')

    # Pipeline orchestration actions
    if action == 'list_customers':
        rows = _sql("SELECT customer_id FROM customers WHERE status='ACTIVE'")
        ids = [_val(r[0]) for r in rows]
        return {'customer_ids': ids, 'count': len(ids)}

    if action == 'report_status':
        # Write pipeline run status to DynamoDB for dashboard
        import boto3 as _b3
        from datetime import datetime as _dt
        ddb = _b3.resource('dynamodb', region_name='eu-west-1')
        table = ddb.Table('aibank-pipeline-runs')
        try:
            nba_count = _sql("SELECT COUNT(*) FROM next_best_actions WHERE source='rule' AND status='active'")
            total_nbas = int(_val(nba_count[0][0]) or 0) if nba_count else 0
            table.put_item(Item={
                'pipeline_id': f"nba_daily_{_dt.utcnow().strftime('%Y%m%d_%H%M')}",
                'pipeline_name': 'nba_daily',
                'status': event.get('status', 'UNKNOWN'),
                'run_date': _dt.utcnow().isoformat(),
                'enrichment': str(event.get('enrichment', {})),
                'scanner': str(event.get('scanner', {})),
                'error': str(event.get('error', '')),
                'total_nbas_generated': total_nbas,
                'ttl': int(_dt.utcnow().timestamp()) + 90 * 86400  # 90 day retention
            })
        except Exception as e:
            logger.warning(f"Failed to write pipeline status: {e}")
        # Also export to S3 CSV for QuickSight SPICE
        try:
            import csv, io
            s3 = _b3.client('s3', region_name='eu-west-1')
            # Scan all pipeline runs
            all_runs = table.scan().get('Items', [])
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['pipeline_id','status','run_date','customers_processed','total_nbas_generated','duration_seconds','error'])
            for item in all_runs:
                writer.writerow([item.get('pipeline_id',''), item.get('status',''), item.get('run_date',''),
                    item.get('customers_processed',0), item.get('total_nbas_generated',0),
                    item.get('duration_seconds',0), item.get('error','')])
            s3.put_object(Bucket='aibank-ui-prod-eu-west-1', Key='data/pipeline-runs.csv',
                Body=output.getvalue(), ContentType='text/csv')
        except Exception:
            pass
        return {'statusCode': 200, 'status': event.get('status')}

    customer_ids = event.get('customer_ids')
    limit = event.get('limit', 300)

    if not customer_ids:
        rows = _sql(f"SELECT customer_id FROM customers WHERE status='ACTIVE' LIMIT {limit}")
        customer_ids = [_val(r[0]) for r in rows]

    templates = get_templates()
    logger.info(f"Processing {len(customer_ids)} customers against {len(templates)} templates")

    total_nbas = 0
    for i, cid in enumerate(customer_ids):
        nbas = process_customer(cid, templates)
        total_nbas += nbas
        if (i + 1) % 50 == 0:
            logger.info(f"  Processed {i+1}/{len(customer_ids)} customers, {total_nbas} NBAs generated")

    logger.info(f"DONE: {len(customer_ids)} customers, {total_nbas} NBAs generated")
    return {
        'statusCode': 200,
        'customers_processed': len(customer_ids),
        'nbas_generated': total_nbas,
    }
