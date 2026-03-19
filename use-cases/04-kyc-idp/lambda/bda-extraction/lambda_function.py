"""AIBank KYC — BDA Extraction. Invokes Bedrock Data Automation, extracts fields, updates DynamoDB."""
import json, logging, os, boto3
from datetime import datetime
from time import sleep
from urllib.parse import unquote_plus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

BDA_REGION = os.environ.get('BDA_REGION', 'eu-west-1')
BUCKET_REGION = os.environ.get('BUCKET_REGION', 'me-south-1')
DDB_REGION = os.environ.get('DDB_REGION', 'me-south-1')
PROJECT_ARN = os.environ['BDA_PROJECT_ARN']
PROFILE_ARN = os.environ.get('BDA_PROFILE_ARN', f'arn:aws:bedrock:{BDA_REGION}:519124228967:data-automation-profile/eu.data-automation-v1')
TABLE_NAME = os.environ.get('KYC_TABLE', 'aibank-customer-kyc')
VERIFICATION_LAMBDA = os.environ.get('VERIFICATION_LAMBDA_ARN', '')

s3 = boto3.client('s3', region_name=BUCKET_REGION)
run_client = boto3.client('bedrock-data-automation-runtime', region_name=BDA_REGION)
dynamodb = boto3.resource('dynamodb', region_name=DDB_REGION)
lambda_client = boto3.client('lambda', region_name=BDA_REGION)

def lambda_handler(event, context):
    # EventBridge sends event at top level
    if 'detail' in event:
        bucket = event['detail']['bucket']['name']
        key = unquote_plus(event['detail']['object']['key'])
        process_record(bucket, key)
    else:
        for record in event.get('Records', []):
            bucket = record['s3']['bucket']['name']
            key = unquote_plus(record['s3']['object']['key'])
            process_record(bucket, key)

def process_record(bucket, key):
    parts = key.split('/')
    if len(parts) < 5 or parts[1] != 'input':
        return
    customer_id, doc_type = parts[2], parts[3]

    resp = run_client.invoke_data_automation_async(
        dataAutomationConfiguration={'dataAutomationProjectArn': PROJECT_ARN, 'stage': 'LIVE'},
        dataAutomationProfileArn=PROFILE_ARN,
        inputConfiguration={'s3Uri': f's3://{bucket}/{key}'},
        outputConfiguration={'s3Uri': f's3://{bucket}/documents/output/{customer_id}/{doc_type}'},
    )
    invoke_arn = resp['invocationArn']
    logger.info(f'BDA invoked: {invoke_arn}')

    for _ in range(60):
        progress = run_client.get_data_automation_status(invocationArn=invoke_arn)
        if progress['status'] != 'InProgress':
            break
        sleep(10)
    else:
        raise TimeoutError('BDA timeout after 600s')

    if progress['status'] != 'Success':
        raise RuntimeError(f'BDA failed: {progress["status"]}')

    result = parse_bda_output(progress)
    if not result:
        logger.warning(f'No blueprint match for {key}')
        return

    mapped = map_fields(result['matched_blueprint'], result['inference_result'], doc_type)
    update_dynamodb(customer_id, mapped)
    check_triggers(customer_id)

def parse_bda_output(progress):
    job_uri = progress['outputConfiguration']['s3Uri']
    job_data = read_s3_json(job_uri)
    for seg in job_data.get('output_metadata', [{}])[0].get('segment_metadata', []):
        if seg.get('custom_output_status') == 'MATCH' and seg.get('custom_output_path'):
            custom = read_s3_json(seg['custom_output_path'])
            return {
                'inference_result': custom.get('inference_result', {}),
                'matched_blueprint': custom.get('matched_blueprint', {}),
            }
    return None

def read_s3_json(s3_uri):
    parts = s3_uri.replace('s3://', '').split('/', 1)
    resp = s3.get_object(Bucket=parts[0], Key=parts[1])
    return json.loads(resp['Body'].read().decode())

def map_fields(blueprint, inference, doc_type):
    name = blueprint.get('name', '')
    fields = {'blueprint_name': name, 'document_type': doc_type, 'confidence': blueprint.get('confidence', 0)}

    if 'Passport' in name:
        fields.update({
            'passport_number': inference.get('passport_number', ''),
            'full_name': inference.get('full_name', inference.get('name', '')),
            'gender': inference.get('gender', ''),
            'nationality': inference.get('nationality', ''),
            'date_of_birth': inference.get('date_of_birth', ''),
            'date_of_expiry': inference.get('date_of_expiry', ''),
        })
    elif 'CPR' in name:
        fields.update({
            'id_number': inference.get('personal_number', inference.get('id_number', '')),
            'full_name': inference.get('full_name', inference.get('name', '')),
            'gender': inference.get('gender', ''),
            'nationality': inference.get('nationality', ''),
            'date_of_birth': inference.get('date_of_birth', ''),
            'date_of_expiry': inference.get('expiry_date', inference.get('date_of_expiry', '')),
        })
    elif 'License' in name:
        fields.update({
            'licence_number': inference.get('licence_number', ''),
            'full_name': inference.get('name', inference.get('full_name', '')),
            'address': inference.get('address', ''),
            'nationality': inference.get('nationality', ''),
            'date_of_birth': inference.get('date_of_birth', ''),
            'gender': inference.get('gender', ''),
            'date_of_expiry': inference.get('expiry_date', inference.get('date_of_expiry', '')),
        })
    else:
        fields['raw_extraction'] = inference

    return fields

def update_dynamodb(customer_id, mapped):
    table = dynamodb.Table(TABLE_NAME)
    ts = datetime.utcnow().isoformat()
    doc_type = mapped['document_type']

    # Build dynamic update expression
    update_parts = ['last_updated = :t']
    vals = {':t': ts}

    field_map = {
        'full_name': ':fn', 'gender': ':gn', 'nationality': ':na',
        'date_of_birth': ':db', 'passport_number': ':pp', 'id_number': ':id',
        'licence_number': ':ln', 'date_of_expiry': ':de', 'address': ':ad',
    }

    for field, placeholder in field_map.items():
        if mapped.get(field):
            update_parts.append(f'{field} = {placeholder}')
            vals[placeholder] = mapped[field]

    if doc_type == 'address':
        update_parts.append('address_document_type = :adt')
        vals[':adt'] = mapped['blueprint_name']

    table.update_item(
        Key={'customer_id': customer_id},
        UpdateExpression='SET ' + ', '.join(update_parts),
        ExpressionAttributeValues=vals,
    )
    logger.info(f'DynamoDB updated: {customer_id} — {mapped["blueprint_name"]}')

def check_triggers(customer_id):
    table = dynamodb.Table(TABLE_NAME)
    item = table.get_item(Key={'customer_id': customer_id}).get('Item', {})

    id_collected = int(item.get('total_id_collected_no', 0))
    addr_collected = int(item.get('total_address_collected_no', 0))

    if id_collected >= 2 and addr_collected >= 1:
        id_verified = int(item.get('total_id_verified_no', 0))
        addr_verified = int(item.get('total_address_verified_no', 0))

        if id_verified != -1 or addr_verified != -1:
            table.update_item(
                Key={'customer_id': customer_id},
                UpdateExpression='SET total_id_verified_no = :t, total_address_verified_no = :t',
                ExpressionAttributeValues={':t': -1},
            )
            if VERIFICATION_LAMBDA:
                lambda_client.invoke(
                    FunctionName=VERIFICATION_LAMBDA, InvocationType='Event',
                    Payload=json.dumps({'customer_id': customer_id}),
                )
                logger.info(f'Verification invoked for {customer_id}')
