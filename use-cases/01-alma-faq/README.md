# Use Case 01: Alma FAQ Chatbot

Public-facing AI assistant that answers questions about AI Bank products and services using a Bedrock Knowledge Base.

## Architecture

```
User → API Gateway → Lambda Proxy → Alma Agent (AgentCore, HTTP)
                                      ├── search_bank_info → Bedrock KB
                                      └── start_onboarding → Onboarding Agent (A2A)
```

## Components

| Component | Description |
|-----------|-------------|
| `agent/alma_agentcore.py` | Strands agent with KB search + onboarding handoff tools |
| `agent/requirements.txt` | Python dependencies |
| `lambda/handler.py` | API Gateway proxy with DynamoDB session routing for multi-turn onboarding |
| `data/aibank-public-faq.csv` | 56 Q&A pairs for the Knowledge Base |

## Prerequisites

- Foundation layer deployed
- Bedrock Knowledge Base created with `data/aibank-public-faq.csv`
- `ALMA_KB_ID` set in `config/env.sh`

## Deploy

```bash
./deploy.sh
```

This will:
1. Deploy the Alma agent to AgentCore (HTTP protocol)
2. Create the Lambda proxy function
3. Create the API Gateway endpoint
4. Create the DynamoDB session routing table

## Multi-Turn Session Routing

When a user says "I want to open an account", Alma's LLM detects the intent and calls the onboarding agent via A2A. The Lambda proxy then:

1. Detects the handoff (response contains onboarding signals)
2. Creates a dedicated onboarding session in DynamoDB
3. Routes all subsequent messages in that session directly to the onboarding agent
4. Clears the session when account creation completes

This avoids the need for keyword-based intent classification — the LLM handles it naturally.
