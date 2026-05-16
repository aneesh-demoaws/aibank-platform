import json, urllib.request, ssl, os

NEPTUNE = os.environ.get('NEPTUNE_ENDPOINT',
    'aibank-nba-graph-db.cluster-cwzfjxlxp1pw.eu-west-1.neptune.amazonaws.com')
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

def gremlin(query):
    url = f"https://{NEPTUNE}:8182/gremlin"
    body = json.dumps({"gremlin": query}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req, timeout=30, context=CTX)
    return json.loads(resp.read())

def handler(event, context):
    results = {}
    results['total_vertices'] = gremlin("g.V().count()")['result']['data']['@value'][0]['@value']
    results['total_edges'] = gremlin("g.E().count()")['result']['data']['@value'][0]['@value']
    results['customer_nodes'] = gremlin("g.V().hasLabel('Customer').count()")['result']['data']['@value'][0]['@value']
    results['account_nodes'] = gremlin("g.V().hasLabel('Account').count()")['result']['data']['@value'][0]['@value']
    results['merchant_nodes'] = gremlin("g.V().hasLabel('Merchant').count()")['result']['data']['@value'][0]['@value']
    results['has_account_edges'] = gremlin("g.E().hasLabel('HAS_ACCOUNT').count()")['result']['data']['@value'][0]['@value']
    results['joint_holder_edges'] = gremlin("g.E().hasLabel('JOINT_HOLDER').count()")['result']['data']['@value'][0]['@value']
    results['transacts_with_edges'] = gremlin("g.E().hasLabel('TRANSACTS_WITH').count()")['result']['data']['@value'][0]['@value']
    return results
