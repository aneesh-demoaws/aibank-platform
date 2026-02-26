# Alma Banking Assistant (Use Case 03) — Tasks

## Architecture Source
- NeoBank Kiku reviewed: [NEOBANK-KIKU-REVIEW.md](./NEOBANK-KIKU-REVIEW.md)
- KYC IDP pipeline reviewed: [../kyc-onboarding/EXISTING-NEOBANK-KYC-REVIEW.md](../kyc-onboarding/EXISTING-NEOBANK-KYC-REVIEW.md)

## Prerequisites
- [x] Foundation (Aurora, Cognito, SES)
- [x] Customer Onboarding (customers with accounts)
- [x] Synthetic Data (82 customers, 33K transactions)
- [x] Alma Public agent (FAQ + onboarding)
- [x] NeoBank Kiku text-to-SQL reviewed
- [x] NeoBank KYC IDP pipeline reviewed

## Phase 1: Core Agent + Text-to-SQL (Priority — immediate wow)
- [ ] Build `query_customer_data` MCP tool Lambda
  - Aurora Data API (me-south-1, cross-region from eu-west-1)
  - Read-only, customer_id scoping enforced at tool level
  - Schema: customers, accounts, transactions, merchant_categories
- [ ] Build Alma Banking Assistant Strands agent
  - System prompt with DB schema + data scoping rules
  - Claude Sonnet on Bedrock
  - Tool: query_customer_data
- [ ] Deploy on AgentCore Runtime (HTTP, ARM64, eu-west-1)
- [ ] Build Lambda proxy
  - Cognito JWT validation → extract customer_id
  - DynamoDB conversation history (load/save)
  - Inject customer_id into agent context
- [ ] API Gateway: POST /banking/chat (Cognito authorizer)
- [ ] DynamoDB table: aibank-banking-assistant-sessions
- [ ] Test: login as seeded customer → query transactions → verify data scoping

## Phase 2: KYC Document Processing Tools
- [ ] S3 bucket: aibank-kyc-processing (me-south-1)
- [ ] DynamoDB table: aibank-customer-kyc (me-south-1, Stream enabled)
- [ ] BDA project: AIBank-KYC (eu-west-1) with blueprints
  - Reuse: Passport, Bahrain CPR, Bahrain License
  - New: Salary Certificate, Saudi Iqama, UAE Emirates ID
- [ ] Build MCP tool: `get_kyc_status`
- [ ] Build MCP tool: `generate_upload_url`
- [ ] Build MCP tool: `check_document_status`
- [ ] Build MCP tool: `get_extraction_results`
- [ ] Build Lambda: BDA extraction processor (S3 trigger)
- [ ] Build Lambda: KYC verification (cross-check vs Aurora)
- [ ] Build Lambda: KYC sync (DynamoDB Stream → Aurora kyc_status)
- [ ] Add KYC tools to agent, redeploy
- [ ] Test: conversational KYC flow end-to-end

## Phase 3: Financial Goals & Insights Tools
- [ ] Build MCP tool: `get_spending_patterns`
- [ ] Build MCP tool: `get_financial_health`
- [ ] Build MCP tool: `set_savings_goal`
- [ ] Build MCP tool: `get_goals_progress`
- [ ] Add goals tools to agent, redeploy
- [ ] Compute customer_360_metrics for all 82 customers (batch)
- [ ] Test: spending analysis, goal creation, health score

## Phase 4: Frontend Integration
- [ ] Banking chat page: /banking/alma-chat.html
  - Authenticated (Cognito session required)
  - File upload support (for KYC documents)
  - Conversation history display
- [ ] Dashboard widget: "Ask Alma" quick access
- [ ] KYC status banner with "Complete with Alma" CTA
- [ ] Update use-cases.js: add Alma Banking Assistant card (status: live)

## Phase 5: NBA Integration (After NBA is built)
- [ ] Build MCP tool: `get_recommendations` (Personalize)
- [ ] Build MCP tool: `accept_offer` (log decision)
- [ ] Add NBA tools to agent, redeploy
- [ ] "For You" cards surfaced in conversation

## Build Order
```
Phase 1 (Text-to-SQL) → immediate demo value, 82 customers with rich data
Phase 2 (KYC IDP)     → new customers can verify, feeds NBA pipeline
Phase 3 (Goals)        → engagement, financial wellness
Phase 4 (Frontend)     → can run in parallel with Phase 2-3
Phase 5 (NBA)          → after NBA use case is built
```

## Status: NEXT TO BUILD ← START WITH PHASE 1
