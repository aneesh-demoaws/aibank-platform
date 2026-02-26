# AI Bank Platform

AI-native banking platform for GCC Financial Services, powered by Amazon Bedrock AgentCore, Strands Agents, and the A2A protocol.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     AI Bank Platform                         │
├──────────────────────┬───────────────────────────────────────┤
│   Foundation Layer   │         Use Case Modules              │
│                      │                                       │
│  • Aurora Serverless │  01-alma-faq       (FAQ Chatbot)      │
│  • Cognito Auth      │  02-customer-onboarding (A2A Agent)   │
│  • SES Email         │  03-personal-banking    (Coming)      │
│  • VPC / Networking  │  04-loan-automation     (Coming)      │
│                      │  05-trade-finance       (Coming)      │
└──────────────────────┴───────────────────────────────────────┘
```

## Regions

| Purpose | Region | Why |
|---------|--------|-----|
| Data (Aurora, Cognito) | `me-south-1` (Bahrain) | GCC data residency |
| AI/Compute (AgentCore, Bedrock) | `eu-west-1` (Ireland) | Bedrock model availability |

## Quick Start

### 1. Configure

```bash
cp config/env.template config/env.sh
# Edit config/env.sh with your AWS account details
```

### 2. Deploy Foundation (required once)

```bash
cd foundation && ./deploy-all.sh
# Update config/env.sh with output values
```

### 3. Deploy Use Cases (pick any)

```bash
cd use-cases/01-alma-faq && ./deploy.sh
cd use-cases/02-customer-onboarding && ./deploy.sh
```

## Module Dependencies

```
foundation/ ─── (required by all)
  └── use-cases/01-alma-faq ─── (standalone)
        └── use-cases/02-customer-onboarding ─── (requires 01)
```

## Repository Structure

```
aibank-platform/
├── README.md
├── config/
│   └── env.template              # All configurable values
├── foundation/                   # DEPLOY FIRST
│   ├── 01-aurora/
│   │   ├── deploy.sh
│   │   └── schema.sql            # 15 tables + 1 view
│   ├── 02-cognito/
│   │   └── deploy.sh
│   └── 03-ses/
│       └── deploy.sh
└── use-cases/
    ├── 01-alma-faq/
    │   ├── README.md
    │   ├── deploy.sh
    │   ├── agent/                # AgentCore agent (HTTP)
    │   ├── lambda/               # API proxy + session routing
    │   └── data/                 # Knowledge Base CSV
    └── 02-customer-onboarding/
        ├── README.md
        ├── deploy.sh
        └── agent/                # AgentCore agent (A2A)
```

## How A2A Integration Works

The Alma FAQ agent and Customer Onboarding agent communicate via the A2A protocol:

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
| Models | Amazon Bedrock (Nova Lite) |
| Agent Runtime | Amazon Bedrock AgentCore |
| Agent Protocol | A2A (JSON-RPC) for inter-agent communication |
| Database | Aurora Serverless v2 (MySQL) via Data API |
| Auth | Amazon Cognito |
| Email | Amazon SES |
| Session Routing | DynamoDB + Lambda |

## Prerequisites

- AWS CLI v2
- Python 3.10+
- Docker
- `pip install bedrock-agentcore-starter-toolkit`

## License

MIT
