"""
Lambda authorizer for loan-agent-api.
Validates aibank_sid cookie against DynamoDB session table,
injects customer_id into authorizer context.
"""
import os, boto3

SESSION_TABLE = os.environ.get('SESSION_TABLE', 'aibank-session-routing')
ddb = boto3.resource('dynamodb', region_name='eu-west-1')

def lambda_handler(event, context):
    # Extract session cookie from header
    cookie_header = event.get('authorizationToken', '')  # passed as token source
    sid = None
    for part in cookie_header.split(';'):
        part = part.strip()
        if part.startswith('aibank_sid='):
            sid = part[len('aibank_sid='):]
            break

    if not sid:
        raise Exception('Unauthorized')

    item = ddb.Table(SESSION_TABLE).get_item(Key={'session_id': sid}).get('Item')
    if not item or item.get('status') != 'active':
        raise Exception('Unauthorized')

    customer_id = item.get('customer_id', '')
    if not customer_id:
        raise Exception('Unauthorized')

    return {
        'principalId': customer_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [{'Action': 'execute-api:Invoke', 'Effect': 'Allow', 'Resource': event['methodArn'].rsplit('/', 1)[0] + '/*'}]
        },
        'context': {
            'customer_id': customer_id,
            'cognito_sub':  item.get('cognito_sub', ''),
        }
    }
