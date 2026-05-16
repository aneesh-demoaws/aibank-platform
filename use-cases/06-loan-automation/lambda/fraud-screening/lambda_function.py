"""
Enhanced Fraud Screening Lambda Function - X-Ray Enabled
Checks customer against neobank_fraud_list and updates application status accordingly
"""

import json
import logging
import os
import boto3
from typing import Dict, Any
from datetime import datetime, timezone
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS SDK calls for X-Ray tracing
patch_all()

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
fraud_table = dynamodb.Table('neobank_fraud_list')
loan_table = dynamodb.Table('aibank-personal-loan')

@xray_recorder.capture('lambda_handler')
def lambda_handler(event, context):
    """
    Enhanced fraud screening with comprehensive X-Ray tracing
    """
    logger.info(f"Fraud screening event: {json.dumps(event, default=str)}")
    
    # Add X-Ray annotations
    xray_recorder.put_annotation('service', 'loan-processing')
    xray_recorder.put_annotation('component', 'fraud-screening')
    xray_recorder.put_annotation('stage', 'pre-processing')
    
    try:
        with xray_recorder.in_subsegment('extract_request_data'):
            customer_id = event.get('processingContext', {}).get('customer_id') or event.get('inputData', {}).get('customer_id')
            application_id = event.get('processingContext', {}).get('application_id') or event.get('inputData', {}).get('application_id')
            
            if not customer_id or not application_id:
                raise ValueError("Missing customer_id or application_id in event")
            
            xray_recorder.put_annotation('customer_id', customer_id)
            xray_recorder.put_annotation('application_id', application_id)
        
        with xray_recorder.in_subsegment('fraud_screening_check'):
            fraud_result = perform_fraud_screening(customer_id, application_id)
            
            xray_recorder.put_annotation('fraud_detected', fraud_result['fraud_detected'])
            xray_recorder.put_annotation('fraud_severity', fraud_result.get('fraud_severity', 'NONE'))
            xray_recorder.put_annotation('screening_status', fraud_result['status'])
        
        with xray_recorder.in_subsegment('update_processing_chain'):
            processing_chain = event.get('processing_chain', {})
            processing_chain['fraud_screening'] = {
                'status': 'COMPLETED',
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'fraud_result': fraud_result,
                'fraud_detected': fraud_result['fraud_detected'],
                'screening_status': fraud_result['status'],
                'request_id': context.aws_request_id if context else 'unknown'
            }
        
        # Determine if workflow should continue or stop
        workflow_status = 'CONTINUE' if not fraud_result['fraud_detected'] else 'STOP_FRAUD_DETECTED'
        
        logger.info(f"Fraud screening completed: {fraud_result['status']}")
        
        return {
            'statusCode': 200,
            'processing_chain': processing_chain,
            'fraud_result': fraud_result,
            'workflow_status': workflow_status,
            'inputData': event.get('inputData', {}),
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }
        
    except Exception as e:
        xray_recorder.put_annotation('error', str(e))
        xray_recorder.put_annotation('fraud_screening_success', False)
        logger.error(f"Fraud screening failed: {str(e)}")
        
        return {
            'statusCode': 500,
            'error': str(e),
            'processing_chain': event.get('processing_chain', {}),
            'inputData': event.get('inputData', {}),
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }

@xray_recorder.capture('perform_fraud_screening')
def perform_fraud_screening(customer_id: str, application_id: str) -> Dict[str, Any]:
    """
    Perform comprehensive fraud screening check
    """
    try:
        logger.info(f"Performing fraud screening for customer: {customer_id}")
        
        with xray_recorder.in_subsegment('check_fraud_list'):
            # Check if customer is in fraud list
            fraud_record = check_fraud_list(customer_id)
            
            if fraud_record:
                logger.warning(f"🚨 FRAUD DETECTED: Customer {customer_id} found in fraud list")
                
                # Update application status to rejected_fraud
                update_result = update_application_status(customer_id, application_id, 'rejected_fraud', fraud_record)
                
                return {
                    'status': 'FRAUD_DETECTED',
                    'fraud_detected': True,
                    'fraud_severity': fraud_record.get('severity', 'UNKNOWN'),
                    'fraud_reason': fraud_record.get('reason', 'Listed in fraud database'),
                    'fraud_record': fraud_record,
                    'application_status_updated': update_result,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'customer_id': customer_id,
                    'application_id': application_id
                }
            
            else:
                logger.info(f"✅ NO FRAUD DETECTED: Customer {customer_id} not found in fraud list")
                
                return {
                    'status': 'NO_FRAUD_DETECTED',
                    'fraud_detected': False,
                    'fraud_severity': 'NONE',
                    'fraud_reason': None,
                    'screening_passed': True,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'customer_id': customer_id,
                    'application_id': application_id
                }
        
    except Exception as e:
        logger.error(f"Error in fraud screening: {str(e)}")
        return {
            'status': 'ERROR',
            'fraud_detected': False,
            'error': str(e),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }

@xray_recorder.capture('check_fraud_list')
def check_fraud_list(customer_id: str) -> Dict[str, Any]:
    """
    Check if customer is in the fraud list
    """
    try:
        response = fraud_table.get_item(
            Key={'customer_id': customer_id}
        )
        
        if 'Item' in response:
            fraud_record = response['Item']
            logger.info(f"Found fraud record for customer {customer_id}: {fraud_record.get('reason', 'No reason specified')}")
            return fraud_record
        else:
            logger.info(f"No fraud record found for customer {customer_id}")
            return None
            
    except Exception as e:
        logger.error(f"Error checking fraud list: {str(e)}")
        return None

@xray_recorder.capture('update_application_status')
def update_application_status(customer_id: str, application_id: str, status: str, fraud_record: Dict[str, Any]) -> bool:
    """
    Update loan application status when fraud is detected
    """
    try:
        logger.info(f"Updating application {application_id} status to {status}")
        
        # Update the loan application with fraud information
        loan_table.update_item(
            Key={
                'customer_id': customer_id,
                'application_id': application_id
            },
            UpdateExpression='SET #status = :status, fraud_detected = :fraud_detected, fraud_reason = :fraud_reason, fraud_severity = :fraud_severity, fraud_detected_at = :timestamp, updated_at = :updated',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':status': status,
                ':fraud_detected': True,
                ':fraud_reason': fraud_record.get('reason', 'Listed in fraud database'),
                ':fraud_severity': fraud_record.get('severity', 'UNKNOWN'),
                ':timestamp': datetime.now(timezone.utc).isoformat(),
                ':updated': datetime.now(timezone.utc).isoformat()
            }
        )
        
        logger.info(f"✅ Successfully updated application {application_id} status to {status}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error updating application status: {str(e)}")
        return False

