"""AIBank KYC — Document Processor. Validates uploads, creates DynamoDB records."""
import json, logging, os, boto3
from datetime import datetime
from urllib.parse import unquote_plus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client('s3', region_name=os.environ.get('BUCKET_REGION', 'me-south-1'))
dynamodb = boto3.resource('dynamodb', region_name=os.environ.get('DDB_REGION', 'me-south-1'))
TABLE = os.environ.get('KYC_TABLE', 'aibank-customer-kyc')
ALLOWED_EXTS = {'.pdf', '.jpg', '.jpeg', '.png'}
MAX_SIZE = 10 * 1024 * 1024

def lambda_handler(event, context):
    # EventBridge sends event at top level, not in Records[]
    if 'detail' in event:
        bucket = event['detail']['bucket']['name']
        key = unquote_plus(event['detail']['object']['key'])
        size = event['detail']['object'].get('size', 0)
        process_document(bucket, key, size)
    else:
        for record in event.get('Records', []):
            bucket = record['s3']['bucket']['name']
            key = unquote_plus(record['s3']['object']['key'])
            size = record['s3']['object'].get('size', 0)
            process_document(bucket, key, size)

def process_document(bucket, key, size):
    parts = key.split('/')
    if len(parts) < 5 or parts[0] != 'documents' or parts[1] != 'input':
        logger.warning(f'Unexpected key format: {key}')
        return

    customer_id, doc_type = parts[2], parts[3]
    filename = parts[-1]
    ext = '.' + filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

    # Validate
    if ext not in ALLOWED_EXTS:
        logger.warning(f'Invalid extension {ext}, quarantining {key}')
        quarantine(bucket, key, 'invalid_extension')
        return

    if size > MAX_SIZE:
        logger.warning(f'File too large ({size}), quarantining {key}')
        quarantine(bucket, key, 'file_too_large')
        return

    # Create/update DynamoDB record
    table = dynamodb.Table(TABLE)
    ts = datetime.utcnow().isoformat()

    counter_field = 'total_id_collected_no' if doc_type == 'identity' else 'total_address_collected_no'

    try:
        table.update_item(
            Key={'customer_id': customer_id},
            UpdateExpression=f'SET kyc_status = :s, last_updated = :t ADD {counter_field} :one',
            ExpressionAttributeValues={':s': 'PROCESSING', ':t': ts, ':one': 1},
        )
        logger.info(f'DynamoDB updated: {customer_id}, {doc_type}, {filename}')
    except Exception as e:
        logger.error(f'DynamoDB update failed: {e}')

def quarantine(bucket, key, reason):
    new_key = key.replace('documents/input/', 'documents/quarantine/')
    try:
        s3.copy_object(Bucket=bucket, CopySource={'Bucket': bucket, 'Key': key}, Key=new_key,
                       Metadata={'quarantine_reason': reason}, MetadataDirective='REPLACE')
        s3.delete_object(Bucket=bucket, Key=key)
        logger.info(f'Quarantined {key} → {new_key} ({reason})')
    except Exception as e:
        logger.error(f'Quarantine failed: {e}')
