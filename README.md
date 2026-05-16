# AI Bank Platform

Production-grade AI-native banking platform for GCC Financial Services, powered by Amazon Bedrock AgentCore, Strands Agents, and the A2A protocol.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     AI Bank Platform                         │
├──────────────────────┬───────────────────────────────────────┤
│   Foundation Layer   │         Use Case Modules              │
│                      │                                       │
│  • Aurora Serverless │  01-alma-faq          (FAQ Chatbot)   │
│  • Cognito Auth      │  02-customer-onboarding (A2A Agent)   │
│  • SES Email         │  03-alma-banking-assistant (Customer) │
│  • IAM Identity      │  04-kyc-idp            (KYC + IDP)    │
│    Center            │  05-nba                (Next Best     │
│  • Neptune           │                         Action)       │
│    Analytics         │  06-loan-automation    (5Cs Loan)     │
│  • S3 + Athena       │  07-customer-360       (RM Portal)    │
│  • CloudFront        │  08-trade-finance      (Trade)        │
│  • EventBridge       │  09-atm-optimizer      (Cash Mgmt)    │
└──────────────────────┴───────────────────────────────────────┘
```

## Regions

| Purpose | Region | Why |
|---------|--------|-----|
| Data (Aurora, Cognito, DynamoDB) | `eu-west-1` (Ireland) | Aurora data residency + Bedrock co-location |
| AI/Compute (AgentCore, Bedrock) | `eu-west-1` (Ireland) | Bedrock model availability |
| CloudFront edge + Lambda@Edge auth | `us-east-1` | CloudFront global edge |

## Use Case Catalog

| # | Use Case | Status | Key Components |
|---|---|---|---|
| 01 | **Alma FAQ** | ✅ Production | AgentCore agent, Knowledge Base, A2A start-onboarding |
| 02 | **Customer Onboarding** | ✅ Production | A2A agent, KYC integration, Cognito user creation |
| 03 | **Alma Banking Assistant** | ✅ Production | Customer dashboard, accounts, transfers, transactions, cards, support, alma-chat |
| 04 | **KYC + Intelligent Document Processing** | ✅ Production | Bedrock Data Automation, presigned uploads, AD verification |
| 05 | **Next Best Action (NBA)** | ✅ Production | Daily Aurora→Neptune→S3 pipeline, 14 Lambdas, batch + realtime, For You page, FHS |
| 06 | **Loan Automation (5Cs)** | ✅ Production | 5Cs Step Function (26 Lambdas), Loan Officer portal, auto + manual approval, transaction-module integration for disbursement |
| 07 | **Customer 360** | ✅ Production | RM portal, D3.js Neptune graph, Athena peer stats, embedded QuickSight + Quick Suite Chat Agent |
| 08 | **Trade Finance** | 🚧 Beta | LC issuance, RM advisor agent |
| 09 | **ATM Optimizer** | ✅ Production | Cash forecasting, branch performance |

## Quick Start

### 1. Configure

```bash
cp config/env.template config/env.sh
# Edit config/env.sh with your AWS account ID, region, Aurora ARN, Neptune graph ID, etc.
```

### 2. Deploy Foundation (required once)

```bash
cd foundation && ./deploy-all.sh
# This deploys: Aurora cluster + schema, Cognito user pools (customer + employee),
# Identity Center integration, SES sender role, IAM roles
```

### 3. Deploy Use Cases (pick any)

Each use case is independently deployable. Dependencies are noted in each README.

```bash
cd use-cases/05-nba && ./deploy.sh           # Deploy NBA (depends on foundation)
cd use-cases/06-loan-automation && ./deploy.sh  # Loan automation (depends on foundation + 04 for KYC)
cd use-cases/07-customer-360 && ./deploy.sh    # C360 (depends on 05 for peer stats)
```

## Module Dependencies

```
foundation/  ─── (required by all)
  ├── 01-alma-faq                    (standalone)
  ├── 02-customer-onboarding         (depends on 01)
  ├── 03-alma-banking-assistant      (standalone, integrates with 04, 05, 06)
  ├── 04-kyc-idp                     (standalone)
  ├── 05-nba                         (depends on Aurora + Neptune)
  ├── 06-loan-automation             (depends on 04 for KYC, integrates with transaction-module from 03)
  ├── 07-customer-360                (depends on 05 for peer stats, 06 for loan history)
  ├── 08-trade-finance               (standalone)
  └── 09-atm-optimizer               (standalone, reads from Aurora)
```

## Repository Structure

```
aibank-platform/
├── README.md                        ← This file
├── config/
│   └── env.template                 ← All configurable values
├── foundation/                      ← DEPLOY FIRST
│   ├── 01-aurora/                   ← MySQL schema + cluster
│   ├── 02-cognito/                  ← Customer + Employee user pools
│   ├── 03-ses/                      ← Cross-account SES sender role
│   └── portal/                      ← Shared landing + login pages
└── use-cases/
    ├── 01-alma-faq/
    ├── 02-customer-onboarding/
    ├── 03-alma-banking-assistant/
    │   ├── agent/                   ← AgentCore Strands agent
    │   ├── frontend/                ← dashboard, accounts, transfers, transactions, cards, support
    │   └── lambda/
    │       ├── session-api/         ← Customer session + accounts/transfers/transactions endpoints
    │       └── transaction-module/  ← Purchase, disburse_loan, reverse_disbursement
    ├── 04-kyc-idp/
    │   └── lambda/presigned-url/    ← KYC reset + S3 presigned uploads
    ├── 05-nba/
    │   ├── frontend/                ← for-you, financial-health, employee/nba-insights
    │   ├── lambda/                  ← 14 Lambdas (neptune-loader, neptune-enrichment, batch-generator, etc.)
    │   ├── stepfunctions/           ← daily-pipeline + batch-workflow ASL
    │   └── eventbridge/             ← cron schedules
    ├── 06-loan-automation/
    │   ├── lambda/                  ← 26 Lambdas (5Cs analyzers, decision engine, notification dispatcher)
    │   ├── portal/                  ← loans.html (customer) + officer/ (loan-queue, application-review)
    │   └── stepfunctions/           ← five-cs-loan-processing-workflow ASL
    ├── 07-customer-360/
    │   ├── athena/                  ← Glue table + Neptune→S3 export
    │   ├── lambda/c360-api/         ← REST API for C360 portal
    │   └── portal/rm/               ← customer360 + customers list
    ├── 08-trade-finance/
    └── 09-atm-optimizer/
```

## How A2A Integration Works

The Alma FAQ agent and downstream agents (Onboarding, Loan, NBA) communicate via the A2A protocol:

1. User tells Alma "I want to open an account"
2. Alma's LLM detects the intent and calls `start_onboarding` tool
3. The tool sends a JSON-RPC message to the Onboarding A2A server via `invoke_agent_runtime`
4. Lambda proxy stores the session in DynamoDB for multi-turn routing
5. Subsequent messages route directly to the Onboarding agent
6. After account creation, the session clears and routes back to Alma

## Tech Stack

| Component | Technology |
|-----------|-----------|
| AI Framework | [Strands Agents](https://strandsagents.com/) |
| Models | Amazon Bedrock (Nova Pro, Nova Lite) |
| Agent Runtime | Amazon Bedrock AgentCore |
| Agent Protocol | A2A (JSON-RPC) for inter-agent communication |
| Database | Aurora Serverless v2 (MySQL) via Data API |
| Graph | Amazon Neptune Analytics (`g-ruhyz8aj39`) |
| Auth | Amazon Cognito + IAM Identity Center (employee SSO) |
| Email | Amazon SES (cross-account sender) |
| Session Routing | DynamoDB + Lambda |
| Analytics | Athena + Glue Data Catalog + QuickSight |
| Step Functions | NBA daily pipeline + 5Cs loan processing |
| EventBridge | Daily cron schedules |
| CDN | CloudFront with Lambda@Edge auth |

## Prerequisites

- AWS CLI v2
- Python 3.10+
- Docker
- `pip install bedrock-agentcore-starter-toolkit`

## License

MIT
