# Alma Banking Assistant (Use Case 03) — Requirements

## Overview
Authenticated AI banking assistant that serves as the customer's single interface for all banking interactions — transaction insights (Text-to-SQL), KYC document processing (BDA), financial goals, and personalized recommendations. Multi-capability agent on AgentCore with MCP tools.

Raises the bar from NeoBank's Kiku (single-purpose text-to-SQL) to a multi-agentic architecture where one agent orchestrates multiple capabilities.

## Depends On
| Dependency | Status |
|-----------|--------|
| Foundation (Aurora, Cognito, SES) | ✅ Done |
| Customer Onboarding | ✅ Done |
| Synthetic Data (82 customers, 33K txns) | ✅ Done |
| Alma Public (FAQ + onboarding handoff) | ✅ Done |

## Unlocks
| Downstream | How |
|-----------|-----|
| KYC — IDP | Conversational KYC via document upload + BDA |
| NBA | "For You" recommendations surfaced through Alma |
| Customer 360 | Financial health insights delivered conversationally |

## Naming Hierarchy
| Agent | Audience | Auth | Purpose |
|-------|----------|------|---------|
| **Alma** | Public visitors | None | FAQ, product info, onboarding handoff |
| **Alma Banking Assistant** | Logged-in customers | Cognito JWT | Transactions, KYC, goals, recommendations |
| Alma RM Advisor | Bank employees | Employee JWT | Customer 360, NBA reasoning (future) |

## Key Requirements

### Core
| ID | Requirement | Priority |
|----|-------------|----------|
| AB-01 | Cognito JWT authentication — customer_id extracted from token | P0 |
| AB-02 | Data scoping — agent can ONLY access authenticated customer's data | P0 |
| AB-03 | Conversation history — multi-turn with DynamoDB session storage | P0 |
| AB-04 | AgentCore Runtime deployment (HTTP, ARM64, eu-west-1) | P0 |
| AB-05 | MCP tools via AgentCore Gateway | P0 |

### Capability 1: Transaction Insights (Text-to-SQL)
| ID | Requirement | Priority |
|----|-------------|----------|
| AB-10 | Natural language → SQL queries on customer's transactions | P0 |
| AB-11 | Spending analysis by category, merchant, time period | P0 |
| AB-12 | Account balance and summary | P0 |
| AB-13 | Income vs expense trends | P0 |
| AB-14 | Zero hallucination — every number must come from a DB query | P0 |
| AB-15 | Read-only — no INSERT/UPDATE/DELETE allowed | P0 |

### Capability 2: KYC — Intelligent Document Processing
| ID | Requirement | Priority |
|----|-------------|----------|
| AB-20 | Conversational KYC guidance ("I need 3 documents...") | P0 |
| AB-21 | Generate S3 presigned upload URLs for document upload | P0 |
| AB-22 | Trigger BDA processing on uploaded documents | P0 |
| AB-23 | Real-time extraction feedback ("I can see this is a passport for...") | P1 |
| AB-24 | KYC status tracking and completion notification | P0 |
| AB-25 | Support: Passport, CPR, License, Salary Cert, Iqama, Emirates ID | P0 |

### Capability 3: Financial Goals & Insights
| ID | Requirement | Priority |
|----|-------------|----------|
| AB-30 | Spending pattern analysis ("You spend 35% on groceries") | P1 |
| AB-31 | Savings goal creation and tracking | P1 |
| AB-32 | Budget recommendations based on spending history | P1 |
| AB-33 | Financial health score explanation | P1 |

### Capability 4: NBA Recommendations (Future)
| ID | Requirement | Priority |
|----|-------------|----------|
| AB-40 | Surface "For You" recommendations in conversation | P2 |
| AB-41 | Accept/decline offers through chat | P2 |
| AB-42 | Explain why an offer is recommended | P2 |

## Data Access Rules (CRITICAL)
```
Customer CUST00000001 logs in
  → JWT contains customer_id = CUST00000001
  → ALL SQL queries MUST include: WHERE customer_id = 'CUST00000001'
  → Agent CANNOT query other customers' data
  → Agent CANNOT run queries without customer_id filter
  → Agent CANNOT execute DDL or DML (read-only)
```

## Status: NEXT TO BUILD
