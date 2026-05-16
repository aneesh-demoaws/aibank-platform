"""Cash-flow Scanner — detects upcoming debit shortfalls.

Runs daily (09:00 UTC via EventBridge). For each customer:
1. Find recurring debits (same merchant, similar amount, monthly pattern)
2. Project balance forward 7 days
3. If projected_balance < upcoming_debit: emit cashflow.shortfall_predicted

The NBA Real-Time Agent (or Alma's next_best_action node) picks up the event
and generates the "rescue" NBA.
"""
import json, logging, boto3
from datetime import datetime, timezone

logger = logging.getLogger()
logger.setLevel(logging.INFO)

rds = boto3.client('rds-data', region_name='eu-west-1')
events = boto3.client('events', region_name='eu-west-1')

CLUSTER = "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr"
SECRET = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"
DB = "corebanking"


def _sql(sql, params=None):
    kwargs = dict(resourceArn=CLUSTER, secretArn=SECRET, database=DB, sql=sql)
    if params:
        kwargs['parameters'] = params
    return rds.execute_statement(**kwargs).get('records', [])


def _val(cell):
    if cell.get('isNull'):
        return None
    return list(cell.values())[0]


def scan_customer(customer_id):
    """Check if customer has an upcoming debit they can't cover."""
    params = [{'name': 'cid', 'value': {'stringValue': customer_id}}]

    # Current balance
    bal_rows = _sql(
        "SELECT SUM(balance) FROM accounts WHERE customer_id=:cid AND status='ACTIVE'", params)
    balance = float(_val(bal_rows[0][0]) or 0) if bal_rows else 0

    # Find recurring debits (same merchant, appeared 2+ times in last 90 days)
    recurring = _sql(
        "SELECT t.merchant_name, AVG(t.amount) as avg_amount, COUNT(*) as occurrences, "
        "MAX(t.transaction_date) as last_date "
        "FROM transactions t JOIN accounts a ON t.account_id=a.account_id "
        "WHERE a.customer_id=:cid AND t.transaction_type='debit' "
        "AND t.transaction_date > DATE_SUB(NOW(), INTERVAL 90 DAY) "
        "GROUP BY t.merchant_name HAVING COUNT(*) >= 2 AND AVG(t.amount) > 10 "
        "ORDER BY avg_amount DESC", params)

    # Project: for each recurring debit, check if it's due in next 7 days
    # Simple heuristic: if last occurrence was ~30 days ago, it's due soon
    shortfalls = []
    for r in recurring:
        merchant = _val(r[0])
        avg_amount = float(_val(r[1]) or 0)
        last_date_str = str(_val(r[3]))[:10]

        try:
            last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
            days_since = (datetime.now() - last_date).days
            # If 25-35 days since last debit, it's likely due in next 7 days
            if 25 <= days_since <= 35:
                projected_balance = balance - avg_amount
                if projected_balance < 0:
                    shortfall = abs(projected_balance)
                    shortfalls.append({
                        'bill_name': merchant,
                        'bill_amount_bhd': round(avg_amount, 3),
                        'projected_shortfall_bhd': round(shortfall, 3),
                        'suggested_transfer_bhd': round(avg_amount + 10, 3),
                        'days_until_due': 35 - days_since,
                        'current_balance': round(balance, 3),
                    })
        except (ValueError, TypeError):
            continue

    return shortfalls


def handler(event, context):
    """Scan all customers or a specific list."""
    customer_ids = event.get('customer_ids')

    if not customer_ids:
        rows = _sql("SELECT customer_id FROM customers WHERE status='active' OR status IS NULL LIMIT 500")
        customer_ids = [_val(r[0]) for r in rows]

    logger.info(f"Scanning {len(customer_ids)} customers for cash-flow shortfalls")

    alerts_emitted = 0
    for cid in customer_ids:
        shortfalls = scan_customer(cid)
        for sf in shortfalls:
            # Emit event for NBA Real-Time Agent
            events.put_events(Entries=[{
                'Source': 'aibank.nba',
                'DetailType': 'cashflow.shortfall_predicted',
                'Detail': json.dumps({
                    'customer_id': cid,
                    'event_type': 'cashflow.shortfall_predicted',
                    **sf,
                })
            }])
            alerts_emitted += 1
            logger.info(f"  Shortfall: {cid} — {sf['bill_name']} BHD {sf['bill_amount_bhd']}, "
                        f"short by BHD {sf['projected_shortfall_bhd']}")

    return {
        'statusCode': 200,
        'customers_scanned': len(customer_ids),
        'alerts_emitted': alerts_emitted,
    }
