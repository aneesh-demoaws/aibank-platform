import json
import boto3
import logging
from datetime import datetime
from strands import Agent
from strands.models import BedrockModel
from strands_tools import think

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
dynamodb = boto3.client('dynamodb')

def create_loan_underwriting_agent():
    """Create and configure the enhanced loan underwriting agent with 5 Cs of Credit framework."""
    return Agent(
        system_prompt="""# Personal Loan Underwriting Agent: 5 Cs of Credit Analysis

You are an expert Personal Loan Underwriting Agent for a NeoBank tasked with analyzing loan applications using the comprehensive 5 Cs of Credit framework. Your goal is to provide a structured, thorough underwriting decision that accurately assesses risk and makes appropriate lending recommendations.

## TASK OVERVIEW
Analyze the provided loan application information and generate a complete underwriting assessment following the 5 Cs of Credit framework: Character, Capacity, Capital, Collateral, and Conditions.

## ASSESSMENT FRAMEWORK: THE 5 Cs OF CREDIT

<framework>
### 1. CHARACTER
- Credit history and payment behavior
- Social media presence and digital reputation
- Employment history and professional stability
- Personal integrity and trustworthiness indicators

### 2. CAPACITY
- Debt-to-income ratio analysis
- Monthly cash flow and repayment ability
- Income stability and employment security
- Existing financial obligations

### 3. CAPITAL
- Personal financial resources and assets
- Down payment or equity contribution
- Savings and investment portfolio
- Financial reserves and liquidity

### 4. COLLATERAL
- Available security or guarantees
- Asset valuation and marketability
- Risk mitigation through secured lending
- Recovery potential in default scenarios

### 5. CONDITIONS
- Economic environment and market conditions
- Industry-specific risks and opportunities
- Loan purpose and intended use of funds
- Regulatory and compliance considerations
</framework>

## ANALYSIS INSTRUCTIONS
1. Evaluate each of the 5 Cs thoroughly based on the applicant information
2. Assign a score out of 20 points for each C (total possible score: 100)
3. Determine risk levels (Low/Medium/High) for each category
4. Calculate an overall risk rating (LOW/MEDIUM/HIGH/VERY HIGH)
5. Make a final decision (APPROVE/CONDITIONAL APPROVE/REJECT)
6. Provide detailed justification for your decision
7. Include risk mitigation strategies where appropriate

## REQUIRED OUTPUT FORMAT

<output_template>
### EXECUTIVE SUMMARY
**Final Decision**: [APPROVE/CONDITIONAL APPROVE/REJECT]
**Recommended Loan Amount**: BHD [Amount]
**Recommended Term**: [Duration] months
**Overall Risk Rating**: [LOW/MEDIUM/HIGH/VERY HIGH]

### 5 Cs OF CREDIT ANALYSIS

#### 1. CHARACTER ASSESSMENT
**Score**: [X/20]
**Key Findings**:
- Credit History: [Assessment]
- Social Media Analysis: [Assessment]
- Professional Reputation: [Assessment]
**Risk Level**: [Low/Medium/High]

#### 2. CAPACITY ANALYSIS
**Score**: [X/20]
**Key Findings**:
- Debt-to-Income Ratio: [X]%
- Monthly Disposable Income: BHD [Amount]
- Repayment Capacity: [Assessment]
**Risk Level**: [Low/Medium/High]

#### 3. CAPITAL EVALUATION
**Score**: [X/20]
**Key Findings**:
- Available Assets: [Assessment]
- Financial Reserves: [Assessment]
- Investment Portfolio: [Assessment]
**Risk Level**: [Low/Medium/High]

#### 4. COLLATERAL ASSESSMENT
**Score**: [X/20]
**Key Findings**:
- Security Available: [Assessment]
- Asset Valuation: [Assessment]
- Recovery Potential: [Assessment]
**Risk Level**: [Low/Medium/High]

#### 5. CONDITIONS REVIEW
**Score**: [X/20]
**Key Findings**:
- Economic Environment: [Assessment]
- Industry Stability: [Assessment]
- Loan Purpose: [Assessment]
**Risk Level**: [Low/Medium/High]

### COMPREHENSIVE RISK ANALYSIS
**Total 5 Cs Score**: [X/100]
**Primary Risk Factors**:
1. [Risk Factor 1] - [Impact Level]
2. [Risk Factor 2] - [Impact Level]
3. [Risk Factor 3] - [Impact Level]

**Risk Mitigation Strategies**:
1. [Strategy 1]
2. [Strategy 2]
3. [Strategy 3]

### UNDERWRITING DECISION RATIONALE
**Decision Justification**: [Detailed explanation based on 5 Cs analysis]

**Alternative Recommendations** (if applicable):
- Modified Loan Amount: BHD [Amount]
- Required Conditions: [List conditions]

**Manual Review Required**: [Yes/No]
**Review Priority**: [High/Medium/Low]
</output_template>

Based on the loan application information provided, conduct your analysis and provide your complete 5 Cs of Credit underwriting assessment following the exact structure above. Your response should include ONLY the completed assessment without any additional explanations or preamble.""",
        model=BedrockModel(model_id="eu.anthropic.claude-3-7-sonnet-20250219-v1:0", region="eu-west-1"),
        tools=[think],
    )

def get_loan_application_data(customer_id: str, application_id: str) -> dict:
    """Retrieve comprehensive loan application data from DynamoDB."""
    try:
        response = dynamodb.get_item(
            TableName='aibank-personal-loan',
            Key={
                'customer_id': {'S': customer_id},
                'application_id': {'S': application_id}
            }
        )
        
        if 'Item' in response:
            item = response['Item']
            
            # Helper function to safely get string values
            def get_str(key):
                return item.get(key, {}).get('S', '')
            
            # Helper function to safely get number values
            def get_num(key):
                return item.get(key, {}).get('N', '0')
            
            # Helper function to safely get nested JSON values
            def get_json_str(key, nested_key):
                try:
                    json_data = json.loads(get_str(key))
                    return str(json_data.get(nested_key, ''))
                except:
                    return ''
            
            # Extract comprehensive loan and customer data
            loan_data = {
                'customer_id': get_str('customer_id'),
                'application_id': get_str('application_id'),
                'amount': get_num('amount'),
                'duration': get_num('duration'),
                'basic_salary': get_num('basic_salary'),
                'monthly_payment': get_str('monthly_payment'),
                'status': get_str('status'),
                'customer_segment': get_str('customer_segment'),
                'employer_name': get_str('employer_name'),
                'bank_name': get_str('bank_name'),
                'average_balance': get_num('average_balance'),
                'ending_balance': get_num('ending_balance')
            }
            
            # Extract KYC and customer details
            customer_data = {
                'full_name': get_json_str('kyc_details', 'full_name'),
                'nationality': get_json_str('kyc_details', 'nationality'),
                'date_of_birth': get_json_str('kyc_details', 'date_of_birth'),
                'job_description': get_json_str('kyc_details', 'job_description'),
                'current_residency': get_json_str('kyc_details', 'current_residency'),
                'designation': get_str('designation')
            }
            
            # Extract all analysis data for 5 Cs framework
            analysis_data = {
                'employer_analysis': get_str('employer_analysis'),
                'social_analysis': get_str('social_analysis'),
                'credit_bureau_analysis': get_str('credit_bureau_analysis'),
                'financial_behaviour_analysis': get_str('financial_behaviour_analysis'),
                'debt_to_income_analysis_summary': get_str('debt_to_income_analysis_summary'),
                'company_analysis_summary': get_str('company_analysis_summary')
            }
            
            return {
                'loan_data': loan_data,
                'customer_data': customer_data,
                'analysis_data': analysis_data,
                'found': True
            }
        
        return {'found': False}
        
    except Exception as e:
        logger.error(f"Error retrieving loan application data: {str(e)}")
        return {'found': False, 'error': str(e)}

def update_loan_underwriting_recommendations(customer_id: str, application_id: str, recommendations: str) -> bool:
    """Update loan application with enhanced underwriting recommendations."""
    try:
        response = dynamodb.update_item(
            TableName='aibank-personal-loan',
            Key={
                'customer_id': {'S': customer_id},
                'application_id': {'S': application_id}
            },
            UpdateExpression='SET loan_underwritting_recommendations = :recommendations',
            ExpressionAttributeValues={
                ':recommendations': {'S': recommendations}
            },
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Successfully updated loan_underwritting_recommendations for {customer_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating loan_underwritting_recommendations: {str(e)}")
        return False

def lambda_handler(event, context):
    """Enhanced Loan Underwriting Summary Lambda Function with 5 Cs of Credit Framework"""
    
    try:
        customer_id = event.get('processingContext', {}).get('customer_id', 'UNKNOWN')
        application_id = event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        
        logger.info(f"Processing enhanced loan underwriting with 5 Cs framework for customer_id: {customer_id}")
        
        if customer_id == 'UNKNOWN' or application_id == 'UNKNOWN':
            raise ValueError("Missing customer_id or application_id in processing context")
        
        # Retrieve comprehensive loan application data
        app_data = get_loan_application_data(customer_id, application_id)
        
        if not app_data.get('found'):
            raise ValueError(f"No loan application data found for {customer_id}")
        
        # Create and initialize the enhanced agent
        underwriting_agent = create_loan_underwriting_agent()
        
        # Prepare comprehensive 5 Cs analysis query
        loan_data = app_data['loan_data']
        customer_data = app_data['customer_data']
        analysis_data = app_data['analysis_data']
        
        # Calculate key financial metrics
        loan_amount = float(loan_data.get('amount', 0))
        monthly_salary = float(loan_data.get('basic_salary', 0))
        loan_duration = int(loan_data.get('duration', 36))
        
        # Estimate monthly payment (simple calculation)
        if loan_amount > 0 and loan_duration > 0:
            estimated_monthly_payment = loan_amount / loan_duration
            dti_ratio = (estimated_monthly_payment / monthly_salary * 100) if monthly_salary > 0 else 0
        else:
            estimated_monthly_payment = 0
            dti_ratio = 0
        
        query = f"""Please analyze this loan application using the 5 Cs of Credit framework:

<loan_application_details>
Customer: {customer_data.get('full_name', 'Unknown')}
Loan Amount Requested: BHD {loan_amount:,.2f}
Loan Duration: {loan_duration} months
Monthly Salary: BHD {monthly_salary:,.2f}
Estimated Monthly Payment: BHD {estimated_monthly_payment:.2f}
Estimated DTI Ratio: {dti_ratio:.1f}%
Employer: {loan_data.get('employer_name', 'Unknown')}
Customer Segment: {loan_data.get('customer_segment', 'Unknown')}
</loan_application_details>

<customer_profile>
Nationality: {customer_data.get('nationality', 'Unknown')}
Current Residency: {customer_data.get('current_residency', 'Unknown')}
Job Description: {customer_data.get('job_description', 'Not provided')}
Designation: {customer_data.get('designation', 'Unknown')}
Banking Relationship: {loan_data.get('bank_name', 'Unknown')}
Average Balance: BHD {float(loan_data.get('average_balance', 0)):,.2f}
</customer_profile>

<five_cs_analysis_data>
CHARACTER Analysis:
{analysis_data.get('social_analysis', 'No social media analysis available')}

Credit Bureau Analysis:
{analysis_data.get('credit_bureau_analysis', 'No credit bureau analysis available')}

CAPACITY Analysis:
{analysis_data.get('debt_to_income_analysis_summary', 'No DTI analysis available')}

Financial Behavior Analysis:
{analysis_data.get('financial_behaviour_analysis', 'No financial behavior analysis available')}

CAPITAL & CONDITIONS Analysis:
Employer Analysis:
{analysis_data.get('employer_analysis', 'No employer analysis available')}

Company Analysis:
{analysis_data.get('company_analysis_summary', 'No company analysis available')}
</five_cs_analysis_data>

Provide your complete 5 Cs of Credit underwriting assessment following the required output format."""
        
        # Log query for debugging
        logger.info(f"5 Cs Analysis Query for {customer_id} - Loan Amount: BHD {loan_amount:,.2f}")
        
        # Get AI underwriting analysis
        underwriting_result = underwriting_agent(query)
        underwriting_text = str(underwriting_result)
        
        logger.info(f"Enhanced loan underwriting analysis completed successfully")
        
        # Update loan application with recommendations
        update_success = update_loan_underwriting_recommendations(
            customer_id, 
            application_id, 
            underwriting_text
        )
        
        # Check manual processing flag
        manual_processing_enabled = event.get('manual_processing_enabled', True)
        loan_type = event.get('loan_data', {}).get('loan_type', 'personal')
        
        # Enhanced underwriting decision logic
        if loan_type == 'instant_money':
            # Parse AI decision from underwriting text
            text_upper = underwriting_text.upper()
            if '**FINAL DECISION**: APPROVE' in text_upper or 'FINAL DECISION**: APPROVE' in text_upper:
                if 'CONDITIONAL' not in text_upper.split('FINAL DECISION')[1][:30]:
                    underwriting_decision = {
                        "decision": "APPROVE",
                        "manual_review_required": False,
                        "reason": "Instant Money — AI underwriting approved, all criteria met"
                    }
                else:
                    underwriting_decision = {
                        "decision": "CONDITIONAL_APPROVE",
                        "manual_review_required": False,
                        "reason": "Instant Money — conditional approval, auto-rejected per policy"
                    }
            else:
                underwriting_decision = {
                    "decision": "REJECT",
                    "manual_review_required": False,
                    "reason": "Instant Money — AI underwriting did not approve"
                }
        elif manual_processing_enabled:
            underwriting_decision = {
                "decision": "MANUAL_REVIEW_REQUIRED",
                "manual_review_required": True,
                "reason": "5 Cs of Credit analysis completed - Manual underwriter review required",
                "review_factors": [
                    "5 Cs of Credit framework analysis completed",
                    "Comprehensive risk assessment performed",
                    "Manual underwriter review required per policy"
                ],
                "underwriting_notes": "Complete 5 Cs analysis performed with structured recommendations for manual review."
            }
        else:
            underwriting_decision = "Manual processing required for 5 Cs framework implementation"
        
        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'underwriting_recommendations': underwriting_text,
            'five_cs_analysis': underwriting_text,
            'update_success': update_success,
            'underwriting_decision': underwriting_decision,
            'processing_metadata': {
                'function_name': 'enhanced-loan-underwriting-5cs',
                'timestamp': datetime.utcnow().isoformat(),
                'manual_processing_enabled': manual_processing_enabled,
                'analysis_version': '3.0-5Cs',
                'framework': '5 Cs of Credit'
            },
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }
        
    except Exception as e:
        logger.error(f"Enhanced loan underwriting analysis error: {str(e)}")
        
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('processingContext', {}).get('customer_id', 'UNKNOWN'),
            'application_id': event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        }
