"""Customer 360 API — Employee RM Portal backend.

Endpoints:
  GET /c360/customers       — Portfolio list with summary metrics
  GET /c360/detail?id=X     — Full 360 view for a customer
  GET /c360/graph?id=X      — Neptune graph data (nodes + edges for D3.js)

Data sources:
  - Aurora MySQL (corebanking): customers, accounts, transactions, FHS, NBAs, products, signals
  - Neptune Analytics: peer stats, community, relationships (via direct API)
"""
import json, logging, os, boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

rds = boto3.client("rds-data", region_name="eu-west-1")
neptune = boto3.client("neptune-graph", region_name="eu-west-1")

CLUSTER = os.environ.get("AURORA_CLUSTER_ARN", "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr")
SECRET = os.environ.get("AURORA_SECRET_ARN", "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6")
DB = "corebanking"
GRAPH_ID = "g-ruhyz8aj39"
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "https://aibank.demoaws.com")


def _sql(sql, params=None):
    kwargs = {"resourceArn": CLUSTER, "secretArn": SECRET, "database": DB, "sql": sql, "includeResultMetadata": True}
    if params:
        kwargs["parameters"] = params
    return rds.execute_statement(**kwargs)


def _rows(resp):
    cols = [c["name"] for c in resp["columnMetadata"]]
    rows = []
    for rec in resp["records"]:
        row = {}
        for c, f in zip(cols, rec):
            if "stringValue" in f: row[c] = f["stringValue"]
            elif "longValue" in f: row[c] = f["longValue"]
            elif "doubleValue" in f: row[c] = round(f["doubleValue"], 3)
            elif "booleanValue" in f: row[c] = f["booleanValue"]
            elif "isNull" in f: row[c] = None
            else: row[c] = str(f)
        rows.append(row)
    return rows


def _gq(cypher):
    r = neptune.execute_query(graphIdentifier=GRAPH_ID, queryString=cypher, language='OPEN_CYPHER')
    return json.loads(r['payload'].read()).get('results', [])


def _cors(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=str),
    }


def lambda_handler(event, context):
    method = event.get("httpMethod", "GET")
    if method == "OPTIONS":
        return _cors(200, {})

    path = event.get("path") or event.get("rawPath", "")
    qs = event.get("queryStringParameters") or {}

    if "/c360/customers" in path:
        return handle_customers(event)
    elif "/c360/detail" in path:
        return handle_detail(qs.get("id", ""))
    elif "/c360/graph" in path:
        return handle_graph(qs.get("id", ""))
    return _cors(404, {"error": "Not found"})


def handle_customers(event):
    """List all customers with C360 summary."""
    resp = _sql("""
        SELECT c.customer_id, c.first_name, c.last_name, c.email, c.city, c.country,
               c.kyc_status, c.nationality,
               (SELECT SUM(balance) FROM accounts WHERE customer_id=c.customer_id AND status='ACTIVE') as total_balance,
               cfh.score as fhs_score, cfh.band as fhs_band,
               (SELECT COUNT(*) FROM next_best_actions WHERE customer_id=c.customer_id AND status='active') as active_nbas,
               (SELECT COUNT(*) FROM customer_products WHERE customer_id=c.customer_id AND status='active') as products_owned
        FROM customers c
        LEFT JOIN customer_financial_health cfh ON c.customer_id = cfh.customer_id
        WHERE c.status = 'ACTIVE'
        ORDER BY total_balance DESC
    """)
    return _cors(200, {"customers": _rows(resp), "count": len(resp.get("records", []))})


def handle_detail(cid):
    """Full 360 view for a single customer."""
    if not cid:
        return _cors(400, {"error": "id parameter required"})

    result = {}

    # 1. Profile
    resp = _sql("""
        SELECT customer_id, first_name, last_name, email, nationality, city, country,
               kyc_status, status
        FROM customers WHERE customer_id = :cid
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    profiles = _rows(resp)
    if not profiles:
        return _cors(404, {"error": "Customer not found"})
    result["profile"] = profiles[0]

    # 2. Accounts
    resp = _sql("""
        SELECT account_id, account_type, account_number, balance, currency, status
        FROM accounts WHERE customer_id = :cid ORDER BY balance DESC
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    result["accounts"] = _rows(resp)
    result["total_balance"] = sum(float(a.get("balance") or 0) for a in result["accounts"])

    # 3. Financial Health Score
    resp = _sql("""
        SELECT score, band, subscore_debt, subscore_savings, subscore_spending,
               subscore_income, subscore_credit, subscore_behavior,
               peer_percentile, trend_30d, calculated_at
        FROM customer_financial_health WHERE customer_id = :cid
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    fhs = _rows(resp)
    result["financial_health"] = fhs[0] if fhs else None

    # 4. Active NBAs
    resp = _sql("""
        SELECT action_id, template_id, category, priority, confidence, title, reasoning,
               metrics, source, product_type, status, generated_at, view_count
        FROM next_best_actions WHERE customer_id = :cid AND status = 'active'
        AND (expires_at IS NULL OR expires_at > NOW())
        ORDER BY priority DESC
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    result["next_best_actions"] = _rows(resp)

    # 5. Life Events
    resp = _sql("""
        SELECT event_id, event_type, detected_at, detection_source, confidence, attributes, status
        FROM customer_life_events WHERE customer_id = :cid
        ORDER BY detected_at DESC LIMIT 10
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    result["life_events"] = _rows(resp)

    # 6. Products Owned
    resp = _sql("""
        SELECT product_id, product_type, product_name, amount_bhd, status, purchased_at, receipt_id
        FROM customer_products WHERE customer_id = :cid
        ORDER BY purchased_at DESC
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    result["products"] = _rows(resp)

    # 7. Spending by Category (last 90 days)
    resp = _sql("""
        SELECT mc.category_name, COUNT(*) as txn_count, SUM(t.amount) as total_amount
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        JOIN merchant_categories mc ON t.category_id = mc.category_id
        WHERE a.customer_id = :cid AND t.transaction_type = 'debit'
          AND t.transaction_date >= DATE_SUB(NOW(), INTERVAL 90 DAY)
        GROUP BY mc.category_name ORDER BY total_amount DESC
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    result["spending_by_category"] = _rows(resp)

    # 8. Recent Transactions (last 15)
    resp = _sql("""
        SELECT t.transaction_date, t.description, t.merchant_name,
               t.transaction_type, t.amount, mc.category_name
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        LEFT JOIN merchant_categories mc ON t.category_id = mc.category_id
        WHERE a.customer_id = :cid
        ORDER BY t.transaction_date DESC LIMIT 15
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    result["recent_transactions"] = _rows(resp)

    # 9. Goals
    resp = _sql("""
        SELECT goal_type, goal_title, target_amount, current_amount, target_date, status
        FROM customer_goals WHERE customer_id = :cid ORDER BY target_date
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    result["goals"] = _rows(resp)

    # 10. Loan Applications
    resp = _sql("""
        SELECT application_id, loan_type, amount, status, monthly_payment, duration, interest, purpose
        FROM loan_applications WHERE customer_id = :cid ORDER BY application_id DESC LIMIT 5
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    result["loans"] = _rows(resp)

    # 11. Behavioural Signals
    resp = _sql("""
        SELECT signal_type, confidence, attributes, detected_at
        FROM customer_signals WHERE customer_id = :cid
        AND (expires_at IS NULL OR expires_at > NOW()) AND consumed_at IS NULL
        ORDER BY detected_at DESC
    """, [{"name": "cid", "value": {"stringValue": cid}}])
    result["signals"] = _rows(resp)

    # 12. Peer Stats (from Neptune — materialized on Customer node)
    try:
        peer_data = _gq(f"""
            MATCH (c:Customer {{`~id`:'{cid}'}})
            RETURN c.peer_count as peer_count, c.community_id as community_id,
                   c.peer_pct_home_loan as peer_pct_home_loan,
                   c.peer_pct_products as peer_pct_products,
                   c.peer_avg_merchants as peer_avg_merchants,
                   c.peer_pct_goals as peer_pct_goals,
                   c.peer_pct_high_balance as peer_pct_high_balance,
                   c.income_band as income_band, c.fhs_band as fhs_band
        """)
        result["peer_stats"] = peer_data[0] if peer_data else {}
    except Exception as e:
        logger.warning(f"Neptune peer stats failed: {e}")
        result["peer_stats"] = {}

    return _cors(200, result)


def handle_graph(cid):
    """Return graph data for D3.js visualization."""
    if not cid:
        return _cors(400, {"error": "id parameter required"})

    nodes = []
    edges = []

    try:
        # Customer node (center)
        nodes.append({"id": cid, "type": "customer", "label": cid, "size": 30})

        # Merchants
        merchants = _gq(f"""
            MATCH (c:Customer {{`~id`:'{cid}'}})-[:TRANSACTS_WITH]->(m:Merchant)
            RETURN m.`~id` as id, m.name as name
        """)
        for m in merchants:
            nodes.append({"id": m["id"], "type": "merchant", "label": m.get("name", m["id"])})
            edges.append({"source": cid, "target": m["id"], "type": "TRANSACTS_WITH"})

        # Products
        products = _gq(f"""
            MATCH (c:Customer {{`~id`:'{cid}'}})-[:HAS_PRODUCT]->(p:Product)
            RETURN p.`~id` as id, p.name as name
        """)
        for p in products:
            nodes.append({"id": p["id"], "type": "product", "label": p.get("name", p["id"])})
            edges.append({"source": cid, "target": p["id"], "type": "HAS_PRODUCT"})

        # Top 5 similar peers
        peers = _gq(f"""
            MATCH (c:Customer {{`~id`:'{cid}'}})-[r:SIMILAR_TO]->(peer:Customer)
            RETURN peer.`~id` as id, r.score as score, peer.fhs_score as fhs
            ORDER BY r.score DESC LIMIT 5
        """)
        for p in peers:
            nodes.append({"id": p["id"], "type": "peer", "label": p["id"], "fhs": p.get("fhs")})
            edges.append({"source": cid, "target": p["id"], "type": "SIMILAR_TO", "weight": p.get("score", 1)})

        # Goals
        goals = _gq(f"""
            MATCH (c:Customer {{`~id`:'{cid}'}})-[:HAS_GOAL]->(g:Goal)
            RETURN g.`~id` as id, g.type as goal_type
        """)
        for g in goals:
            nodes.append({"id": g["id"], "type": "goal", "label": g.get("goal_type", g["id"])})
            edges.append({"source": cid, "target": g["id"], "type": "HAS_GOAL"})

        # Household (joint holders)
        household = _gq(f"""
            MATCH (c:Customer {{`~id`:'{cid}'}})-[:JOINT_HOLDER]->(other:Customer)
            RETURN other.`~id` as id
        """)
        for h in household:
            nodes.append({"id": h["id"], "type": "household", "label": h["id"]})
            edges.append({"source": cid, "target": h["id"], "type": "HOUSEHOLD"})

        # Shared merchants between customer and peers (shows WHY they're similar)
        if peers:
            top_peer = peers[0]["id"]
            shared = _gq(f"""
                MATCH (me:Customer {{`~id`:'{cid}'}})-[:TRANSACTS_WITH]->(m:Merchant)<-[:TRANSACTS_WITH]-(peer:Customer {{`~id`:'{top_peer}'}})
                RETURN m.`~id` as merchant_id
            """)
            for s in shared:
                mid = s["merchant_id"]
                # Add edge from peer to shared merchant (merchant node already exists)
                if any(n["id"] == mid for n in nodes):
                    edges.append({"source": top_peer, "target": mid, "type": "TRANSACTS_WITH"})

    except Exception as e:
        logger.warning(f"Graph query failed: {e}")

    return _cors(200, {"nodes": nodes, "edges": edges, "center": cid})
