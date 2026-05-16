"""get_graph_context MCP Tool — Neptune Database + Analytics queries.

Returns enriched graph context for NBA agents:
- Household members (joint accounts)
- Peer similarity (Jaccard from Analytics)
- Community membership (Louvain from Analytics)
- Top merchants by spend

Called by: Personal Reasoning Agent, NBA Batch Generator, NBA Real-Time Agent.
"""
import json, logging, os, boto3, urllib.request, ssl

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Neptune Database (Gremlin endpoint — VPC-internal)
NEPTUNE_DB_ENDPOINT = os.environ.get('NEPTUNE_DB_ENDPOINT',
    'aibank-nba-graph-db.cluster-cwzfjxlxp1pw.eu-west-1.neptune.amazonaws.com')
NEPTUNE_DB_PORT = 8182

# Neptune Analytics (openCypher — public endpoint)
NEPTUNE_ANALYTICS_GRAPH_ID = os.environ.get('NEPTUNE_ANALYTICS_GRAPH_ID', 'g-ruhyz8aj39')
neptune_graph_client = boto3.client('neptune-graph', region_name='eu-west-1')

# SSL context for Neptune Database (self-signed in VPC)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


def _gremlin(query):
    """Query Neptune Database via Gremlin HTTP endpoint."""
    url = f"https://{NEPTUNE_DB_ENDPOINT}:{NEPTUNE_DB_PORT}/gremlin"
    body = json.dumps({"gremlin": query}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req, timeout=15, context=SSL_CTX)
        return json.loads(resp.read())
    except Exception as e:
        logger.warning(f"Gremlin query failed: {e}")
        return None


def _analytics_query(cypher):
    """Query Neptune Analytics via openCypher."""
    try:
        resp = neptune_graph_client.execute_query(
            graphIdentifier=NEPTUNE_ANALYTICS_GRAPH_ID,
            queryString=cypher,
            language='OPEN_CYPHER'
        )
        return json.loads(resp['payload'].read())
    except Exception as e:
        logger.warning(f"Analytics query failed: {e}")
        return None


def get_household(customer_id):
    """Get household members via JOINT_HOLDER edges."""
    result = _analytics_query(
        f"MATCH (a {{`~id`: '{customer_id}'}})-[:JOINT_HOLDER]-(b:Customer) "
        f"RETURN b.`~id` as member_id"
    )
    if not result:
        return {"size": 1, "members": []}
    members = [r['member_id'] for r in result.get('results', [])]
    return {"size": len(members) + 1, "members": members}


def get_peer_similarity(customer_id, top_n=20):
    """Get top-N similar customers via Jaccard similarity."""
    result = _analytics_query(
        f"MATCH (a {{`~id`: '{customer_id}'}}) "
        f"MATCH (b:Customer) WHERE b <> a "
        f"CALL neptune.algo.jaccardSimilarity(a, b) YIELD score "
        f"WHERE score > 0.1 "
        f"RETURN b.`~id` as peer_id, score "
        f"ORDER BY score DESC LIMIT {top_n}"
    )
    if not result:
        return {"count": 0, "peers": [], "avg_score": 0}
    peers = result.get('results', [])
    avg_score = sum(p['score'] for p in peers) / len(peers) if peers else 0
    return {
        "count": len(peers),
        "avg_score": round(avg_score, 3),
        "top_peer_ids": [p['peer_id'] for p in peers[:5]],
    }


def get_community(customer_id):
    """Get customer's Louvain community and community stats."""
    # Get this customer's community_id
    result = _analytics_query(
        f"MATCH (n {{`~id`: '{customer_id}'}}) "
        f"RETURN n.community_id as community_id"
    )
    if not result or not result.get('results'):
        return {"community_id": None, "community_size": 0}

    community_id = result['results'][0].get('community_id')
    if community_id is None:
        return {"community_id": None, "community_size": 0}

    # Get community size
    size_result = _analytics_query(
        f"MATCH (n:Customer) WHERE n.community_id = {community_id} "
        f"RETURN count(n) as size"
    )
    size = size_result['results'][0]['size'] if size_result and size_result.get('results') else 0

    # Get merchants in same community
    merchants_result = _analytics_query(
        f"MATCH (m:Merchant) WHERE m.community_id = {community_id} "
        f"RETURN m.`~id` as merchant_id, m.name as merchant_name"
    )
    merchants = [r.get('merchant_name') or r.get('merchant_id') for r in (merchants_result or {}).get('results', [])]

    return {
        "community_id": community_id,
        "community_size": size,
        "shared_merchants": merchants[:10],
    }


def get_top_merchants(customer_id, limit=5):
    """Get customer's top merchants by spend."""
    result = _analytics_query(
        f"MATCH (a {{`~id`: '{customer_id}'}})-[t:TRANSACTS_WITH]->(m:Merchant) "
        f"RETURN m.`~id` as merchant_id, m.name as name, t.total_amount as spend, t.txn_count as txns "
        f"ORDER BY t.total_amount DESC LIMIT {limit}"
    )
    if not result:
        return []
    return [
        {"merchant": r.get('name') or r.get('merchant_id'), "spend_bhd": r.get('spend', 0), "txns": r.get('txns', 0)}
        for r in result.get('results', [])
    ]


def handler(event, context):
    """MCP tool handler — returns full graph context for a customer."""
    customer_id = event.get('customer_id')
    if not customer_id:
        return {"statusCode": 400, "error": "customer_id required"}

    context_types = event.get('context_types', ['household', 'peer_similarity', 'community', 'merchants'])

    result = {"customer_id": customer_id}

    if 'household' in context_types:
        result['household'] = get_household(customer_id)

    if 'peer_similarity' in context_types:
        result['peer_similarity'] = get_peer_similarity(customer_id)

    if 'community' in context_types:
        result['community'] = get_community(customer_id)

    if 'merchants' in context_types:
        result['top_merchants'] = get_top_merchants(customer_id)

    return {"statusCode": 200, **result}
