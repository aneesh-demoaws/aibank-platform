import json
import os
import boto3
import logging
from datetime import datetime, timedelta
from strands import Agent
from strands.models import BedrockModel
from strands_tools import think

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
dynamodb = boto3.client('dynamodb')
rds_data = boto3.client('rds-data', region_name='eu-west-1')

AURORA_CLUSTER_ARN = os.environ.get('AURORA_CLUSTER_ARN', 'arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr')
AURORA_SECRET_ARN  = os.environ.get('AURORA_SECRET_ARN',  'arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6')
AURORA_DB_NAME     = os.environ.get('AURORA_DB_NAME', 'corebanking')

def create_financial_profile_agent():
    """Create and configure the financial profile analysis agent."""
    return Agent(
        system_prompt="""<instruction>
I'll analyze the following extracted context carefully. This context contains important information that I should use to formulate my response.
</instruction>

<context>
"# NeoBank Financial Profile Analysis Agent

## ROLE
You are an expert Financial Profile Analysis Agent for a NeoBank. Your responsibility is to analyze customer transaction patterns and financial behavior to provide comprehensive insights for loan underwriting decisions.

## TASK
Analyze the provided customer transaction data and deliver a structured financial behavior assessment with clear insights for loan underwriting.

## EVALUATION CRITERIA
1. **Cash Flow Analysis**: Analyze income patterns, regularity, and stability
2. **Spending Behavior**: Evaluate spending categories, patterns, and financial discipline
3. **Account Management**: Assess balance management and overdraft usage
4. **Financial Stability**: Review transaction consistency and financial health indicators
5. **Risk Indicators**: Identify potential red flags or concerning patterns
6. **Savings Behavior**: Analyze saving patterns and financial planning discipline
7. **Payment Patterns**: Review bill payments and financial obligations management

## DECISION GUIDELINES
- Focus on transaction patterns over the last 12 months
- Identify income stability and regularity
- Assess spending discipline and financial management
- Flag any concerning financial behaviors
- Consider seasonal variations in income/spending
- Evaluate overall financial health trajectory

## REQUIRED OUTPUT FORMAT
Please provide your assessment in the following structured format:

### 1. FINANCIAL BEHAVIOR ASSESSMENT
**Overall Financial Health Score**: [X/10]

**Income Analysis**:
- Income Stability: [Stable/Variable/Irregular]
- Average Monthly Income: $[Amount]
- Income Sources: [Description]

**Spending Analysis**:
- Spending Discipline: [Excellent/Good/Fair/Poor]
- Major Spending Categories: [List top categories]
- Average Monthly Expenses: $[Amount]

### 2. CASH FLOW PATTERNS
**Monthly Cash Flow**: [Positive/Negative/Variable]
**Savings Rate**: [X%] of income
**Account Balance Trend**: [Increasing/Stable/Decreasing]

### 3. RISK FACTORS & INSIGHTS
**Risk Factor 1**: [Category]
- Description: [Details]
- Impact: [High/Medium/Low]

**Risk Factor 2**: [Category]
- Description: [Details]
- Impact: [High/Medium/Low]

### 4. LOAN UNDERWRITING INSIGHTS
**Repayment Capacity**: [Strong/Moderate/Weak]
**Financial Stability Score**: [X/10]
**Recommended Loan Decision**: [APPROVE/REVIEW/DECLINE]

Provide your complete financial profile analysis following the above structure without any preamble."
</context>

<task>
Based on the extracted context above, I'll provide a comprehensive and accurate financial behavior analysis that directly addresses the transaction data and financial patterns presented.
</task>""",
        model=BedrockModel(model_id="eu.anthropic.claude-3-haiku-20240307-v1:0", region="eu-west-1"),
        tools=[think],
    )

def get_customer_transactions(customer_id: str):
    """Get customer transactions from core banking database via RDS Data API."""
    try:
        end_date   = datetime.now()
        start_date = end_date - timedelta(days=365)

        acct_resp = rds_data.execute_statement(
            resourceArn=AURORA_CLUSTER_ARN, secretArn=AURORA_SECRET_ARN, database=AURORA_DB_NAME,
            sql="SELECT account_id, account_type, balance, currency FROM accounts WHERE customer_id = :cid AND status = 'ACTIVE'",
            parameters=[{'name': 'cid', 'value': {'stringValue': customer_id}}]
        )
        accounts = []
        account_ids = []
        for row in acct_resp.get('records', []):
            aid = row[0].get('stringValue','')
            account_ids.append(aid)
            accounts.append({'account_id': aid, 'account_type': row[1].get('stringValue',''),
                              'balance': float(row[2].get('stringValue') or row[2].get('longValue',0) or 0),
                              'currency': row[3].get('stringValue','')})

        if not account_ids:
            logger.warning(f"No active accounts found for customer {customer_id}")
            return {'accounts': [], 'transactions': []}

        transactions = []
        for aid in account_ids:
            txn_resp = rds_data.execute_statement(
                resourceArn=AURORA_CLUSTER_ARN, secretArn=AURORA_SECRET_ARN, database=AURORA_DB_NAME,
                sql="""SELECT transaction_id, account_id, transaction_type, amount, currency,
                              description, balance_after, transaction_date, merchant_name, category_id, channel
                       FROM transactions
                       WHERE account_id = :aid AND transaction_date >= :sd AND status = 'completed'
                       ORDER BY transaction_date DESC LIMIT 500""",
                parameters=[
                    {'name': 'aid', 'value': {'stringValue': aid}},
                    {'name': 'sd',  'value': {'stringValue': start_date.strftime('%Y-%m-%d')}}
                ]
            )
            for row in txn_resp.get('records', []):
                def sv(f): return f.get('stringValue') or (f.get('isNull') and '') or ''
                transactions.append({
                    'transaction_id':   sv(row[0]), 'account_id': sv(row[1]),
                    'transaction_type': sv(row[2]), 'amount': float(sv(row[3]) or 0),
                    'currency':         sv(row[4]), 'description': sv(row[5]),
                    'balance_after':    float(sv(row[6]) or 0), 'transaction_date': sv(row[7]),
                    'merchant_name':    sv(row[8]), 'category_id': sv(row[9]), 'channel': sv(row[10]),
                })

        logger.info(f"Retrieved {len(accounts)} accounts and {len(transactions)} transactions for {customer_id}")
        return {'accounts': accounts, 'transactions': transactions}

    except Exception as e:
        logger.error(f"Error retrieving customer transactions: {str(e)}")
        return {'accounts': [], 'transactions': [], 'error': str(e)}


def update_financial_behavior_analysis(customer_id: str, application_id: str, analysis: str) -> bool:
    """Update loan application with financial behavior analysis."""
    try:
        response = dynamodb.update_item(
            TableName='aibank-personal-loan',
            Key={
                'customer_id': {'S': customer_id},
                'application_id': {'S': application_id}
            },
            UpdateExpression='SET financial_behaviour_analysis = :analysis',
            ExpressionAttributeValues={
                ':analysis': {'S': analysis}
            },
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Successfully updated financial_behaviour_analysis for {customer_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error updating financial behavior analysis: {str(e)}")
        return False

def lambda_handler(event, context):
    """Financial Profile Analysis Summary Lambda Function using Strands AI Agent"""
    
    try:
        customer_id = event.get('processingContext', {}).get('customer_id', 'UNKNOWN')
        application_id = event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        
        logger.info(f"Processing financial profile analysis for customer_id: {customer_id}")
        
        if customer_id == 'UNKNOWN':
            raise ValueError("Missing customer_id in processing context")
        
        # Get customer transaction data from core banking database
        banking_data = get_customer_transactions(customer_id)
        
        if 'error' in banking_data:
            raise ValueError(f"Database error: {banking_data['error']}")
        
        if not banking_data['transactions']:
            logger.warning(f"No transactions found for customer {customer_id}")
        
        # Create and initialize the agent
        financial_agent = create_financial_profile_agent()
        
        # Prepare financial analysis query with transaction data
        accounts_summary = []
        for acc in banking_data['accounts']:
            accounts_summary.append(f"- {acc['account_type'].title()} Account: {acc['currency']} {acc['balance']:,.3f}")
        
        # Analyze transactions by type and category
        credits = [t for t in banking_data['transactions'] if t['transaction_type'] == 'credit']
        debits = [t for t in banking_data['transactions'] if t['transaction_type'] == 'debit']
        
        total_credits = sum(t['amount'] for t in credits)
        total_debits = sum(t['amount'] for t in debits)
        
        # Sample recent transactions for analysis
        recent_transactions = banking_data['transactions'][:20]
        transaction_summary = []
        for txn in recent_transactions:
            transaction_summary.append(
                f"- {txn['transaction_date']}: {txn['transaction_type'].title()} "
                f"{txn['currency']} {txn['amount']:,.3f} - {txn['description'][:50]}"
            )
        
        query = f"""Please analyze this customer's financial profile and transaction behavior:

<account_information>
{chr(10).join(accounts_summary) if accounts_summary else "- No active accounts found"}
</account_information>

<transaction_summary>
Total Transactions Analyzed: {len(banking_data['transactions'])}
Credit Transactions: {len(credits)} (Total: BHD {total_credits:,.3f})
Debit Transactions: {len(debits)} (Total: BHD {total_debits:,.3f})
Net Cash Flow: BHD {total_credits - total_debits:,.3f}
</transaction_summary>

<recent_transactions>
{chr(10).join(transaction_summary[:10]) if transaction_summary else "- No recent transactions available"}
</recent_transactions>

<analysis_period>
Analysis Period: Last 12 months
Data Source: Core Banking System
Customer ID: {customer_id}
</analysis_period>

Provide your complete financial profile analysis following the required output format."""
        
        # Log the query for debugging
        logger.info(f"Financial Analysis Query for {customer_id}:")
        logger.info(f"Accounts: {len(banking_data['accounts'])}, Transactions: {len(banking_data['transactions'])}")
        
        # Get AI financial analysis
        financial_result = financial_agent(query)
        
        # Convert AgentResult to string
        financial_analysis_text = str(financial_result)
        
        logger.info(f"Financial profile analysis completed successfully")
        
        # Update loan application with financial analysis
        update_success = update_financial_behavior_analysis(
            customer_id, 
            application_id, 
            financial_analysis_text
        )
        
        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'financial_analysis': financial_analysis_text,
            'transaction_summary': {
                'total_transactions': len(banking_data['transactions']),
                'total_credits': total_credits,
                'total_debits': total_debits,
                'net_cash_flow': total_credits - total_debits,
                'accounts_count': len(banking_data['accounts'])
            },
            'update_success': update_success,
            'processing_metadata': {
                'function_name': 'financial-profile-analysis',
                'timestamp': datetime.utcnow().isoformat(),
                'analysis_version': '1.0'
            },
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }
        
    except Exception as e:
        logger.error(f"Financial profile analysis error: {str(e)}")
        
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('processingContext', {}).get('customer_id', 'UNKNOWN'),
            'application_id': event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        }
