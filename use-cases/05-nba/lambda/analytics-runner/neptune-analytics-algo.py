import json, boto3, os

GRAPH_ID = os.environ.get('GRAPH_ID', 'g-ruhyz8aj39')
REGION = 'eu-west-1'

client = boto3.client('neptune-graph', region_name=REGION)

def handler(event, context):
    algorithm = event.get('algorithm', 'stats')
    
    queries = {
        'stats': "MATCH (n) RETURN labels(n)[0] as label, count(n) as cnt",
        'louvain': "CALL neptune.algo.louvain.mutate({writeProperty: 'community_id', edgeLabels: ['TRANSACTS_WITH']}) YIELD success RETURN success",
        'labelPropagation': "CALL neptune.algo.labelPropagation.mutate({writeProperty: 'customer_community', edgeLabels: ['TRANSACTS_WITH'], vertexLabel: 'Customer'}) YIELD success RETURN success",
        'jaccard': None,  # handled separately
        'verify_communities': "MATCH (n) WHERE n.community_id IS NOT NULL RETURN labels(n)[0] as label, n.community_id as community, count(*) as cnt ORDER BY cnt DESC LIMIT 20",
        'verify_customer_communities': "MATCH (n:Customer) WHERE n.customer_community IS NOT NULL RETURN n.customer_community as community, count(*) as cnt ORDER BY cnt DESC LIMIT 10",
    }
    
    if algorithm == 'jaccard':
        cust = event.get('customer_id', 'CUST20250100')
        query = f"MATCH (a {{`~id`: '{cust}'}}) MATCH (b) WHERE b <> a AND labels(b)[0] = 'Customer' CALL neptune.algo.jaccardSimilarity(a, b) YIELD score WHERE score > 0.1 RETURN b.`~id` as similar_customer, score ORDER BY score DESC LIMIT 20"
    else:
        query = queries.get(algorithm)
        if not query:
            return {"error": f"Unknown algorithm: {algorithm}"}
    
    try:
        # Set longer timeout for mutate algorithms
        timeout_ms = 300000 if 'mutate' in algorithm or algorithm in ('louvain', 'labelPropagation') else 30000
        
        resp = client.execute_query(
            graphIdentifier=GRAPH_ID,
            queryString=query,
            language='OPEN_CYPHER',
            parameters={'queryTimeoutMilliseconds': timeout_ms}
        )
        payload = json.loads(resp['payload'].read())
        return {"statusCode": 200, "algorithm": algorithm, "result": payload}
    except Exception as e:
        return {"statusCode": 500, "error": str(e)[:500], "type": type(e).__name__}
