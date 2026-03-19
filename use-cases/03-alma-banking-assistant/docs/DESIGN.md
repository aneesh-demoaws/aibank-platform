# Alma Banking Assistant (Use Case 03) — Solution Design

## Architecture

```
Customer logs in (Cognito JWT)
    │
    ▼
┌─ API Gateway (eu-west-1) ────────────────────────────────────┐
│  POST /banking/chat                                           │
│  Cognito Authorizer → extract customer_id from JWT            │
└───────────────────────────┬───────────────────────────────────┘
                            │
                            ▼
┌─ Lambda Proxy (eu-west-1) ────────────────────────────────────┐
│  • Validate JWT, extract customer_id                          │
│  • Load/save conversation history (DynamoDB)                  │
│  • Inject customer_id into agent context                      │
│  • Route to AgentCore Runtime                                 │
└───────────────────────────┬───────────────────────────────────┘
                            │
                            ▼
┌─ AgentCore Runtime (eu-west-1) ──────────────────────────────┐
│                                                               │
│  ALMA BANKING ASSISTANT                                       │
│  Strands Agent + Claude Sonnet                                │
│  System prompt with DB schema + data scoping rules            │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              MCP TOOLS (via AgentCore Gateway)           │ │
│  │                                                         │ │
│  │  ┌─────────────────────────────────────────────────┐   │ │
│  │  │  CAPABILITY 1: Transaction Insights              │   │ │
│  │  │  Tool: query_customer_data                       │   │ │
│  │  │  • MySQL MCP Server (read-only)                  │   │ │
│  │  │  • Auto-injects WHERE customer_id = :id          │   │ │
│  │  │  • Queries: transactions, accounts, balances     │   │ │
│  │  │  Source: Aurora me-south-1 via Data API           │   │ │
│  │  └─────────────────────────────────────────────────┘   │ │
│  │                                                         │ │
│  │  ┌─────────────────────────────────────────────────┐   │ │
│  │  │  CAPABILITY 2: KYC Document Processing           │   │ │
│  │  │  Tools:                                          │   │ │
│  │  │  • get_kyc_status — check current KYC state      │   │ │
│  │  │  • generate_upload_url — S3 presigned URL        │   │ │
│  │  │  • check_document_status — BDA processing state  │   │ │
│  │  │  • get_extraction_results — what BDA extracted   │   │ │
│  │  │  Source: S3 + BDA + DynamoDB                     │   │ │
│  │  └─────────────────────────────────────────────────┘   │ │
│  │                                                         │ │
│  │  ┌─────────────────────────────────────────────────┐   │ │
│  │  │  CAPABILITY 3: Financial Goals & Insights        │   │ │
│  │  │  Tools:                                          │   │ │
│  │  │  • get_spending_patterns — category breakdown    │   │ │
│  │  │  • get_financial_health — savings rate, ratios   │   │ │
│  │  │  • set_savings_goal — create/update goals        │   │ │
│  │  │  • get_goals_progress — track active goals       │   │ │
│  │  │  Source: Aurora me-south-1                        │   │ │
│  │  └─────────────────────────────────────────────────┘   │ │
│  │                                                         │ │
│  │  ┌─────────────────────────────────────────────────┐   │ │
│  │  │  CAPABILITY 4: NBA (Future — plug in later)      │   │ │
│  │  │  Tools:                                          │   │ │
│  │  │  • get_recommendations — Personalize scores      │   │ │
│  │  │  • accept_offer — log decision                   │   │ │
│  │  │  Source: Personalize + Aurora                     │   │ │
│  │  └─────────────────────────────────────────────────┘   │ │
│  │                                                         │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

### 1. AgentCore Runtime, not Lambda
NeoBank Kiku runs as a Lambda with MCP via stdio subprocess — slow cold starts (spawns uvx), 1.5GB memory, 300s timeout. Alma Banking Assistant runs on AgentCore Runtime — persistent process, no cold starts, MCP tools via Gateway.

### 2. MCP Tools via AgentCore Gateway, not stdio
Kiku spawns `awslabs.mysql-mcp-server` as a subprocess per invocation. Alma uses AgentCore Gateway to host MCP tools as managed endpoints — faster, more reliable, no subprocess overhead.

### 3. Data Scoping at Tool Level
Kiku injects customer phone into the prompt and hopes Claude filters. Alma enforces at the tool level — the `query_customer_data` tool receives customer_id as a parameter and ALWAYS adds `WHERE customer_id = :id` to every query. The agent cannot bypass this.

### 4. Multi-Capability, Single Agent
Not a multi-agent A2A setup. One Strands agent with multiple tool groups. Claude naturally routes based on intent:
- "How much did I spend?" → query_customer_data tool
- "I want to verify my identity" → KYC tools
- "Help me save money" → goals tools

This is simpler, faster, and avoids A2A overhead for what is fundamentally one conversation.

### 5. Conversation History
DynamoDB table stores conversation messages per session. Lambda proxy loads last N messages and passes to agent. Agent maintains context across turns.

## System Prompt (Core)

```
You are Alma Banking Assistant for AI Bank. You are the customer's personal AI
banking assistant, available after they log in.

## YOUR CAPABILITIES
1. Transaction Insights — query the customer's accounts, transactions, spending
2. KYC Verification — guide document upload and track verification status
3. Financial Goals — analyze spending, set savings goals, track progress
4. Recommendations — personalized offers and advice (coming soon)

## CRITICAL DATA RULES
- You can ONLY access data for the authenticated customer: {customer_id}
- Every database query MUST filter by this customer_id
- NEVER fabricate financial data — every number must come from a query result
- If a query returns no data, say so honestly
- You are READ-ONLY — never modify account data

## DATABASE SCHEMA
{schema}

## RESPONSE STYLE
- Friendly, professional, concise
- Use currency symbols (BHD, SAR, AED) based on customer's country
- Format numbers with proper decimal places (BHD uses 3 decimals)
- Use tables for transaction lists
- Keep responses focused — don't over-explain

## KYC GUIDANCE
When customer asks about KYC or verification:
1. Check their current status with get_kyc_status
2. If PENDING: guide them to upload 2 identity docs + 1 address doc
3. Generate upload URLs with generate_upload_url
4. After upload: check processing status
5. Supported docs: Passport, CPR, Iqama, Emirates ID, License, Salary Certificate

## GCC CONTEXT
- Multi-currency: BHD (3 decimals), SAR (2 decimals), AED (2 decimals)
- Salary typically credited 25th-28th of month
- Categories: Groceries, Dining, Housing, Transport, Entertainment, Shopping, Health, Telecom
```

## MCP Tools Specification

### Tool 1: query_customer_data
```
Purpose: Text-to-SQL for customer's banking data
Input: natural_language_query (string), customer_id (string, injected)
Backend: Aurora MySQL via Data API (read-only)
Security: ALL queries auto-filtered by customer_id
Example: "spending by category last 3 months" →
  SELECT category_id, SUM(amount) FROM transactions t
  JOIN accounts a ON t.account_id = a.account_id
  WHERE a.customer_id = 'CUST00000001'
  AND t.transaction_date >= DATE_SUB(NOW(), INTERVAL 3 MONTH)
  GROUP BY category_id ORDER BY SUM(amount) DESC
```

### Tool 2: get_kyc_status
```
Purpose: Check customer's KYC verification status
Input: customer_id (injected)
Backend: Aurora (customers.kyc_status) + DynamoDB (aibank-customer-kyc)
Output: {status, documents_collected, documents_needed}
```

### Tool 3: generate_upload_url
```
Purpose: Generate S3 presigned URL for document upload
Input: customer_id (injected), document_type (identity|address), file_name
Backend: S3 presigned URL generation
Output: {upload_url, expires_in, instructions}
```

### Tool 4: check_document_status
```
Purpose: Check BDA processing status for uploaded documents
Input: customer_id (injected)
Backend: DynamoDB (aibank-customer-kyc)
Output: {documents: [{type, status, extracted_fields}]}
```

### Tool 5: get_spending_patterns
```
Purpose: Analyze spending by category, merchant, time period
Input: customer_id (injected), period (default: 3 months)
Backend: Aurora (transactions + merchant_categories)
Output: {categories: [{name, total, pct, trend}], top_merchants, monthly_trend}
```

### Tool 6: get_financial_health
```
Purpose: Calculate financial health metrics
Input: customer_id (injected)
Backend: Aurora (transactions + accounts + customer_360_metrics)
Output: {score, savings_rate, expense_ratio, income, top_expense_category}
```

### Tool 7: set_savings_goal
```
Purpose: Create or update a savings goal
Input: customer_id (injected), goal_type, target_amount, target_date, title
Backend: Aurora (customer_goals table — INSERT/UPDATE only on goals)
Output: {goal_id, status, monthly_contribution_needed}
```

### Tool 8: get_goals_progress
```
Purpose: Get active savings/investment goals with progress
Input: customer_id (injected)
Backend: Aurora (customer_goals table)
Output: {goals: [{title, target, current, progress_pct, on_track}]}
```

## AWS Services

| Service | Region | Purpose |
|---------|--------|---------|
| AgentCore Runtime | eu-west-1 | Alma Banking Assistant agent |
| AgentCore Gateway | eu-west-1 | MCP tool hosting (8 tools) |
| Lambda | eu-west-1 | Proxy + individual tool Lambdas |
| API Gateway | eu-west-1 | POST /banking/chat with Cognito auth |
| DynamoDB | eu-west-1 | Conversation history |
| Aurora | me-south-1 | Core Banking data (cross-region) |
| S3 | me-south-1 | KYC document storage |
| BDA | eu-west-1 | Document extraction |
| Cognito | me-south-1 | Customer authentication |

## Conversation Examples

### Transaction Insights
```
Customer: "How much did I spend on dining last month?"
Alma: "Last month you spent BHD 245.500 on dining across 12 transactions.
       Your top restaurants were:
       • Saffron Bistro — BHD 85.000 (3 visits)
       • The Meat Co — BHD 62.500 (2 visits)
       • Café Lilou — BHD 45.000 (4 visits)
       That's 18% of your total spending. Want me to compare with previous months?"
```

### KYC Processing
```
Customer: "I need to complete my verification"
Alma: "Your KYC status is currently PENDING. To verify your identity, I need:
       1. 📄 Two identity documents (Passport, CPR, or License)
       2. 🏠 One proof of address (utility bill, bank statement, or CPR)
       
       Ready to upload your first document? I'll generate a secure upload link."

Customer: "Yes, I have my passport"
Alma: "Here's your secure upload link for your passport:
       [Upload Passport] (link expires in 1 hour)
       
       Please upload a clear PDF or photo of the first page."

Customer: [uploads file]
Alma: "Processing your passport... ✅ Done!
       I extracted: Deepthy Vijayan, Indian nationality, DOB 15-May-1990
       ✅ Name matches your account record.
       
       Now I need one more identity document. Do you have a CPR or license?"
```

### Financial Goals
```
Customer: "I want to save for a vacation"
Alma: "Great idea! Based on your finances:
       • Monthly income: BHD 3,500
       • Current savings rate: 22% (BHD 770/month)
       
       How much do you want to save, and by when?
       For example, BHD 2,000 by December would need BHD 200/month."
```

## Status: NEXT TO BUILD
