"""
AWS Lambda Function: Personal Loan Document Processor

This Lambda function processes personal loan documents (salary certificates and bank statements)
uploaded to S3. It uses Amazon Bedrock Data Automation to extract relevant data from documents
and stores the results in DynamoDB.

Author: Generated for neobank-personal-loan-document-processor
Runtime: Python 3.12
Region: us-west-2
Lambda ARN: arn:aws:lambda:us-west-2:519124228967:function:neobank-personal-loan-document-processor

Environment Variables Required:
- REGION: AWS region (default: us-west-2)
- BEDROCK_PROJECT_ARN: Bedrock Data Automation project ARN
- DYNAMODB_TABLE: DynamoDB table name (default: neobank-personal-loan)
- BEDROCK_PROFILE_ARN: Bedrock Data Automation profile ARN
"""

import boto3
import json
from urllib.parse import unquote_plus
import os
from time import sleep
import logging

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment Variables Configuration
REGION = os.environ.get('REGION', 'us-west-2')
BEDROCK_PROJECT_ARN = os.environ.get('BEDROCK_PROJECT_ARN', 'arn:aws:bedrock:us-west-2:519124228967:data-automation-project/7bd1ebc47a88')
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'neobank-personal-loan')
BEDROCK_PROFILE_ARN = os.environ.get('BEDROCK_PROFILE_ARN', 'arn:aws:bedrock:us-west-2:519124228967:data-automation-profile/us.data-automation-v1')

# Constants
ALLOWED_FILE_TYPES = ['.pdf', '.jpg', '.jpeg', '.png']
SUPPORTED_DOCUMENT_TYPES = ['salary_certificate', 'bank_statement']
BEDROCK_POLLING_INTERVAL = 10  # seconds

# Custom Exception Classes
class DocumentProcessingError(Exception):
    """Custom exception for document processing errors"""
    pass

class BedrockProcessingError(Exception):
    """Custom exception for Bedrock processing errors"""
    pass

class DynamoDBError(Exception):
    """Custom exception for DynamoDB operation errors"""
    pass

def parse_numeric_value(value):
    """
    Parse numeric values that may contain formatting like commas, currency symbols, etc.
    
    Args:
        value: The value to parse (could be string, number, etc.)
        
    Returns:
        int: Parsed integer value, or 'unavailable' if parsing fails
    """
    if not value or value == 'unavailable':
        return 'unavailable'
    
    try:
        # Convert to string and clean up formatting
        value_str = str(value).strip()
        
        # Remove common currency symbols and text
        currency_symbols = ['BHD', 'USD', 'EUR', 'GBP', '$', '€', '£', 'BD']
        for symbol in currency_symbols:
            value_str = value_str.replace(symbol, '').strip()
        
        # Remove commas and other formatting
        value_str = value_str.replace(',', '').replace(' ', '')
        
        # Handle empty string after cleanup
        if not value_str:
            return 'unavailable'
        
        # Convert to float first (to handle decimals) then to int
        numeric_value = float(value_str)
        return int(numeric_value)
        
    except (ValueError, TypeError, AttributeError):
        return 'unavailable'

def validate_environment_variables():
    """
    Validate that all required environment variables are set.
    
    Raises:
        ValueError: If required environment variables are missing
    """
    required_vars = {
        'BEDROCK_PROJECT_ARN': BEDROCK_PROJECT_ARN,
        'DYNAMODB_TABLE': DYNAMODB_TABLE,
        'BEDROCK_PROFILE_ARN': BEDROCK_PROFILE_ARN
    }
    
    missing_vars = []
    
    for var_name, var_value in required_vars.items():
        if not var_value:
            missing_vars.append(var_name)
    
    if missing_vars:
        error_msg = f"Missing required environment variables: {missing_vars}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    
    logger.info(f"Environment configuration validated - Region: {REGION}, Table: {DYNAMODB_TABLE}")
    logger.debug(f"Bedrock Project ARN: {BEDROCK_PROJECT_ARN}")
    logger.debug(f"Bedrock Profile ARN: {BEDROCK_PROFILE_ARN}")

def extract_ids_and_doc_type(s3_path):
    """
    Extract customer_id, application_id, and document_type from S3 object path.
    
    Expected path format: documents/input/{customer_id}/{application_id}/{document_type}/filename
    
    Args:
        s3_path (str): Full S3 object key path
        
    Returns:
        tuple: (customer_id, application_id, document_type)
        
    Raises:
        ValueError: If path format is invalid or missing components
    """
    try:
        path_parts = s3_path.split('/')
        
        # Validate minimum path length
        if len(path_parts) < 6:
            raise ValueError(f"Invalid S3 path format. Expected at least 6 parts, got {len(path_parts)}")
        
        # Extract components based on expected structure
        # documents/input/{customer_id}/{application_id}/{document_type}/filename
        if path_parts[0] != 'documents' or path_parts[1] != 'input':
            raise ValueError("Path must start with 'documents/input/'")
            
        customer_id = path_parts[2]
        application_id = path_parts[3]
        document_type = path_parts[4]
        
        # Validate extracted components
        if not customer_id or not application_id or not document_type:
            raise ValueError("Missing required path components: customer_id, application_id, or document_type")
            
        # Validate document type
        if document_type not in SUPPORTED_DOCUMENT_TYPES:
            raise ValueError(f"Unsupported document type: {document_type}. Supported types: {SUPPORTED_DOCUMENT_TYPES}")
            
        logger.info(f"Extracted - Customer ID: {customer_id}, Application ID: {application_id}, Document Type: {document_type}")
        
        return customer_id, application_id, document_type
        
    except Exception as e:
        logger.error(f"Error parsing S3 path '{s3_path}': {str(e)}")
        raise ValueError(f"Failed to parse S3 path: {str(e)}")

def validate_file_extension(filename):
    """
    Validate if the file has an allowed extension.
    
    Args:
        filename (str): Name of the file
        
    Returns:
        bool: True if file extension is allowed, False otherwise
    """
    if not filename:
        return False
        
    # Get file extension (case insensitive)
    file_ext = os.path.splitext(filename.lower())[1]
    
    is_valid = file_ext in ALLOWED_FILE_TYPES
    
    if not is_valid:
        logger.warning(f"Invalid file extension '{file_ext}' for file '{filename}'. Allowed types: {ALLOWED_FILE_TYPES}")
    
    return is_valid

def get_filename_from_s3_path(s3_path):
    """
    Extract filename from S3 object path.
    
    Args:
        s3_path (str): Full S3 object key path
        
    Returns:
        str: Filename
    """
    return s3_path.split('/')[-1]

def get_s3_to_dict(s3_client, s3_uri):
    """
    Helper function to get JSON content from S3.
    
    Args:
        s3_client: Boto3 S3 client
        s3_uri (str): S3 URI in format s3://bucket/key
        
    Returns:
        dict: Parsed JSON content from S3 object
        
    Raises:
        Exception: If S3 object cannot be retrieved or parsed
    """
    try:
        # Parse S3 URI
        bucket = s3_uri.split('/')[2]
        key = '/'.join(s3_uri.split('/')[3:])
        
        logger.info(f"Retrieving S3 object: bucket={bucket}, key={key}")
        
        # Get object from S3
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response['Body'].read().decode('utf-8')
        
        # Parse JSON content
        json_data = json.loads(content)
        logger.debug(f"Successfully parsed JSON from S3: {len(content)} characters")
        
        return json_data
        
    except Exception as e:
        logger.error(f"Error retrieving JSON from S3 URI '{s3_uri}': {str(e)}")
        raise

def invoke_bedrock_processing(run_client, input_s3_uri, output_s3_uri, project_arn, profile_arn):
    """
    Invoke Bedrock Data Automation processing and poll for completion.
    
    Args:
        run_client: Boto3 Bedrock Data Automation Runtime client
        input_s3_uri (str): S3 URI of input document
        output_s3_uri (str): S3 URI for output results
        project_arn (str): Bedrock Data Automation project ARN
        profile_arn (str): Bedrock Data Automation profile ARN
        
    Returns:
        dict: Processing results from Bedrock
        
    Raises:
        Exception: If Bedrock processing fails
    """
    try:
        logger.info(f"Invoking Bedrock Data Automation - Input: {input_s3_uri}, Output: {output_s3_uri}")
        
        # Invoke Bedrock Data Automation
        response = run_client.invoke_data_automation_async(
            dataAutomationConfiguration={
                "dataAutomationProjectArn": project_arn,
                "stage": 'LIVE'
            },
            dataAutomationProfileArn=profile_arn,
            inputConfiguration={
                's3Uri': input_s3_uri
            },
            outputConfiguration={
                's3Uri': output_s3_uri
            }
        )
        
        invoke_arn = response['invocationArn']
        logger.info(f'Bedrock invocation ARN: {invoke_arn}')
        
        # Poll for completion with 10-second intervals
        while True:
            progress = run_client.get_data_automation_status(invocationArn=invoke_arn)
            status = progress['status']
            
            logger.info(f'Bedrock processing status: {status}')
            
            if status != 'InProgress':
                break
                
            sleep(BEDROCK_POLLING_INTERVAL)
        
        logger.info(f'Bedrock processing completed with status: {status}')
        
        # Check if processing was successful
        if status != 'Success':
            error_msg = f"Bedrock Data Automation processing failed with status: {status}"
            logger.error(error_msg)
            raise Exception(error_msg)
        
        return progress
        
    except Exception as e:
        logger.error(f"Error in Bedrock processing: {str(e)}")
        raise

def extract_inference_results(s3_client, bedrock_output):
    """
    Extract inference results from Bedrock Data Automation output.
    
    Args:
        s3_client: Boto3 S3 client
        bedrock_output (dict): Bedrock processing output containing S3 URIs
        
    Returns:
        list: List of inference results
        
    Raises:
        Exception: If results cannot be extracted
    """
    try:
        # Get the main job output
        job_json_obj = get_s3_to_dict(s3_client, bedrock_output['outputConfiguration']['s3Uri'])
        results_meta = job_json_obj["output_metadata"][0]["segment_metadata"]
        
        # Extract inference results from each segment
        results_all = []
        for result in results_meta:
            custom_output_obj = get_s3_to_dict(s3_client, result["custom_output_path"])
            inference_results = custom_output_obj['inference_result']
            results_all.append(inference_results)
        
        logger.info(f"Extracted {len(results_all)} inference results from Bedrock output")
        return results_all
        
    except Exception as e:
        logger.error(f"Error extracting inference results: {str(e)}")
        raise

def process_salary_certificate(inference_results):
    """
    Process salary certificate inference results and extract required fields.
    
    Args:
        inference_results (list): List of inference results from Bedrock
        
    Returns:
        dict: Processed salary certificate data with required fields
    """
    try:
        # Get the first result (assuming single document processing)
        salary_data = inference_results[0] if inference_results else {}
        
        logger.info(f'Processing salary certificate data: {salary_data}')
        
        # Extract required fields with 'unavailable' as default
        processed_data = {
            'basic_salary': salary_data.get('basic_salary', 'unavailable'),
            'employer_name': salary_data.get('employer_name', 'unavailable'),
            'salary_certificate_issued_date': salary_data.get('salary_certificate_issued_date', 'unavailable')
        }
        
        # Convert basic_salary using the helper function
        original_salary = processed_data['basic_salary']
        processed_data['basic_salary'] = parse_numeric_value(original_salary)
        
        if processed_data['basic_salary'] == 'unavailable' and original_salary != 'unavailable':
            logger.warning(f"Could not convert basic_salary to int: {original_salary}")
        
        # Log missing fields
        missing_fields = [field for field, value in processed_data.items() if value == 'unavailable']
        if missing_fields:
            logger.warning(f"Missing or unavailable salary certificate fields: {missing_fields}")
        
        logger.info(f"Processed salary certificate data: {processed_data}")
        return processed_data
        
    except Exception as e:
        logger.error(f"Error processing salary certificate data: {str(e)}")
        # Return default values in case of error
        return {
            'basic_salary': 'unavailable',
            'employer_name': 'unavailable',
            'salary_certificate_issued_date': 'unavailable'
        }

def process_bank_statement(inference_results):
    """
    Process bank statement inference results and extract required fields.
    
    Args:
        inference_results (list): List of inference results from Bedrock
        
    Returns:
        dict: Processed bank statement data with required fields
    """
    try:
        # Get the first result (assuming single document processing)
        bank_data = inference_results[0] if inference_results else {}
        
        logger.info(f'Processing bank statement data: {bank_data}')
        
        # Extract required fields with 'unavailable' as default
        processed_data = {
            'average_balance': bank_data.get('average_balance', 'unavailable'),
            'bank_name': bank_data.get('bank_name', 'unavailable'),
            'ending_balance': bank_data.get('ending_balance', 'unavailable'),
            'salary_transfer': bank_data.get('salary_transfer', 'unavailable')
        }
        
        # Convert numeric fields using the helper function
        numeric_fields = ['average_balance', 'ending_balance']
        for field in numeric_fields:
            original_value = processed_data[field]
            processed_data[field] = parse_numeric_value(original_value)
            
            if processed_data[field] == 'unavailable' and original_value != 'unavailable':
                logger.warning(f"Could not convert {field} to int: {original_value}")
        
        # Log missing fields
        missing_fields = [field for field, value in processed_data.items() if value == 'unavailable']
        if missing_fields:
            logger.warning(f"Missing or unavailable bank statement fields: {missing_fields}")
        
        logger.info(f"Processed bank statement data: {processed_data}")
        return processed_data
        
    except Exception as e:
        logger.error(f"Error processing bank statement data: {str(e)}")
        # Return default values in case of error
        return {
            'average_balance': 'unavailable',
            'bank_name': 'unavailable',
            'ending_balance': 'unavailable',
            'salary_transfer': 'unavailable'
        }

def check_and_set_processing_trigger(dynamodb, table_name, customer_id, application_id, current_document_type):
    """
    Check if both documents are received and set processing trigger if needed.
    
    Args:
        dynamodb: Boto3 DynamoDB resource
        table_name (str): DynamoDB table name
        customer_id (str): Customer ID
        application_id (str): Application ID
        current_document_type (str): Type of document just processed
        
    Returns:
        bool: True if processing trigger was set, False otherwise
    """
    try:
        table = dynamodb.Table(table_name)
        
        # Get current record to check document status
        response = table.get_item(
            Key={
                'customer_id': customer_id,
                'application_id': application_id
            }
        )
        
        if 'Item' not in response:
            logger.error(f"Application not found: {customer_id}/{application_id}")
            return False
        
        item = response['Item']
        
        # Check current document flags (the one we just processed will be True)
        salary_received = item.get('loan_salary_document_received', False)
        statement_received = item.get('loan_statement_document_received', False)
        
        # Set the current document flag to True
        if current_document_type == 'salary_certificate':
            salary_received = True
        elif current_document_type == 'bank_statement':
            statement_received = True
        
        # Check if both documents are now received
        both_received = salary_received and statement_received
        
        logger.info(f"Document status check - Salary: {salary_received}, Statement: {statement_received}, Both: {both_received}")
        
        if both_received:
            # Set processing trigger to True
            table.update_item(
                Key={
                    'customer_id': customer_id,
                    'application_id': application_id
                },
                UpdateExpression='SET loan_processing_trigger = :trigger, #status = :status',
                ExpressionAttributeNames={'#status': 'status'},
                ExpressionAttributeValues={
                    ':trigger': True,
                    ':status': 'processing'
                }
            )
            
            logger.info(f"✅ Processing trigger set to True for application {application_id} - status changed to 'processing'")
            return True
        else:
            logger.info(f"Processing trigger not set - waiting for remaining documents")
            return False
            
    except Exception as e:
        logger.error(f"Error checking/setting processing trigger: {str(e)}")
        return False

def update_customer_application(dynamodb, table_name, customer_id, application_id, document_data, document_type):
    """
    Update existing customer application record with extracted document data and mark document as received.
    
    Args:
        dynamodb: Boto3 DynamoDB resource
        table_name (str): DynamoDB table name
        customer_id (str): Customer ID (partition key)
        application_id (str): Application ID (sort key)
        document_data (dict): Processed document data
        document_type (str): Type of document (salary_certificate or bank_statement)
        
    Raises:
        Exception: If DynamoDB update fails
    """
    try:
        table = dynamodb.Table(table_name)
        
        logger.info(f"Updating DynamoDB record - Customer ID: {customer_id}, Application ID: {application_id}, Document Type: {document_type}")
        
        if document_type == 'salary_certificate':
            # Update salary certificate fields and mark as received
            update_expression = '''SET basic_salary = :bs, 
                                      employer_name = :en, 
                                      salary_certificate_issued_date = :scid,
                                      loan_salary_document = :lsd,
                                      loan_salary_document_received = :lsdr'''
            expression_attribute_values = {
                ':bs': document_data['basic_salary'],
                ':en': document_data['employer_name'],
                ':scid': document_data['salary_certificate_issued_date'],
                ':lsd': document_data,  # Store the complete document data
                ':lsdr': True  # Mark as received
            }
            
        elif document_type == 'bank_statement':
            # Update bank statement fields and mark as received
            update_expression = '''SET average_balance = :ab, 
                                      bank_name = :bn, 
                                      ending_balance = :eb, 
                                      salary_transfer = :st,
                                      loan_statement_document = :lsd,
                                      loan_statement_document_received = :lsdr'''
            expression_attribute_values = {
                ':ab': document_data['average_balance'],
                ':bn': document_data['bank_name'],
                ':eb': document_data['ending_balance'],
                ':st': document_data['salary_transfer'],
                ':lsd': document_data,  # Store the complete document data
                ':lsdr': True  # Mark as received
            }
            
        else:
            raise ValueError(f"Unsupported document type for DynamoDB update: {document_type}")
        
        # Perform the update using composite key
        response = table.update_item(
            Key={
                'customer_id': customer_id,
                'application_id': application_id
            },
            UpdateExpression=update_expression,
            ExpressionAttributeValues=expression_attribute_values,
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Successfully updated DynamoDB record for customer {customer_id}, application {application_id}")
        logger.info(f"Document {document_type} marked as received: True")
        
        # Check if both documents are received and set processing trigger
        trigger_set = check_and_set_processing_trigger(dynamodb, table_name, customer_id, application_id, document_type)
        
        logger.debug(f"Updated attributes: {response.get('Attributes', {})}")
        
    except Exception as e:
        error_msg = f"Failed to update DynamoDB record for customer {customer_id}, application {application_id}: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)

def process_document_by_type(s3_client, run_client, dynamodb, bucket_name, object_key, customer_id, application_id, document_type):
    """
    Route document processing based on document type and orchestrate the complete workflow.
    
    Args:
        s3_client: Boto3 S3 client
        run_client: Boto3 Bedrock Data Automation Runtime client
        dynamodb: Boto3 DynamoDB resource
        bucket_name (str): S3 bucket name
        object_key (str): S3 object key
        customer_id (str): Customer ID
        application_id (str): Application ID
        document_type (str): Document type (salary_certificate or bank_statement)
        
    Returns:
        dict: Processing results
        
    Raises:
        Exception: If processing fails at any step
    """
    try:
        logger.info(f"Starting document processing - Type: {document_type}, Customer: {customer_id}, Application: {application_id}")
        
        # Use global configuration constants
        region = REGION
        project_arn = BEDROCK_PROJECT_ARN
        profile_arn = BEDROCK_PROFILE_ARN
        table_name = DYNAMODB_TABLE
        
        # Construct S3 URIs
        input_s3_uri = f"s3://{bucket_name}/{object_key}"
        output_s3_uri = f"s3://{bucket_name}/documents/output/{customer_id}/{application_id}/{document_type}/"
        
        logger.info(f"Input S3 URI: {input_s3_uri}")
        logger.info(f"Output S3 URI: {output_s3_uri}")
        
        # Invoke Bedrock Data Automation processing
        bedrock_output = invoke_bedrock_processing(
            run_client=run_client,
            input_s3_uri=input_s3_uri,
            output_s3_uri=output_s3_uri,
            project_arn=project_arn,
            profile_arn=profile_arn
        )
        
        # Extract inference results
        inference_results = extract_inference_results(s3_client, bedrock_output)
        
        # Process results based on document type
        if document_type == 'salary_certificate':
            processed_data = process_salary_certificate(inference_results)
        elif document_type == 'bank_statement':
            processed_data = process_bank_statement(inference_results)
        else:
            raise ValueError(f"Unsupported document type: {document_type}")
        
        # Update DynamoDB with processed data, mark document as received, and check processing trigger
        update_customer_application(
            dynamodb=dynamodb,
            table_name=table_name,
            customer_id=customer_id,
            application_id=application_id,
            document_data=processed_data,
            document_type=document_type
        )
        
        # Return success response
        result = {
            'status': 'success',
            'customer_id': customer_id,
            'application_id': application_id,
            'document_type': document_type,
            'processed_data': processed_data,
            'document_received': True,
            'bedrock_output_uri': bedrock_output['outputConfiguration']['s3Uri']
        }
        
        logger.info(f"Document processing completed successfully for {document_type} - marked as received")
        return result
        
    except Exception as e:
        error_msg = f"Document processing failed for {document_type}: {str(e)}"
        logger.error(error_msg)
        raise Exception(error_msg)

def lambda_handler(event, context):
    """
    Main Lambda handler function for processing personal loan documents.
    
    This function processes S3 events triggered by document uploads,
    extracts data using Bedrock Data Automation, and stores results in DynamoDB.
    
    Args:
        event (dict): S3 event containing bucket and object information
        context: Lambda context object
        
    Returns:
        dict: Response with status code and processing results
        
    Raises:
        Exception: If processing fails at any step
    """
    logger.info(f'Lambda event input: {json.dumps(event)}')
    
    try:
        # Validate environment variables first
        validate_environment_variables()
        
        # Initialize AWS clients
        region_name = REGION
        s3_client = boto3.client('s3', region_name=region_name)
        bedrock_client = boto3.client('bedrock-data-automation', region_name=region_name)
        run_client = boto3.client('bedrock-data-automation-runtime', region_name=region_name)
        dynamodb = boto3.resource('dynamodb', region_name=region_name)
        
        logger.info(f"Initialized AWS clients for region: {region_name}")
        
        # Parse S3 event details
        bucket_name = event['Records'][0]['s3']['bucket']['name']
        object_key = unquote_plus(event['Records'][0]['s3']['object']['key'])
        
        logger.info(f"Processing S3 event - Bucket: {bucket_name}, Object: {object_key}")
        
        # Validate file extension
        filename = get_filename_from_s3_path(object_key)
        if not validate_file_extension(filename):
            error_msg = f"Invalid file extension for file: {filename}. Allowed types: {ALLOWED_FILE_TYPES}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Extract customer_id, application_id, and document_type from S3 path
        customer_id, application_id, document_type = extract_ids_and_doc_type(object_key)
        
        logger.info(f"Extracted identifiers - Customer ID: {customer_id}, Application ID: {application_id}, Document Type: {document_type}")
        
        # Process document based on type
        processing_result = process_document_by_type(
            s3_client=s3_client,
            run_client=run_client,
            dynamodb=dynamodb,
            bucket_name=bucket_name,
            object_key=object_key,
            customer_id=customer_id,
            application_id=application_id,
            document_type=document_type
        )
        
        # Return success response
        response = {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Successfully processed {document_type} and updated DynamoDB',
                'customer_id': customer_id,
                'application_id': application_id,
                'document_type': document_type,
                'document_received': True,
                'processing_details': processing_result
            })
        }
        
        logger.info(f"Lambda execution completed successfully for customer {customer_id} - {document_type} marked as received")
        return response
        
    except Exception as e:
        error_msg = f"Lambda execution failed: {str(e)}"
        logger.error(error_msg)
        
        # Return error response
        error_response = {
            'statusCode': 500,
            'body': json.dumps({
                'error': 'Document processing failed',
                'message': str(e)
            })
        }
        
        # Re-raise the exception for Lambda error handling
        raise e

# Print boto3 version for debugging
print(f"boto3 version: {boto3.__version__}")
