"""Neptune Graph Enrichment Pipeline — daily sync + analytics + peer stat materialization.

Pipeline steps:
1. SYNC: Aurora → Neptune (customer properties, products, goals, transactions)
2. ANALYTICS: Run Louvain communities + compute SIMILAR_TO edges
3. ENRICH: Add template-specific edges (IDLE_BALANCE, NEEDS_ALERT, etc.)
4. MATERIALIZE: Compute per-customer peer stats for each NBA template

Triggered daily by EventBridge → Step Function (before NBA batch).
"""
import boto3, json, logging, os, uuid
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLUSTER = os.environ.get('AURORA_CLUSTER_ARN',
    'arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr')
SECRET = os.environ.get('AURORA_SECRET_ARN',
    'arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6')
DB = 'corebanking'
GRAPH_ID = 'g-ruhyz8aj39'

rds = boto3.client('rds-data', region_name='eu-west-1')
neptune = boto3.client('neptune-graph', region_name='eu-west-1')


def _sql(sql, params=None):
    kwargs = dict(resourceArn=CLUSTER, secretArn=SECRET, database=DB, sql=sql)
    if params:
        kwargs['parameters'] = params
    return rds.execute_statement(**kwargs).get('records', [])


def _val(cell):
    if cell.get('isNull'):
        return None
    return cell.get('stringValue') or cell.get('longValue') or cell.get('doubleValue')


def _gq(cypher):
    """Execute Neptune openCypher query."""
    resp = neptune.execute_query(graphIdentifier=GRAPH_ID, queryString=cypher, language='OPEN_CYPHER')
    return json.loads(resp['payload'].read()).get('results', [])


# ═══════════════════════════════════════════════════════════════
# STEP 1: SYNC Aurora → Neptune
# ═══════════════════════════════════════════════════════════════

def step1_sync():
    """Sync latest Aurora data into Neptune graph."""
    logger.info("Step 1: Syncing Aurora → Neptune")

    # 1a. Sync customer properties (FHS, income, balance band)
    rows = _sql(
        "SELECT c.customer_id, cfh.score, cfh.band, "
        "(SELECT COALESCE(SUM(balance),0) FROM accounts WHERE customer_id=c.customer_id AND status='ACTIVE') as balance, "
        "(SELECT AVG(t.amount) FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        " WHERE a.customer_id=c.customer_id AND t.transaction_type='credit' AND t.amount>400 "
        " AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 90 DAY)) as income "
        "FROM customers c LEFT JOIN customer_financial_health cfh ON c.customer_id=cfh.customer_id "
        "WHERE c.status='ACTIVE'")

    updated = 0
    for r in rows:
        cid = _val(r[0])
        fhs = int(_val(r[1]) or 0)
        band = _val(r[2]) or 'unknown'
        balance = float(_val(r[3]) or 0)
        income = float(_val(r[4]) or 0)
        # Compute bands
        balance_band = 'high' if balance > 10000 else 'medium' if balance > 3000 else 'low'
        income_band = 'high' if income > 2000 else 'medium' if income > 1000 else 'low'

        _gq(f"""
            MATCH (c:Customer {{`~id`:'{cid}'}})
            SET c.fhs_score = {fhs}, c.fhs_band = '{band}',
                c.balance_band = '{balance_band}', c.income_band = '{income_band}',
                c.balance = {balance}, c.monthly_income = {income}
        """)
        updated += 1

    # 1b. Sync customer_products → HAS_PRODUCT edges
    # First ensure Product nodes exist for all product types
    prod_rows = _sql("SELECT DISTINCT product_type, product_name FROM product_catalog WHERE status='active'")
    for pr in prod_rows:
        ptype = _val(pr[0])
        pname = _val(pr[1])
        _gq(f"MERGE (p:Product {{`~id`:'product_{ptype}'}}) SET p.name = '{pname}'")

    # Sync active customer products
    cp_rows = _sql("SELECT customer_id, product_type FROM customer_products WHERE status='active'")
    for cp in cp_rows:
        cid = _val(cp[0])
        ptype = _val(cp[1])
        _gq(f"""
            MATCH (c:Customer {{`~id`:'{cid}'}})
            MATCH (p:Product {{`~id`:'product_{ptype}'}})
            MERGE (c)-[:HAS_PRODUCT]->(p)
        """)

    # 1c. Sync customer_goals → HAS_GOAL edges
    goal_rows = _sql("SELECT customer_id, goal_type FROM customer_goals WHERE status='active'")
    for g in goal_rows:
        cid = _val(g[0])
        gtype = _val(g[1])
        _gq(f"""
            MATCH (c:Customer {{`~id`:'{cid}'}})
            MERGE (c)-[:HAS_GOAL]->(:Goal {{`~id`:'goal_{cid}_{gtype}', type: '{gtype}'}})
        """)

    logger.info(f"Step 1 complete: {updated} customers synced")
    return updated


# ═══════════════════════════════════════════════════════════════
# STEP 2: RUN Neptune Analytics (community detection + similarity)
# ═══════════════════════════════════════════════════════════════

def step2_analytics():
    """Compute SIMILAR_TO edges based on shared merchant patterns."""
    logger.info("Step 2: Computing similarity edges")

    # Community IDs already set from step1_sync (synced from graph context Lambda)
    # Just create SIMILAR_TO edges based on shared merchants

    # Remove old SIMILAR_TO edges
    _gq("MATCH ()-[r:SIMILAR_TO]->() DELETE r")

    # Create SIMILAR_TO: customers sharing 5+ merchants are truly similar (out of ~30 total)
    # Also require same income_band OR same fhs_band for relevance
    _gq("""
        MATCH (c1:Customer)-[:TRANSACTS_WITH]->(m:Merchant)<-[:TRANSACTS_WITH]-(c2:Customer)
        WHERE c1.`~id` < c2.`~id`
        WITH c1, c2, count(DISTINCT m) as shared
        WHERE shared >= 5 AND (c1.income_band = c2.income_band OR c1.fhs_band = c2.fhs_band)
        CREATE (c1)-[:SIMILAR_TO {score: shared}]->(c2)
        CREATE (c2)-[:SIMILAR_TO {score: shared}]->(c1)
    """)

    sim_count = _gq("MATCH ()-[r:SIMILAR_TO]->() RETURN count(r) as cnt")
    count = sim_count[0].get('cnt', 0) if sim_count else 0
    logger.info(f"Step 2 complete: {count} SIMILAR_TO edges created")
    return {'similar_edges': count}


# ═══════════════════════════════════════════════════════════════
# STEP 3: ENRICH graph with template-specific markers
# ═══════════════════════════════════════════════════════════════

def step3_enrich():
    """Enrich graph with template markers AND detect behavioural signals (merged pattern scanner)."""
    logger.info("Step 3: Enriching graph + detecting signals")

    # 3a. Compute template markers on Customer nodes
    _gq("""
        MATCH (c:Customer)
        WHERE c.balance > 0 AND c.monthly_income > 0
        SET c.balance_to_income_ratio = c.balance / c.monthly_income
    """)

    _gq("""
        MATCH (c:Customer)-[:TRANSACTS_WITH]->(m:Merchant)
        WITH c, count(DISTINCT m) as merchant_count
        SET c.merchant_count = merchant_count
    """)

    _gq("""
        MATCH (c:Customer)
        SET c.eligible_home_loan = CASE
            WHEN c.monthly_income >= 1500 AND c.fhs_score >= 75 THEN true
            ELSE false END
    """)

    # 3b. Detect behavioural signals from graph properties (replaces Aurora-based pattern scanner)
    # Signal: large_balance_idle — balance > 4x income, no FD product
    idle_results = _gq("""
        MATCH (c:Customer)
        WHERE c.balance_to_income_ratio >= 4 AND c.balance > 2000
        OPTIONAL MATCH (c)-[:HAS_PRODUCT]->(fd:Product {`~id`:'product_fixed_deposit'})
        WITH c WHERE fd IS NULL
        RETURN c.`~id` as customer_id, c.balance as balance, c.balance_to_income_ratio as ratio
    """)

    # Signal: subscription_heavy — 6+ merchants (high recurring spend risk)
    sub_results = _gq("""
        MATCH (c:Customer)
        WHERE c.merchant_count >= 6
        RETURN c.`~id` as customer_id, c.merchant_count as merchants
    """)

    # Signal: peer_product_gap — similar peers have products, customer doesn't
    gap_results = _gq("""
        MATCH (c:Customer)
        OPTIONAL MATCH (c)-[:HAS_PRODUCT]->(owned:Product)
        WITH c WHERE owned IS NULL
        MATCH (c)-[:SIMILAR_TO]-(peer:Customer)-[:HAS_PRODUCT]->(p:Product)
        WITH c, count(DISTINCT peer) as peers_with_products
        WHERE peers_with_products >= 2
        RETURN c.`~id` as customer_id, peers_with_products
    """)

    # Write signals to Aurora customer_signals table
    import uuid
    # Clear expired signals first
    _sql("DELETE FROM customer_signals WHERE expires_at < NOW()")

    signal_count = 0
    for r in idle_results:
        cid = r.get('customer_id', '')
        _sql("INSERT IGNORE INTO customer_signals (signal_id, customer_id, signal_type, confidence, attributes, expires_at) "
             "VALUES (:sid, :cid, 'large_balance_idle', 0.80, :attrs, DATE_ADD(NOW(), INTERVAL 7 DAY))",
             [{'name':'sid','value':{'stringValue':f"sig_{uuid.uuid4().hex[:12]}"}},
              {'name':'cid','value':{'stringValue':cid}},
              {'name':'attrs','value':{'stringValue':json.dumps({'balance':r.get('balance',0),'ratio':r.get('ratio',0)})}}])
        signal_count += 1

    for r in sub_results:
        cid = r.get('customer_id', '')
        _sql("INSERT IGNORE INTO customer_signals (signal_id, customer_id, signal_type, confidence, attributes, expires_at) "
             "VALUES (:sid, :cid, 'subscription_heavy', 0.70, :attrs, DATE_ADD(NOW(), INTERVAL 7 DAY))",
             [{'name':'sid','value':{'stringValue':f"sig_{uuid.uuid4().hex[:12]}"}},
              {'name':'cid','value':{'stringValue':cid}},
              {'name':'attrs','value':{'stringValue':json.dumps({'merchant_count':r.get('merchants',0)})}}])
        signal_count += 1

    for r in gap_results:
        cid = r.get('customer_id', '')
        _sql("INSERT IGNORE INTO customer_signals (signal_id, customer_id, signal_type, confidence, attributes, expires_at) "
             "VALUES (:sid, :cid, 'peer_product_gap', 0.70, :attrs, DATE_ADD(NOW(), INTERVAL 7 DAY))",
             [{'name':'sid','value':{'stringValue':f"sig_{uuid.uuid4().hex[:12]}"}},
              {'name':'cid','value':{'stringValue':cid}},
              {'name':'attrs','value':{'stringValue':json.dumps({'peers_with_products':r.get('peers_with_products',0)})}}])
        signal_count += 1

    logger.info(f"Step 3 complete: enriched + {signal_count} signals detected "
                f"(idle={len(idle_results)}, subs={len(sub_results)}, gap={len(gap_results)})")
    return {'signals_detected': signal_count, 'idle': len(idle_results), 
            'subscription_heavy': len(sub_results), 'peer_gap': len(gap_results)}


# ═══════════════════════════════════════════════════════════════
# STEP 4: MATERIALIZE peer stats per customer per template
# ═══════════════════════════════════════════════════════════════

def step4_materialize():
    """Compute peer stats using SIMILAR_TO edges consistently for all metrics."""
    logger.info("Step 4: Materializing peer stats (all from SIMILAR_TO peers)")

    # 4a. peer_count: number of similar peers (for display)
    _gq("""
        MATCH (c:Customer)
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer:Customer)
        WITH c, count(DISTINCT peer) as cnt
        SET c.peer_count = cnt
    """)

    # 4b. peer_pct_home_loan: % of SIMILAR peers with home loan
    _gq("""
        MATCH (c:Customer)
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer:Customer)
        WITH c, count(DISTINCT peer) as total_peers
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer2:Customer)-[:HAS_PRODUCT]->(p:Product {`~id`:'product_home_loan'})
        WITH c, total_peers, count(DISTINCT peer2) as with_product
        SET c.peer_pct_home_loan = CASE WHEN total_peers > 0 THEN toInteger(1000.0 * with_product / total_peers) / 10.0 ELSE 0 END
    """)

    # 4c. peer_pct_products: % of SIMILAR peers with ANY product
    _gq("""
        MATCH (c:Customer)
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer:Customer)
        WITH c, count(DISTINCT peer) as total_peers
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer2:Customer)-[:HAS_PRODUCT]->(p:Product)
        WITH c, total_peers, count(DISTINCT peer2) as with_product
        SET c.peer_pct_products = CASE WHEN total_peers > 0 THEN toInteger(1000.0 * with_product / total_peers) / 10.0 ELSE 0 END
    """)

    # 4d. peer_avg_merchants: avg merchant count among SIMILAR peers
    _gq("""
        MATCH (c:Customer)
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer:Customer)-[:TRANSACTS_WITH]->(m:Merchant)
        WITH c, peer, count(DISTINCT m) as peer_merchants
        WITH c, avg(peer_merchants) as avg_m
        SET c.peer_avg_merchants = toInteger(avg_m * 10) / 10.0
    """)

    # 4e. peer_pct_goals: % of SIMILAR peers with savings goals
    _gq("""
        MATCH (c:Customer)
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer:Customer)
        WITH c, count(DISTINCT peer) as total_peers
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer2:Customer)-[:HAS_GOAL]->()
        WITH c, total_peers, count(DISTINCT peer2) as with_goals
        SET c.peer_pct_goals = CASE WHEN total_peers > 0 THEN toInteger(1000.0 * with_goals / total_peers) / 10.0 ELSE 0 END
    """)

    # 4f. peer_pct_high_txn: % of SIMILAR peers with high-value transactions (need alerts)
    # Proxy: peers with balance > 5000 (likely have high-value transactions)
    _gq("""
        MATCH (c:Customer)
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer:Customer)
        WITH c, count(DISTINCT peer) as total_peers
        OPTIONAL MATCH (c)-[:SIMILAR_TO]-(peer2:Customer)
        WHERE peer2.balance > 5000
        WITH c, total_peers, count(DISTINCT peer2) as high_balance_peers
        SET c.peer_pct_high_balance = CASE WHEN total_peers > 0 THEN toInteger(1000.0 * high_balance_peers / total_peers) / 10.0 ELSE 0 END
    """)

    logger.info("Step 4 complete: all peer stats from SIMILAR_TO edges")


def handler(event, context):
    """Lambda handler — runs the full enrichment pipeline."""
    step = event.get('step', 'all')
    results = {}

    if step in ('all', 'sync'):
        results['sync'] = step1_sync()
    if step in ('all', 'analytics'):
        results['analytics'] = step2_analytics()
    if step in ('all', 'enrich'):
        results['enrich'] = step3_enrich()
    if step in ('all', 'materialize'):
        results['materialize'] = step4_materialize()

    logger.info(f"Pipeline complete: {results}")
    return {'statusCode': 200, 'results': results}
