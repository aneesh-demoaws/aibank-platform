import json
import boto3
import logging
from datetime import datetime
from decimal import Decimal

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    DTI Calculator Lambda Function - Calculates debt-to-income ratio for capacity assessment
    """
    
    try:
        # Extract basic data from event
        customer_id = event.get('processingContext', {}).get('customer_id', 'UNKNOWN')
        application_id = event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        processing_chain = event.get('processing_chain', {})
        
        logger.info(f"Processing DTI Calculator for customer_id: {customer_id}, application_id: {application_id}")
        
        # Simulate analysis results
        stage_results = {
            'stage': 'dti_analysis',
            'timestamp': datetime.utcnow().isoformat(),
            'analysis_status': 'COMPLETED',
            'customer_id': customer_id,
            'application_id': application_id,
            'dti_assessment': {
                'score': 35,
                'assessment': 'ACCEPTABLE_DTI',
                'confidence': 'MEDIUM',
                'analysis_method': 'SIMULATED',
                'factors_analyzed': ['monthly_income', 'existing_obligations', 'disposable_income'],
                'risk_level': 'MODERATE'
            },
            'recommendations': [
                'Standard underwriting procedures apply',
                'No additional risk factors identified',
                'Continue with normal processing'
            ],
            'processing_notes': 'Analysis completed successfully using simulation data'
        }
        
        # Add to processing chain (simple approach)
        updated_chain = processing_chain.copy()
        stage_key = "dti_analysis_" + str(len(updated_chain) + 1).zfill(3)
        updated_chain[stage_key] = stage_results
        
        logger.info(f"DTI Calculator completed successfully for {customer_id}")
        
        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'processing_chain': updated_chain,
            'stage_results': stage_results
        }
        
    except Exception as e:
        logger.error(f"DTI Calculator error: {str(e)}")
        
        # Return error with processing chain preserved
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('processingContext', {}).get('customer_id', 'UNKNOWN'),
            'application_id': event.get('processingContext', {}).get('application_id', 'UNKNOWN'),
            'processing_chain': event.get('processing_chain', {})
        }
