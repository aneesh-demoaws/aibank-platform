import json
import os
import boto3
import logging
from datetime import datetime

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb', region_name='eu-west-1')
sns = boto3.client('sns', region_name='eu-west-1')

TABLE = 'aibank-personal-loan'
SNS_TOPIC_ARN = os.environ.get('NOTIFICATION_TOPIC_ARN', os.environ.get('SNS_TOPIC_ARN', ''))
LOAN_OFFICER_EMAIL = os.environ.get('LOAN_OFFICER_EMAIL', '')

def lambda_handler(event, context):
    logger.info(f"notification_dispatcher event: {json.dumps(event, default=str)[:1000]}")

    customer_id = (event.get('processingContext', {}).get('customer_id')
                   or event.get('customer_id', 'UNKNOWN'))
    application_id = (event.get('processingContext', {}).get('application_id')
                      or event.get('application_id', 'UNKNOWN'))

    # Determine review type from event
    forced_review = event.get('forcedManualReview', {})
    manual_review_required = forced_review.get('manual_review_required', True)

    new_status = 'PENDING_REVIEW' if manual_review_required else 'PENDING_REVIEW'

    try:
        # Update DynamoDB status
        table = dynamodb.Table(TABLE)
        table.update_item(
            Key={'customer_id': customer_id, 'application_id': application_id},
            UpdateExpression='SET #s = :s, updated_at = :t',
            ExpressionAttributeNames={'#s': 'status'},
            ExpressionAttributeValues={
                ':s': new_status,
                ':t': datetime.utcnow().isoformat()
            }
        )
        logger.info(f"Updated {application_id} status to {new_status}")

        # Send SNS notification
        if SNS_TOPIC_ARN:
            underwriting = event.get('loanUnderwriting', {}).get('Payload', {})
            amount = event.get('loan_data', {}).get('amount') or \
                     event.get('originalApplicationData', {}).get('amount', 'N/A')
            employer = event.get('customer_data', {}).get('employer_name') or \
                       event.get('originalApplicationData', {}).get('employer_name', 'N/A')

            message = (
                f"Loan Application Requires Review\n\n"
                f"Application ID: {application_id}\n"
                f"Customer: {customer_id}\n"
                f"Amount: {amount} BHD\n"
                f"Employer: {employer}\n"
                f"Status: {new_status}\n"
                f"Reason: {forced_review.get('reason', 'Manual review required')}\n\n"
                f"Review at: https://aibank.demoaws.com/banking/portal/review.html"
            )
            sns.publish(
                TopicArn=SNS_TOPIC_ARN,
                Subject=f"[AI Bank] Loan Review Required: {application_id}",
                Message=message
            )
            logger.info(f"SNS notification sent for {application_id}")

    except Exception as e:
        logger.error(f"notification_dispatcher error: {str(e)}")
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': customer_id,
            'application_id': application_id,
            'processingContext': event.get('processingContext', {})
        }

    return {
        'statusCode': 200,
        'customer_id': customer_id,
        'application_id': application_id,
        'status_updated': new_status,
        'notification_sent': bool(SNS_TOPIC_ARN),
        'processingContext': event.get('processingContext', {})
    }
