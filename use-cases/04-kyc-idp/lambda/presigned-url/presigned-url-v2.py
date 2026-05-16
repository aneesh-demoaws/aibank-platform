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
rds = boto3.client('rds-data', region_name=os.environ.get('DB_REGION', 'eu-west-1'))
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
        # Clean up S3 documents to prevent re-processing
        s3_resource = boto3.resource('s3', region_name=os.environ.get('BUCKET_REGION', 'eu-west-1'))
        bucket = s3_resource.Bucket(os.environ.get('KYC_BUCKET', 'aibank-kyc-documents-eu-west-1'))
        bucket.objects.filter(Prefix=f'documents/input/{cid}/').delete()
        bucket.objects.filter(Prefix=f'documents/output/{cid}/').delete()
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

        from boto3.dynamodb.conditions import Key, Attr
        cleanup_log = {'aurora_children': 0, 'aurora_apps': 0, 'ddb_personal_loan': 0,
                       'ddb_loan_processing': 0, 'ddb_task_tokens': 0, 'ddb_sessions': 0,
                       'errors': []}

        # ── Step 1: Get the list of application_ids for this customer (from Aurora + DDB) ──
        app_ids = set()
        try:
            res = rds.execute_statement(
                resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
                sql="SELECT application_id FROM loan_applications WHERE customer_id = :c",
                parameters=[{'name': 'c', 'value': {'stringValue': cid}}],
            )
            for r in res.get('records', []):
                v = r[0]
                if not v.get('isNull'):
                    app_ids.add(v.get('stringValue'))
        except Exception as e:
            cleanup_log['errors'].append(f'aurora_list: {e}')

        try:
            loan_ddb = boto3.resource('dynamodb', region_name='eu-west-1').Table('aibank-personal-loan')
            resp = loan_ddb.query(KeyConditionExpression=Key('customer_id').eq(cid))
            ddb_items = resp.get('Items', [])
            for it in ddb_items:
                if it.get('application_id'):
                    app_ids.add(it['application_id'])
        except Exception as e:
            cleanup_log['errors'].append(f'ddb_list: {e}')
            ddb_items = []

        # ── Step 1.5: Reverse any prior loan disbursements before deletion ──
        # Query disbursed applications first, then call transaction-module to reverse each.
        # This deletes the credit transactions and adjusts customer balance back.
        cleanup_log['reversed_disbursements'] = 0
        try:
            if app_ids:
                placeholders_d = ','.join([f':d{i}' for i in range(len(app_ids))])
                params_d = [{'name': f'd{i}', 'value': {'stringValue': aid}} for i, aid in enumerate(app_ids)]
                disbursed = rds.execute_statement(
                    resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
                    sql=(f"SELECT application_id FROM loan_applications "
                         f"WHERE application_id IN ({placeholders_d}) "
                         f"AND disbursement_txn_id IS NOT NULL"),
                    parameters=params_d,
                ).get('records', [])

                if disbursed:
                    lambda_client = boto3.client('lambda', region_name='eu-west-1')
                    for rec in disbursed:
                        if rec[0].get('isNull'):
                            continue
                        aid_to_reverse = rec[0].get('stringValue')
                        try:
                            resp = lambda_client.invoke(
                                FunctionName='aibank-transaction-module',
                                InvocationType='RequestResponse',
                                Payload=json.dumps({
                                    'action': 'reverse_disbursement',
                                    'customer_id': cid,
                                    'application_id': aid_to_reverse,
                                }).encode('utf-8'),
                            )
                            payload = json.loads(resp['Payload'].read())
                            if payload.get('success') and payload.get('reversed'):
                                cleanup_log['reversed_disbursements'] += 1
                            elif not payload.get('success'):
                                cleanup_log['errors'].append(f'reverse_{aid_to_reverse}: {payload.get("error","unknown")}')
                        except Exception as e:
                            cleanup_log['errors'].append(f'reverse_{aid_to_reverse}: {e}')
        except Exception as e:
            cleanup_log['errors'].append(f'reverse_disbursements_query: {e}')

        # ── Step 2: Aurora — delete child rows first to avoid FK errors, then parent ──
        if app_ids:
            placeholders = ','.join([f':a{i}' for i in range(len(app_ids))])
            params = [{'name': f'a{i}', 'value': {'stringValue': aid}} for i, aid in enumerate(app_ids)]
            for child in ('loan_decisions', 'loan_workflow_steps', 'loan_documents', 'loan_contracts'):
                try:
                    r = rds.execute_statement(
                        resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
                        sql=f"DELETE FROM {child} WHERE application_id IN ({placeholders})",
                        parameters=params,
                    )
                    cleanup_log['aurora_children'] += r.get('numberOfRecordsUpdated', 0)
                except Exception as e:
                    cleanup_log['errors'].append(f'aurora_{child}: {e}')

        try:
            r = rds.execute_statement(
                resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
                sql="DELETE FROM loan_applications WHERE customer_id = :c",
                parameters=[{'name': 'c', 'value': {'stringValue': cid}}],
            )
            cleanup_log['aurora_apps'] = r.get('numberOfRecordsUpdated', 0)
        except Exception as e:
            cleanup_log['errors'].append(f'aurora_apps: {e}')

        # ── Step 3: DDB — aibank-personal-loan ──
        try:
            for it in ddb_items:
                loan_ddb.delete_item(Key={'customer_id': cid, 'application_id': it['application_id']})
                cleanup_log['ddb_personal_loan'] += 1
        except Exception as e:
            cleanup_log['errors'].append(f'ddb_personal_loan: {e}')

        # ── Step 4: DDB — aibank-loan-processing (alternate store) ──
        try:
            proc_ddb = boto3.resource('dynamodb', region_name='eu-west-1').Table('aibank-loan-processing')
            scan = proc_ddb.scan(FilterExpression=Attr('customer_id').eq(cid))
            for it in scan.get('Items', []):
                # Discover key schema dynamically
                key = {}
                if 'customer_id' in it: key['customer_id'] = it['customer_id']
                if 'application_id' in it: key['application_id'] = it['application_id']
                if key:
                    proc_ddb.delete_item(Key=key)
                    cleanup_log['ddb_loan_processing'] += 1
        except Exception as e:
            cleanup_log['errors'].append(f'ddb_loan_processing: {e}')

        # ── Step 5: DDB — aibank-loan-task-tokens (only this customer's apps) ──
        try:
            if app_ids:
                tt_ddb = boto3.resource('dynamodb', region_name='eu-west-1').Table('aibank-loan-task-tokens')
                for aid in app_ids:
                    scan = tt_ddb.scan(FilterExpression=Attr('application_id').eq(aid))
                    for it in scan.get('Items', []):
                        if it.get('application_id'):
                            tt_ddb.delete_item(Key={'application_id': it['application_id']})
                            cleanup_log['ddb_task_tokens'] += 1
        except Exception as e:
            cleanup_log['errors'].append(f'ddb_task_tokens: {e}')

        # ── Step 6: Sessions — only THIS customer's loan sessions (filter by customer_id) ──
        try:
            session_ddb = ddb.Table(SESSION_TABLE)
            scan = session_ddb.scan(
                FilterExpression=Attr('session_id').begins_with('loan:') & Attr('customer_id').eq(cid)
            )
            for item in scan.get('Items', []):
                session_ddb.delete_item(Key={'session_id': item['session_id']})
                cleanup_log['ddb_sessions'] += 1
        except Exception as e:
            cleanup_log['errors'].append(f'ddb_sessions: {e}')

        logger.info(f'Loans reset for {cid}: {cleanup_log}')
        if cleanup_log['errors']:
            return _resp(207, {
                'message': f"Partial reset for {cid}: DDB={cleanup_log['ddb_personal_loan']}, Aurora={cleanup_log['aurora_apps']}, errors={len(cleanup_log['errors'])}",
                'customer_id': cid, 'details': cleanup_log,
            })
        return _resp(200, {
            'message': f"Loans reset: {cleanup_log['ddb_personal_loan']} applications deleted",
            'customer_id': cid, 'details': cleanup_log,
        })

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
