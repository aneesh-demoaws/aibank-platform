# Loan AI Agent — A2A Integration Design

## Architecture

Follows the exact pattern of Alma FAQ → Onboarding Agent (A2A):

```
┌─ Alma Banking Assistant (HTTP, AgentCore) ──────────────────────┐
│  Existing: query_customer_data, check_kyc_status,               │
│            generate_kyc_upload_url                               │
│  NEW:      start_loan_application (tool)                        │
│            ↓ invoke_agent_runtime (A2A protocol)                │
└─────────────────────────┬───────────────────────────────────────┘
                          │ bedrock-agentcore invoke_agent_runtime
                          ▼
┌─ Loan AI Agent (A2A, AgentCore) ────────────────────────────────┐
│  Strands Agent + A2AServer on AgentCore Runtime                 │
│  Model: eu.anthropic.claude-3-haiku-20240307-v1:0               │
│                                                                  │
│  Tools:                                                          │
│  ├─ check_loan_eligibility  — Aurora: salary, existing loans     │
│  ├─ calculate_loan           — EMI, total interest, total cost   │
│  ├─ submit_loan_application  — DynamoDB put + S3 presigned URLs  │
│  └─ check_loan_status        — DynamoDB query by customer_id     │
│                                                                  │
│  Flow:                                                           │
│  1. Collect: loan_type, amount, tenure, purpose                  │
│  2. check_loan_eligibility → salary-based max amount             │
│  3. calculate_loan → show EMI breakdown                          │
│  4. Customer confirms → submit_loan_application                  │
│  5. Return: app_id + upload URLs for salary cert & bank stmt     │
│  6. Frontend/voice handles doc upload via [ACTION:LOAN_UPLOAD]   │
└─────────────────────────────────────────────────────────────────┘
```

## Communication Flow

### Text (Chat) Path
```
Customer → alma-chat.html → Lambda proxy → Alma Banking (AgentCore HTTP)
  → start_loan_application tool → invoke_agent_runtime → Loan Agent (A2A)
  → response includes upload URLs → Lambda proxy relays to frontend
  → Frontend shows loan upload widget
```

### Voice Path
```
Customer → WebSocket → Nova Sonic BidiAgent → start_loan_application tool
  → invoke_agent_runtime → Loan Agent (A2A)
  → response includes [ACTION:LOAN_UPLOAD:salary_certificate,bank_statement]
  → Voice says "I've submitted your application, please upload your documents"
  → Frontend shows loan upload widget on screen
```

## Key Design Decisions

1. **Loan Agent is A2A** (like Onboarding) — not HTTP. Alma Banking calls it via `invoke_agent_runtime` with JSON-RPC payload.

2. **Alma Banking is NOT modified structurally** — we only ADD a new `start_loan_application` tool. Existing tools (query_customer_data, KYC) untouched.

3. **Document upload stays visual** — voice says "upload your documents on screen", frontend shows upload widget. Same pattern as KYC `[ACTION:KYC_UPLOAD]`.

4. **Loan Agent returns structured JSON** — includes `upload_urls`, `application_id`, `status`. Alma Banking formats it for the customer.

5. **Session routing** — like Alma FAQ's onboarding session routing, once loan flow starts, subsequent messages route to the Loan Agent session until complete.

6. **customer_id passed through** — Alma Banking injects customer_id into the A2A message payload. Loan Agent uses it for DynamoDB writes and eligibility checks.

## Components to Create/Modify

### NEW: Loan AI Agent (A2A Server)
- Path: `use-cases/06-loan-automation/agent/`
- Files: `main.py`, `Dockerfile`, `requirements.txt`
- AgentCore: protocol=A2A, ECR repo `bedrock-agentcore-loan-agent`
- Runtime name: `loan_agent_a2a`

### MODIFY: Alma Banking Assistant (AgentCore HTTP)
- Path: `use-cases/03-alma-banking-assistant/agent/main.py`
- Add: `start_loan_application` tool (invoke_agent_runtime to Loan Agent A2A)
- Add: `LOAN_AGENT_ARN` env var
- Add: loan-related system prompt section
- Rebuild & redeploy Docker image

### MODIFY: Alma Banking Voice
- Path: `use-cases/03-alma-banking-assistant/voice/alma_banking_voice.py`
- Add: `start_loan_application` tool (same pattern)
- Add: `[ACTION:LOAN_UPLOAD:...]` marker handling in system prompt
- Add: loan section to voice system prompt

### MODIFY: Alma Banking Lambda Proxy
- Path: `use-cases/03-alma-banking-assistant/lambda/lambda_function.py`
- Add: loan session routing (like FAQ's onboarding routing)
- Add: loan upload URL extraction from response

### MODIFY: Alma Banking Frontend
- Path: `use-cases/03-alma-banking-assistant/frontend/alma-chat.html`
- Add: loan upload widget (similar to KYC upload widget)
- Add: `[ACTION:LOAN_UPLOAD]` marker handling

## Loan Products

| Product | loanType | Amount (BHD) | Tenure | Rate | Auto? |
|---------|----------|-------------|--------|------|-------|
| Instant Money | `instant_money` | 100–500 | 3–12 mo | 7.5% | Yes |
| Personal Finance | `personal` | 500–20,000 | 6–60 mo | 4.5% | No |

## Eligibility Rules (Loan Agent)
- Must have salary credits in last 3 months (Aurora transactions)
- Max loan = 20× monthly salary (instant money), 40× (personal)
- No existing loan in PENDING_REVIEW or APPROVED status
- KYC must be VERIFIED (check aibank-customer-kyc DynamoDB)
