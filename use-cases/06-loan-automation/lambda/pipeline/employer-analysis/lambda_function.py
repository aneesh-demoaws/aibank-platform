import json
import boto3
import logging
from datetime import datetime
from tavily import TavilyClient
from pydantic import BaseModel

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
dynamodb = boto3.client('dynamodb')

# Tavily API configuration
TAVILY_API_KEY = "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"

# Structured output class for employer financial analysis
class EmployerFinancialInfo(BaseModel):
    company_name: str
    financial_status: str
    recent_revenue: str
    profit_loss: str
    balance_sheet_summary: str
    debt_levels: str
    credit_rating: str
    recent_earnings: str
    market_performance: str
    employee_count: str
    industry_sector: str
    financial_stability_score: str
    potential_risks: str
    complete_financial_profile: str

def get_loan_application_data(customer_id: str, application_id: str) -> dict:
    """Retrieve loan application data from DynamoDB table."""
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
            return {
                'employer_name': item.get('employer_name', {}).get('S', ''),
                'basic_salary': item.get('basic_salary', {}).get('N', ''),
                'status': item.get('status', {}).get('S', ''),
                'amount': item.get('amount', {}).get('N', '')
            }
        else:
            logger.warning(f"No loan application found for customer_id: {customer_id}, application_id: {application_id}")
            return {}
            
    except Exception as e:
        logger.error(f"Error retrieving loan application data for {customer_id}/{application_id}: {str(e)}")
        return {}

def EmployerAnalysis(company_name: str, location: str = "") -> EmployerFinancialInfo:
    """Search for employer financial information including balance sheet and earnings."""
    
    client = TavilyClient(api_key=TAVILY_API_KEY)
    
    # Construct financial search queries
    queries = [
        f'"{company_name}" financial results earnings revenue 2024 2023',
        f'"{company_name}" balance sheet annual report financial statement',
        f'"{company_name}" debt credit rating financial stability',
        f'"{company_name}" quarterly earnings profit loss income'
    ]
    
    all_financial_content = ""
    
    # Perform multiple searches for comprehensive financial data
    for query in queries:
        try:
            results = client.search(
                query=query,
                max_results=3,
                search_depth="advanced"
            )
            
            for result in results.get('results', []):
                content = result.get('content', '')
                title = result.get('title', '')
                
                # Filter for financial and corporate content
                if _is_financial_content(title, content, company_name):
                    all_financial_content += f"{title} {content} "
                    
        except Exception as e:
            logger.error(f"Search error for query '{query}': {str(e)}")
            continue
    
    # Extract structured financial data
    if not all_financial_content.strip():
        extracted_info = {
            "company_name": company_name,
            "financial_status": "No financial data found",
            "recent_revenue": "Not available",
            "profit_loss": "Not available", 
            "balance_sheet_summary": "Not available",
            "debt_levels": "Not available",
            "credit_rating": "Not available",
            "recent_earnings": "Not available",
            "market_performance": "Not available",
            "employee_count": "Not available",
            "industry_sector": "Not available",
            "financial_stability_score": "Unable to assess",
            "potential_risks": "Insufficient data for analysis",
            "complete_financial_profile": "No financial information found"
        }
    else:
        extracted_info = {
            "company_name": company_name,
            "financial_status": _extract_financial_field(all_financial_content, ["financial status", "financially stable", "financial health"]),
            "recent_revenue": _extract_financial_field(all_financial_content, ["revenue", "sales", "income", "turnover"]),
            "profit_loss": _extract_financial_field(all_financial_content, ["profit", "loss", "net income", "earnings"]),
            "balance_sheet_summary": _extract_financial_field(all_financial_content, ["balance sheet", "assets", "liabilities", "equity"]),
            "debt_levels": _extract_financial_field(all_financial_content, ["debt", "borrowing", "leverage", "liabilities"]),
            "credit_rating": _extract_financial_field(all_financial_content, ["credit rating", "rating", "grade", "score"]),
            "recent_earnings": _extract_financial_field(all_financial_content, ["quarterly earnings", "annual earnings", "Q1", "Q2", "Q3", "Q4"]),
            "market_performance": _extract_financial_field(all_financial_content, ["stock price", "market cap", "share price", "valuation"]),
            "employee_count": _extract_financial_field(all_financial_content, ["employees", "workforce", "staff", "headcount"]),
            "industry_sector": _extract_financial_field(all_financial_content, ["industry", "sector", "business", "operates in"]),
            "financial_stability_score": _assess_financial_stability(all_financial_content),
            "potential_risks": _identify_financial_risks(all_financial_content),
            "complete_financial_profile": all_financial_content.strip()
        }
    
    return EmployerFinancialInfo(**extracted_info)

def _is_financial_content(title: str, content: str, company_name: str) -> bool:
    """Check if content is relevant financial information for the company."""
    title_lower = title.lower()
    content_lower = content.lower()
    company_lower = company_name.lower()
    
    # Must mention the company
    if company_lower not in title_lower and company_lower not in content_lower:
        return False
    
    # Must contain financial keywords
    financial_keywords = [
        'financial', 'earnings', 'revenue', 'profit', 'loss', 'balance sheet',
        'annual report', 'quarterly', 'debt', 'assets', 'liabilities', 'income',
        'cash flow', 'credit rating', 'financial results'
    ]
    
    return any(keyword in content_lower for keyword in financial_keywords)

def _extract_financial_field(content: str, keywords: list) -> str:
    """Extract financial information based on keywords."""
    if not content:
        return "Not found"
    
    content_lower = content.lower()
    
    for keyword in keywords:
        if keyword.lower() in content_lower:
            start = content_lower.find(keyword.lower())
            if start != -1:
                # Extract financial data snippet
                snippet = content[start:start+400]
                lines = snippet.split('.')
                for line in lines:
                    if keyword.lower() in line.lower() and len(line.strip()) > len(keyword):
                        # Look for numbers and financial indicators
                        if any(char.isdigit() for char in line) or any(term in line.lower() for term in ['million', 'billion', 'percent', '%', '$']):
                            return line.strip()
                return lines[0].strip() if lines else "Not found"
    
    return "Not found"

def _assess_financial_stability(content: str) -> str:
    """Assess financial stability based on content analysis."""
    if not content:
        return "Unable to assess"
    
    content_lower = content.lower()
    
    # Positive indicators
    positive_indicators = [
        'profitable', 'growth', 'strong revenue', 'increased earnings',
        'positive cash flow', 'stable', 'healthy', 'improved'
    ]
    
    # Negative indicators  
    negative_indicators = [
        'loss', 'decline', 'decreased', 'debt', 'bankruptcy', 'restructuring',
        'layoffs', 'downsizing', 'financial difficulties', 'struggling'
    ]
    
    positive_count = sum(1 for indicator in positive_indicators if indicator in content_lower)
    negative_count = sum(1 for indicator in negative_indicators if indicator in content_lower)
    
    if positive_count > negative_count * 2:
        return "Strong - Multiple positive financial indicators"
    elif positive_count > negative_count:
        return "Stable - Generally positive financial outlook"
    elif negative_count > positive_count:
        return "Concerning - Multiple negative financial indicators"
    else:
        return "Mixed - Balanced positive and negative indicators"

def _identify_financial_risks(content: str) -> str:
    """Identify potential financial risks from content."""
    if not content:
        return "Unable to assess risks"
    
    content_lower = content.lower()
    
    risk_indicators = {
        'High debt levels': ['high debt', 'debt burden', 'leverage', 'borrowing'],
        'Declining revenue': ['revenue decline', 'sales drop', 'decreased income'],
        'Market volatility': ['volatile', 'uncertainty', 'market pressure'],
        'Regulatory issues': ['regulatory', 'compliance', 'investigation', 'fine'],
        'Operational challenges': ['restructuring', 'layoffs', 'cost cutting'],
        'Credit concerns': ['credit downgrade', 'rating cut', 'default risk']
    }
    
    identified_risks = []
    
    for risk_type, keywords in risk_indicators.items():
        if any(keyword in content_lower for keyword in keywords):
            identified_risks.append(risk_type)
    
    return "; ".join(identified_risks) if identified_risks else "No significant risks identified"

def update_loan_application_with_employer_analysis(customer_id: str, application_id: str, employer_analysis: dict) -> bool:
    """Update loan application with employer financial analysis data."""
    try:
        # Prepare the employer financial analysis data
        employer_financial_analysis = {
            "M": {
                "company_name": {"S": employer_analysis.get("company_name", "")},
                "financial_status": {"S": employer_analysis.get("financial_status", "")},
                "recent_revenue": {"S": employer_analysis.get("recent_revenue", "")},
                "profit_loss": {"S": employer_analysis.get("profit_loss", "")},
                "balance_sheet_summary": {"S": employer_analysis.get("balance_sheet_summary", "")},
                "debt_levels": {"S": employer_analysis.get("debt_levels", "")},
                "credit_rating": {"S": employer_analysis.get("credit_rating", "")},
                "recent_earnings": {"S": employer_analysis.get("recent_earnings", "")},
                "market_performance": {"S": employer_analysis.get("market_performance", "")},
                "employee_count": {"S": employer_analysis.get("employee_count", "")},
                "industry_sector": {"S": employer_analysis.get("industry_sector", "")},
                "financial_stability_score": {"S": employer_analysis.get("financial_stability_score", "")},
                "potential_risks": {"S": employer_analysis.get("potential_risks", "")},
                "analysis_timestamp": {"S": datetime.utcnow().isoformat()}
            }
        }
        
        # Update the loan application record
        response = dynamodb.update_item(
            TableName='aibank-personal-loan',
            Key={
                'customer_id': {'S': customer_id},
                'application_id': {'S': application_id}
            },
            UpdateExpression='SET employer_financial_analysis = :employer_analysis',
            ExpressionAttributeValues={
                ':employer_analysis': employer_financial_analysis
            },
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Successfully updated loan application with employer analysis for {customer_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating loan application with employer analysis: {str(e)}")
        return False

def lambda_handler(event, context):
    """Employer Analysis Lambda Function with Financial Data Search"""
    
    try:
        # Extract basic data from event
        customer_id = event.get('processingContext', {}).get('customer_id', 'UNKNOWN')
        application_id = event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        processing_chain = event.get('processing_chain', {})
        
        logger.info(f"Processing Employer Analysis for customer_id: {customer_id}")
        
        if customer_id == 'UNKNOWN' or application_id == 'UNKNOWN':
            raise ValueError("Missing customer_id or application_id in processing context")
        
        # Get loan application data to extract employer name
        loan_data = get_loan_application_data(customer_id, application_id)
        
        if not loan_data:
            raise ValueError(f"No loan application found for customer_id: {customer_id}, application_id: {application_id}")
        
        employer_name = loan_data.get('employer_name', '')
        
        if not employer_name:
            raise ValueError(f"No employer name found in loan application for {customer_id}")
        
        logger.info(f"Analyzing employer: {employer_name}")
        
        # Perform employer financial analysis
        employer_analysis = None
        analysis_error = None
        
        try:
            employer_analysis = EmployerAnalysis(employer_name)
            logger.info(f"Employer analysis completed successfully for {employer_name}")
            
            # Update loan application with employer analysis
            if employer_analysis:
                update_success = update_loan_application_with_employer_analysis(
                    customer_id, 
                    application_id, 
                    employer_analysis.model_dump()
                )
                if update_success:
                    logger.info(f"Employer analysis saved to loan application for {customer_id}")
                else:
                    logger.warning(f"Failed to save employer analysis to loan application for {customer_id}")
                    
        except Exception as analysis_ex:
            analysis_error = str(analysis_ex)
            logger.error(f"Employer analysis failed: {analysis_error}")
        
        # Create stage results
        stage_results = {
            'stage': 'employer_analysis',
            'timestamp': datetime.utcnow().isoformat(),
            'analysis_status': 'COMPLETED' if employer_analysis else 'PARTIAL_SUCCESS',
            'customer_id': customer_id,
            'application_id': application_id,
            'employer_name': employer_name,
            'employer_assessment': {
                'financial_analysis_performed': employer_analysis is not None,
                'financial_stability': employer_analysis.financial_stability_score if employer_analysis else "Unable to assess",
                'potential_risks': employer_analysis.potential_risks if employer_analysis else "Unable to assess",
                'analysis_method': 'TAVILY_SEARCH',
                'confidence': 'HIGH' if employer_analysis else 'LOW',
                'error': analysis_error
            },
            'employer_financial_data': employer_analysis.model_dump() if employer_analysis else None
        }
        
        # Add to processing chain
        updated_chain = processing_chain.copy()
        stage_key = f"employer_analysis_{len(updated_chain) + 1:03d}"
        updated_chain[stage_key] = stage_results
        
        logger.info(f"Employer Analysis completed for {customer_id}")
        
        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'processing_chain': updated_chain,
            'stage_results': stage_results,
            'employer_analysis_results': employer_analysis.model_dump() if employer_analysis else None,
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }
        
    except Exception as e:
        logger.error(f"Employer Analysis error: {str(e)}")
        
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('processingContext', {}).get('customer_id', 'UNKNOWN'),
            'application_id': event.get('processingContext', {}).get('application_id', 'UNKNOWN'),
            'processing_chain': event.get('processing_chain', {}),
            'stage_results': {
                'stage': 'employer_analysis',
                'timestamp': datetime.utcnow().isoformat(),
                'status': 'ERROR',
                'error': str(e)
            },
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }
