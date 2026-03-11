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

# Initialize AWS clients
ssm_client = boto3.client('ssm')

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

def convert_float_to_decimal(obj):
    """Recursively convert float values to Decimal for DynamoDB compatibility"""
    from decimal import Decimal
    
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: convert_float_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_float_to_decimal(v) for v in obj]
    else:
        return obj

def lambda_handler(event, context):
    """
    Segment Configuration Loader Lambda Function
    Loads segment-specific configuration with business rules and risk parameters
    """
    
    try:
        # Extract data from event - handle Step Functions Lambda invoke payload structure
        if 'Payload' in event:
            # This is coming from Step Functions Lambda invoke
            payload_data = event['Payload']
            customer_id = payload_data.get('customer_id', 'UNKNOWN')
            application_id = payload_data.get('application_id', 'UNKNOWN')
            processing_chain = payload_data.get('processing_chain', {})
            stage_results = payload_data.get('stage_results', {})
        else:
            # Direct invocation
            customer_id = event.get('customer_id', event.get('processingContext', {}).get('customer_id', 'UNKNOWN'))
            application_id = event.get('application_id', event.get('processingContext', {}).get('application_id', 'UNKNOWN'))
            processing_chain = event.get('processing_chain', {})
            stage_results = event.get('stage_results', {})
        
        logger.info(f"Loading segment configuration for customer_id: {customer_id}, application_id: {application_id}")
        
        # Start X-Ray subsegment for segment configuration loading
        with xray_recorder.in_subsegment('segment_configuration_loading'):
            # Extract customer segment and profile from processing chain or stage results
            customer_segment, customer_profile = get_customer_segment_and_profile_from_data(processing_chain, stage_results)
            
            if not customer_segment:
                logger.warning("Customer segment not found in event, using default")
                customer_segment = 'NonSalaryAccount_Expat'
            
            logger.info(f"Loaded configuration for segment: {customer_segment}")
            
            # Log customer profile information if available
            if customer_profile:
                nationality = customer_profile.get('nationality', 'UNKNOWN')
                is_bahraini = customer_profile.get('is_bahraini', False)
                logger.info(f"Customer profile available - nationality: {nationality}, is_bahraini: {is_bahraini}")
            
            # Load segment-specific configuration
            segment_config = load_segment_configuration(customer_segment)
            
            # Validate configuration completeness
            validated_config = validate_configuration(segment_config, customer_segment)
            
            # Enrich customer profile with segment configuration for underwriting stages
            enriched_customer_profile = customer_profile.copy() if customer_profile else {}
            enriched_customer_profile.update({
                'customer_segment': customer_segment,
                'segment_configuration': validated_config,
                'config_loaded_at': datetime.utcnow().isoformat(),
                'config_source': validated_config.get('source', 'default'),
                'config_version': validated_config.get('version', '1.0')
            })
            
            # Store enriched customer profile in loan table
            store_customer_profile_in_loan_table(customer_id, application_id, enriched_customer_profile)
            
            # Prepare stage results
            new_stage_results = {
                'stage': 'segment_configuration',
                'timestamp': datetime.utcnow().isoformat(),
                'customer_segment': customer_segment,
                'segment_configuration': validated_config,
                'config_version': validated_config.get('version', '1.0'),
                'config_source': validated_config.get('source', 'default'),
                'customer_profile': enriched_customer_profile  # Pass enriched profile to next stages
            }
            
            # Add to processing chain (simple approach)
            updated_chain = processing_chain.copy()
            stage_key = f"segment_configuration_{len(updated_chain) + 1:03d}"
            updated_chain[stage_key] = new_stage_results
            
            # Add X-Ray annotations
            xray_recorder.put_annotation('customer_id', customer_id)
            xray_recorder.put_annotation('customer_segment', customer_segment)
            xray_recorder.put_annotation('config_version', validated_config.get('version', '1.0'))
            
            return {
                'statusCode': 200,
                'customer_id': customer_id,
                'application_id': application_id,
                'processing_chain': updated_chain,
                'stage_results': new_stage_results
            }
            
    except Exception as e:
        logger.error(f"Error in segment configuration loading: {str(e)}")
        xray_recorder.put_annotation('error', str(e))
        
        # Return error with processing chain preserved
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('customer_id', 'UNKNOWN'),
            'application_id': event.get('application_id', 'UNKNOWN'),
            'processing_chain': event.get('processing_chain', {})
        }

def get_customer_segment_and_profile_from_data(processing_chain, stage_results):
    """Extract customer segment and customer profile from processing chain or stage results"""
    
    try:
        # First check stage_results (direct from previous function)
        if isinstance(stage_results, dict):
            customer_segment = stage_results.get('customer_segment')
            customer_profile = stage_results.get('customer_profile', {})
            if customer_segment:
                logger.info(f"Found customer segment in stage_results: {customer_segment}")
                if customer_profile:
                    nationality = customer_profile.get('nationality', 'UNKNOWN')
                    logger.info(f"Found customer profile with nationality: {nationality}")
                return customer_segment, customer_profile
        
        # Look for customer segmentation results in the processing chain
        if isinstance(processing_chain, dict):
            for stage_key, stage_data in processing_chain.items():
                if isinstance(stage_data, dict) and stage_data.get('stage') == 'customer_segmentation':
                    customer_segment = stage_data.get('customer_segment')
                    customer_profile = stage_data.get('customer_profile', {})
                    if customer_segment:
                        logger.info(f"Found customer segment in processing_chain: {customer_segment}")
                        if customer_profile:
                            nationality = customer_profile.get('nationality', 'UNKNOWN')
                            logger.info(f"Found customer profile with nationality: {nationality}")
                        return customer_segment, customer_profile
            
            # If not found in structured format, look for direct customer_segment key
            customer_segment = processing_chain.get('customer_segment')
            if customer_segment:
                logger.info(f"Found customer segment as direct key: {customer_segment}")
                return customer_segment, {}
        
        logger.warning("Customer segment not found in any data source")
        return None, {}
        
    except Exception as e:
        logger.error(f"Error extracting customer segment from data: {str(e)}")
        return None, {}

@xray_recorder.capture('load_segment_configuration')
def load_segment_configuration(customer_segment):
    """Load segment-specific configuration from SSM Parameter Store with fallback"""
    
    try:
        # Try to load from SSM Parameter Store first
        try:
            ssm_config = load_config_from_ssm(customer_segment)
            if ssm_config:
                logger.info(f"Loaded configuration from SSM for segment: {customer_segment}")
                return ssm_config
        except Exception as e:
            logger.warning(f"Failed to load SSM configuration: {str(e)}")
        
        # Fallback to default configuration
        default_config = get_default_configuration(customer_segment)
        logger.info(f"Using default configuration for segment: {customer_segment}")
        return default_config
        
    except Exception as e:
        logger.error(f"Error loading segment configuration: {str(e)}")
        return get_emergency_fallback_configuration()

# S3 configuration loading removed - using SSM only for simpler architecture

def load_config_from_ssm(customer_segment):
    """Load configuration from SSM Parameter Store"""
    
    try:
        parameter_name = f'/neobank/loan-processing/segments/{customer_segment}/config'
        
        response = ssm_client.get_parameter(
            Name=parameter_name,
            WithDecryption=True
        )
        
        config_data = json.loads(response['Parameter']['Value'])
        config_data['source'] = 'ssm'
        config_data['loaded_at'] = datetime.utcnow().isoformat()
        
        return config_data
        
    except Exception as e:
        logger.warning(f"SSM configuration load failed: {str(e)}")
        return None

def get_default_configuration(customer_segment):
    """Get default configuration for the segment"""
    
    try:
        # Base configuration template
        base_config = {
            'version': '1.0',
            'source': 'default',
            'loaded_at': datetime.utcnow().isoformat(),
            'segment': customer_segment,
            'business_rules': {
                'age_limits': {
                    'minimum_age': 21,
                    'maximum_age_at_maturity': 65
                },
                'income_requirements': {
                    'minimum_salary': 500,  # BHD
                    'income_multiplier': 20,  # Maximum loan = salary * multiplier
                    'dti_threshold': 50  # Maximum debt-to-income ratio %
                },
                'employment_requirements': {
                    'minimum_employment_duration': 6,  # months
                    'probation_period_allowed': False
                },
                'credit_requirements': {
                    'minimum_credit_score': 600,
                    'maximum_delinquency_days': 90,
                    'bankruptcy_exclusion_years': 7
                }
            },
            'risk_parameters': {
                'scoring_weights': {
                    'credit_score': 0.3,
                    'dti_ratio': 0.25,
                    'banking_relationship': 0.2,
                    'employment_stability': 0.15,
                    'financial_behavior': 0.1
                },
                'approval_thresholds': {
                    'auto_approve': 85,
                    'manual_review': 65,
                    'auto_decline': 40
                },
                'enhanced_due_diligence_threshold': 70
            },
            'pricing_parameters': {
                'base_interest_rate': 8.5,  # %
                'risk_adjustment_range': [-2.0, 4.0],  # % adjustment based on risk
                'processing_fee': 50,  # BHD
                'early_settlement_penalty': 2.0  # %
            }
        }
        
        # Segment-specific adjustments
        segment_adjustments = get_segment_specific_adjustments(customer_segment)
        
        # Apply segment adjustments
        adjusted_config = apply_segment_adjustments(base_config, segment_adjustments)
        
        return adjusted_config
        
    except Exception as e:
        logger.error(f"Error creating default configuration: {str(e)}")
        return get_emergency_fallback_configuration()

def get_segment_specific_adjustments(customer_segment):
    """Get segment-specific configuration adjustments"""
    
    adjustments = {
        'SalaryAccount_Local': {
            'business_rules.income_requirements.minimum_salary': 400,
            'business_rules.income_requirements.income_multiplier': 25,
            'business_rules.income_requirements.dti_threshold': 55,
            'risk_parameters.scoring_weights.banking_relationship': 0.25,
            'risk_parameters.approval_thresholds.auto_approve': 80,
            'pricing_parameters.base_interest_rate': 7.5,
            'pricing_parameters.risk_adjustment_range': [-1.5, 3.0]
        },
        'SalaryAccount_Expat': {
            'business_rules.income_requirements.minimum_salary': 600,
            'business_rules.income_requirements.income_multiplier': 22,
            'business_rules.income_requirements.dti_threshold': 50,
            'risk_parameters.scoring_weights.banking_relationship': 0.22,
            'risk_parameters.approval_thresholds.auto_approve': 82,
            'pricing_parameters.base_interest_rate': 8.0,
            'pricing_parameters.risk_adjustment_range': [-1.0, 3.5]
        },
        'NonSalaryAccount_Local': {
            'business_rules.income_requirements.minimum_salary': 500,
            'business_rules.income_requirements.income_multiplier': 18,
            'business_rules.income_requirements.dti_threshold': 45,
            'risk_parameters.scoring_weights.banking_relationship': 0.15,
            'risk_parameters.approval_thresholds.auto_approve': 87,
            'pricing_parameters.base_interest_rate': 9.0,
            'pricing_parameters.risk_adjustment_range': [-0.5, 4.0]
        },
        'NonSalaryAccount_Expat': {
            'business_rules.income_requirements.minimum_salary': 700,
            'business_rules.income_requirements.income_multiplier': 15,
            'business_rules.income_requirements.dti_threshold': 40,
            'risk_parameters.scoring_weights.banking_relationship': 0.12,
            'risk_parameters.approval_thresholds.auto_approve': 90,
            'pricing_parameters.base_interest_rate': 9.5,
            'pricing_parameters.risk_adjustment_range': [0.0, 4.5]
        }
    }
    
    return adjustments.get(customer_segment, {})

def apply_segment_adjustments(base_config, adjustments):
    """Apply segment-specific adjustments to base configuration"""
    
    try:
        import copy
        adjusted_config = copy.deepcopy(base_config)
        
        for path, value in adjustments.items():
            # Navigate to the nested key and update value
            keys = path.split('.')
            current = adjusted_config
            
            # Navigate to parent of target key
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                elif not isinstance(current[key], dict):
                    # If the current value is not a dict, we can't navigate further
                    logger.warning(f"Cannot navigate to {key} in path {path}, current value is not a dict: {type(current[key])}")
                    break
                current = current[key]
            
            # Set the final value only if we successfully navigated to the parent
            if isinstance(current, dict):
                current[keys[-1]] = value
            else:
                logger.warning(f"Cannot set value for path {path}, parent is not a dict: {type(current)}")
        
        return adjusted_config
        
    except Exception as e:
        logger.error(f"Error applying segment adjustments: {str(e)}")
        return base_config

def get_emergency_fallback_configuration():
    """Get emergency fallback configuration for critical failures"""
    
    return {
        'version': '1.0-emergency',
        'source': 'emergency_fallback',
        'loaded_at': datetime.utcnow().isoformat(),
        'segment': 'NonSalaryAccount_Expat',
        'business_rules': {
            'age_limits': {
                'minimum_age': 21,
                'maximum_age_at_maturity': 65
            },
            'income_requirements': {
                'minimum_salary': 1000,  # Conservative high minimum
                'income_multiplier': 10,  # Conservative low multiplier
                'dti_threshold': 30  # Conservative low DTI
            },
            'employment_requirements': {
                'minimum_employment_duration': 12,  # Conservative high duration
                'probation_period_allowed': False
            },
            'credit_requirements': {
                'minimum_credit_score': 700,  # Conservative high score
                'maximum_delinquency_days': 30,  # Conservative low delinquency
                'bankruptcy_exclusion_years': 10
            }
        },
        'risk_parameters': {
            'scoring_weights': {
                'credit_score': 0.4,
                'dti_ratio': 0.3,
                'banking_relationship': 0.1,
                'employment_stability': 0.15,
                'financial_behavior': 0.05
            },
            'approval_thresholds': {
                'auto_approve': 95,  # Very high threshold
                'manual_review': 80,
                'auto_decline': 60
            },
            'enhanced_due_diligence_threshold': 85
        },
        'pricing_parameters': {
            'base_interest_rate': 12.0,  # Conservative high rate
            'risk_adjustment_range': [0.0, 5.0],
            'processing_fee': 100,
            'early_settlement_penalty': 3.0
        }
    }

@xray_recorder.capture('validate_configuration')
def validate_configuration(config, customer_segment):
    """Validate configuration completeness and consistency"""
    
    try:
        # Required configuration sections
        required_sections = [
            'business_rules',
            'risk_parameters', 
            'pricing_parameters'
        ]
        
        # Check for required sections
        for section in required_sections:
            if section not in config:
                logger.warning(f"Missing configuration section: {section}")
                config[section] = {}
        
        # Validate business rules
        validate_business_rules(config.get('business_rules', {}))
        
        # Validate risk parameters
        validate_risk_parameters(config.get('risk_parameters', {}))
        
        # Validate pricing parameters
        validate_pricing_parameters(config.get('pricing_parameters', {}))
        
        # Add validation metadata
        config['validation'] = {
            'validated_at': datetime.utcnow().isoformat(),
            'validation_status': 'passed',
            'segment': customer_segment
        }
        
        return config
        
    except Exception as e:
        logger.error(f"Configuration validation error: {str(e)}")
        config['validation'] = {
            'validated_at': datetime.utcnow().isoformat(),
            'validation_status': 'failed',
            'validation_error': str(e),
            'segment': customer_segment
        }
        return config

def validate_business_rules(business_rules):
    """Validate business rules section"""
    
    # Validate age limits
    age_limits = business_rules.get('age_limits', {})
    if age_limits.get('minimum_age', 0) < 18:
        business_rules['age_limits']['minimum_age'] = 21
    
    # Validate income requirements
    income_req = business_rules.get('income_requirements', {})
    if income_req.get('minimum_salary', 0) < 200:
        business_rules['income_requirements']['minimum_salary'] = 500

def validate_risk_parameters(risk_parameters):
    """Validate risk parameters section"""
    
    # Validate scoring weights sum to 1.0
    weights = risk_parameters.get('scoring_weights', {})
    total_weight = sum(weights.values())
    
    if abs(total_weight - 1.0) > 0.01:  # Allow small floating point differences
        logger.warning(f"Scoring weights sum to {total_weight}, normalizing to 1.0")
        # Normalize weights
        for key in weights:
            weights[key] = weights[key] / total_weight

def validate_pricing_parameters(pricing_parameters):
    """Validate pricing parameters section"""
    
    # Validate interest rate bounds
    base_rate = pricing_parameters.get('base_interest_rate', 0)
    if base_rate < 1.0 or base_rate > 25.0:
        pricing_parameters['base_interest_rate'] = 8.5
        logger.warning("Invalid base interest rate, reset to default")

def store_customer_profile_in_loan_table(customer_id, application_id, enriched_customer_profile):
    """Store enriched customer profile with segment configuration in loan table"""
    
    try:
        import boto3
        dynamodb = boto3.resource('dynamodb')
        
        # Update the loan table with enriched customer profile
        loan_table = dynamodb.Table('neobank-personal-loan')
        
        # Prepare update data
        update_data = {
            'customer_profile': enriched_customer_profile,
            'customer_segment': enriched_customer_profile.get('customer_segment'),
            'segment_config_loaded_at': datetime.utcnow().isoformat(),
            'underwriting_ready': True  # Flag that segment config is loaded and ready for underwriting
        }
        
        # Extract key underwriting parameters for easy access
        segment_config = enriched_customer_profile.get('segment_configuration', {})
        business_rules = segment_config.get('business_rules', {})
        income_req = business_rules.get('income_requirements', {})
        pricing = segment_config.get('pricing_parameters', {})
        risk_params = segment_config.get('risk_parameters', {})
        
        # Add key underwriting parameters as top-level fields for easy querying
        update_data.update({
            'minimum_salary_required': income_req.get('minimum_salary', 500),
            'income_multiplier': income_req.get('income_multiplier', 20),
            'dti_threshold': income_req.get('dti_threshold', 50),
            'base_interest_rate': pricing.get('base_interest_rate', 8.5),
            'processing_fee': pricing.get('processing_fee', 50),
            'auto_approve_threshold': risk_params.get('approval_thresholds', {}).get('auto_approve', 85),
            'manual_review_threshold': risk_params.get('approval_thresholds', {}).get('manual_review', 65)
        })
        
        # Convert all float values to Decimal for DynamoDB compatibility
        update_data = convert_float_to_decimal(update_data)
        
        # Update the loan record
        response = loan_table.update_item(
            Key={'customer_id': customer_id, 'application_id': application_id},
            UpdateExpression='SET customer_profile = :profile, customer_segment = :segment, segment_config_loaded_at = :loaded_at, underwriting_ready = :ready, minimum_salary_required = :min_salary, income_multiplier = :multiplier, dti_threshold = :dti, base_interest_rate = :rate, processing_fee = :fee, auto_approve_threshold = :auto_approve, manual_review_threshold = :manual_review',
            ExpressionAttributeValues={
                ':profile': update_data['customer_profile'],
                ':segment': update_data['customer_segment'],
                ':loaded_at': update_data['segment_config_loaded_at'],
                ':ready': update_data['underwriting_ready'],
                ':min_salary': update_data['minimum_salary_required'],
                ':multiplier': update_data['income_multiplier'],
                ':dti': update_data['dti_threshold'],
                ':rate': update_data['base_interest_rate'],
                ':fee': update_data['processing_fee'],
                ':auto_approve': update_data['auto_approve_threshold'],
                ':manual_review': update_data['manual_review_threshold']
            },
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Successfully stored enriched customer profile for {customer_id} in loan table")
        logger.info(f"Key underwriting parameters: min_salary={update_data['minimum_salary_required']}, rate={update_data['base_interest_rate']}%")
        
        return True
        
    except Exception as e:
        logger.error(f"Error storing customer profile in loan table: {str(e)}")
        # Don't fail the entire function if storage fails
        return False