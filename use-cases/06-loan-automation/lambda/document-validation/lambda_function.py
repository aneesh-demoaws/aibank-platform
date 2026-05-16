"""
Document Validation Lambda Function - Fixed (No X-Ray Dependencies)
Validates required attributes for loan applications and updates DynamoDB status
"""

import json
import logging
import os
import boto3
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
import re

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('aibank-personal-loan')

def lambda_handler(event, context):
    """
    Main Lambda handler for document validation
    Validates that all required attributes are present for Five C's analysis
    """
    logger.info(f"Document validation event: {json.dumps(event, default=str)}")
    
    try:
        # Extract customer and application IDs from the event
        customer_id = event.get('processingContext', {}).get('customer_id') or event.get('inputData', {}).get('customer_id')
        application_id = event.get('processingContext', {}).get('application_id') or event.get('inputData', {}).get('application_id')
        
        if not customer_id or not application_id:
            raise ValueError("Missing customer_id or application_id in event")
        
        logger.info(f"Validating documents for customer {customer_id}, application {application_id}")
        
        # Get application data from DynamoDB
        application_data = get_application_data(customer_id, application_id)
        
        if not application_data:
            raise ValueError(f"Application not found: {customer_id}/{application_id}")
        
        # Validate required attributes for Five C's analysis
        validation_result = validate_required_attributes(application_data)
        
        # Update processing chain with validation results
        processing_chain = event.get('processing_chain', {})
        processing_chain['document_validation'] = {
            'status': 'COMPLETED',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'validation_result': validation_result,
            'all_attributes_present': validation_result['all_attributes_present'],
            'missing_attributes': validation_result['missing_attributes'],
            'validation_score': validation_result['validation_score']
        }
        
        # Log validation results
        logger.info(f"Validation status: {'SUCCESS' if validation_result['all_attributes_present'] else 'MISSING_DOCUMENTS'}")
        logger.info(f"Missing attributes count: {len(validation_result['missing_attributes'])}")
        
        return {
            'statusCode': 200,
            'processing_chain': processing_chain,
            'validation_result': validation_result,
            'inputData': event.get('inputData', {}),
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }
        
    except Exception as e:
        logger.error(f"Document validation failed: {str(e)}")
        logger.error(f"Error annotation: {str(e)}")
        
        return {
            'statusCode': 500,
            'error': str(e),
            'processing_chain': event.get('processing_chain', {}),
            'inputData': event.get('inputData', {}),
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }

def get_application_data(customer_id: str, application_id: str) -> Dict[str, Any]:
    """Get application data from DynamoDB"""
    try:
        response = table.get_item(
            Key={
                'customer_id': customer_id,
                'application_id': application_id
            }
        )
        
        if 'Item' not in response:
            logger.error(f"Application not found: {customer_id}/{application_id}")
            return {}
        
        return response['Item']
        
    except Exception as e:
        logger.error(f"Error getting application data: {str(e)}")
        return {}

def validate_required_attributes(application_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate that all required attributes are present for Five C's analysis
    """
    try:
        # Required attributes for comprehensive Five C's analysis
        required_attributes = [
            'customer_id',
            'application_id', 
            'amount',
            'duration',
            'basic_salary',
            'employer_name',
            'bank_name',
            'loan_salary_document_received',
            'loan_statement_document_received'
        ]
        
        # Optional but recommended attributes
        recommended_attributes = [
            'nationality',
            'salary_transfer',
            'average_balance',
            'ending_balance',
            'created_at',
            'updated_at'
        ]
        
        missing_required = []
        missing_recommended = []
        present_attributes = []
        
        # Check required attributes
        for attr in required_attributes:
            if attr not in application_data or application_data[attr] is None:
                missing_required.append(attr)
            else:
                present_attributes.append(attr)
        
        # Check recommended attributes
        for attr in recommended_attributes:
            if attr not in application_data or application_data[attr] is None:
                missing_recommended.append(attr)
            else:
                present_attributes.append(attr)
        
        # Calculate validation score
        total_possible = len(required_attributes) + len(recommended_attributes)
        present_count = len(present_attributes)
        validation_score = (present_count / total_possible) * 100
        
        # Check document receipt status
        salary_doc_received = application_data.get('loan_salary_document_received', False)
        statement_doc_received = application_data.get('loan_statement_document_received', False)
        
        all_attributes_present = len(missing_required) == 0
        documents_received = salary_doc_received and statement_doc_received
        
        validation_result = {
            'all_attributes_present': all_attributes_present,
            'documents_received': documents_received,
            'missing_attributes': missing_required,
            'missing_recommended': missing_recommended,
            'present_attributes': present_attributes,
            'validation_score': round(validation_score, 2),
            'required_count': len(required_attributes),
            'present_required_count': len(required_attributes) - len(missing_required),
            'document_status': {
                'salary_document_received': salary_doc_received,
                'statement_document_received': statement_doc_received,
                'both_documents_received': documents_received
            }
        }
        
        logger.info(f"Document validation completed:")
        logger.info(f"  - All required attributes present: {all_attributes_present}")
        logger.info(f"  - Documents received: {documents_received}")
        logger.info(f"  - Validation score: {validation_score}%")
        logger.info(f"  - Missing required: {missing_required}")
        
        return validation_result
        
    except Exception as e:
        logger.error(f"Error validating attributes: {str(e)}")
        return {
            'all_attributes_present': False,
            'documents_received': False,
            'missing_attributes': ['validation_error'],
            'validation_score': 0,
            'error': str(e)
        }
