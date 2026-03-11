import json
import boto3
import logging
from datetime import datetime, date
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS SDK calls for X-Ray tracing
patch_all()

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
ddb_me_south = boto3.resource('dynamodb', region_name='me-south-1')

@xray_recorder.capture('lambda_handler')
def lambda_handler(event, context):
    """
    Simplified Customer Profile Retrieval Lambda Function
    Retrieves only essential customer data needed for segmentation from real production tables:
    - Customer nationality from neo-bank-customer-kyc table (for Local vs Expat classification)
    - KYC verification status
    - Salary account relationship from neobank-personal-loan table
    """
    
    try:
        # Extract input data from the workflow event
        input_data = event.get('inputData', event)
        customer_id = event.get('processingContext', {}).get('customer_id') or input_data.get('customer_id')
        application_id = event.get('processingContext', {}).get('application_id') or input_data.get('application_id')
        
        if not customer_id or not application_id:
            raise ValueError("Missing required customer_id or application_id")
        
        logger.info(f"Processing simplified customer profile for customer_id: {customer_id}")
        
        # Start X-Ray subsegment for customer profile retrieval
        with xray_recorder.in_subsegment('customer_profile_retrieval'):
            # Retrieve customer KYC data from real production table
            kyc_data = get_customer_kyc_data(customer_id)
            
            # Retrieve loan application data to determine salary account relationship
            loan_data = get_loan_application_data(customer_id, application_id)
            
            # Determine nationality classification
            nationality = kyc_data.get('nationality', 'UNKNOWN').upper()
            is_bahraini = nationality == 'BAHRAINI'
            
            # Calculate customer age from date of birth
            date_of_birth = kyc_data.get('date_of_birth')
            age = calculate_customer_age(date_of_birth)
            
            # Determine salary account relationship from real loan data and input data
            has_salary_account = determine_salary_account_relationship(loan_data, input_data)
            
            # Determine customer segment based on nationality and salary account relationship
            customer_segment = determine_customer_segment(is_bahraini, has_salary_account)
            
            # Get bank name from loan data or input data
            bank_name = loan_data.get('bank_name') or input_data.get('bank_name', '')
            
            # Extract only the essential fields for segmentation
            customer_profile = {
                'customer_id': customer_id,
                'nationality': nationality,
                'is_bahraini': is_bahraini,
                'date_of_birth': date_of_birth,
                'age': age,
                'kyc_status': kyc_data.get('kyc_status', 'PENDING'),
                'has_salary_account': has_salary_account,
                'bank_name': bank_name,
                'customer_segment': customer_segment,
                'profile_retrieved': True
            }
            
            # Add X-Ray annotations
            xray_recorder.put_annotation('customer_id', customer_id)
            xray_recorder.put_annotation('nationality', nationality)
            xray_recorder.put_annotation('is_bahraini', is_bahraini)
            xray_recorder.put_annotation('age', age)
            xray_recorder.put_annotation('has_salary_account', has_salary_account)
            xray_recorder.put_annotation('customer_segment', customer_segment)
            
            logger.info(f"Customer profile retrieved: nationality={nationality}, is_bahraini={is_bahraini}, age={age}, has_salary_account={has_salary_account}, customer_segment={customer_segment}")
            
            # Store customer profile in the loan application record
            store_customer_profile_in_loan_table(customer_id, application_id, customer_profile)
            
            # Prepare stage results in the expected format
            stage_results = {
                'stage': 'customer_profile_retrieval',
                'timestamp': datetime.utcnow().isoformat(),
                'status': 'COMPLETED',
                'customer_profile': customer_profile,
                'data_sources': ['neo-bank-customer-kyc', 'neobank-personal-loan'],
                'processing_notes': 'Simplified profile retrieval using real production data'
            }
            
            return {
                'statusCode': 200,
                'customer_id': customer_id,
                'application_id': application_id,
                'stage_results': stage_results,
                'customer_profile': customer_profile,  # Pass to next stage
                'inputData': {  # Ensure input data is passed forward with customer profile
                    **input_data,
                    'customer_profile': customer_profile
                }
            }
            
    except Exception as e:
        logger.error(f"Error in customer profile retrieval: {str(e)}")
        xray_recorder.put_annotation('error', str(e))
        
        # Return error with basic structure
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('inputData', event).get('customer_id'),
            'application_id': event.get('inputData', event).get('application_id'),
            'stage_results': {
                'stage': 'customer_profile_retrieval',
                'timestamp': datetime.utcnow().isoformat(),
                'status': 'ERROR',
                'error': str(e)
            }
        }

@xray_recorder.capture('get_customer_kyc_data')
def get_customer_kyc_data(customer_id):
    """Retrieve customer KYC data from real production neo-bank-customer-kyc table"""
    
    try:
        table = ddb_me_south.Table('aibank-customer-kyc')
        
        response = table.get_item(
            Key={'customer_id': customer_id}
        )
        
        if 'Item' not in response:
            logger.warning(f"Customer KYC data not found for customer_id: {customer_id}")
            # Return default values instead of failing
            return {
                'nationality': 'UNKNOWN',
                'kyc_status': 'PENDING'
            }
        
        kyc_item = response['Item']
        logger.info(f"Retrieved KYC data for customer: {customer_id}, nationality: {kyc_item.get('nationality', 'UNKNOWN')}")
        
        return kyc_item
        
    except Exception as e:
        logger.error(f"Error retrieving KYC data: {str(e)}")
        # Return default values instead of failing
        return {
            'nationality': 'UNKNOWN',
            'kyc_status': 'PENDING'
        }

@xray_recorder.capture('get_loan_application_data')
def get_loan_application_data(customer_id, application_id):
    """Retrieve loan application data from real production neobank-personal-loan table"""
    
    try:
        table = dynamodb.Table('aibank-personal-loan')
        
        response = table.get_item(
            Key={
                'customer_id': customer_id,
                'application_id': application_id
            }
        )
        
        if 'Item' not in response:
            logger.warning(f"Loan application data not found for customer_id: {customer_id}, application_id: {application_id}")
            return {}
        
        loan_item = response['Item']
        logger.info(f"Retrieved loan application data for customer: {customer_id}, bank_name: {loan_item.get('bank_name', 'N/A')}")
        
        return loan_item
        
    except Exception as e:
        logger.error(f"Error retrieving loan application data: {str(e)}")
        return {}

@xray_recorder.capture('store_customer_profile_in_loan_table')
def store_customer_profile_in_loan_table(customer_id, application_id, customer_profile):
    """Store customer profile as an attribute in the neobank-personal-loan table"""
    
    try:
        table = dynamodb.Table('aibank-personal-loan')
        
        # Update the loan application record with customer profile
        response = table.update_item(
            Key={
                'customer_id': customer_id,
                'application_id': application_id
            },
            UpdateExpression='SET customer_profile = :profile, updated_at = :updated_at',
            ExpressionAttributeValues={
                ':profile': customer_profile,
                ':updated_at': datetime.utcnow().isoformat()
            },
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Customer profile stored in loan table for customer_id: {customer_id}, application_id: {application_id}")
        return response
        
    except Exception as e:
        logger.error(f"Error storing customer profile in loan table: {str(e)}")
        # Don't fail the entire function if storage fails, just log the error
        return None

def determine_salary_account_relationship(loan_data, input_data):
    """
    Determine if customer has salary account relationship with NeoBank
    IMPORTANT: Only customers with salary paid to NeoBank should be SalaryAccount
    Customers with salary paid to MyBank or other banks should be NonSalaryAccount
    """
    
    try:
        # Get bank name from loan data or input data
        bank_name = (loan_data.get('bank_name') or input_data.get('bank_name', '')).upper()
        
        # Check if bank name indicates NeoBank relationship
        neobank_indicators = ['NEOBANK', 'NEO-BANK', 'NEO BANK']
        
        # First check: Is the bank_name a NeoBank?
        if bank_name and any(indicator in bank_name for indicator in neobank_indicators):
            logger.info(f"Salary account relationship detected - bank_name is NeoBank: {bank_name}")
            return True
        
        # Check loan_statement_document for NeoBank indicators
        statement_doc = loan_data.get('loan_statement_document', {}) or input_data.get('loan_statement_document', {})
        if isinstance(statement_doc, dict):
            statement_bank_name = statement_doc.get('bank_name', '').upper()
            
            # Only consider it a salary account if the statement bank is NeoBank
            if statement_bank_name and any(indicator in statement_bank_name for indicator in neobank_indicators):
                logger.info(f"Salary account relationship detected - statement bank_name is NeoBank: {statement_bank_name}")
                return True
        
        # If we reach here, the salary is paid to a different bank (like MyBank)
        # This should be NonSalaryAccount regardless of salary_transfer amount
        if bank_name:
            logger.info(f"Non-salary account relationship - salary paid to different bank: {bank_name}")
        else:
            logger.info("Non-salary account relationship - no clear NeoBank indicators")
        
        return False
        
    except Exception as e:
        logger.error(f"Error determining salary account relationship: {str(e)}")
        return False  # Conservative default

def calculate_customer_age(date_of_birth):
    """
    Calculate customer age from date of birth
    Handles DD/MM/YYYY format from KYC data (e.g., "13/01/1986")
    """
    
    try:
        if not date_of_birth:
            logger.warning("Date of birth not provided")
            return None
        
        # Handle DD/MM/YYYY format
        if isinstance(date_of_birth, str) and '/' in date_of_birth:
            try:
                # Parse DD/MM/YYYY format
                birth_date = datetime.strptime(date_of_birth, '%d/%m/%Y').date()
            except ValueError:
                try:
                    # Try MM/DD/YYYY format as fallback
                    birth_date = datetime.strptime(date_of_birth, '%m/%d/%Y').date()
                except ValueError:
                    try:
                        # Try YYYY-MM-DD format as fallback
                        birth_date = datetime.strptime(date_of_birth, '%Y-%m-%d').date()
                    except ValueError:
                        logger.error(f"Unable to parse date of birth format: {date_of_birth}")
                        return None
        else:
            logger.error(f"Unexpected date of birth format: {date_of_birth}")
            return None
        
        # Calculate age
        today = date.today()
        age = today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))
        
        logger.info(f"Calculated age: {age} from date of birth: {date_of_birth}")
        return age
        
    except Exception as e:
        logger.error(f"Error calculating age: {str(e)}")
        return None

def determine_customer_segment(is_bahraini, has_salary_account):
    """
    Determine customer segment based on nationality and salary account relationship
    
    Customer Segments:
    - SalaryAccount_Local: Existing Customer, Salary paid to neobank, Bahrain National
    - SalaryAccount_Expat: Existing Customer, Salary paid to neobank, Non-Bahrain National  
    - NonSalaryAccount_Local: Existing Customer, Non-Salary paid to neobank, Bahrain National
    - NonSalaryAccount_Expat: Existing Customer, Non-Salary paid to neobank, Non-Bahrain National
    """
    
    try:
        # Determine nationality classification
        nationality_classification = 'Local' if is_bahraini else 'Expat'
        
        # Determine banking relationship classification
        banking_relationship = 'SalaryAccount' if has_salary_account else 'NonSalaryAccount'
        
        # Combine to create final segment
        customer_segment = f"{banking_relationship}_{nationality_classification}"
        
        logger.info(f"Customer segment determined: {customer_segment} (is_bahraini={is_bahraini}, has_salary_account={has_salary_account})")
        
        return customer_segment
        
    except Exception as e:
        logger.error(f"Error determining customer segment: {str(e)}")
        return 'NonSalaryAccount_Expat'  # Conservative fallback