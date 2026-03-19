import json
import boto3
import logging
from decimal import Decimal
from datetime import datetime

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def lambda_handler(event, context):
    """
    Loan Underwriting Function - Comprehensive risk assessment and underwriting decision
    
    This function performs detailed underwriting analysis but does NOT make final approval decisions
    when manual_processing_enabled flag is True (default behavior).
    """
    
    try:
        logger.info("Starting loan underwriting analysis")
        
        # Extract data from previous stages
        customer_profile = event.get('customerProfile', {}).get('Payload', {})
        customer_segmentation = event.get('customerSegmentation', {}).get('Payload', {})
        segment_config = event.get('segmentConfig', {}).get('Payload', {})
        parallel_results = event.get('parallelResults', [])
        
        # Check if manual processing is enabled (default: True)
        manual_processing_enabled = event.get('manual_processing_enabled', True)
        
        logger.info(f"Manual processing enabled: {manual_processing_enabled}")
        
        # Extract parallel results
        dti_result = {}
        credit_bureau_result = {}
        financial_profile_result = {}
        employer_analysis_result = {}
        social_media_result = {}
        
        for result in parallel_results:
            if 'Payload' in result:
                payload = result['Payload']
                if 'dti_ratio' in payload:
                    dti_result = payload
                elif 'credit_score' in payload:
                    credit_bureau_result = payload
                elif 'financial_stability_score' in payload:
                    financial_profile_result = payload
                elif 'employer_verification' in payload:
                    employer_analysis_result = payload
                elif 'social_media_score' in payload:
                    social_media_result = payload
        
        # Perform comprehensive underwriting analysis
        underwriting_analysis = perform_underwriting_analysis(
            customer_profile,
            customer_segmentation,
            segment_config,
            dti_result,
            credit_bureau_result,
            financial_profile_result,
            employer_analysis_result,
            social_media_result
        )
        
        # Determine underwriting decision based on manual processing flag
        if manual_processing_enabled:
            # When manual processing is enabled, always recommend manual review
            underwriting_decision = {
                "decision": "MANUAL_REVIEW_REQUIRED",
                "manual_review_required": True,
                "reason": "Manual processing enabled - all applications require human underwriter review",
                "confidence_score": underwriting_analysis['overall_risk_score'],
                "risk_level": underwriting_analysis['risk_level'],
                "review_factors": [
                    "Manual processing flag enabled",
                    "Comprehensive underwriting analysis completed",
                    "Human underwriter review required"
                ],
                "automated_recommendation": underwriting_analysis.get('automated_recommendation', 'PENDING_REVIEW'),
                "underwriting_notes": "Complete underwriting analysis performed. Manual review required per policy."
            }
        else:
            # When manual processing is disabled, make automated decision
            underwriting_decision = make_automated_underwriting_decision(underwriting_analysis)
        
        # Prepare response
        response = {
            "statusCode": 200,
            "underwriting_analysis": underwriting_analysis,
            "underwriting_decision": underwriting_decision,
            "processing_metadata": {
                "function_name": "loan-underwriting",
                "timestamp": datetime.utcnow().isoformat(),
                "manual_processing_enabled": manual_processing_enabled,
                "analysis_version": "2.0"
            }
        }
        
        logger.info(f"Underwriting analysis completed. Decision: {underwriting_decision['decision']}")
        
        return response
        
    except Exception as e:
        logger.error(f"Error in loan underwriting: {str(e)}")
        return {
            "statusCode": 500,
            "error": "UNDERWRITING_ERROR",
            "message": str(e),
            "underwriting_decision": {
                "decision": "MANUAL_REVIEW_REQUIRED",
                "manual_review_required": True,
                "reason": f"Underwriting analysis failed: {str(e)}",
                "review_factors": ["System error during underwriting analysis"]
            }
        }

def perform_underwriting_analysis(customer_profile, customer_segmentation, segment_config, 
                                dti_result, credit_bureau_result, financial_profile_result,
                                employer_analysis_result, social_media_result):
    """Perform comprehensive underwriting analysis"""
    
    # Five Cs Analysis
    character_score = calculate_character_score(credit_bureau_result, social_media_result, employer_analysis_result)
    capacity_score = calculate_capacity_score(dti_result, financial_profile_result, customer_profile)
    capital_score = calculate_capital_score(financial_profile_result, customer_profile)
    collateral_score = calculate_collateral_score(customer_profile, financial_profile_result)
    conditions_score = calculate_conditions_score(customer_segmentation, segment_config)
    
    # Overall risk assessment
    overall_risk_score = (character_score + capacity_score + capital_score + collateral_score + conditions_score) / 5
    
    # Determine risk level
    if overall_risk_score >= 80:
        risk_level = "LOW"
        automated_recommendation = "APPROVE"
    elif overall_risk_score >= 65:
        risk_level = "MEDIUM"
        automated_recommendation = "CONDITIONAL_APPROVE"
    elif overall_risk_score >= 50:
        risk_level = "HIGH"
        automated_recommendation = "MANUAL_REVIEW"
    else:
        risk_level = "VERY_HIGH"
        automated_recommendation = "DECLINE"
    
    return {
        "five_cs_analysis": {
            "character_score": character_score,
            "capacity_score": capacity_score,
            "capital_score": capital_score,
            "collateral_score": collateral_score,
            "conditions_score": conditions_score
        },
        "overall_risk_score": overall_risk_score,
        "risk_level": risk_level,
        "automated_recommendation": automated_recommendation,
        "analysis_details": {
            "dti_analysis": dti_result,
            "credit_analysis": credit_bureau_result,
            "financial_analysis": financial_profile_result,
            "employer_analysis": employer_analysis_result,
            "social_analysis": social_media_result
        }
    }

def make_automated_underwriting_decision(underwriting_analysis):
    """Make automated underwriting decision when manual processing is disabled"""
    
    overall_score = underwriting_analysis['overall_risk_score']
    risk_level = underwriting_analysis['risk_level']
    automated_recommendation = underwriting_analysis['automated_recommendation']
    
    if automated_recommendation == "APPROVE" and overall_score >= 80:
        return {
            "decision": "APPROVED",
            "manual_review_required": False,
            "reason": f"Automated approval - Low risk profile (Score: {overall_score})",
            "confidence_score": overall_score,
            "risk_level": risk_level,
            "review_factors": [],
            "automated_decision": True
        }
    elif automated_recommendation == "DECLINE" and overall_score < 50:
        return {
            "decision": "DECLINED",
            "manual_review_required": False,
            "reason": f"Automated decline - High risk profile (Score: {overall_score})",
            "confidence_score": overall_score,
            "risk_level": risk_level,
            "review_factors": ["High risk score", "Multiple risk factors identified"],
            "automated_decision": True
        }
    else:
        return {
            "decision": "MANUAL_REVIEW_REQUIRED",
            "manual_review_required": True,
            "reason": f"Risk profile requires human review (Score: {overall_score})",
            "confidence_score": overall_score,
            "risk_level": risk_level,
            "review_factors": ["Medium risk profile", "Complex underwriting factors"],
            "automated_decision": False
        }

def calculate_character_score(credit_bureau_result, social_media_result, employer_analysis_result):
    """Calculate character score based on credit history and reputation"""
    base_score = 50
    
    # Credit score component
    credit_score = credit_bureau_result.get('credit_score', 650)
    if credit_score >= 750:
        base_score += 30
    elif credit_score >= 700:
        base_score += 20
    elif credit_score >= 650:
        base_score += 10
    elif credit_score < 600:
        base_score -= 20
    
    # Payment history
    payment_history = credit_bureau_result.get('payment_history_score', 70)
    base_score += (payment_history - 70) * 0.3
    
    # Social media analysis
    social_score = social_media_result.get('social_media_score', 70)
    base_score += (social_score - 70) * 0.2
    
    # Employer verification
    employer_verified = employer_analysis_result.get('employer_verification', {}).get('verified', False)
    if employer_verified:
        base_score += 10
    
    return min(100, max(0, base_score))

def calculate_capacity_score(dti_result, financial_profile_result, customer_profile):
    """Calculate capacity score based on ability to repay"""
    base_score = 50
    
    # DTI ratio
    dti_ratio = dti_result.get('dti_ratio', 0.4)
    if dti_ratio <= 0.2:
        base_score += 30
    elif dti_ratio <= 0.3:
        base_score += 20
    elif dti_ratio <= 0.4:
        base_score += 10
    elif dti_ratio > 0.5:
        base_score -= 20
    
    # Income stability
    income_stability = financial_profile_result.get('income_stability_score', 70)
    base_score += (income_stability - 70) * 0.3
    
    # Employment status
    employment_status = customer_profile.get('employment_status', 'unemployed')
    if employment_status == 'employed':
        base_score += 15
    elif employment_status == 'self_employed':
        base_score += 5
    else:
        base_score -= 15
    
    return min(100, max(0, base_score))

def calculate_capital_score(financial_profile_result, customer_profile):
    """Calculate capital score based on assets and net worth"""
    base_score = 50
    
    # Savings and assets
    savings_score = financial_profile_result.get('savings_score', 50)
    base_score += (savings_score - 50) * 0.4
    
    # Net worth
    net_worth = financial_profile_result.get('estimated_net_worth', 0)
    if net_worth > 100000:
        base_score += 25
    elif net_worth > 50000:
        base_score += 15
    elif net_worth > 20000:
        base_score += 10
    elif net_worth < 0:
        base_score -= 20
    
    return min(100, max(0, base_score))

def calculate_collateral_score(customer_profile, financial_profile_result):
    """Calculate collateral score based on available security"""
    base_score = 40  # Personal loans typically have lower collateral scores
    
    # Property ownership
    owns_property = financial_profile_result.get('property_ownership', False)
    if owns_property:
        base_score += 30
    
    # Vehicle ownership
    owns_vehicle = financial_profile_result.get('vehicle_ownership', False)
    if owns_vehicle:
        base_score += 15
    
    # Other assets
    other_assets_value = financial_profile_result.get('other_assets_value', 0)
    if other_assets_value > 25000:
        base_score += 15
    elif other_assets_value > 10000:
        base_score += 10
    
    return min(100, max(0, base_score))

def calculate_conditions_score(customer_segmentation, segment_config):
    """Calculate conditions score based on market and economic factors"""
    base_score = 70  # Neutral market conditions
    
    # Customer segment risk
    segment = customer_segmentation.get('segment', 'standard')
    segment_risk = segment_config.get('risk_multiplier', 1.0)
    
    if segment_risk < 0.8:
        base_score += 20
    elif segment_risk < 1.0:
        base_score += 10
    elif segment_risk > 1.2:
        base_score -= 15
    elif segment_risk > 1.5:
        base_score -= 25
    
    # Economic conditions (could be enhanced with real-time data)
    base_score += 10  # Assuming stable economic conditions
    
    return min(100, max(0, base_score))
