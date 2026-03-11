import json
import boto3
import logging
from datetime import datetime
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS SDK calls for X-Ray tracing
patch_all()

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Import shared utilities
import sys
sys.path.append('/opt/python')
try:
    from data_chaining.chain_manager import DataChainManager
    from data_chaining.validation import validate_processing_chain
except ImportError:
    # Fallback for local testing
    class DataChainManager:
        def add_stage_results(self, processing_chain, stage_results):
            # Simple fallback implementation
            stage_key = f"{stage_results.get('stage', 'unknown')}_{len(processing_chain) + 1:03d}"
            processing_chain[stage_key] = stage_results
            return processing_chain
    
    def validate_processing_chain(event):
        return True

@xray_recorder.capture('lambda_handler')
def lambda_handler(event, context):
    """
    Customer Segmentation Lambda Function
    Classifies customers into four segments based on banking relationship and nationality
    """
    
    try:
        # Initialize data chaining (simplified approach)
        # chain_manager = DataChainManager({})
        
        # Validate input processing chain
        if not validate_processing_chain(event):
            raise ValueError("Invalid processing chain structure")
        
        # Extract data from event - handle Step Functions input structure
        processing_context = event.get('processingContext', {})
        customer_id = processing_context.get('customer_id') or event.get('customer_id', 'UNKNOWN')
        application_id = processing_context.get('application_id') or event.get('application_id', 'UNKNOWN')
        processing_chain = event.get('processing_chain', {})
        
        logger.info(f"Processing customer segmentation for customer_id: {customer_id}, application_id: {application_id}")
        
        # Start X-Ray subsegment for customer segmentation
        with xray_recorder.in_subsegment('customer_segmentation'):
            # Extract required data from processing chain and inputData
            customer_profile = get_customer_profile_from_chain(processing_chain)
            application_data = get_application_data_from_chain(processing_chain)
            
            # If no customer profile found in processing chain, try to get from inputData
            if not customer_profile:
                input_data = event.get('inputData', {})
                customer_profile = extract_customer_profile_from_input_data(input_data)
                logger.info("Extracted customer profile from inputData")
            
            # If no application data found, use inputData
            if not application_data:
                application_data = event.get('inputData', {})
                logger.info("Using inputData as application data")
            
            # Perform customer segmentation
            segmentation_result = perform_customer_segmentation(customer_profile, application_data)
            
            # Validate segmentation result
            validated_segment = validate_and_fallback_segment(segmentation_result)
            
            # Prepare stage results
            stage_results = {
                'stage': 'customer_segmentation',
                'timestamp': datetime.utcnow().isoformat(),
                'customer_segment': validated_segment['segment'],
                'segmentation_factors': validated_segment['factors'],
                'confidence_score': validated_segment['confidence'],
                'fallback_applied': validated_segment['fallback_applied'],
                'segment_details': {
                    'banking_relationship': validated_segment['banking_relationship'],
                    'nationality_classification': validated_segment['nationality_classification'],
                    'decision_reasoning': validated_segment['reasoning']
                },
                'customer_profile': customer_profile  # Include enriched customer profile for next stage
            }
            
            # Add to processing chain (simple approach)
            updated_chain = processing_chain.copy()
            stage_key = f"customer_segmentation_{len(updated_chain) + 1:03d}"
            updated_chain[stage_key] = stage_results
            
            # Add X-Ray annotations
            xray_recorder.put_annotation('customer_id', customer_id)
            xray_recorder.put_annotation('customer_segment', validated_segment['segment'])
            xray_recorder.put_annotation('confidence_score', validated_segment['confidence'])
            xray_recorder.put_annotation('fallback_applied', validated_segment['fallback_applied'])
            
            return {
                'statusCode': 200,
                'customer_id': customer_id,
                'application_id': application_id,
                'processing_chain': updated_chain,
                'stage_results': stage_results
            }
            
    except Exception as e:
        logger.error(f"Error in customer segmentation: {str(e)}")
        xray_recorder.put_annotation('error', str(e))
        
        # Return error with processing chain preserved
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('customer_id'),
            'application_id': event.get('application_id'),
            'processing_chain': event.get('processing_chain', {})
        }

def get_customer_profile_from_chain(processing_chain):
    """Extract customer profile data from processing chain"""
    
    try:
        # Look for customer profile in the processing chain stages
        for stage_key, stage_data in processing_chain.items():
            if isinstance(stage_data, dict):
                # Check if this stage has customer profile data
                if stage_data.get('stage') == 'customer_profile_retrieval':
                    customer_profile = stage_data.get('customer_profile', {})
                    if customer_profile:
                        logger.info(f"Found customer profile in stage: {stage_key}")
                        return customer_profile
                
                # Also check for direct customer_profile key in stage data
                if 'customer_profile' in stage_data:
                    customer_profile = stage_data['customer_profile']
                    if customer_profile:
                        logger.info(f"Found customer profile data in stage: {stage_key}")
                        return customer_profile
        
        # Look for customer profile in the customerSetup results (from parallel execution)
        customer_setup = processing_chain.get('customerSetup', [])
        if isinstance(customer_setup, list):
            for branch_result in customer_setup:
                if isinstance(branch_result, dict):
                    payload = branch_result.get('Payload', {})
                    if payload.get('stage_results', {}).get('stage') == 'customer_profile_retrieval':
                        return payload.get('customer_profile', {})
        
        # Fallback: look for direct customer_profile key
        customer_profile = processing_chain.get('customer_profile', {})
        if customer_profile:
            logger.info("Found customer profile as direct key")
            return customer_profile
        
        logger.warning("No customer profile found in processing chain")
        return {}
        
    except Exception as e:
        logger.error(f"Error extracting customer profile from chain: {str(e)}")
        return {}

def get_application_data_from_chain(processing_chain):
    """Extract application data from processing chain"""
    
    try:
        # Look for application data in the processing chain stages
        for stage_key, stage_data in processing_chain.items():
            if isinstance(stage_data, dict):
                # Check for application_data in stage data
                if 'application_data' in stage_data:
                    application_data = stage_data['application_data']
                    if application_data:
                        logger.info(f"Found application data in stage: {stage_key}")
                        return application_data
        
        # If not found in structured format, look for direct application_data key
        application_data = processing_chain.get('application_data', {})
        if application_data:
            logger.info("Found application data as direct key")
            return application_data
        
        logger.warning("No application data found in processing chain")
        return {}
        
    except Exception as e:
        logger.error(f"Error extracting application data from chain: {str(e)}")
        return {}

def extract_customer_profile_from_input_data(input_data):
    """Extract customer profile information from inputData"""
    
    try:
        # Extract relevant customer profile information from inputData
        customer_profile = {}
        
        # Bank information
        bank_name = input_data.get('bank_name', '')
        customer_profile['bank_name'] = bank_name
        
        # Determine if this is a salary account based on bank name ONLY
        # Only NEOBANK customers should be considered salary account customers
        if bank_name and ('NEOBANK' in bank_name.upper() or 'NEO-BANK' in bank_name.upper()):
            customer_profile['has_salary_account'] = True
        else:
            # All other banks (including MyBank) are NonSalaryAccount
            customer_profile['has_salary_account'] = False
        
        # Try to get nationality from database using customer_id
        customer_id = input_data.get('customer_id')
        if customer_id:
            nationality_data = get_customer_nationality_from_database(customer_id)
            customer_profile.update(nationality_data)
        else:
            # Fallback if no customer_id
            customer_profile['nationality'] = 'UNKNOWN'
            customer_profile['is_bahraini'] = False
        
        logger.info(f"Extracted customer profile: bank={bank_name}, has_salary_account={customer_profile['has_salary_account']}, nationality={customer_profile.get('nationality')}")
        return customer_profile
        
    except Exception as e:
        logger.error(f"Error extracting customer profile from input data: {str(e)}")
        return {}

def get_customer_nationality_from_database(customer_id):
    """Retrieve customer nationality information from database"""
    
    try:
        import boto3
        dynamodb = boto3.resource('dynamodb')
        
        # Try to get from customer profile table first
        try:
            customer_table = dynamodb.Table('neobank-customer-profile')
            response = customer_table.get_item(Key={'customer_id': customer_id})
            
            if 'Item' in response:
                item = response['Item']
                nationality = item.get('nationality', 'UNKNOWN')
                is_bahraini = item.get('is_bahraini', False)
                
                # Also check for alternative field names
                if nationality == 'UNKNOWN':
                    nationality = item.get('personal_info', {}).get('nationality', 'UNKNOWN')
                    is_bahraini = item.get('personal_info', {}).get('is_local', False)
                
                logger.info(f"Retrieved nationality from customer profile: {nationality}, is_bahraini: {is_bahraini}")
                return {
                    'nationality': nationality,
                    'is_bahraini': is_bahraini
                }
        except Exception as e:
            logger.warning(f"Could not access customer profile table: {str(e)}")
        
        # Try to get from KYC table as fallback (CORRECT TABLE NAME)
        try:
            kyc_table = dynamodb.Table('neo-bank-customer-kyc')
            response = kyc_table.get_item(Key={'customer_id': customer_id})
            
            if 'Item' in response:
                item = response['Item']
                nationality = item.get('nationality', 'UNKNOWN')
                is_bahraini = nationality.upper() == 'BAHRAINI' if nationality != 'UNKNOWN' else False
                
                logger.info(f"Retrieved nationality from KYC data: {nationality}, is_bahraini: {is_bahraini}")
                return {
                    'nationality': nationality,
                    'is_bahraini': is_bahraini
                }
        except Exception as e:
            logger.warning(f"Could not access KYC table: {str(e)}")
        
        # Try personal loan table as last resort
        try:
            loan_table = dynamodb.Table('neobank-personal-loan')
            response = loan_table.get_item(Key={'customer_id': customer_id})
            
            if 'Item' in response:
                item = response['Item']
                nationality = item.get('nationality', 'UNKNOWN')
                is_bahraini = nationality.upper() == 'BAHRAINI' if nationality != 'UNKNOWN' else False
                
                logger.info(f"Retrieved nationality from loan table: {nationality}, is_bahraini: {is_bahraini}")
                return {
                    'nationality': nationality,
                    'is_bahraini': is_bahraini
                }
        except Exception as e:
            logger.warning(f"Could not access loan table: {str(e)}")
        
        # If all database lookups fail, return conservative defaults
        logger.warning(f"Could not retrieve nationality for customer {customer_id}, using conservative defaults")
        return {
            'nationality': 'UNKNOWN',
            'is_bahraini': False  # Conservative assumption
        }
        
    except Exception as e:
        logger.error(f"Error retrieving nationality from database: {str(e)}")
        return {
            'nationality': 'UNKNOWN',
            'is_bahraini': False
        }

@xray_recorder.capture('perform_customer_segmentation')
def perform_customer_segmentation(customer_profile, application_data):
    """Perform customer segmentation based on banking relationship and nationality"""
    
    try:
        # Extract nationality information (simplified structure)
        nationality = customer_profile.get('nationality', '').upper()
        is_bahraini = customer_profile.get('is_bahraini', False)
        
        # Extract banking relationship information
        has_salary_account = customer_profile.get('has_salary_account', False)
        bank_name = customer_profile.get('bank_name', application_data.get('bank_name', '')).upper()
        
        # Determine nationality classification
        nationality_classification = classify_nationality(nationality, is_bahraini)
        
        # Determine banking relationship
        banking_relationship = classify_banking_relationship(has_salary_account, bank_name)
        
        # Determine final segment
        customer_segment = determine_customer_segment(nationality_classification, banking_relationship)
        
        # Calculate confidence score
        confidence_score = calculate_segmentation_confidence(
            customer_profile, application_data, nationality_classification, banking_relationship
        )
        
        # Generate reasoning
        reasoning = generate_segmentation_reasoning(
            nationality, is_bahraini, bank_name, has_salary_account, 
            nationality_classification, banking_relationship, customer_segment
        )
        
        segmentation_result = {
            'segment': customer_segment,
            'nationality_classification': nationality_classification,
            'banking_relationship': banking_relationship,
            'confidence': confidence_score,
            'factors': {
                'nationality': nationality,
                'is_bahraini': is_bahraini,
                'bank_name': bank_name,
                'has_salary_account': has_salary_account
            },
            'reasoning': reasoning,
            'fallback_applied': False
        }
        
        logger.info(f"Customer segmentation result: {customer_segment} (confidence: {confidence_score})")
        return segmentation_result
        
    except Exception as e:
        logger.error(f"Error in customer segmentation: {str(e)}")
        raise

def classify_nationality(nationality, is_bahraini):
    """Classify customer nationality as Local or Expat"""
    
    try:
        # Primary classification based on is_bahraini flag or nationality
        if is_bahraini or nationality == 'BAHRAINI':
            return 'Local'
        
        # Known expat nationalities
        expat_nationalities = [
            'INDIAN', 'INDIA', 'PAKISTANI', 'PAKISTAN', 'BANGLADESHI', 'BANGLADESH',
            'FILIPINO', 'PHILIPPINES', 'EGYPTIAN', 'EGYPT', 'JORDANIAN', 'JORDAN',
            'LEBANESE', 'LEBANON', 'SYRIAN', 'SYRIA', 'SUDANESE', 'SUDAN',
            'BRITISH', 'UK', 'AMERICAN', 'USA', 'CANADIAN', 'CANADA'
        ]
        
        if nationality in expat_nationalities:
            return 'Expat'
        
        # Default to Expat for unknown nationalities (conservative approach)
        logger.warning(f"Unknown nationality: {nationality}, defaulting to Expat")
        return 'Expat'
        
    except Exception as e:
        logger.error(f"Error classifying nationality: {str(e)}")
        return 'Expat'  # Conservative fallback

def classify_banking_relationship(has_salary_account, bank_name):
    """Classify banking relationship as SalaryAccount or NonSalaryAccount"""
    
    try:
        # Check if salary account relationship is explicitly determined
        if has_salary_account:
            return 'SalaryAccount'
        
        # Double-check with bank name indicators - ONLY NEOBANK is salary account
        neobank_indicators = ['NEOBANK', 'NEO-BANK', 'NEO BANK']
        
        if bank_name and any(indicator in bank_name.upper() for indicator in neobank_indicators):
            return 'SalaryAccount'
        
        # All other banks (including MyBank) are NonSalaryAccount
        return 'NonSalaryAccount'
        
    except Exception as e:
        logger.error(f"Error classifying banking relationship: {str(e)}")
        return 'NonSalaryAccount'  # Conservative fallback

def determine_customer_segment(nationality_classification, banking_relationship):
    """Determine final customer segment"""
    
    try:
        # Four possible segments based on nationality and banking relationship
        segment_matrix = {
            ('Local', 'SalaryAccount'): 'SalaryAccount_Local',
            ('Local', 'NonSalaryAccount'): 'NonSalaryAccount_Local',
            ('Expat', 'SalaryAccount'): 'SalaryAccount_Expat',
            ('Expat', 'NonSalaryAccount'): 'NonSalaryAccount_Expat'
        }
        
        segment_key = (nationality_classification, banking_relationship)
        return segment_matrix.get(segment_key, 'NonSalaryAccount_Expat')  # Conservative fallback
        
    except Exception as e:
        logger.error(f"Error determining customer segment: {str(e)}")
        return 'NonSalaryAccount_Expat'  # Most conservative segment

def calculate_segmentation_confidence(customer_profile, application_data, nationality_classification, banking_relationship):
    """Calculate confidence score for segmentation decision"""
    
    try:
        confidence_factors = []
        
        # Nationality confidence
        nationality = customer_profile.get('personal_info', {}).get('nationality')
        is_local = customer_profile.get('personal_info', {}).get('is_local')
        
        if nationality and is_local is not None:
            confidence_factors.append(0.9)  # High confidence with both fields
        elif nationality:
            confidence_factors.append(0.7)  # Medium confidence with nationality only
        else:
            confidence_factors.append(0.3)  # Low confidence without nationality
        
        # Banking relationship confidence
        bank_name = application_data.get('bank_name')
        salary_transfer = application_data.get('salary_transfer')
        
        if salary_transfer is not None and bank_name:
            confidence_factors.append(0.9)  # High confidence with both fields
        elif salary_transfer is not None or bank_name:
            confidence_factors.append(0.6)  # Medium confidence with one field
        else:
            confidence_factors.append(0.3)  # Low confidence without clear indicators
        
        # KYC verification confidence
        kyc_status = customer_profile.get('kyc_status', {}).get('verification_status')
        if kyc_status == 'VERIFIED':
            confidence_factors.append(0.8)
        else:
            confidence_factors.append(0.5)
        
        # Calculate overall confidence
        overall_confidence = sum(confidence_factors) / len(confidence_factors)
        return round(overall_confidence * 100, 2)
        
    except Exception as e:
        logger.error(f"Error calculating segmentation confidence: {str(e)}")
        return 50.0  # Default medium confidence

def generate_segmentation_reasoning(nationality, is_bahraini, bank_name, has_salary_account, 
                                  nationality_classification, banking_relationship, customer_segment):
    """Generate human-readable reasoning for segmentation decision"""
    
    try:
        reasoning_parts = []
        
        # Nationality reasoning
        if is_bahraini or nationality == 'BAHRAINI':
            reasoning_parts.append(f"Customer classified as Local based on nationality: {nationality}")
        else:
            reasoning_parts.append(f"Customer classified as Expat based on nationality: {nationality}")
        
        # Banking relationship reasoning
        if has_salary_account:
            reasoning_parts.append("Salary account relationship confirmed")
        elif bank_name and any(indicator in bank_name.upper() for indicator in ['NEOBANK', 'NEO-BANK', 'NEO BANK', 'MYBANK']):
            reasoning_parts.append(f"Salary account relationship inferred from bank name: {bank_name}")
        else:
            reasoning_parts.append("Non-salary account relationship - no clear salary transfer indicators")
        
        # Final segment reasoning
        reasoning_parts.append(f"Final segment assignment: {customer_segment}")
        
        return "; ".join(reasoning_parts)
        
    except Exception as e:
        logger.error(f"Error generating segmentation reasoning: {str(e)}")
        return "Segmentation completed with default reasoning"

@xray_recorder.capture('validate_and_fallback_segment')
def validate_and_fallback_segment(segmentation_result):
    """Validate segmentation result and apply fallback if needed"""
    
    try:
        valid_segments = [
            'SalaryAccount_Local',
            'SalaryAccount_Expat', 
            'NonSalaryAccount_Local',
            'NonSalaryAccount_Expat'
        ]
        
        current_segment = segmentation_result.get('segment')
        confidence = segmentation_result.get('confidence', 0)
        
        # Check if segment is valid
        if current_segment not in valid_segments:
            logger.warning(f"Invalid segment detected: {current_segment}, applying fallback")
            segmentation_result['segment'] = 'NonSalaryAccount_Expat'
            segmentation_result['fallback_applied'] = True
            segmentation_result['reasoning'] += "; Fallback applied due to invalid segment"
        
        # Check if confidence is too low
        elif confidence < 30:
            logger.warning(f"Low confidence segmentation: {confidence}%, applying conservative fallback")
            segmentation_result['segment'] = 'NonSalaryAccount_Expat'
            segmentation_result['fallback_applied'] = True
            segmentation_result['reasoning'] += "; Conservative fallback applied due to low confidence"
        
        return segmentation_result
        
    except Exception as e:
        logger.error(f"Error in segment validation: {str(e)}")
        # Return most conservative segment as ultimate fallback
        return {
            'segment': 'NonSalaryAccount_Expat',
            'nationality_classification': 'Expat',
            'banking_relationship': 'NonSalaryAccount',
            'confidence': 25.0,
            'factors': {},
            'reasoning': 'Emergency fallback applied due to validation error',
            'fallback_applied': True
        }