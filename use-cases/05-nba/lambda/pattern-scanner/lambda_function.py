"""NBA Pattern Scanner — detects behavioural signals from transaction patterns and Neptune graph.

Runs as a Step Function step BEFORE the batch generator.
Scans all customers, writes signals to customer_signals table.
Signals are consumed by the batch generator as additional eligibility context.
"""
import boto3, json, uuid, logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLUSTER_ARN = "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr"
SECRET_ARN = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"
DB_NAME = "corebanking"
GRAPH_ID = "g-ruhyz8aj39"

rds = boto3.client('rds-data', region_name='eu-west-1')
neptune = boto3.client('neptune-graph', region_name='eu-west-1')


def _sql(sql, params=None):
    kwargs = dict(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME, sql=sql)
    if params:
        kwargs['parameters'] = params
    return rds.execute_statement(**kwargs).get('records', [])


def _val(cell):
    if cell.get('isNull'):
        return None
    return cell.get('stringValue') or cell.get('longValue') or cell.get('doubleValue')


def _neptune_query(cypher):
    try:
        resp = neptune.execute_query(graphIdentifier=GRAPH_ID, queryString=cypher, language='OPEN_CYPHER')
        return json.loads(resp['payload'].read()).get('results', [])
    except Exception as e:
        logger.warning(f"Neptune query failed: {e}")
        return []


def _insert_signal(customer_id, signal_type, confidence, attributes):
    signal_id = f"sig_{uuid.uuid4().hex[:12]}"
    _sql(
        "INSERT IGNORE INTO customer_signals (signal_id, customer_id, signal_type, confidence, attributes, expires_at) "
        "VALUES (:sid, :cid, :stype, :conf, :attrs, DATE_ADD(NOW(), INTERVAL 7 DAY))",
        [
            {'name': 'sid', 'value': {'stringValue': signal_id}},
            {'name': 'cid', 'value': {'stringValue': customer_id}},
            {'name': 'stype', 'value': {'stringValue': signal_type}},
            {'name': 'conf', 'value': {'doubleValue': confidence}},
            {'name': 'attrs', 'value': {'stringValue': json.dumps(attributes)}},
        ]
    )


def scan_large_balance_idle():
    """Detect customers with balance > 4x monthly spend and no fixed deposit."""
    rows = _sql(
        "SELECT a.customer_id, SUM(a.balance) as total_bal, "
        "(SELECT COALESCE(SUM(t.amount),0) FROM transactions t JOIN accounts a2 ON t.account_id=a2.account_id "
        " WHERE a2.customer_id=a.customer_id AND t.transaction_type='debit' "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 30 DAY)) as monthly_spend "
        "FROM accounts a WHERE a.status='ACTIVE' GROUP BY a.customer_id "
        "HAVING total_bal > 2000 AND monthly_spend > 0 AND total_bal / monthly_spend >= 4"
    )
    count = 0
    for r in rows:
        cid = _val(r[0])
        balance = float(_val(r[1]) or 0)
        spend = float(_val(r[2]) or 0)
        # Exclude if already has fixed deposit
        existing = _sql("SELECT 1 FROM customer_products WHERE customer_id=:cid AND product_type='fixed_deposit' AND status='active' LIMIT 1",
                        [{'name': 'cid', 'value': {'stringValue': cid}}])
        if not existing:
            _insert_signal(cid, 'large_balance_idle', 0.80, {'balance': balance, 'monthly_spend': spend, 'ratio': round(balance/spend, 1)})
            count += 1
    return count


def scan_savings_rate_increasing():
    """Detect customers whose savings rate increased >20% vs prior period."""
    rows = _sql(
        "SELECT a.customer_id, "
        "(SELECT COALESCE(SUM(t.amount),0) FROM transactions t JOIN accounts a2 ON t.account_id=a2.account_id "
        " WHERE a2.customer_id=a.customer_id AND t.transaction_type='credit' "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 30 DAY)) as income_30d, "
        "(SELECT COALESCE(SUM(t.amount),0) FROM transactions t JOIN accounts a2 ON t.account_id=a2.account_id "
        " WHERE a2.customer_id=a.customer_id AND t.transaction_type='debit' "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 30 DAY)) as spend_30d, "
        "(SELECT COALESCE(SUM(t.amount),0) FROM transactions t JOIN accounts a2 ON t.account_id=a2.account_id "
        " WHERE a2.customer_id=a.customer_id AND t.transaction_type='debit' "
        " AND t.transaction_date BETWEEN DATE_SUB(NOW(), INTERVAL 60 DAY) AND DATE_SUB(NOW(), INTERVAL 30 DAY)) as spend_prior "
        "FROM accounts a WHERE a.status='ACTIVE' GROUP BY a.customer_id "
        "HAVING income_30d > 0 AND spend_30d > 0 AND spend_prior > 0"
    )
    count = 0
    for r in rows:
        cid = _val(r[0])
        income = float(_val(r[1]) or 0)
        spend_30 = float(_val(r[2]) or 0)
        spend_prior = float(_val(r[3]) or 0)
        savings_rate_now = (income - spend_30) / income if income > 0 else 0
        savings_rate_prior = (income - spend_prior) / income if income > 0 else 0
        if savings_rate_now > 0.25 and savings_rate_now > savings_rate_prior + 0.10:
            _insert_signal(cid, 'savings_rate_increasing', 0.75, {
                'savings_rate_current': round(savings_rate_now, 2),
                'savings_rate_prior': round(savings_rate_prior, 2),
                'increase_pct': round((savings_rate_now - savings_rate_prior) * 100, 1)
            })
            count += 1
    return count


def scan_peer_product_gap():
    """Detect customers whose peer cluster has high product adoption but they don't."""
    results = _neptune_query("""
        MATCH (c:Customer)
        WHERE NOT EXISTS { MATCH (c)-[:HAS_PRODUCT]->(:Product) }
        WITH c
        MATCH (c)-[:SIMILAR_TO]-(peer:Customer)-[:HAS_PRODUCT]->(p:Product)
        WITH c.`~id` as customer_id, count(DISTINCT peer) as peers_with_products, 
             count(DISTINCT p) as product_count
        WHERE peers_with_products >= 3
        RETURN customer_id, peers_with_products, product_count
    """)
    count = 0
    for r in results:
        cid = r.get('customer_id', '').replace('customer_', '')
        peers = r.get('peers_with_products', 0)
        if peers >= 3:
            _insert_signal(cid, 'peer_product_gap', 0.70, {'peers_with_products': peers, 'products_in_cluster': r.get('product_count', 0)})
            count += 1
    return count


def scan_subscription_spike():
    """Detect customers whose recurring spend increased >30% month-over-month."""
    rows = _sql(
        "SELECT a.customer_id, "
        "(SELECT COUNT(DISTINCT t.merchant_name) FROM transactions t JOIN accounts a2 ON t.account_id=a2.account_id "
        " WHERE a2.customer_id=a.customer_id AND t.transaction_type='debit' "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 30 DAY) "
        " AND t.merchant_name IN (SELECT t2.merchant_name FROM transactions t2 JOIN accounts a3 ON t2.account_id=a3.account_id "
        "   WHERE a3.customer_id=a.customer_id AND t2.transaction_type='debit' "
        "   AND t2.transaction_date BETWEEN DATE_SUB(NOW(), INTERVAL 60 DAY) AND DATE_SUB(NOW(), INTERVAL 30 DAY))) as recurring_merchants, "
        "(SELECT COALESCE(SUM(t.amount),0) FROM transactions t JOIN accounts a2 ON t.account_id=a2.account_id "
        " WHERE a2.customer_id=a.customer_id AND t.transaction_type='debit' AND t.category_id='CAT003' "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 30 DAY)) as entertainment_30d, "
        "(SELECT COALESCE(SUM(t.amount),0) FROM transactions t JOIN accounts a2 ON t.account_id=a2.account_id "
        " WHERE a2.customer_id=a.customer_id AND t.transaction_type='debit' AND t.category_id='CAT003' "
        " AND t.transaction_date BETWEEN DATE_SUB(NOW(), INTERVAL 60 DAY) AND DATE_SUB(NOW(), INTERVAL 30 DAY)) as entertainment_prior "
        "FROM accounts a WHERE a.status='ACTIVE' GROUP BY a.customer_id "
        "HAVING entertainment_30d > 0 AND entertainment_prior > 0"
    )
    count = 0
    for r in rows:
        cid = _val(r[0])
        ent_30 = float(_val(r[2]) or 0)
        ent_prior = float(_val(r[3]) or 0)
        if ent_prior > 0 and ent_30 / ent_prior > 1.3:
            _insert_signal(cid, 'recurring_spend_spike', 0.70, {
                'entertainment_current': round(ent_30, 2),
                'entertainment_prior': round(ent_prior, 2),
                'increase_pct': round((ent_30/ent_prior - 1) * 100, 1)
            })
            count += 1
    return count


def handler(event, context):
    """Lambda handler — runs all pattern scans."""
    logger.info("Starting pattern scan")

    # Clear expired signals
    _sql("DELETE FROM customer_signals WHERE expires_at < NOW()")

    results = {}
    results['large_balance_idle'] = scan_large_balance_idle()
    results['savings_rate_increasing'] = scan_savings_rate_increasing()
    results['peer_product_gap'] = scan_peer_product_gap()
    results['subscription_spike'] = scan_subscription_spike()

    total = sum(results.values())
    logger.info(f"Pattern scan complete: {total} signals detected — {results}")

    return {'statusCode': 200, 'signals_detected': total, 'breakdown': results}
