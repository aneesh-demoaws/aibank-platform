"""Neptune → S3 Export for Athena/QuickSight.

Exports materialized peer stats from Neptune Analytics to S3 CSV.
Glue catalog table (neptune_c360.customer_peer_stats) reads from this export.
Run daily as part of the NBA enrichment pipeline.
"""
import boto3, json, csv, io, os

neptune = boto3.client('neptune-graph', region_name='eu-west-1')
s3 = boto3.client('s3', region_name='eu-west-1')
GRAPH_ID = os.environ.get('NEPTUNE_GRAPH_ID', 'g-ruhyz8aj39')
BUCKET = os.environ.get('EXPORT_BUCKET', 'aibank-athena-results-eu-west-1')


def handler(event, context):
    # Export customer peer stats
    r = neptune.execute_query(graphIdentifier=GRAPH_ID, queryString="""
        MATCH (c:Customer)
        RETURN c.`~id` as customer_id, c.community_id as community_id,
               c.fhs_score as fhs_score, c.fhs_band as fhs_band,
               c.income_band as income_band, c.balance as balance,
               c.peer_count as peer_count,
               c.peer_pct_home_loan as peer_pct_home_loan,
               c.peer_pct_products as peer_pct_products,
               c.peer_avg_merchants as peer_avg_merchants,
               c.peer_pct_goals as peer_pct_goals,
               c.peer_pct_high_balance as peer_pct_high_balance,
               c.merchant_count as merchant_count,
               c.eligible_home_loan as eligible_home_loan
    """, language='OPEN_CYPHER')
    results = json.loads(r['payload'].read()).get('results', [])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['customer_id', 'community_id', 'fhs_score', 'fhs_band', 'income_band',
                     'balance', 'peer_count', 'peer_pct_home_loan', 'peer_pct_products',
                     'peer_avg_merchants', 'peer_pct_goals', 'peer_pct_high_balance',
                     'merchant_count', 'eligible_home_loan'])
    for row in results:
        writer.writerow([row.get(k, '') for k in ['customer_id', 'community_id', 'fhs_score',
                         'fhs_band', 'income_band', 'balance', 'peer_count', 'peer_pct_home_loan',
                         'peer_pct_products', 'peer_avg_merchants', 'peer_pct_goals',
                         'peer_pct_high_balance', 'merchant_count', 'eligible_home_loan']])

    s3.put_object(Bucket=BUCKET, Key='neptune-exports/customer_peer_stats.csv',
                  Body=output.getvalue(), ContentType='text/csv')

    return {'statusCode': 200, 'exported': len(results)}
