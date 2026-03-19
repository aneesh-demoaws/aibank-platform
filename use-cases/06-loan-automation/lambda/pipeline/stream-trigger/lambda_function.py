"""
DynamoDB Stream Trigger Lambda Function - Corrected Input Structure
Detects status changes to 'processing' and triggers Five C's Step Functions workflow
"""

import json
import boto3
import logging
import os
from typing import Dict, Any, List
from datetime import datetime, timezone

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
stepfunctions_client = boto3.client('stepfunctions')
dynamodb = boto3.resource('dynamodb')

# Environment variables
STATE_MACHINE_ARN = os.environ.get('STATE_MACHINE_ARN', 'arn:aws:states:us-west-2:519124228967:stateMachine:five-cs-personal-loan-processing-workflow')
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'neobank-personal-loan')

def lambda_handler(event, context):
    """Main Lambda handler for DynamoDB stream events"""
    logger.info(f"Received DynamoDB stream event: {json.dumps(event, default=str)}")
    
    try:
        processed_records = 0
        triggered_workflows = 0
        
        for record in event.get('Records', []):
            processed_records += 1
            
            # Process only MODIFY events
            if record['eventName'] != 'MODIFY':
                logger.info(f"Skipping {record['eventName']} event")
                continue
            
            # Check if status changed to 'processing'
            if is_status_change_to_processing(record):
                application_data = extract_application_data(record)
                
                if application_data:
                    logger.info(f"🚀 Status changed to 'processing' for application {application_data['application_id']}")
                    
                    # Trigger Five C's Step Functions workflow
                    execution_result = trigger_five_cs_workflow(application_data, record)
                    
                    if execution_result:
                        triggered_workflows += 1
                        logger.info(f"✅ Five C's workflow triggered for application {application_data['application_id']}")
                    else:
                        logger.error(f"❌ Failed to trigger Five C's workflow for application {application_data['application_id']}")
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'processed_records': processed_records,
                'triggered_workflows': triggered_workflows,
                'workflow_type': 'five_cs_credit_analysis',
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        }
        
    except Exception as e:
        logger.error(f"Error processing DynamoDB stream event: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'timestamp': datetime.now(timezone.utc).isoformat()
            })
        }

def is_status_change_to_processing(record: Dict[str, Any]) -> bool:
    """Check if the record represents a status change to 'processing'"""
    try:
        old_image = record.get('dynamodb', {}).get('OldImage', {})
        new_image = record.get('dynamodb', {}).get('NewImage', {})
        
        old_status = old_image.get('status', {}).get('S', '')
        new_status = new_image.get('status', {}).get('S', '')
        
        if new_status == 'processing' and old_status != 'processing':
            logger.info(f"Status change detected: {old_status} -> {new_status}")
            return True
        
        return False
        
    except Exception as e:
        logger.error(f"Error checking status change: {str(e)}")
        return False

def extract_application_data(record: Dict[str, Any]) -> Dict[str, Any]:
    """Extract application data from DynamoDB stream record"""
    try:
        new_image = record.get('dynamodb', {}).get('NewImage', {})
        
        # Extract composite key
        customer_id = new_image.get('customer_id', {}).get('S', '')
        application_id = new_image.get('application_id', {}).get('S', '')
        
        if not customer_id or not application_id:
            logger.error("Missing composite key in stream record")
            return {}
        
        # Extract application data
        application_data = {
            'customer_id': customer_id,
            'application_id': application_id,
            'amount': float(new_image.get('amount', {}).get('N', '0')),
            'duration': int(new_image.get('duration', {}).get('N', '0')),
            'basic_salary': float(new_image.get('basic_salary', {}).get('N', '0')),
            'employer_name': new_image.get('employer_name', {}).get('S', ''),
            'bank_name': new_image.get('bank_name', {}).get('S', ''),
            'status': new_image.get('status', {}).get('S', ''),
            'created_at': new_image.get('created_at', {}).get('S', ''),
            'updated_at': new_image.get('updated_at', {}).get('S', ''),
            'loan_salary_document_received': new_image.get('loan_salary_document_received', {}).get('BOOL', False),
            'loan_statement_document_received': new_image.get('loan_statement_document_received', {}).get('BOOL', False)
        }
        
        # Extract optional fields
        optional_fields = ['nationality', 'salary_transfer', 'average_balance', 'ending_balance']
        for field in optional_fields:
            if field in new_image:
                if 'S' in new_image[field]:
                    application_data[field] = new_image[field]['S']
                elif 'N' in new_image[field]:
                    application_data[field] = float(new_image[field]['N'])
        
        logger.info(f"Extracted application data for: {application_id}")
        return application_data
        
    except Exception as e:
        logger.error(f"Error extracting application data: {str(e)}")
        return {}

def trigger_five_cs_workflow(application_data: Dict[str, Any], stream_record: Dict[str, Any]) -> bool:
    """Trigger Five C's Step Functions workflow with correct input structure"""
    try:
        # Prepare workflow input - CORRECTED STRUCTURE
        workflow_input = {
            'applicationData': application_data,
            'triggerContext': {
                'streamEventId': stream_record.get('eventID', ''),
                'eventTimestamp': stream_record.get('dynamodb', {}).get('ApproximateCreationDateTime', ''),
                'eventSource': 'DynamoDB',
                'triggerTimestamp': datetime.now(timezone.utc).isoformat()
            }
        }
        
        # Generate execution name
        execution_name = f"five-cs-{application_data['application_id']}-{int(datetime.now().timestamp())}"
        
        logger.info(f"🚀 Starting Five C's workflow execution: {execution_name}")
        logger.info(f"Input structure: applicationData + triggerContext")
        
        # Start Step Functions execution
        response = stepfunctions_client.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=execution_name,
            input=json.dumps(workflow_input, default=str)
        )
        
        execution_arn = response['executionArn']
        logger.info(f"✅ Five C's Step Functions execution started: {execution_arn}")
        
        # Update application record with execution ARN
        update_application_with_execution_arn(
            application_data['customer_id'],
            application_data['application_id'],
            execution_arn
        )
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Error triggering Five C's workflow: {str(e)}")
        return False

def update_application_with_execution_arn(customer_id: str, application_id: str, execution_arn: str) -> bool:
    """Update application record with workflow execution ARN"""
    try:
        table = dynamodb.Table(DYNAMODB_TABLE)
        
        table.update_item(
            Key={
                'customer_id': customer_id,
                'application_id': application_id
            },
            UpdateExpression='SET five_cs_execution_arn = :arn, five_cs_triggered_at = :timestamp',
            ExpressionAttributeValues={
                ':arn': execution_arn,
                ':timestamp': datetime.now(timezone.utc).isoformat()
            }
        )
        
        logger.info(f"✅ Updated application {application_id} with execution ARN")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error updating application: {str(e)}")
        return False
