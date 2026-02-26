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

## Phase 1: Core Agent + Text-to-SQL ✅ COMPLETE
- [x] Build `query_customer_data` tool with row-level security (CTE wrapping)
- [x] Build Alma Banking Assistant Strands agent (Claude Sonnet, 5 few-shot SQL examples)
- [x] Deploy on AgentCore Runtime (HTTP, ARM64, eu-west-1) — `alma_banking_assistant-zxGWis2H4O`
- [x] Build Lambda proxy (`alma-banking-api`) with session cookie auth + email→customer_id lookup
- [x] API Gateway: POST /banking/chat on existing HTTP API (`nowkq8lqy5`)
- [x] Enable AgentCore Memory (STM + LTM) — `alma_banking_assistant_mem-ijns9pFcc6`
  - STM: Multi-turn context within session (tested: follow-up queries work)
  - LTM: 3 strategies — SemanticFacts, SessionSummaries, UserPreferences
- [x] Test: balance, spending, security (OR attack, UNION, no-WHERE — all blocked)
- [x] Frontend: `/banking/alma-chat.html` with markdown rendering

## Phase 2: KYC Document Processing Tools
- [ ] S3 bucket: aibank-kyc-processing (me-south-1)
- [ ] DynamoDB table: aibank-customer-kyc (me-south-1, Stream enabled)
- [ ] BDA project: AIBank-KYC (eu-west-1) with blueprints
  - Reuse: Passport, Bahrain CPR, Bahrain License
  - New: Salary Certificate, Saudi Iqama, UAE Emirates ID
- [ ] Build tool: `get_kyc_status`
- [ ] Build tool: `generate_upload_url`
- [ ] Build tool: `check_document_status`
- [ ] Build tool: `get_extraction_results`
- [ ] Build Lambda: BDA extraction processor (S3 trigger)
- [ ] Build Lambda: KYC verification (cross-check vs Aurora)
- [ ] Build Lambda: KYC sync (DynamoDB Stream → Aurora kyc_status)
- [ ] Add KYC tools to agent, redeploy
- [ ] Test: conversational KYC flow end-to-end

## Phase 3: Financial Goals & Insights Tools
- [ ] Build tool: `get_spending_patterns`
- [ ] Build tool: `get_financial_health`
- [ ] Build tool: `set_savings_goal`
- [ ] Build tool: `get_goals_progress`
- [ ] Add goals tools to agent, redeploy
- [ ] Compute customer_360_metrics for all 82 customers (batch)
- [ ] Test: spending analysis, goal creation, health score

## Phase 4: Frontend Integration
- [x] Banking chat page: /banking/alma-chat.html (authenticated, markdown)
- [ ] File upload support (for KYC documents)
- [ ] Dashboard widget: "Ask Alma" quick access
- [ ] KYC status banner with "Complete with Alma" CTA
- [ ] Update use-cases.js: status from coming-soon to live

## Phase 5: NBA Integration (After NBA is built)
- [ ] Build tool: `get_recommendations` (Personalize)
- [ ] Build tool: `accept_offer` (log decision)
- [ ] Add NBA tools to agent, redeploy
- [ ] "For You" cards surfaced in conversation

## Key Resources
```
AgentCore Runtime: alma_banking_assistant-zxGWis2H4O (eu-west-1, READY)
AgentCore Memory:  alma_banking_assistant_mem-ijns9pFcc6 (STM+LTM, ACTIVE)
ECR Image:         519124228967.dkr.ecr.eu-west-1.amazonaws.com/bedrock-agentcore-alma-banking-assistant
Lambda:            alma-banking-api (eu-west-1)
API Gateway:       POST /banking/chat on nowkq8lqy5
Frontend:          https://d1pfo41ge1bxh5.cloudfront.net/banking/alma-chat.html
```

## Status: PHASE 1 COMPLETE → NEXT: PHASE 2 (KYC IDP)
