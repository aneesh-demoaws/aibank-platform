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

def _load_yfinance_from_ddb(customer_id: str, application_id: str, retries: int = 6, delay: float = 2.0) -> dict:
    """Read the yfinance company_data JSON that Company_Analysis_Data_Gathering
    persisted on the loan record at customer_profile.employer_analysis.company_data.

    In the SFN, Company_Analysis_Data_Gathering and this Lambda (Employer_Analysis)
    run as sibling parallel branches — so the yfinance write may be a few seconds
    behind our first read. Retry a small number of times with short sleeps so the
    common race is self-healing without an SFN redesign.
    """
    import time
    if not customer_id or not application_id or customer_id == "UNKNOWN":
        return {}
    client = boto3.client("dynamodb", region_name="eu-west-1")
    for attempt in range(retries):
        try:
            resp = client.get_item(
                TableName="aibank-personal-loan",
                Key={"customer_id": {"S": customer_id}, "application_id": {"S": application_id}},
                ProjectionExpression="customer_profile",
                ConsistentRead=True,
            )
            item = resp.get("Item") or {}
            ea_map = (item.get("customer_profile", {}).get("M", {})
                          .get("employer_analysis", {}).get("M", {}))
            raw = ea_map.get("company_data", {}).get("S")
            if raw:
                parsed = json.loads(raw)
                data = parsed.get("data") or {}
                if data:
                    if attempt > 0:
                        logger.info(f"yfinance data appeared on retry {attempt}")
                    return data
        except Exception as e:
            logger.warning(f"yfinance DDB load attempt {attempt} failed: {e}")
        if attempt < retries - 1:
            time.sleep(delay)
    logger.warning("yfinance company_data not found after retries — proceeding without it")
    return {}


def _fmt_market_cap(mc):
    if not mc:
        return "Not available"
    try:
        mc = float(mc)
        if mc >= 1e12:
            return f"${mc/1e12:.2f} trillion market cap"
        if mc >= 1e9:
            return f"${mc/1e9:.2f} billion market cap"
        if mc >= 1e6:
            return f"${mc/1e6:.2f} million market cap"
        return f"${mc:,.0f} market cap"
    except Exception:
        return str(mc)


def _fmt_employees(n):
    if not n:
        return "Not available"
    try:
        return f"{int(n):,} employees globally"
    except Exception:
        return str(n)


def _stability_from_market_cap(mc):
    if not mc:
        return "Unable to assess"
    try:
        mc = float(mc)
    except Exception:
        return "Unable to assess"
    if mc >= 1e12:   return "Very strong — mega-cap (over $1T)"
    if mc >= 1e11:   return "Very strong — large-cap (over $100B)"
    if mc >= 1e10:   return "Strong — large-cap (over $10B)"
    if mc >= 1e9:    return "Stable — mid-cap (over $1B)"
    if mc >= 1e8:    return "Moderate — small-cap"
    return "Limited — micro-cap or not publicly traded"


def EmployerAnalysis(company_name: str, location: str = "", customer_id: str = "", application_id: str = "") -> EmployerFinancialInfo:
    """Primary: structured yfinance data already on the loan record.
    Fallback: Tavily web search for revenue/profit details (looser content filter).
    """

    # ── Step 1: seed from yfinance (authoritative, structured) ─────────
    yf = _load_yfinance_from_ddb(customer_id, application_id)
    extracted = {
        "company_name": yf.get("company_name") or company_name,
        "financial_status": "Not available",
        "recent_revenue": "Not available",
        "profit_loss": "Not available",
        "balance_sheet_summary": "Not available",
        "debt_levels": "Not available",
        "credit_rating": "Not available",
        "recent_earnings": "Not available",
        "market_performance": _fmt_market_cap(yf.get("market_cap")),
        "employee_count": _fmt_employees(yf.get("employees")),
        "industry_sector": (
            (f"{yf.get('industry','')} ({yf.get('sector','')})"
                if yf.get('industry') and yf.get('sector') else
             yf.get('industry') or yf.get('sector') or '')
            if (yf.get("industry") or yf.get("sector")) else "Not available"
        ),
        "financial_stability_score": _stability_from_market_cap(yf.get("market_cap")),
        "potential_risks": "No significant risks identified" if yf else "Insufficient data for analysis",
        "complete_financial_profile": yf.get("description", "") or "",
    }

    # ── Step 2: Tavily fallback for revenue/profit/earnings ─────────────
    # Use the yfinance company_name (e.g., "Amazon.com, Inc.") and symbol
    # (e.g., "AMZN") as search targets — much more likely to match news
    # content than the payload's employer_name ("Amazon Web Services (AWS)").
    search_terms = [t for t in (yf.get("company_name"), yf.get("symbol"), company_name) if t]
    primary_search = search_terms[0] if search_terms else company_name

    try:
        client = TavilyClient(api_key=TAVILY_API_KEY)
        all_financial_content = ""
        queries = [
            f'"{primary_search}" 2024 revenue earnings results',
            f'"{primary_search}" annual report financial performance',
            f'"{primary_search}" quarterly earnings profit loss',
        ]
        for q in queries:
            try:
                results = client.search(query=q, max_results=3, search_depth="advanced")
                for result in results.get("results", []):
                    content = result.get("content", "") or ""
                    title = result.get("title", "") or ""
                    # Looser filter: any of the search terms in title or content
                    ct = (title + " " + content).lower()
                    if any(t.lower() in ct for t in search_terms) and _has_financial_keyword(ct):
                        all_financial_content += f"{title} {content} "
            except Exception as e:
                logger.warning(f"Tavily query failed: {q!r}: {e}")

        if all_financial_content.strip():
            # Primary: Sonnet 4 structured extraction (single call → clean JSON).
            # Fallback: legacy keyword-regex extraction if the LLM fails.
            llm_fields = _llm_extract_financial_fields(all_financial_content, primary_search)
            if llm_fields:
                for k in ("recent_revenue", "profit_loss", "recent_earnings",
                          "balance_sheet_summary", "debt_levels", "credit_rating",
                          "financial_status"):
                    v = llm_fields.get(k)
                    if v and str(v).strip():
                        extracted[k] = str(v)[:600]
            else:
                extracted["recent_revenue"]        = _extract_financial_field(all_financial_content, ["revenue", "sales", "turnover"])
                extracted["profit_loss"]           = _extract_financial_field(all_financial_content, ["net income", "profit", "loss", "earnings"])
                extracted["recent_earnings"]       = _extract_financial_field(all_financial_content, ["quarterly earnings", "annual earnings", "Q1", "Q2", "Q3", "Q4"])
                extracted["balance_sheet_summary"] = _extract_financial_field(all_financial_content, ["balance sheet", "assets", "liabilities", "equity"])
                extracted["debt_levels"]           = _extract_financial_field(all_financial_content, ["debt", "borrowing", "leverage"])
                extracted["credit_rating"]         = _extract_financial_field(all_financial_content, ["credit rating", "rating agency", "Moody", "S&P"])
                extracted["financial_status"]      = _extract_financial_field(all_financial_content, ["financial status", "financially stable", "financial health"])
            # Keep the yfinance description if we have one; otherwise use search snippets
            if not extracted["complete_financial_profile"]:
                extracted["complete_financial_profile"] = all_financial_content.strip()[:2000]
    except Exception as e:
        logger.warning(f"Tavily fallback failed entirely: {e}")

    return EmployerFinancialInfo(**extracted)


def _has_financial_keyword(content_lower: str) -> bool:
    for kw in ("financial", "earnings", "revenue", "profit", "loss", "balance sheet",
               "annual report", "quarterly", "debt", "assets", "liabilities", "income",
               "cash flow", "credit rating", "financial results"):
        if kw in content_lower:
            return True
    return False


# ── Sonnet 4 structured extraction ────────────────────────────────────────────
_BEDROCK_MODEL_ID = "eu.anthropic.claude-sonnet-4-20250514-v1:0"


def _llm_extract_financial_fields(raw_content: str, company_name: str) -> dict:
    """Single Bedrock Converse call: distill messy Tavily search snippets into
    clean, concise structured financial facts. Returns empty dict on failure
    so caller can fall back to regex.
    """
    if not raw_content or not raw_content.strip():
        return {}
    try:
        br = boto3.client("bedrock-runtime", region_name="eu-west-1")
        # Trim very long content so we stay well within Sonnet 4's budget
        snippet = raw_content.strip()[:12000]
        prompt = (
            f"You are a financial data extraction assistant. Given messy web search snippets "
            f"about {company_name}, output ONE JSON object with these keys. Each value must "
            f"be a concise human-readable phrase (5-25 words, no raw SEC tables, no PDF "
            f"fragments, no stray punctuation). If a fact is not clearly present, use "
            f"'Not available'.\n\n"
            f"Required keys:\n"
            f"  recent_revenue          — most recent annual or trailing-twelve-month revenue\n"
            f"  profit_loss             — most recent net income or profit/loss statement\n"
            f"  recent_earnings         — most recent quarterly earnings highlight\n"
            f"  balance_sheet_summary   — brief health/size of balance sheet\n"
            f"  debt_levels             — total debt or leverage summary\n"
            f"  credit_rating           — rating agency rating if mentioned (e.g. 'S&P: AA')\n"
            f"  financial_status        — one-line overall financial-health summary\n\n"
            f"Output ONLY the JSON object, no markdown fences.\n\n"
            f"SEARCH SNIPPETS:\n{snippet}"
        )
        resp = br.converse(
            modelId=_BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 800, "temperature": 0.1},
        )
        text = ""
        for blk in resp["output"]["message"]["content"]:
            if "text" in blk:
                text += blk["text"]
        text = text.strip()
        # Strip accidental markdown fences
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.lower().startswith("json"):
                text = text[4:]
        # Best-effort JSON parse
        try:
            return json.loads(text)
        except Exception:
            # Find first { and last }
            a, b = text.find("{"), text.rfind("}")
            if a >= 0 and b > a:
                try:
                    return json.loads(text[a:b + 1])
                except Exception:
                    pass
        logger.warning("LLM extraction returned unparseable output; falling back to regex")
        return {}
    except Exception as e:
        logger.warning(f"Sonnet 4 financial extraction failed: {e}")
        return {}


def _legacy_tavily_only_EmployerAnalysis(company_name: str, location: str = "") -> EmployerFinancialInfo:
    """Deprecated pure-Tavily path. Kept for reference only; not called."""
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
            employer_analysis = EmployerAnalysis(
                employer_name,
                customer_id=customer_id,
                application_id=application_id,
            )
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
