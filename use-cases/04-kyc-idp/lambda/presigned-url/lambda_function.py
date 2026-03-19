"""AIBank KYC — Presigned URL Generator. Session-authenticated, server-side customer_id resolution."""
import json, os, uuid, boto3, logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUCKET = os.environ.get('KYC_BUCKET', 'aibank-kyc-documents-eu-west-1')
SESSION_TABLE = os.environ.get('SESSION_TABLE', 'aibank-session-routing')
CLUSTER_ARN = os.environ.get('AURORA_CLUSTER_ARN', 'arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking')
SECRET_ARN = os.environ.get('AURORA_SECRET_ARN', 'arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ')
DB_NAME = os.environ.get('DB_NAME', 'corebanking')
MAX_FILE_SIZE = 10 * 1024 * 1024
VALID_DOC_TYPES = ('identity', 'address')

ddb = boto3.resource('dynamodb', region_name=os.environ.get('AWS_REGION', 'eu-west-1'))
rds = boto3.client('rds-data', region_name='me-south-1')
s3 = boto3.client('s3', region_name=os.environ.get('BUCKET_REGION', 'eu-west-1'))
session_table = ddb.Table(SESSION_TABLE)


def _resp(code, body):
    return {
        'statusCode': code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': os.environ.get('ALLOWED_ORIGIN', 'https://aibank.demoaws.com'),
            'Access-Control-Allow-Methods': 'POST,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
            'Access-Control-Allow-Credentials': 'true',
        },
        'body': json.dumps(body),
    }


def _get_cookie(event, name):
    for c in event.get('cookies', []):
        if c.startswith(f'{name}='):
            return c.split('=', 1)[1]
    raw = event.get('headers', {}).get('cookie', '') or ''
    for part in raw.split(';'):
        k, _, v = part.strip().partition('=')
        if k == name:
            return v
    return None


def _validate_session(event):
    sid = _get_cookie(event, 'aibank_sid')
    if not sid:
        return None
    resp = session_table.get_item(Key={'session_id': sid})
    item = resp.get('Item')
    if not item or item.get('status') != 'active':
        return None
    return item.get('user_email')


def _get_customer_id(email):
    try:
        resp = rds.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql='SELECT customer_id FROM customers WHERE email = :e LIMIT 1',
            parameters=[{'name': 'e', 'value': {'stringValue': email}}],
        )
        rows = resp.get('records', [])
        return rows[0][0].get('stringValue') if rows else None
    except Exception as e:
        logger.error(f'Customer lookup error: {e}')
        return None


def lambda_handler(event, context):
    if event.get('requestContext', {}).get('http', {}).get('method') == 'OPTIONS':
        return _resp(200, {})

    # Check if this is a reset request
    path = event.get('requestContext', {}).get('http', {}).get('path', '')
    if path.endswith('/kyc/reset'):
        email = _validate_session(event)
        if not email:
            return _resp(401, {'error': 'Authentication required.'})
        cid = _get_customer_id(email)
        if not cid:
            return _resp(403, {'error': 'No banking profile found.'})
        ddb_table = boto3.resource('dynamodb', region_name=os.environ.get('DDB_REGION', 'me-south-1')).Table(os.environ.get('KYC_TABLE', 'aibank-customer-kyc'))
        ddb_table.delete_item(Key={'customer_id': cid})
        rds.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="UPDATE customers SET kyc_status = 'NOT_STARTED' WHERE customer_id = :c",
            parameters=[{'name': 'c', 'value': {'stringValue': cid}}],
        )
        return _resp(200, {'message': 'KYC reset successfully', 'customer_id': cid})

    if path.endswith('/kyc/loans-reset'):
        email = _validate_session(event)
        if not email:
            return _resp(401, {'error': 'Authentication required.'})
        cid = _get_customer_id(email)
        if not cid:
            return _resp(403, {'error': 'No banking profile found.'})
        # Delete from DynamoDB (aibank-personal-loan)
        loan_ddb = boto3.resource('dynamodb', region_name='eu-west-1').Table('aibank-personal-loan')
        resp = loan_ddb.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('customer_id').eq(cid)
        )
        deleted = 0
        for item in resp.get('Items', []):
            loan_ddb.delete_item(Key={'customer_id': cid, 'application_id': item['application_id']})
            deleted += 1
        # Delete loan sessions
        session_ddb = ddb.Table(SESSION_TABLE)
        scan = session_ddb.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr('session_id').begins_with('loan:')
        )
        for item in scan.get('Items', []):
            session_ddb.delete_item(Key={'session_id': item['session_id']})
        # Delete from core banking MySQL
        rds.execute_statement(
            resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
            sql="DELETE FROM loan_applications WHERE customer_id = :c",
            parameters=[{'name': 'c', 'value': {'stringValue': cid}}],
        )
        return _resp(200, {'message': f'Loans reset: {deleted} applications deleted', 'customer_id': cid})

    # Resolve customer_id: internal invoke (from agent) or session auth (from frontend)
    try:
        body = json.loads(event.get('body', '{}'))
    except (json.JSONDecodeError, TypeError):
        return _resp(400, {'error': 'Invalid JSON'})

    is_internal = not event.get('requestContext', {}).get('http')
    if is_internal and body.get('customer_id'):
        # Internal invoke from Alma agent — trust the customer_id
        customer_id = body['customer_id']
    else:
        # Frontend invoke — authenticate via session cookie
        email = _validate_session(event)
        if not email:
            return _resp(401, {'error': 'Authentication required. Please log in.'})
        customer_id = _get_customer_id(email)
        if not customer_id:
            return _resp(403, {'error': 'No banking profile found.'})

    doc_type = body.get('documentType', '')
    file_name = body.get('fileName', 'document.pdf')
    file_size = body.get('fileSize', 0)

    if doc_type not in VALID_DOC_TYPES:
        return _resp(400, {'error': f'Invalid documentType. Must be: {", ".join(VALID_DOC_TYPES)}'})

    if file_size and file_size > MAX_FILE_SIZE:
        return _resp(400, {'error': f'File too large. Max {MAX_FILE_SIZE // (1024*1024)}MB'})

    ext = file_name.lower().rsplit('.', 1)[-1] if '.' in file_name else 'pdf'
    if ext not in ('pdf', 'jpg', 'jpeg', 'png'):
        ext = 'pdf'

    file_id = str(uuid.uuid4())
    s3_key = f'documents/input/{customer_id}/{doc_type}/{file_id}_{file_name}'

    url = s3.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': BUCKET,
            'Key': s3_key,
            'ContentType': 'application/pdf' if ext == 'pdf' else f'image/{ext}',
        },
        ExpiresIn=3600,
    )

    return _resp(200, {
        'uploadUrl': url,
        'key': s3_key,
        'documentType': doc_type,
        'fileId': file_id,
        'expiresIn': 3600,
    })
