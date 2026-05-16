import json
import boto3
import logging
from datetime import datetime
from decimal import Decimal
from strands import Agent
from strands.models import BedrockModel
from strands_tools import think

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
dynamodb = boto3.client('dynamodb')

def create_dti_analysis_agent():
    """Create and configure the DTI analysis agent."""
    return Agent(
        system_prompt="""<instruction>
I'll analyze the following extracted context carefully. This context contains important information that I should use to formulate my response.
</instruction>

<context>
"# NeoBank Debt-to-Income Analysis Agent

## ROLE
You are an expert Debt-to-Income Analysis Agent for a NeoBank. Your responsibility is to analyze customer debt-to-income ratios and provide comprehensive capacity assessments for loan underwriting decisions.

## TASK
Analyze the provided customer financial data and deliver a structured DTI assessment with clear capacity evaluation and risk analysis.

## EVALUATION CRITERIA
1. **DTI Ratio Calculation**: Analyze total monthly debt payments vs gross monthly income
2. **Capacity Assessment**: Evaluate ability to handle new loan payment
3. **Existing Obligations**: Review current loan commitments and payment history
4. **Income Stability**: Assess salary consistency and employment security
5. **Disposable Income**: Calculate remaining income after all debt obligations
6. **Threshold Compliance**: Compare against segment-specific DTI limits
7. **Risk Factors**: Identify potential capacity constraints

## DECISION GUIDELINES
- DTI ratios below threshold indicate good capacity
- Consider income stability and employment type
- Factor in existing loan performance
- Assess impact of new loan on overall financial health
- Provide actionable recommendations for improvement
- Flag any critical capacity concerns
- Consider seasonal income variations if applicable
- The provided personal loan interest rates are fixed for the customer segment, and cannot to reduced to mitigate the risk.

## REQUIRED OUTPUT FORMAT
Please provide your assessment in the following structured format:

### 1. DTI CAPACITY ASSESSMENT
**Capacity Decision**: [APPROVED/REVIEW_REQUIRED/DECLINED]

**DTI Ratio**: [X.XX%]
**Threshold**: [X.XX%]
**Threshold Status**: [WITHIN_LIMITS/EXCEEDS_THRESHOLD]

**Primary Assessment**:
[Provide 2-3 sentences explaining the capacity decision]

### 2. FINANCIAL CAPACITY ANALYSIS
**Monthly Gross Income**: $[Amount]
**Total Monthly Debt Payments**: $[Amount]
**New Loan Monthly Payment**: $[Amount]
**Remaining Disposable Income**: $[Amount]

**Income Stability Score**: [X/10]
**Debt Management Score**: [X/10]

### 3. RISK FACTORS & RECOMMENDATIONS
**Risk Factor 1**: [Category]
- Impact: [High/Medium/Low]
- Recommendation: [Specific action]

**Risk Factor 2**: [Category]
- Impact: [High/Medium/Low]
- Recommendation: [Specific action]

**Overall DTI Health Score**: [X/10]

### 4. CAPACITY IMPROVEMENT SUGGESTIONS
[If DTI exceeds threshold, provide specific recommendations]

Provide your complete DTI analysis following the above structure without any preamble or additional explanations."
</context>

<task>
Based on the extracted context above, I'll provide a comprehensive and accurate DTI analysis that directly addresses the financial capacity information presented.
</task>""",
        model=BedrockModel(model_id="eu.amazon.nova-2-lite-v1:0", region="eu-west-1", max_tokens=4096),
        tools=[think],
    )

def get_dti_analysis_data(customer_id: str, application_id: str) -> dict:
    """Retrieve DTI analysis data from DynamoDB."""
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
            
            def get_value(key):
                field = item.get(key, {})
                return field.get('S', '') or field.get('N', '0')
            
            # Extract basic financial data
            basic_data = {
                'basic_salary': get_value('basic_salary'),
                'average_balance': get_value('average_balance'),
                'amount': get_value('amount'),
                'duration': get_value('duration'),
                'monthly_payment': get_value('monthly_payment'),
                'customer_segment': get_value('customer_segment')
            }
            
            # Extract DTI requirements - use direct fields from table
            dti_requirements = {
                'dti_threshold': get_value('dti_threshold') or '40',
                'income_multiplier': get_value('income_multiplier') or '5',
                'minimum_salary': get_value('minimum_salary_required') or '5000'
            }
            
            return {
                'basic_data': basic_data,
                'dti_requirements': dti_requirements,
                'found': True
            }
        
        return {'found': False}
        
    except Exception as e:
        logger.error(f"Error retrieving DTI analysis data: {str(e)}")
        return {'found': False, 'error': str(e)}

def _compute_emi(amount, tenure_months, loan_type):
    """Compute monthly EMI using the product rate matching aibank-loan-config 'product' rows."""
    try:
        P = float(amount)
        n = int(tenure_months)
        if P <= 0 or n <= 0:
            return 0.0
    except Exception:
        return 0.0
    rate_pct = {"instant_money": 7.0, "personal": 5.5}.get((str(loan_type) or "").lower(), 7.0)
    r = rate_pct / 100.0 / 12.0
    if r == 0:
        return P / n
    return P * r * (1 + r) ** n / ((1 + r) ** n - 1)


# Loan statuses that represent an ACTIVE or IN-PIPELINE obligation the customer
# must already be paying (or expected to pay). REJECTED / AUTO_REJECTED loans do
# NOT count; everything else does — including PENDING_REVIEW (officer hasn't
# finalized yet but the debt is effectively committed until rejected).
_ACTIVE_OBLIGATION_STATUSES = {
    "APPROVED", "AUTO_APPROVED", "PENDING_REVIEW",
    "SUBMITTED", "PROCESSING", "APPROVED_AND_NOTIFIED",
}


def get_existing_obligations(customer_id: str, current_application_id: str) -> list:
    """Get existing loan obligations for DTI purposes.

    Counts APPROVED, AUTO_APPROVED, PENDING_REVIEW, SUBMITTED etc. —
    everything except REJECTED / AUTO_REJECTED / canceled.
    If the DDB record doesn't have a stored `monthly_payment`, we compute the
    EMI from (amount, tenure, loan_type) using the product rate.
    """
    try:
        logger.info(f"Querying existing obligations for {customer_id} (excluding {current_application_id})")
        # customer_id is the table's HASH key — no GSI needed.
        response = dynamodb.query(
            TableName='aibank-personal-loan',
            KeyConditionExpression='customer_id = :customer_id',
            ExpressionAttributeValues={':customer_id': {'S': customer_id}}
        )
        logger.info(f"Main table query returned {len(response.get('Items', []))} items for {customer_id}")

        existing_loans = []
        for item in response.get('Items', []):
            def gv(key):
                field = item.get(key, {}) or {}
                return field.get('S') or field.get('N') or ''
            app_id = gv('application_id')
            status = (gv('status') or '').upper()
            amount = gv('amount')
            tenure = gv('tenure_months') or gv('duration')
            loan_type = gv('loan_type')
            mp_stored = gv('monthly_payment')

            if app_id == current_application_id:
                continue
            if status not in _ACTIVE_OBLIGATION_STATUSES:
                logger.info(f"  skip {app_id}: status={status!r} not an active obligation")
                continue

            try:
                mp = float(mp_stored) if mp_stored else _compute_emi(amount, tenure, loan_type)
            except Exception:
                mp = _compute_emi(amount, tenure, loan_type)

            loan_data = {
                'application_id': app_id,
                'amount': amount,
                'tenure_months': tenure,
                'loan_type': loan_type,
                'status': status,
                'monthly_payment': round(mp, 3),
            }
            existing_loans.append(loan_data)
            logger.info(f"  include {app_id} {loan_type} amt={amount} tenure={tenure} EMI={mp:.3f}")

        total = sum(l['monthly_payment'] for l in existing_loans)
        logger.info(f"Found {len(existing_loans)} active obligations, total monthly EMI = {total:.3f}")
        return existing_loans

    except Exception as e:
        logger.error(f"Error getting existing obligations: {str(e)}")
        return []

def update_dti_analysis_summary(customer_id: str, application_id: str, analysis: str) -> bool:
    """Update loan application with DTI analysis summary in both fields."""
    try:
        response = dynamodb.update_item(
            TableName='aibank-personal-loan',
            Key={
                'customer_id': {'S': customer_id},
                'application_id': {'S': application_id}
            },
            UpdateExpression='SET debt_to_income_analysis_summary = :analysis, credit_bureau_analysis = :analysis',
            ExpressionAttributeValues={
                ':analysis': {'S': analysis}
            },
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Successfully updated debt_to_income_analysis_summary and credit_bureau_analysis for {customer_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating DTI analysis summary: {str(e)}")
        return False

def lambda_handler(event, context):
    """DTI Analysis Summary Lambda Function using Strands AI Agent"""
    
    try:
        customer_id = event.get('processingContext', {}).get('customer_id', 'UNKNOWN')
        application_id = event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        
        logger.info(f"Processing DTI analysis for customer_id: {customer_id}")
        
        if customer_id == 'UNKNOWN' or application_id == 'UNKNOWN':
            raise ValueError("Missing customer_id or application_id in processing context")
        
        # Retrieve DTI analysis data from DynamoDB
        dti_data = get_dti_analysis_data(customer_id, application_id)
        
        if not dti_data.get('found'):
            raise ValueError(f"No DTI analysis data found for {customer_id}")
        
        # Get existing obligations
        existing_obligations = get_existing_obligations(customer_id, application_id)
        
        # Create and initialize the agent
        dti_agent = create_dti_analysis_agent()
        
        # Prepare DTI analysis query with structured data
        basic_data = dti_data['basic_data']
        dti_requirements = dti_data['dti_requirements']
        
        # Calculate DTI metrics with safe float conversion
        def safe_float(value, default=0):
            try:
                return float(value) if value and str(value).strip() else default
            except (ValueError, TypeError):
                return default
        
        monthly_income = safe_float(basic_data.get('basic_salary'))
        # Compute CURRENT application's EMI — don't rely on `monthly_payment`
        # field in DDB (only legacy Aurora-backed records have it).
        current_amount   = safe_float(basic_data.get('amount'))
        current_tenure   = safe_float(basic_data.get('tenure_months')) or safe_float(basic_data.get('duration'))
        current_loan_type = basic_data.get('loan_type') or ''
        stored_mp        = safe_float(basic_data.get('monthly_payment'))
        current_monthly_payment = stored_mp if stored_mp > 0 else _compute_emi(current_amount, current_tenure, current_loan_type)

        existing_monthly_payments = sum(safe_float(loan.get('monthly_payment')) for loan in existing_obligations)
        total_monthly_debt = existing_monthly_payments + current_monthly_payment
        dti_ratio = (total_monthly_debt / monthly_income * 100) if monthly_income > 0 else 0
        
        # Format data sections
        financial_section = []
        for key, value in basic_data.items():
            if value and value.strip():
                financial_section.append(f"- {key.replace('_', ' ').title()}: {value}")
        
        requirements_section = []
        for key, value in dti_requirements.items():
            if value and value.strip():
                requirements_section.append(f"- {key.replace('_', ' ').title()}: {value}")
        
        obligations_section = []
        if existing_obligations:
            for i, loan in enumerate(existing_obligations, 1):
                obligations_section.append(
                    f"- Existing Loan {i}: {loan.get('loan_type','?')} "
                    f"BHD {float(loan.get('amount', 0) or 0):,.3f} "
                    f"over {loan.get('tenure_months','?')} months → "
                    f"EMI BHD {float(loan.get('monthly_payment', 0) or 0):,.3f} "
                    f"(status={loan.get('status','?')}, app={loan.get('application_id','?')})"
                )
        else:
            obligations_section.append("- No existing loan obligations")
        
        query = f"""Please analyze this customer's debt-to-income capacity:

<financial_profile>
{chr(10).join(financial_section) if financial_section else "- No financial data available"}
- Current Application: {current_loan_type} BHD {current_amount:,.3f} over {int(current_tenure)} months → EMI BHD {current_monthly_payment:,.3f}\n- Calculated DTI Ratio: {dti_ratio:.2f}%
- Total Monthly Debt Payments: BHD {total_monthly_debt:,.3f}
</financial_profile>

<dti_requirements>
{chr(10).join(requirements_section) if requirements_section else "- No DTI requirements available"}
</dti_requirements>

<existing_obligations>
{chr(10).join(obligations_section)}
- Total Existing Monthly Payments: ${existing_monthly_payments:,.2f}
</existing_obligations>

Provide your complete DTI capacity assessment following the required output format."""
        
        # Log the complete query
        logger.info(f"DTI Analysis Query for {customer_id}:")
        logger.info(query)
        
        # Get AI DTI analysis
        dti_result = dti_agent(query)
        
        # Convert AgentResult to string
        dti_analysis_text = str(dti_result)
        
        logger.info(f"DTI analysis completed successfully")
        
        # Update loan application with DTI analysis
        update_success = update_dti_analysis_summary(
            customer_id, 
            application_id, 
            dti_analysis_text
        )
        
        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'dti_analysis': dti_analysis_text,
            'dti_metrics': {
                'dti_ratio': round(dti_ratio, 2),
                'monthly_income': monthly_income,
                'total_monthly_debt': total_monthly_debt,
                'existing_obligations_count': len(existing_obligations),
                'threshold': safe_float(dti_requirements.get('dti_threshold'), 40)
            },
            'update_success': update_success,
            'processing_metadata': {
                'function_name': 'debt-to-income-analysis',
                'timestamp': datetime.utcnow().isoformat(),
                'analysis_version': '2.0'
            },
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }
        
    except Exception as e:
        logger.error(f"DTI analysis error: {str(e)}")
        
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('processingContext', {}).get('customer_id', 'UNKNOWN'),
            'application_id': event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        }
