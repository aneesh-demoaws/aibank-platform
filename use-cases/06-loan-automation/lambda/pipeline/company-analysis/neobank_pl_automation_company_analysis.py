import json
import boto3
import logging
from datetime import datetime
from typing import Dict, Union
import urllib.parse

# Configure logging first
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Handle layer imports with fallback
try:
    from bs4 import BeautifulSoup
    import yfinance as yf
    import requests
except ImportError as e:
    logger.error(f"Failed to import dependencies: {e}")
    # Try alternative import paths for Lambda layers
    import sys
    sys.path.append('/opt/python')
    sys.path.append('/opt/python/lib/python3.12/site-packages')
    try:
        from bs4 import BeautifulSoup
        import yfinance as yf
        import requests
    except ImportError as e2:
        logger.error(f"Failed to import from layer paths: {e2}")
        raise

# AWS clients
dynamodb = boto3.client('dynamodb')

def get_company_info(ticker: str) -> Union[Dict, str]:
    """Fetches comprehensive company information and financials using Yahoo Finance."""
    try:
        if not ticker.strip():
            return {"status": "error", "message": "Ticker symbol is required"}

        stock = yf.Ticker(ticker)
        info = stock.info

        company_data = {
            "status": "success",
            "data": {
                "symbol": ticker,
                "company_name": info.get("longName", "N/A"),
                "sector": info.get("sector", "N/A"),
                "industry": info.get("industry", "N/A"),
                "description": info.get("longBusinessSummary", "N/A"),
                "website": info.get("website", "N/A"),
                "market_cap": info.get("marketCap", "N/A"),
                "employees": info.get("fullTimeEmployees", "N/A"),
                "country": info.get("country", "N/A"),
                "headquarters": info.get("city", "N/A"),
                "date": datetime.now().strftime("%Y-%m-%d"),
            },
        }
        return company_data

    except Exception as e:
        return {"status": "error", "message": f"Error fetching company info: {str(e)}"}

def get_stock_news(ticker: str) -> Union[Dict, str]:
    """Fetches stock news from multiple sources for comprehensive coverage."""
    try:
        if not ticker.strip():
            return {"status": "error", "message": "Ticker symbol is required"}

        # Get company name for better search results
        try:
            stock = yf.Ticker(ticker)
            company_name = stock.info.get("shortName") or stock.info.get("longName") or ticker
        except Exception:
            company_name = ticker

        logger.info(f"Searching news for {ticker} ({company_name})")

        all_news = []
        sources_tried = []

        # 1. Try Yahoo Finance news API directly
        sources_tried.append("Yahoo Finance API")
        try:
            stock = yf.Ticker(ticker)
            news_data = stock.news

            if news_data and len(news_data) > 0:
                for item in news_data[:5]:
                    news_item = {
                        "title": item.get("title", ""),
                        "summary": item.get("summary", "")[:300] if item.get("summary") else "",
                        "url": item.get("link", ""),
                        "source": item.get("publisher", "Yahoo Finance"),
                        "date": datetime.fromtimestamp(item.get("providerPublishTime", 0)).strftime("%Y-%m-%d"),
                    }
                    if news_item["title"] and news_item["url"]:
                        all_news.append(news_item)

                logger.info(f"Found {len(all_news)} news items from Yahoo Finance API")
        except Exception as e:
            logger.warning(f"Error with Yahoo Finance API: {str(e)}")

        # 2. Try MarketWatch
        if len(all_news) < 5:
            sources_tried.append("MarketWatch")
            try:
                url = f"https://www.marketwatch.com/investing/stock/{ticker.lower()}"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,images/webp,*/*;q=0.8",
                }

                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    articles = soup.select(".article__content")

                    for article in articles[:5]:
                        title_elem = article.select_one(".article__headline")
                        link_elem = article.select_one("a.link")

                        if title_elem and link_elem:
                            title = title_elem.text.strip()
                            link = link_elem.get("href", "")

                            if link and not link.startswith("http"):
                                link = f"https://www.marketwatch.com{link}"

                            news_item = {
                                "title": title,
                                "summary": "",
                                "url": link,
                                "source": "MarketWatch",
                                "date": datetime.now().strftime("%Y-%m-%d"),
                            }

                            if news_item["title"] and news_item["url"] and news_item not in all_news:
                                all_news.append(news_item)

                    logger.info(f"Found {len(articles)} news items from MarketWatch")
            except Exception as e:
                logger.warning(f"Error with MarketWatch: {str(e)}")

        # 3. Try Google News as fallback
        if len(all_news) < 3:
            sources_tried.append("Google News")
            try:
                search_query = f"{company_name} stock news"
                url = f"https://www.google.com/search?q={urllib.parse.quote(search_query)}&tbm=nws"

                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,images/webp,*/*;q=0.8",
                }

                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    soup = BeautifulSoup(response.text, "html.parser")
                    
                    selectors = ["div.SoaBEf", "div.dbsr", "g-card", ".WlydOe", ".ftSUBd"]
                    news_elements = []
                    
                    for selector in selectors:
                        if not news_elements:
                            news_elements = soup.select(selector)

                    for element in news_elements[:3]:
                        title = None
                        link = None

                        if element.name == "a":
                            title = element.text.strip()
                            link = element.get("href", "")
                            if link.startswith("/url?q="):
                                link = link.split("/url?q=")[1].split("&")[0]
                        else:
                            link_elem = element.find("a")
                            if link_elem:
                                title = link_elem.text.strip()
                                link = link_elem.get("href", "")
                                if link.startswith("/url?q="):
                                    link = link.split("/url?q=")[1].split("&")[0]

                        if title and link and len(title) > 10:
                            news_item = {
                                "title": title,
                                "summary": "",
                                "url": link,
                                "source": "Google News",
                                "date": datetime.now().strftime("%Y-%m-%d"),
                            }

                            if news_item["title"] and news_item["url"] and news_item not in all_news:
                                all_news.append(news_item)

                    logger.info(f"Found {len(news_elements)} news items from Google News")
            except Exception as e:
                logger.warning(f"Error with Google News: {str(e)}")

        if all_news:
            logger.info(f"Found total of {len(all_news)} news items from {', '.join(sources_tried)}")
            return {
                "status": "success",
                "data": {
                    "symbol": ticker,
                    "company_name": company_name,
                    "recent_news": all_news[:5],
                    "sources_checked": sources_tried,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                },
            }
        else:
            logger.info(f"No news found for {ticker} after checking {', '.join(sources_tried)}")
            return {
                "status": "no_results",
                "message": f"No news found for {ticker} after checking {', '.join(sources_tried)}",
                "data": {
                    "symbol": ticker,
                    "company_name": company_name,
                    "recent_news": [],
                    "sources_checked": sources_tried,
                    "date": datetime.now().strftime("%Y-%m-%d"),
                },
            }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Error fetching news: {str(e)}",
            "data": {
                "symbol": ticker,
                "recent_news": [],
                "date": datetime.now().strftime("%Y-%m-%d"),
            },
        }

def store_employer_analysis(customer_id: str, application_id: str, company_data: dict, company_stock_news_data: dict) -> bool:
    """Store employer analysis data in DynamoDB."""
    try:
        employer_analysis = {
            "M": {
                "company_data": {"S": json.dumps(company_data)},
                "company_stock_news_data": {"S": json.dumps(company_stock_news_data)},
                "analysis_timestamp": {"S": datetime.utcnow().isoformat()},
                "analysis_method": {"S": "YFINANCE_DATA_GATHERING"}
            }
        }
        
        response = dynamodb.update_item(
            TableName='neobank-personal-loan',
            Key={
                'customer_id': {'S': customer_id},
                'application_id': {'S': application_id}
            },
            UpdateExpression='SET customer_profile.employer_analysis = :analysis',
            ExpressionAttributeValues={
                ':analysis': employer_analysis
            },
            ReturnValues='UPDATED_NEW'
        )
        
        logger.info(f"Successfully stored employer analysis for {customer_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error storing employer analysis: {str(e)}")
        return False

def find_ticker_from_company_name(company_name: str) -> str:
    """Find stock ticker from company name using common mappings and search."""
    if not company_name:
        return ""
    
    # Common company name to ticker mappings
    company_mappings = {
        "amazon web services": "AMZN",
        "amazon": "AMZN", 
        "aws": "AMZN",
        "microsoft": "MSFT",
        "google": "GOOGL",
        "alphabet": "GOOGL",
        "apple": "AAPL",
        "meta": "META",
        "facebook": "META",
        "tesla": "TSLA",
        "netflix": "NFLX",
        "nvidia": "NVDA",
        "oracle": "ORCL",
        "salesforce": "CRM",
        "adobe": "ADBE",
        "intel": "INTC",
        "cisco": "CSCO",
        "ibm": "IBM",
        "paypal": "PYPL",
        "uber": "UBER",
        "airbnb": "ABNB",
        "zoom": "ZM",
        "slack": "WORK",
        "twitter": "TWTR",
        "linkedin": "MSFT",
        "youtube": "GOOGL",
        "whatsapp": "META",
        "instagram": "META"
    }
    
    # Clean and normalize company name
    clean_name = company_name.lower().strip()
    clean_name = clean_name.replace("(", "").replace(")", "").replace(",", "").replace(".", "")
    clean_name = clean_name.replace(" inc", "").replace(" corp", "").replace(" ltd", "").replace(" llc", "")
    
    # Direct lookup
    if clean_name in company_mappings:
        return company_mappings[clean_name]
    
    # Partial matching
    for key, ticker in company_mappings.items():
        if key in clean_name or clean_name in key:
            return ticker
    
    # If no match found, try using the company name as ticker (some companies use their name)
    # Extract first word as potential ticker
    first_word = clean_name.split()[0] if clean_name.split() else clean_name
    return first_word.upper()[:5]  # Limit to 5 chars max

def lambda_handler(event, context):
    """Company Analysis Data Gathering Lambda Function"""
    
    try:
        customer_id = event.get('processingContext', {}).get('customer_id', 'UNKNOWN')
        application_id = event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        
        # Get company name from customer_data.employer_name
        company_name = event.get('customer_data', {}).get('employer_name', '')
        if not company_name:
            company_name = event.get('company_name', '')
        
        # Find ticker from company name
        ticker = find_ticker_from_company_name(company_name)
        
        logger.info(f"Gathering company data for: {company_name} (ticker: {ticker})")
        
        if customer_id == 'UNKNOWN' or application_id == 'UNKNOWN':
            raise ValueError("Missing customer_id or application_id in processing context")
        
        # Get company information and news
        company_data = get_company_info(ticker)
        company_stock_news_data = get_stock_news(ticker)
        
        # Store in DynamoDB
        storage_success = store_employer_analysis(
            customer_id, 
            application_id, 
            company_data, 
            company_stock_news_data
        )
        
        return {
            'statusCode': 200,
            'customer_id': customer_id,
            'application_id': application_id,
            'company_data': company_data,
            'company_stock_news_data': company_stock_news_data,
            'storage_success': storage_success,
            'executionContext': event.get('executionContext', {}),
            'processingContext': event.get('processingContext', {})
        }
        
    except Exception as e:
        logger.error(f"Company data gathering error: {str(e)}")
        
        return {
            'statusCode': 500,
            'error': str(e),
            'customer_id': event.get('processingContext', {}).get('customer_id', 'UNKNOWN'),
            'application_id': event.get('processingContext', {}).get('application_id', 'UNKNOWN')
        }
