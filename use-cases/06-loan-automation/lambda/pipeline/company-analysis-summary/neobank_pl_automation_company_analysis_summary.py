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

def create_company_analysis_agent():
    """Create and configure the company analysis agent."""
    return Agent(
        system_prompt="""You are a comprehensive company analysis specialist for loan underwriting. Analyze provided company data to assess employment stability and financial risk.

<input>
You will receive company information and news data. Analyze this data to provide employment stability assessment.
</input>

<output_format>
1. Company Overview:
   - Company Name and Industry
   - Business Description
   - Employee Count and Market Position

2. Financial Stability:
   - Market Capitalization
   - Financial Health Assessment
   - Business Model Sustainability

3. Employment Risk Assessment:
   - Job Security Indicators
   - Recent News Impact on Employment
   - Industry Stability
   - Layoff/Hiring Trends

4. Loan Underwriting Assessment:
   - Employment Stability Score (1-10)
   - Risk Level (Low/Medium/High)
   - Key Risk Factors
   - Recommendations for Loan Decision
</output_format>""",
        model=BedrockModel(model_id="eu.amazon.nova-lite-v1:0", region="eu-west-1"),
        tools=[think],
    )

def get_employer_analysis_data(customer_id: str, application_id: str) -> dict:
    """Retrieve employer analysis data from DynamoDB."""
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

            # Top-level employer_financial_analysis (written by employer-analysis Lambda)
            efa_raw = item.get('employer_financial_analysis', {})
            employer_financial_analysis = {}
            if efa_raw.get('M'):
                # It's a DynamoDB map — flatten to plain dict
                employer_financial_analysis = {k: list(v.values())[0] for k, v in efa_raw['M'].items()}
            elif efa_raw.get('S'):
                try: employer_financial_analysis = json.loads(efa_raw['S'])
                except: pass

            # Legacy: customer_profile nested structure
            customer_profile = item.get('customer_profile', {}).get('M', {})
            employer_analysis = customer_profile.get('employer_analysis', {}).get('M', {})
            company_data = json.loads(employer_analysis.get('company_data', {}).get('S', '{}'))
            company_stock_news_data = json.loads(employer_analysis.get('company_stock_news_data', {}).get('S', '{}'))

            if employer_financial_analysis or employer_analysis:
                return {
                    'company_data': company_data,
                    'company_stock_news_data': company_stock_news_data,
                    'employer_financial_analysis': employer_financial_analysis,
                    'found': True
                }
        
        return {'found': False}
        
    except Exception as e:
        logger.error(f"Error retrieving employer analysis data: {str(e)}")
        return {'found': False, 'error': str(e)}

def update_loan_application_with_summary(customer_id: str, application_id: str, analysis_result: str) -> bool:
    """Update loan application with AI summary in employer_analysis attribute."""
    try:
        response = dynamodb.update_item(
            TableName='aibank-personal-loan',
            Key={
                'customer_id': {'S': customer_id},
                'application_id': {'S': application_id}
            },
            UpdateExpression='SET employer_analysis = :summary',
            ExpressionAttributeValues={
                ':summary': {'S': analysis_result}
            },
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Successfully updated employer_analysis for {customer_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating employer_analysis: {str(e)}")
        return False

def lambda_handler(event, context):
    """Company Analysis Summary Lambda Function using Strands AI Agent"""
    
    try:
        customer_id = event.get('processingContext', {}).get('customer_id', 'UNKNOWN')
        application_id = event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        
        logger.info(f"Summarizing company analysis for customer_id: {customer_id}")
        
        if customer_id == 'UNKNOWN' or application_id == 'UNKNOWN':
            raise ValueError("Missing customer_id or application_id in processing context")
        
        # Retrieve employer analysis data from DynamoDB
        analysis_data = get_employer_analysis_data(customer_id, application_id)
        
        if not analysis_data.get('found'):
            raise ValueError(f"No employer analysis data found for {customer_id}")
        
        # Create and initialize the agent
        company_analysis_agent = create_company_analysis_agent()
        
        # Prepare analysis query with the gathered data
        query = f"""Please provide a comprehensive employment stability analysis based on the following company data:

Company Information: {json.dumps(analysis_data.get('company_data', {}), indent=2)}

Stock News: {json.dumps(analysis_data.get('company_stock_news_data', {}), indent=2)}

Employer Financial Analysis: {json.dumps(analysis_data.get('employer_financial_analysis', {}), indent=2)}

Focus on loan underwriting risk assessment and employment stability."""
        
        # Get AI analysis
        analysis_result = company_analysis_agent(query)
        
        # Convert AgentResult to string
        analysis_text = str(analysis_result)
        
        logger.info(f"Company analysis completed successfully")
        
        # Update loan application with summary
        update_success = update_loan_application_with_summary(
            customer_id, 
            application_id, 
            analysis_text
        )
        
        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'analysis_result': analysis_text,
            'update_success': update_success,
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }
        
    except Exception as e:
        logger.error(f"Company analysis summary error: {str(e)}")
        
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('processingContext', {}).get('customer_id', 'UNKNOWN'),
            'application_id': event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        }
