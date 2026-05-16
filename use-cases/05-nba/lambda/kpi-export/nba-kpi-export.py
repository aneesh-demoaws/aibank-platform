"""NBA KPI Export — generates daily KPI CSV for QuickSight SPICE import.

Runs daily (06:00 UTC). Queries Aurora for NBA metrics and writes
aggregated CSVs to S3 for QuickSight to ingest.
"""
import json, boto3, csv, io
from datetime import datetime, timezone

rds = boto3.client('rds-data', region_name='eu-west-1')
s3 = boto3.client('s3', region_name='eu-west-1')

CLUSTER = "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr"
SECRET = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"
DB = "corebanking"
BUCKET = "aibank-ui-prod-eu-west-1"
PREFIX = "quicksight/nba-insights"


def _sql(sql):
    return rds.execute_statement(
        resourceArn=CLUSTER, secretArn=SECRET, database=DB, sql=sql
    ).get('records', [])


def _val(cell):
    if cell.get('isNull'):
        return None
    return list(cell.values())[0]


def export_nba_overview():
    """KPI 1-4: Coverage, CTR, Conversion, Dismiss rate."""
    rows = _sql("""
        SELECT 
            (SELECT COUNT(DISTINCT customer_id) FROM next_best_actions WHERE status='active') as customers_with_nba,
            (SELECT COUNT(*) FROM customers WHERE status='active' OR status IS NULL) as total_customers,
            (SELECT COUNT(*) FROM next_best_actions WHERE status='active') as active_nbas,
            (SELECT COUNT(*) FROM nba_interactions WHERE event='viewed') as total_views,
            (SELECT COUNT(*) FROM nba_interactions WHERE event='clicked') as total_clicks,
            (SELECT COUNT(*) FROM nba_interactions WHERE event='converted') as total_conversions,
            (SELECT COUNT(*) FROM nba_interactions WHERE event='dismissed') as total_dismisses
    """)
    if not rows:
        return {}
    r = rows[0]
    vals = [_val(r[i]) or 0 for i in range(7)]
    customers_with_nba, total_customers, active_nbas, views, clicks, conversions, dismisses = vals
    
    return {
        'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'nba_coverage_pct': round(customers_with_nba / total_customers * 100, 1) if total_customers else 0,
        'total_customers': total_customers,
        'customers_with_nba': customers_with_nba,
        'active_nbas': active_nbas,
        'total_views': views,
        'total_clicks': clicks,
        'total_conversions': conversions,
        'total_dismisses': dismisses,
        'ctr_pct': round(clicks / views * 100, 1) if views else 0,
        'conversion_rate_pct': round(conversions / clicks * 100, 1) if clicks else 0,
        'dismiss_rate_pct': round(dismisses / (views or 1) * 100, 1),
    }


def export_category_breakdown():
    """NBAs by category."""
    rows = _sql("""
        SELECT category, COUNT(*) as cnt, 
               AVG(priority) as avg_priority,
               COUNT(CASE WHEN source='agent' THEN 1 END) as realtime_count
        FROM next_best_actions WHERE status='active'
        GROUP BY category ORDER BY cnt DESC
    """)
    results = []
    for r in rows:
        results.append({
            'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'category': _val(r[0]),
            'count': _val(r[1]),
            'avg_priority': round(float(_val(r[2]) or 0), 1),
            'realtime_count': _val(r[3]) or 0,
        })
    return results


def export_fhs_distribution():
    """FHS band distribution."""
    rows = _sql("""
        SELECT band, COUNT(*) as cnt, AVG(score) as avg_score
        FROM customer_financial_health
        GROUP BY band
    """)
    results = []
    for r in rows:
        results.append({
            'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            'band': _val(r[0]),
            'customer_count': _val(r[1]),
            'avg_score': round(float(_val(r[2]) or 0), 1),
        })
    return results


def write_csv(data, key):
    """Write list of dicts to S3 as CSV."""
    if not data:
        return
    if isinstance(data, dict):
        data = [data]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)
    s3.put_object(Bucket=BUCKET, Key=key, Body=output.getvalue(),
                  ContentType='text/csv')


def handler(event, context):
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    
    # Export all KPI datasets
    overview = export_nba_overview()
    write_csv(overview, f"{PREFIX}/overview/{today}.csv")
    write_csv(overview, f"{PREFIX}/overview/latest.csv")
    
    categories = export_category_breakdown()
    write_csv(categories, f"{PREFIX}/categories/{today}.csv")
    write_csv(categories, f"{PREFIX}/categories/latest.csv")
    
    fhs = export_fhs_distribution()
    write_csv(fhs, f"{PREFIX}/fhs/{today}.csv")
    write_csv(fhs, f"{PREFIX}/fhs/latest.csv")
    
    return {
        'statusCode': 200,
        'exported': {
            'overview': bool(overview),
            'categories': len(categories),
            'fhs': len(fhs),
        },
        'date': today,
    }
