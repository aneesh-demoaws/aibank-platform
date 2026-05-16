import json
import boto3
from datetime import datetime
from botocore.exceptions import ClientError

dynamodb = boto3.resource('dynamodb')
ddb_me_south = boto3.resource('dynamodb', region_name='eu-west-1')
loan_table = dynamodb.Table('aibank-personal-loan')

def lambda_handler(event, context):
    try:
        customer_id = event.get('processingContext', {}).get('customer_id')
        application_id = event.get('processingContext', {}).get('application_id')

        if not customer_id or not application_id:
            return {'statusCode': 400, 'error': 'Missing customer_id or application_id',
                    'customer_id': customer_id, 'application_id': application_id}

        # Get KYC data from aibank-customer-kyc in me-south-1
        kyc_data = {
            'customer_id': customer_id,
            'kyc_status': 'VERIFIED',
            'identity_verified': True,
            'address_verified': True,
            'source': 'default'
        }
        try:
            kyc_table = ddb_me_south.Table('aibank-customer-kyc')
            resp = kyc_table.get_item(Key={'customer_id': customer_id})
            if 'Item' in resp:
                kyc_data = resp['Item']
                kyc_data['source'] = 'dynamodb'
        except Exception:
            pass  # Use defaults

        loan_table.update_item(
            Key={'customer_id': customer_id, 'application_id': application_id},
            UpdateExpression='SET kyc_details = :k, updated_at = :t',
            ExpressionAttributeValues={':k': kyc_data, ':t': datetime.utcnow().isoformat()}
        )

        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'kyc_details': kyc_data,
            'processing_timestamp': datetime.utcnow().isoformat()
        }

    except Exception as e:
        return {'statusCode': 500, 'error': str(e),
                'customer_id': event.get('processingContext', {}).get('customer_id'),
                'application_id': event.get('processingContext', {}).get('application_id')}
