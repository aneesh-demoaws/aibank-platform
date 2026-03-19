"""AIBank KYC — Verification. Cross-checks BDA-extracted data vs Aurora onboarding data."""
import json, logging, os, re, boto3
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DDB_REGION = os.environ.get('DDB_REGION', 'me-south-1')
DB_REGION = os.environ.get('DB_REGION', 'me-south-1')
TABLE_NAME = os.environ.get('KYC_TABLE', 'aibank-customer-kyc')
CLUSTER_ARN = os.environ['CLUSTER_ARN']
SECRET_ARN = os.environ['SECRET_ARN']
DB_NAME = os.environ.get('DB_NAME', 'corebanking')

dynamodb = boto3.resource('dynamodb', region_name=DDB_REGION)
rds = boto3.client('rds-data', region_name=DB_REGION)

def lambda_handler(event, context):
    customer_id = event.get('customer_id')
    if not customer_id:
        return {'statusCode': 400, 'body': 'Missing customer_id'}

    table = dynamodb.Table(TABLE_NAME)
    item = table.get_item(Key={'customer_id': customer_id}).get('Item')
    if not item:
        return {'statusCode': 404, 'body': f'No KYC record for {customer_id}'}

    # Get onboarding data from Aurora
    onboarding = get_onboarding_data(customer_id)
    if not onboarding:
        update_status(table, customer_id, 'REJECTED', {'error': 'Customer not found in core banking'})
        return {'statusCode': 404, 'body': 'Customer not in Aurora'}

    # Cross-check
    verification = verify(item, onboarding)
    logger.info(f'Verification result for {customer_id}: {verification["overall_status"]}')

    update_status(table, customer_id, verification['overall_status'], verification)
    return {'statusCode': 200, 'body': json.dumps(verification, default=str)}

def get_onboarding_data(customer_id):
    resp = rds.execute_statement(
        resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DB_NAME,
        sql='SELECT first_name, last_name, date_of_birth, nationality FROM customers WHERE customer_id = :c',
        parameters=[{'name': 'c', 'value': {'stringValue': customer_id}}],
    )
    if not resp['records']:
        return None
    r = resp['records'][0]
    return {
        'first_name': r[0].get('stringValue', ''),
        'last_name': r[1].get('stringValue', ''),
        'date_of_birth': r[2].get('stringValue', ''),
        'nationality': r[3].get('stringValue', ''),
    }

def verify(kyc_item, onboarding):
    extracted_name = (kyc_item.get('full_name') or '').strip().upper()
    onboarding_name = f"{onboarding['first_name']} {onboarding['last_name']}".strip().upper()

    name_score = fuzzy_match(extracted_name, onboarding_name)
    dob_score = match_dob(kyc_item.get('date_of_birth', ''), onboarding.get('date_of_birth', ''))

    # Overall: both must pass
    name_pass = name_score >= 0.8
    dob_pass = dob_score >= 1.0

    overall = 'VERIFIED' if (name_pass and dob_pass) else 'REJECTED'

    return {
        'overall_status': overall,
        'identity_verification': {
            'status': 'PASSED' if name_pass else 'FAILED',
            'confidence': Decimal(str(round(name_score, 2))),
        },
        'name_match': {
            'extracted': extracted_name,
            'onboarding': onboarding_name,
            'score': Decimal(str(round(name_score, 2))),
        },
        'dob_match': {
            'extracted': kyc_item.get('date_of_birth', ''),
            'onboarding': onboarding.get('date_of_birth', ''),
            'score': Decimal(str(round(dob_score, 2))),
        },
        'verification_method': 'AUTOMATED_REAL',
        'timestamp': datetime.utcnow().isoformat(),
    }

def fuzzy_match(a, b):
    """Simple token-based similarity. Handles name order differences."""
    if not a or not b:
        return 0.0
    tokens_a = set(re.split(r'\s+', a))
    tokens_b = set(re.split(r'\s+', b))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    return len(intersection) / max(len(tokens_a), len(tokens_b))

def match_dob(extracted, onboarding):
    """Match DOB across formats: DD/MM/YYYY vs YYYY-MM-DD."""
    if not extracted or not onboarding:
        return 0.0
    try:
        e = normalize_date(extracted)
        o = normalize_date(onboarding)
        return 1.0 if e == o else 0.0
    except Exception:
        return 0.0

def normalize_date(d):
    d = d.strip()
    if re.match(r'\d{4}-\d{2}-\d{2}', d):  # YYYY-MM-DD
        return d.replace('-', '')
    if re.match(r'\d{2}/\d{2}/\d{4}', d):  # DD/MM/YYYY
        parts = d.split('/')
        return f'{parts[2]}{parts[1]}{parts[0]}'
    return d

def update_status(table, customer_id, status, details):
    ts = datetime.utcnow().isoformat()
    update_expr = 'SET kyc_status = :s, verification_details = :d, last_updated = :t'
    vals = {':s': status, ':d': details, ':t': ts}

    if status == 'VERIFIED':
        update_expr += ', total_id_verified_no = :iv, total_address_verified_no = :av'
        vals[':iv'] = 2
        vals[':av'] = 1

    table.update_item(Key={'customer_id': customer_id},
                      UpdateExpression=update_expr, ExpressionAttributeValues=vals)
