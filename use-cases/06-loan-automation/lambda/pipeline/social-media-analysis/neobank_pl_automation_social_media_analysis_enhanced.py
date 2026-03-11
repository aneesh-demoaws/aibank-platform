import json
import os
import boto3
import logging
from datetime import datetime
from tavily import TavilyClient
from strands import Agent
from strands.models import BedrockModel
from strands_tools import think

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
dynamodb = boto3.client('dynamodb')
rds_data = boto3.client('rds-data', region_name='me-south-1')

AURORA_CLUSTER_ARN = os.environ.get('AURORA_CLUSTER_ARN', 'arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking')
AURORA_SECRET_ARN  = os.environ.get('AURORA_SECRET_ARN',  'arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ')
AURORA_DB_NAME     = os.environ.get('AURORA_DB_NAME', 'corebanking')

# Tavily API configuration
TAVILY_API_KEY = "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"

def create_social_media_agent():
    """Create and configure the social media analysis agent."""
    return Agent(
        system_prompt="""You are an expert Social Media Analysis Agent for a NeoBank. Your role is to analyze LinkedIn profiles and social media data to provide comprehensive insights for loan underwriting decisions.

## TASK
Analyze the provided LinkedIn search results and customer information to deliver a structured social media assessment with clear insights for loan underwriting.

## EVALUATION CRITERIA
1. **Professional Verification**: Verify employment details and professional consistency. Give more preference to the first name, job description, employer and current residing country while match matching.
2. **Career Stability**: Assess job tenure, career progression, and employment gaps
3. **Professional Network**: Evaluate connections, endorsements, and industry presence
4. **Digital Reputation**: Review public posts, activities, and professional image
5. **Risk Indicators**: Identify potential red flags or concerning patterns
6. **Employment Validation**: Cross-reference claimed employment with LinkedIn data

## REQUIRED OUTPUT FORMAT
### 1. PROFILE VERIFICATION
**Match Confidence**: [High/Medium/Low]
    - Give more preference to the first name, job description, employer and current residing country while match matching.
**Employment Verification**: [Verified/Partial/Unverified]
**Profile Completeness**: [Complete/Partial/Limited]
**LinkedIn Profile SUMMARY**: Provide the customer matched LinkedIn profile short summary within 100 words.

### 2. PROFESSIONAL ASSESSMENT
**Current Role Verification**: [Confirmed/Inconsistent/Not Found]
**Career Stability Score**: [X/10]
**Professional Network Quality**: [Strong/Moderate/Weak]

### 3. RISK ANALYSIS
**Digital Reputation**: [Positive/Neutral/Concerning]
**Consistency Check**: [Consistent/Minor Discrepancies/Major Discrepancies]
**Red Flags**: [List any concerns or "None detected"]

### 4. LOAN UNDERWRITING INSIGHTS
**Social Media Risk Score**: [X/10] (1=High Risk, 10=Low Risk)
**Employment Confidence**: [High/Medium/Low]
**Recommended Action**: [APPROVE/REVIEW/DECLINE]
**Key Findings**: [Summary of main insights]

Provide your complete social media analysis following the above structure.""",
        model=BedrockModel(model_id="eu.anthropic.claude-3-haiku-20240307-v1:0", region="eu-west-1"),
        tools=[think],
    )

def get_customer_kyc_data(customer_id: str) -> dict:
    """Retrieve customer KYC data from aibank-customer-kyc (me-south-1),
    with Aurora social_name override for LinkedIn search."""
    result = {}

    # 1. Primary: aibank-customer-kyc table in me-south-1
    try:
        ddb_me = boto3.resource('dynamodb', region_name='me-south-1')
        kyc_item = ddb_me.Table('aibank-customer-kyc').get_item(Key={'customer_id': customer_id}).get('Item')
        if kyc_item:
            result = {
                'full_name':         kyc_item.get('full_name', ''),
                'nationality':       kyc_item.get('nationality', ''),
                'current_residency': kyc_item.get('current_residency', kyc_item.get('address', '')),
                'kyc_status':        kyc_item.get('kyc_status', ''),
                'job_description':   kyc_item.get('job_description', ''),
                'date_of_birth':     kyc_item.get('date_of_birth', ''),
            }
            logger.info(f"KYC table data for {customer_id}: name='{result['full_name']}', nat='{result['nationality']}'")
    except Exception as e:
        logger.warning(f"KYC table lookup failed for {customer_id}: {e}")

    # 2. Override name with Aurora social_name if set (better for LinkedIn search)
    try:
        resp = rds_data.execute_statement(
            resourceArn=AURORA_CLUSTER_ARN, secretArn=AURORA_SECRET_ARN, database=AURORA_DB_NAME,
            sql="SELECT social_name FROM customers WHERE customer_id = :cid",
            parameters=[{'name': 'cid', 'value': {'stringValue': customer_id}}]
        )
        rows = resp.get('records', [])
        if rows:
            social_name = rows[0][0].get('stringValue')
            if social_name:
                result['full_name'] = social_name
                logger.info(f"Using social_name '{social_name}' for {customer_id}")
    except Exception as e:
        logger.warning(f"Aurora social_name lookup failed: {e}")

    if not result:
        logger.warning(f"No KYC data found for customer_id: {customer_id}")
    return result

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

def enhanced_linkedin_search(person_name: str, company: str = "", location: str = "", job_description: str = "", current_residency: str = "") -> dict:
    """Enhanced LinkedIn search with better matching logic including job description and current residency."""
    client = TavilyClient(api_key=TAVILY_API_KEY)
    
    # Use current_residency as primary location if available, fallback to default location
    search_location = current_residency if current_residency.strip() else location
    
    # Multiple search strategies with job description and location
    search_queries = [
        f'site:linkedin.com/in "{person_name}" "{company}" "{job_description}" "{search_location}"',
        f'site:linkedin.com/in "{person_name}" "{company}" "{search_location}"',
        f'site:linkedin.com/in "{person_name}" "{job_description}" "{search_location}"',
        f'site:linkedin.com/in "{person_name}" "{company}" "{job_description}"',
        f'site:linkedin.com/in "{person_name}" "{company}"',
        f'site:linkedin.com/in "{person_name}" {search_location}',
        f'site:linkedin.com/in "{person_name}"'
    ]
    
    # Filter out empty queries
    search_queries = [q for q in search_queries if '""' not in q]
    
    all_results = []
    for query in search_queries:
        try:
            results = client.search(
                query=query,
                max_results=3,
                include_domains=["linkedin.com"],
                search_depth="advanced"
            )
            all_results.extend(results.get('results', []))
        except Exception as e:
            logger.warning(f"Search query failed: {query}, error: {str(e)}")
    
    # Enhanced matching logic with job description and location
    name_parts = person_name.lower().split()
    company_keywords = company.lower().split() if company else []
    job_keywords = job_description.lower().split() if job_description else []
    location_keywords = search_location.lower().split() if search_location else []
    
    scored_results = []
    for result in all_results:
        title = result.get('title', '').lower()
        content = result.get('content', '').lower()
        url = result.get('url', '')
        
        # Scoring system
        score = 0
        
        # Name matching (most important)
        name_matches = sum(1 for part in name_parts if part in title)
        score += (name_matches / len(name_parts)) * 35
        
        # Company matching
        if company_keywords:
            company_matches = sum(1 for keyword in company_keywords if keyword in content)
            score += (company_matches / len(company_keywords)) * 20
        
        # Job description matching
        if job_keywords:
            job_matches = sum(1 for keyword in job_keywords if keyword in content)
            score += (job_matches / len(job_keywords)) * 15
        
        # Location matching (current residency)
        if location_keywords:
            location_matches = sum(1 for keyword in location_keywords if keyword in content)
            score += (location_matches / len(location_keywords)) * 15
        
        # URL structure (linkedin.com/in/ is better than company pages)
        if "/in/" in url:
            score += 10
        
        # Content quality
        if len(content) > 100:
            score += 5
        
        scored_results.append({
            'result': result,
            'score': score,
            'name_match_ratio': name_matches / len(name_parts),
            'company_match_count': sum(1 for keyword in company_keywords if keyword in content) if company_keywords else 0,
            'job_match_count': sum(1 for keyword in job_keywords if keyword in content) if job_keywords else 0,
            'location_match_count': sum(1 for keyword in location_keywords if keyword in content) if location_keywords else 0
        })
    
    # Sort by score and return best match
    scored_results.sort(key=lambda x: x['score'], reverse=True)
    
    if scored_results and scored_results[0]['score'] > 25:  # Minimum threshold
        best_match = scored_results[0]
        return {
            'found': True,
            'confidence': 'High' if best_match['score'] > 60 else 'Medium' if best_match['score'] > 40 else 'Low',
            'score': best_match['score'],
            'profile_data': best_match['result'],
            'name_match_ratio': best_match['name_match_ratio'],
            'company_matches': best_match['company_match_count'],
            'job_matches': best_match['job_match_count'],
            'location_matches': best_match['location_match_count'],
            'search_location_used': search_location
        }
    else:
        return {
            'found': False,
            'confidence': 'None',
            'score': 0,
            'profile_data': None,
            'reason': 'No suitable LinkedIn profile matches found',
            'job_matches': 0,
            'location_matches': 0,
            'search_location_used': search_location
        }

def update_social_analysis(customer_id: str, application_id: str, analysis: str) -> bool:
    """Update loan application with social media analysis."""
    try:
        response = dynamodb.update_item(
            TableName='aibank-personal-loan',
            Key={
                'customer_id': {'S': customer_id},
                'application_id': {'S': application_id}
            },
            UpdateExpression='SET social_analysis = :analysis',
            ExpressionAttributeValues={
                ':analysis': {'S': analysis}
            },
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Successfully updated social_analysis for {customer_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating social analysis: {str(e)}")
        return False

def lambda_handler(event, context):
    """Enhanced Social Media Analysis Lambda Function using Strands AI Agent"""
    
    try:
        # Handle Lambda invoke response structure
        if 'Payload' in event:
            event = event['Payload']
        
        # Extract customer_id and application_id - try direct keys first
        customer_id = (
            event.get('customer_id') or
            event.get('processingContext', {}).get('customer_id') or
            event.get('customer_data', {}).get('customer_id') or
            event.get('originalApplicationData', {}).get('customer_id')
        )
        
        application_id = (
            event.get('application_id') or
            event.get('processingContext', {}).get('application_id') or
            event.get('loan_data', {}).get('application_id') or
            event.get('originalApplicationData', {}).get('application_id')
        )
        
        if not customer_id or not application_id:
            logger.error(f"Missing IDs - customer_id: {customer_id}, application_id: {application_id}")
            logger.error(f"Event keys: {list(event.keys())}")
            raise ValueError("Missing customer_id or application_id in processing context")
        
        # Create processing context if not available
        processing_context = event.get('processingContext', {
            'customer_id': customer_id,
            'application_id': application_id,
            'stage': 'social_media_analysis'
        })
        
        logger.info(f"Processing enhanced social media analysis for customer_id: {customer_id}")
        
        # Get customer KYC data — graceful, never hard-fail
        kyc_data = get_customer_kyc_data(customer_id)

        # Get loan application data
        loan_data = get_loan_application_data(customer_id, application_id)
        if not loan_data:
            raise ValueError(f"No loan application found for customer_id: {customer_id}")
        
        # Extract customer information
        customer_name = kyc_data.get('full_name', 'Unknown Customer')
        job_description = kyc_data.get('job_description', '')
        current_residency = kyc_data.get('current_residency', '')
        employer_name = loan_data.get('employer_name', '')
        location = "Bahrain"  # Default fallback location
        
        logger.info(f"Searching LinkedIn for: {customer_name} at {employer_name}")
        if job_description:
            logger.info(f"Job description available: {job_description[:50]}...")
        if current_residency:
            logger.info(f"Current residency: {current_residency}")
        
        # Enhanced LinkedIn search with job description and current residency
        linkedin_search_result = enhanced_linkedin_search(customer_name, employer_name, location, job_description, current_residency)
        
        # Create and initialize the social media agent
        social_agent = create_social_media_agent()
        
        # Prepare analysis query
        if linkedin_search_result['found']:
            profile_data = linkedin_search_result['profile_data']
            query = f"""Please analyze this LinkedIn profile for loan underwriting:

<customer_information>
Name: {customer_name}
Claimed Employer: {employer_name}
Job Description: {job_description if job_description else 'Not provided'}
Current Residency: {current_residency if current_residency else 'Not provided'}
Location: {location}
KYC Status: {kyc_data.get('kyc_status', 'Unknown')}
Loan Amount: BHD {loan_data.get('amount', 'Unknown')}
</customer_information>

<linkedin_search_results>
Search Confidence: {linkedin_search_result['confidence']}
Match Score: {linkedin_search_result['score']}/100
Profile URL: {profile_data.get('url', 'N/A')}
Profile Title: {profile_data.get('title', 'N/A')}
Profile Content: {profile_data.get('content', 'N/A')}
Name Match Ratio: {linkedin_search_result['name_match_ratio']:.2f}
Company Matches: {linkedin_search_result['company_matches']}
Job Description Matches: {linkedin_search_result.get('job_matches', 0)}
Location Matches: {linkedin_search_result.get('location_matches', 0)}
Search Location Used: {linkedin_search_result.get('search_location_used', 'N/A')}
</linkedin_search_results>

<analysis_requirements>
1. Verify if this LinkedIn profile matches the customer. Give more preference to the first name, company, job decsription and presently living country. People may have slight variance in second name due to official name and preffered name. Example, Aneesh Mohandas and Aneesh Mohan may be the same person, as long the other search attributes are matching.
2. Cross-reference employment details with loan application
3. Validate job description consistency between KYC and LinkedIn
4. Verify location consistency between current residency and LinkedIn profile
5. Assess professional credibility and career stability
6. Identify any red flags or inconsistencies
7. Provide loan underwriting recommendation
</analysis_requirements>

Provide your complete social media analysis following the required output format."""
        else:
            query = f"""Please analyze the absence of LinkedIn profile for loan underwriting:

<customer_information>
Name: {customer_name}
Claimed Employer: {employer_name}
Job Description: {job_description if job_description else 'Not provided'}
Current Residency: {current_residency if current_residency else 'Not provided'}
Location: {location}
KYC Status: {kyc_data.get('kyc_status', 'Unknown')}
Loan Amount: BHD {loan_data.get('amount', 'Unknown')}
</customer_information>

<linkedin_search_results>
Search Result: No suitable LinkedIn profile found
Search Confidence: None
Reason: {linkedin_search_result.get('reason', 'Profile not found')}
Job Description Available: {'Yes' if job_description else 'No'}
Current Residency Available: {'Yes' if current_residency else 'No'}
Search Location Used: {linkedin_search_result.get('search_location_used', 'N/A')}
</linkedin_search_results>

<analysis_requirements>
1. Assess the risk of no LinkedIn presence
2. Consider implications for employment verification
3. Evaluate impact on loan underwriting decision
4. Provide alternative verification recommendations
</analysis_requirements>

Provide your complete social media analysis following the required output format."""
        
        # Get AI social media analysis
        social_result = social_agent(query)
        social_analysis_text = str(social_result)
        
        logger.info(f"Social media analysis completed successfully")
        
        # Update loan application with social analysis
        update_success = update_social_analysis(customer_id, application_id, social_analysis_text)
        
        # Prepare response
        timestamp = datetime.utcnow().isoformat()
        
        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'social_analysis': social_analysis_text,
            'linkedin_search_summary': {
                'profile_found': linkedin_search_result['found'],
                'search_confidence': linkedin_search_result['confidence'],
                'match_score': linkedin_search_result['score'],
                'name_match_ratio': linkedin_search_result.get('name_match_ratio', 0),
                'company_matches': linkedin_search_result.get('company_matches', 0),
                'job_matches': linkedin_search_result.get('job_matches', 0),
                'location_matches': linkedin_search_result.get('location_matches', 0),
                'job_description_used': bool(job_description),
                'current_residency_used': bool(current_residency),
                'search_location_used': linkedin_search_result.get('search_location_used', 'N/A')
            },
            'update_success': update_success,
            'processing_metadata': {
                'function_name': 'enhanced-social-media-analysis',
                'timestamp': timestamp,
                'analysis_version': '2.0'
            },
            'executionContext': event.get('executionContext', {}),
            'processingContext': processing_context
        }
        
    except Exception as e:
        logger.error(f"Enhanced social media analysis error: {str(e)}")
        
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('processingContext', {}).get('customer_id', 'UNKNOWN'),
            'application_id': event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        }
