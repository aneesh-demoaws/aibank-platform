"""AIBank KYC — Presigned URL Generator. Returns S3 presigned PUT URL for document upload."""
import json, uuid, logging, os, boto3
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ['KYC_BUCKET']
EXPIRATION = 3600
FRONTEND_ORIGIN = os.environ.get('FRONTEND_ORIGIN', 'https://aibank.demoaws.com')
VALID_DOC_TYPES = {'identity', 'address'}
MAX_FILE_SIZE = 10 * 1024 * 1024

s3 = boto3.client('s3', region_name=os.environ.get('BUCKET_REGION', 'me-south-1'))

def cors_response(status, body):
    return {
        'statusCode': status,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': FRONTEND_ORIGIN,
            'Access-Control-Allow-Methods': 'POST,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Credentials': 'true',
        },
        'body': json.dumps(body),
    }

def lambda_handler(event, context):
    if event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return cors_response(200, {})

    try:
        body = json.loads(event.get('body', '{}'))
    except (json.JSONDecodeError, TypeError):
        return cors_response(400, {'error': 'Invalid JSON'})

    customer_id = body.get('customer_id', '')
    doc_type = body.get('documentType', '')
    file_name = body.get('fileName', '')
    file_size = body.get('fileSize', 0)

    # Validate customer_id format
    if not customer_id or not customer_id.startswith('CUST'):
        return cors_response(400, {'error': 'Invalid customer_id'})

    if doc_type not in VALID_DOC_TYPES:
        return cors_response(400, {'error': f'Invalid documentType. Must be: {", ".join(VALID_DOC_TYPES)}'})

    if not file_name:
        return cors_response(400, {'error': 'fileName required'})

    if file_size and file_size > MAX_FILE_SIZE:
        return cors_response(400, {'error': f'File too large. Max {MAX_FILE_SIZE // (1024*1024)}MB'})

    # Validate file extension
    ext = file_name.lower().rsplit('.', 1)[-1] if '.' in file_name else ''
    if ext not in ('pdf', 'jpg', 'jpeg', 'png'):
        return cors_response(400, {'error': 'Only PDF, JPG, PNG allowed'})

    file_id = str(uuid.uuid4())
    s3_key = f'documents/input/{customer_id}/{doc_type}/{file_id}_{file_name}'

    url = s3.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': BUCKET,
            'Key': s3_key,
            'ContentType': 'application/pdf' if ext == 'pdf' else f'image/{ext}',
            'Metadata': {
                'customer-id': customer_id,
                'document-type': doc_type,
                'original-filename': file_name,
            },
        },
        ExpiresIn=EXPIRATION,
    )

    logger.info(f'Presigned URL generated for {customer_id}/{doc_type}/{file_name}')

    return cors_response(200, {
        'uploadUrl': url,
        'key': s3_key,
        'documentType': doc_type,
        'fileId': file_id,
        'expiresIn': EXPIRATION,
    })
