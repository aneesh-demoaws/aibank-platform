"""AIBank KYC — DynamoDB Stream → Aurora Sync. Syncs kyc_status to core banking."""
import json, logging, os, boto3
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DB_REGION = os.environ.get('DB_REGION', 'me-south-1')
CLUSTER_ARN = os.environ['CLUSTER_ARN']
SECRET_ARN = os.environ['SECRET_ARN']
DB_NAME = os.environ.get('DB_NAME', 'corebanking')

rds = boto3.client('rds-data', region_name=DB_REGION)

VALID_STATUSES = {'PENDING', 'PROCESSING', 'VERIFIED', 'REJECTED', 'EXPIRED'}

def lambda_handler(event, context):
    processed = 0
    for record in event.get('Records', []):
        new_img = record.get('dynamodb', {}).get('NewImage', {})
        if not new_img:
            continue

        customer_id = new_img.get('customer_id', {}).get('S')
        kyc_status = new_img.get('kyc_status', {}).get('S')

        if not customer_id or not kyc_status:
            continue

        if kyc_status not in VALID_STATUSES:
            logger.warning(f'Invalid status {kyc_status} for {customer_id}, skipping')
            continue

        try:
            resp = rds.execute_statement(
                resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
                sql='UPDATE customers SET kyc_status = :s WHERE customer_id = :c',
                parameters=[
                    {'name': 's', 'value': {'stringValue': kyc_status}},
                    {'name': 'c', 'value': {'stringValue': customer_id}},
                ],
            )
            rows = resp.get('numberOfRecordsUpdated', 0)
            logger.info(f'Synced {customer_id} → {kyc_status} ({rows} rows)')
            processed += 1
        except Exception as e:
            logger.error(f'Sync failed for {customer_id}: {e}')

    return {'processed': processed}
