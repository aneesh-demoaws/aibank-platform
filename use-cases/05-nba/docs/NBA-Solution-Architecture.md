# NBA Platform — Solution Architecture Document
## AI-Powered Next Best Action for AI Bank Retail Banking

| Attribute | Value |
|---|---|
| **Version** | 2.3 |
| **Date** | 15 May 2026 (updated) |
| **Status** | Production (Pilot) |
| **Region** | AWS eu-west-1 (Ireland) |
| **Customers** | 291 (pilot), designed for 400K+ |

---

## 1. Executive Summary

The NBA platform generates personalised, explainable, and executable financial recommendations for retail banking customers. It combines deterministic rules, graph analytics, and LLM reasoning to produce recommendations that are grounded in real customer data, validated by peer behaviour, and actionable through the bank's AI assistant (Alma).

---

## 2. End-to-End Process

### 2.1 Two Generation Paths

| Path | Trigger | Latency | Use case |
|---|---|---|---|
| **Batch** | EventBridge cron (every 6h) | ~44s for 291 customers | "Always relevant" baseline recommendations |
| **Real-time** | Customer conversation (life event detected) | <5s per customer | "In the moment" contextual recommendations |

### 2.2 Batch Path — Stage by Stage

```
Stage 1: GATHER CONTEXT
    │ Source: Aurora MySQL + Neptune Analytics
    │ What: balance, income, spend, FHS, goals, suppressions, household
    │ Why: Every recommendation must be grounded in real data
    │
Stage 2: LOAD TEMPLATES
    │ Source: Aurora (nba_templates table)
    │ What: 8 active templates with eligibility rules
    │ Why: Templates define WHAT can be recommended; rules define WHO qualifies
    │
Stage 3: EVALUATE ELIGIBILITY (deterministic)
    │ Logic: Pure Python rule evaluation
    │ What: Check each template's rules against customer context
    │ Why: Ensures only relevant, appropriate recommendations surface
    │ Gates: FHS score, income threshold, existing products, suppressions
    │
Stage 4: RANK + SELECT (deterministic)
    │ Logic: Sort by priority, apply category cap (max 2 per category)
    │ What: Select top-3 candidates from matched templates
    │ Why: Cognitive load — customer sees max 3 diverse recommendations
    │
Stage 5: FETCH GRAPH CONTEXT
    │ Source: Neptune Analytics (Louvain communities, peer similarity, product adoption)
    │ What: Community size, peer count, adoption percentages, shared merchants
    │ Why: Peer insights build trust and increase conversion ("customers like you...")
    │
Stage 6: GENERATE REASONING (LLM)
    │ Model: Nova 2 Lite (bulk) / Nova Pro (flagship templates)
    │ What: AI-authored "why this, why now" explanation citing real numbers + peer stats
    │ Why: Explainability is a regulatory requirement AND a trust builder
    │ Validation: Numbers in output checked against input context
    │
Stage 7: PERSIST
    │ Target: Aurora (next_best_actions table)
    │ What: INSERT with dedup (customer + template + entity_ref)
    │ Why: Single source of truth for the delivery API
```

### 2.2.1 Pattern Scanner (pre-batch step)

```
EventBridge daily → Pattern Scanner Lambda
    │
    ├── Query 1: large_balance_idle
    │   SQL: balance > 4× monthly spend AND no fixed deposit
    │   Result: 156 customers flagged
    │
    ├── Query 2: savings_rate_increasing
    │   SQL: savings rate (income-spend)/income increased >10% vs prior 30d
    │   Result: customers actively saving more
    │
    ├── Query 3: peer_product_gap (Neptune)
    │   Cypher: customer has no products but 3+ similar peers do
    │   Result: customers missing products their peers have
    │
    ├── Query 4: subscription_spike
    │   SQL: entertainment/recurring spend up >30% month-over-month
    │   Result: customers with growing subscriptions
    │
    └── Output: customer_signals table (consumed by batch generator)
```

### 2.3 Real-time Path — Stage by Stage

```
Stage 1: CUSTOMER MESSAGE
    │ Source: Alma chat (in-app)
    │ What: "I booked a flight to London on 15 June"
    │
Stage 2: ROUTER CLASSIFICATION (LLM)
    │ Model: Nova 2 Lite
    │ What: Classifies intent → "next_best_action"
    │ Why: Semantic understanding of natural language (not regex)
    │
Stage 3: NBA AGENT (LLM with tools)
    │ Model: Nova 2 Lite
    │ What: Detects life event, fetches context, selects template, generates reasoning
    │ Tools: persist_life_event, get_customer_context, get_nba_templates, persist_realtime_nba, execute_purchase
    │ Why: Unpredictable event context requires judgment about relevance and urgency
    │
Stage 4: PERSIST + RESPOND
    │ What: NBA written to Aurora + conversational response to customer
    │ Why: Recommendation appears on For You page AND customer gets immediate feedback
    │
Stage 5: EXECUTION (if customer confirms)
    │ What: Product purchase (debit account, create product record, receipt)
    │ Source: product_catalog for pricing, transaction module for execution
    │ Why: "Agentic" — the bank acts on the customer's behalf with consent
```

---

## 3. AWS Services & Features Used

### 3.1 Compute & Orchestration

| Service | Feature | Purpose | Why this service |
|---|---|---|---|
| **Amazon Bedrock** | Nova 2 Lite | Bulk NBA reasoning (batch + real-time) | Cheapest multimodal model with tool use; ~$0.0003/call |
| **Amazon Bedrock** | Nova Pro | Flagship NBA reasoning (home loan) | Better instruction compliance for peer stats; 3× cost of Lite |
| **Amazon Bedrock** | Claude Sonnet 4 | Alma specialist agents (banking, loan, KYC) | Best reasoning for complex multi-step workflows |
| **Bedrock AgentCore** | Runtime | Hosts Alma Graph Agent (Strands SDK) | Managed agent hosting with memory, observability, auto-scaling |
| **Strands Agents SDK** | Graph pattern | Multi-agent orchestration (router → specialists) | Deterministic routing with conditional edges; no uncontrolled cascades |
| **AWS Step Functions** | Standard + Map state | Batch orchestration (40 concurrent) | Built-in retry, per-customer error isolation, visual monitoring |
| **AWS Lambda** | Functions | Batch generator, FHS subscores, transaction module, KPI export | Serverless, pay-per-use, no idle cost at pilot scale |
| **Amazon EventBridge** | Scheduled rules | 6h NBA refresh, daily pattern scan, monthly FHS, daily KPI export | Managed cron with no infrastructure; targets Step Functions directly |

### 3.2 Data Stores

| Service | Feature | Purpose | Why this service |
|---|---|---|---|
| **Aurora MySQL Serverless v2** | Existing cluster (reused) | Operational OLTP: customers, accounts, transactions, NBAs, FHS, goals, products | Already exists; zero incremental cost; sub-10ms queries |
| **Neptune Database Serverless** | openCypher queries | Customer-360 knowledge graph: household, merchant relationships | Graph traversals (household detection, merchant communities) impossible in SQL |
| **Neptune Analytics** | Louvain, similarity | Peer community detection, customer similarity embeddings | Batch graph algorithms that produce the peer stats used in reasoning |
| **DynamoDB** | On-demand tables | Session routing, role config, rate limiting | Sub-5ms key-value lookups; serverless; no connection pooling needed |
| **Aurora** | customer_signals table | Behavioural signals from pattern scanner (large_balance_idle, savings_rate_increasing, peer_product_gap, subscription_spike) | Consumed by batch generator as additional eligibility context |
| **S3** | Standard storage | KPI CSV exports, data lake (Bronze), static frontend assets | Cheapest durable storage; serves as QuickSight data source |

### 3.3 AI/ML Features

| Service | Feature | Purpose | Why this feature |
|---|---|---|---|
| **Neptune Analytics** | Louvain communities | Classifies merchants as essential vs. discretionary (FHS Spending subscore) | Data-driven classification; self-maintaining as new merchants appear |
| **Neptune Analytics** | Customer similarity | Identifies 20 closest peers for each customer | Peer stats in reasoning ("Among 20 similar customers...") |
| **Neptune Analytics** | Product adoption edges | Tracks which products peers have purchased | "4.5% of similar customers explored lending" |
| **Bedrock Guardrails** | (planned) | Prompt injection defence, PII filtering | Regulatory requirement for production |

### 3.4 Security & Identity

| Service | Feature | Purpose | Why this service |
|---|---|---|---|
| **Amazon Cognito** | User pools (2) | Customer auth + Employee auth (with OIDC federation) | Managed auth with OAuth 2.0; supports SAML/OIDC federation |
| **IAM Identity Center** | SAML federation | Employee SSO via corporate AD | Single sign-on; AD group → role mapping |
| **AWS Managed Microsoft AD** | Directory | Source of truth for employee identities and groups | Existing corporate directory; syncs to Identity Center |
| **DynamoDB** | aibank-role-config | AD group → portal role mapping | Configurable without code changes; admin-friendly |
| **Amazon Federate** | OIDC (Midway) | Gates demo access to Amazon internal employees | Pre-approved template; no security review needed |
| **Lambda@Edge** | Viewer Request | Midway session check on every CloudFront request | Transparent auth gate; no page changes needed |
| **KMS** | CMKs | Encryption at rest for customer PII | Regulatory requirement; separate keys per data class |

### 3.5 Delivery & Frontend

| Service | Feature | Purpose | Why this service |
|---|---|---|---|
| **CloudFront** | Distribution | CDN for all static assets + API routing | Single domain; caching; Lambda@Edge support |
| **API Gateway HTTP** | Routes | REST API for session, NBA, FHS, preferences, QuickSight embed | Low-latency HTTP proxy to Lambda; CORS built-in |
| **QuickSight** | SPICE + Embedded | NBA Insights dashboard for employees | Managed BI; SPICE caches data (zero Aurora load); embedded SDK |
| **S3** | Static hosting | Frontend HTML/JS/CSS | Cheapest static hosting; versioned; CloudFront origin |

### 3.6 Observability

| Service | Feature | Purpose | Why this service |
|---|---|---|---|
| **AWS ADOT** | Auto-instrumentation | Traces every agent invocation, tool call, model call | Zero-code instrumentation via container CMD |
| **X-Ray** | Distributed tracing | End-to-end trace waterfall (request → router → agent → tools → response) | Correlates across Lambda, AgentCore, Aurora |
| **CloudWatch** | Logs + Metrics | Structured logs with trace_id on every line | Native integration; 7-year retention for audit |
| **QuickSight** | Dashboard | 8 KPI tiles for NBA platform health | Embedded in employee portal; daily SPICE refresh |

---

## 4. Key Design Decisions

| Decision | Rationale | Alternative considered |
|---|---|---|
| **Rules + LLM (no ML at MVP)** | Launch with 0 interaction data; ML needs 3+ months of feedback | Pure ML (needs training data), Pure rules (no personalisation) |
| **Neptune for peer stats** | Graph algorithms discover communities from data; self-maintaining | Hardcoded merchant lists (brittle), SQL aggregates (can't do community detection) |
| **Nova 2 Lite for bulk** | $0.0003/call; sufficient for 2-3 sentence reasoning | Sonnet 4 (10× cost, overkill for short text) |
| **Nova Pro for flagship** | Better instruction compliance (peer stats always included) | Nova 2 Lite (cheaper but ~70% compliance) |
| **Strands Graph for Alma** | Deterministic routing + specialist agents; observable; testable | Single monolithic agent (harder to debug), Step Functions (adds latency) |
| **SPICE for QuickSight** | Zero Aurora load for dashboard queries; sub-second response | Direct Aurora query (production load risk), Athena (cold start latency) |
| **DynamoDB for role config** | Admin-configurable without code deployment | Hardcoded in Lambda (requires redeploy for new roles) |
| **Query-time expiry** | Zero infrastructure for expiration; just SQL WHERE clause | Cron job to mark expired (extra compute, race conditions) |
| **DB-level unique index for dedup** | UNIQUE(customer_id, template_id, status) + INSERT IGNORE prevents race conditions from parallel LLM tool calls | Application-only dedup (race condition when LLM calls tool twice in same response) |

---

## 5. Solution Pros

### 5.1 Personalisation at scale without ML training
Every customer gets unique, grounded recommendations without needing a trained propensity model. The rules engine + LLM reasoning combination means you can launch with 0 historical interaction data. At 400K customers, this generates 1.2M personalised recommendations every 6 hours.

### 5.2 Explainability is built-in, not bolted on
Every recommendation carries a traceable audit trail: which rule fired, what data was used, what the LLM reasoned, what peer stats supported it. Satisfies regulatory requirements (right-to-explanation) without a separate explainability system.

### 5.3 Graph-powered peer insights create genuine differentiation
Neptune Analytics provides community detection and peer similarity that no rule engine or basic ML model can replicate. "4.5% of customers like you explored lending" is a real stat computed from actual transaction graph patterns — builds trust and increases conversion.

### 5.4 Agentic execution closes the loop
Customers don't just see recommendations — they can act on them in the same conversation. "Yes, buy travel insurance" → real transaction, real debit, real receipt. This is what "agentic AI" means to customers.

### 5.5 Production-grade observability from day one
Every agent invocation is traced end-to-end (ADOT + X-Ray). Every tool call is logged. Every NBA is auditable. The backend team can debug any customer issue by searching a trace_id.

---

## 6. Solution Cons

### 6.1 LLM cost scales linearly
Every customer × 3 NBAs × 4 runs/day = 4.8M LLM calls/day at 400K. At Nova 2 Lite pricing: ~$1,440/month. Add Nova Pro for flagship: ~$1,940/month. A trained ML model would score all 400K for ~$50/month. The LLM approach is 40× more expensive at scale.

### 6.2 LLM non-determinism creates inconsistency
Same customer, same data → slightly different reasoning text each run. ~20% of NBAs may have missing peer insights or generic reasoning. Customer sees "84% of peers" today, refreshes tomorrow and sees no peer stat. At scale, this inconsistency erodes trust.

### 6.3 Neptune query per customer doesn't scale to 400K in 4 hours
400K Neptune queries at ~100 queries/second = 67 minutes just for graph context. Add LLM calls: total batch time ~2 hours. Within the 4-hour window but with no margin. Any throttling pushes it over.

### 6.4 Tool-call reliability is probabilistic
The LLM calls `persist_realtime_nba` ~80-95% of the time (improved with prompt tuning). The remaining 5-20%, the recommendation exists only in the chat response but not on the For You page. Not 100% deterministic.

### 6.5 Cold start latency on first invocation
AgentCore runtime takes 3-5 seconds to cold start after 15 minutes of inactivity. First customer message after idle period gets "I couldn't get a response" and must retry.

---

## 7. Improvement Plan

### 7.1 Short-term (v1.1 — next 4 weeks)

| Improvement | Impact | Effort |
|---|---|---|
| **Deterministic PersistNBANode** | 100% persistence reliability (no LLM dependency) | 2-3 days |
| **Pre-computed peer stats** | Materialize Neptune stats in Aurora column; eliminate per-customer graph query | 2 days |
| **Template-based reasoning with variable substitution** | Deterministic text for batch; LLM only for novel real-time events | 3 days |
| **Provisioned concurrency for AgentCore** | Eliminate cold starts | 1 day (config change) |
| **More NBA templates** (20+) | Diverse recommendations across all 7 categories | 2 days |

### 7.2 Medium-term (v1.2 — 8 weeks)

| Improvement | Impact | Effort |
|---|---|---|
| **Tier 2 ML scoring (SageMaker)** | Propensity model trained on interaction data; LLM only for top-1 | 4 weeks |
| **Kinesis streaming ingest** | Real-time transaction events → sub-30s NBA generation | 2 weeks |
| **Full Gold data lake** | Pre-aggregated features; eliminates Aurora load for batch | 3 weeks |
| **Trusted Identity Propagation** (full) | Per-user QuickSight with RLS; no anonymous fallback | 1 week |
| **Arabic localisation** | Bilingual reasoning text | 2 weeks |

### 7.3 Long-term (v2.0 — 6 months)

| Improvement | Impact | Effort |
|---|---|---|
| **Sublinear cost scaling** | ML scores 400K for $50/month; LLM only for top-1 flagship per customer | Included in v1.2 ML work |
| **Full 26-agent catalog** | Specialized agents for subscription audit, financial twin, multi-goal optimization | 3 months |
| **WhatsApp delivery** | Outbound NBA delivery + inbound document ingestion | Depends on Meta approval |
| **A/B testing framework** | Reasoning variants, CTA copy, category mix experiments | 2 weeks |
| **Amazon Q in QuickSight** | Employees ask natural language questions about NBA performance | 1 week (config) |

---

## 8. Cost Model

### 8.1 Current (291 customers, pilot)

| Component | Monthly cost |
|---|---|
| Bedrock (Nova 2 Lite + Pro) | ~$15 |
| Neptune Serverless (1-8 NCU) | ~$76 |
| Aurora (incremental) | ~$0 (reused) |
| Lambda + Step Functions | ~$5 |
| QuickSight (SPICE) | ~$30 |
| CloudFront + S3 | ~$5 |
| **Total** | **~$131/month** |

### 8.2 Production (400K customers)

| Component | Monthly cost |
|---|---|
| Bedrock (Nova 2 Lite + Pro + Sonnet 4) | ~$1,940 |
| Neptune (4-32 NCU + Analytics) | ~$980 |
| Aurora (read replica) | ~$800 |
| SageMaker (Tier 2 ML, v1.2+) | ~$500 |
| Lambda + Step Functions | ~$400 |
| ElastiCache Redis (Gate 1+) | ~$600 |
| QuickSight | ~$100 |
| **Total** | **~$5,320/month (~$0.16/customer/year)** |

---

## 9. Automated Daily Pipeline

### 9.1 Pipeline Overview

The NBA platform runs a fully automated daily pipeline that refreshes all customer recommendations. The pipeline is orchestrated by AWS Step Functions, triggered by EventBridge at 6:00 AM UTC (9:00 AM Bahrain).

```
EventBridge (daily 6AM UTC)
    │
    ▼
Step Function: aibank-nba-daily-pipeline
    │
    ├── Step 1-4: Neptune Enrichment Lambda
    │     ├── SYNC: Aurora → Neptune (customers, products, goals)
    │     ├── ANALYTICS: Create SIMILAR_TO edges (5+ shared merchants + same band)
    │     ├── ENRICH: Template markers + behavioural signal detection
    │     └── MATERIALIZE: Peer stats on Customer nodes
    │
    ├── GetCustomerList: Fetch all active customer IDs
    │
    ├── BatchGenerate: Map(40 concurrent) per customer
    │     └── For each: context + signals + Neptune peer stats → Nova Pro → persist NBA
    │
    └── ReportSuccess/Failure: Write status to DynamoDB + S3 CSV → QuickSight SPICE
```

### 9.2 Pipeline Components

| Component | Service | ARN |
|---|---|---|
| Orchestrator | Step Functions | `arn:aws:states:eu-west-1:519124228967:stateMachine:aibank-nba-daily-pipeline` |
| Trigger | EventBridge Rule | `arn:aws:events:eu-west-1:519124228967:rule/aibank-nba-daily-pipeline-trigger` |
| Enrichment (Steps 1-4) | Lambda | `arn:aws:lambda:eu-west-1:519124228967:function:aibank-nba-neptune-enrichment` |
| NBA Generation | Lambda | `arn:aws:lambda:eu-west-1:519124228967:function:aibank-nba-batch-generator` |
| Status Table | DynamoDB | `arn:aws:dynamodb:eu-west-1:519124228967:table/aibank-pipeline-runs` |
| Status CSV | S3 | `s3://aibank-ui-prod-eu-west-1/data/pipeline-runs.csv` |
| Schedule | EventBridge cron | `cron(0 6 * * ? *)` — daily 6:00 AM UTC |
| Execution Role | IAM | `arn:aws:iam::519124228967:role/aibank-nba-sfn-role` |
| EB → SFN Role | IAM | `arn:aws:iam::519124228967:role/aibank-nba-eventbridge-sfn-role` |

### 9.3 Neptune Enrichment Pipeline (Steps 1-4)

| Step | What it does | Data source | Output |
|---|---|---|---|
| **1. SYNC** | Syncs customer properties (FHS, income, balance, products, goals) from Aurora to Neptune | Aurora → Neptune | 291 Customer nodes updated |
| **2. ANALYTICS** | Creates SIMILAR_TO edges between customers sharing 5+ merchants AND same income/FHS band | Neptune graph traversal | ~59,000 SIMILAR_TO edges |
| **3. ENRICH** | Sets template markers + detects behavioural signals from graph properties | Neptune queries only | Template markers + ~773 signals |
| **4. MATERIALIZE** | Computes per-customer peer stats for each NBA template | Neptune SIMILAR_TO traversal | 6 peer stats per Customer node |

#### Materialized Peer Stats (on Customer nodes)

| Property | Meaning | Used by template |
|---|---|---|
| `peer_count` | Number of SIMILAR_TO peers | All templates |
| `peer_pct_home_loan` | % of similar peers with home loan | Home Loan |
| `peer_pct_products` | % of similar peers with any product | Fixed Deposit, Travel Insurance |
| `peer_avg_merchants` | Avg merchant count among peers | Subscription Review |
| `peer_pct_goals` | % of similar peers with savings goals | Goal Saver |
| `peer_pct_high_balance` | % of similar peers with balance > 5K | Fixed Deposit, Alerts |

#### Behavioural Signals Detected (Step 3)

| Signal | Detection logic (Neptune) | Triggers template |
|---|---|---|
| `large_balance_idle` | `balance_to_income_ratio >= 4` AND no FD product | Fixed Deposit |
| `subscription_heavy` | `merchant_count >= 6` | Subscription Review |
| `peer_product_gap` | 2+ SIMILAR_TO peers have products, customer doesn't | Home Loan, Fixed Deposit |

### 9.4 Failure Handling & Monitoring

| Scenario | Handling |
|---|---|
| Lambda timeout | Retry 2× with 30s backoff (Step Function Retry) |
| Neptune query failure | Catch → ReportFailure → DynamoDB status=FAILED |
| Batch generator error | Map state isolates per-customer failures |
| Full pipeline failure | Step Function enters FAIL state, status written to DynamoDB |

**CRM team visibility:** Pipeline status is exported to S3 CSV after each run, ingested by QuickSight SPICE, and displayed on the NBA Insights dashboard showing:
- Last run status (SUCCESS/FAILED)
- Run date/time
- NBAs generated count
- Customers processed
- Duration
- Error details (if failed)

### 9.5 Pipeline Performance

| Metric | Value (291 customers) | Projected (400K customers) |
|---|---|---|
| Enrichment (Steps 1-4) | ~9 seconds | ~5 minutes (batch Neptune writes) |
| NBA Generation | ~90 seconds (40 concurrent) | ~45 minutes (500 concurrent) |
| Total pipeline | ~100 seconds | ~50 minutes |
| NBAs generated | 1,192 (4.1 per customer) | ~1.6M |
| SIMILAR_TO edges | 59,474 | ~8M (scales quadratically with shared merchants) |

## 10. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        AI BANK NBA PLATFORM                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  CUSTOMER CHANNELS          EMPLOYEE CHANNELS                           │
│  ┌──────────────┐          ┌──────────────────┐                        │
│  │ Mobile/Web   │          │ RM Portal        │                        │
│  │ • For You    │          │ • NBA Insights   │                        │
│  │ • Alma Chat  │          │ • QuickSight     │                        │
│  │ • FHS Page   │          │ • NBA Advisor    │                        │
│  └──────┬───────┘          └────────┬─────────┘                        │
│         │                           │                                    │
│  ┌──────▼───────────────────────────▼─────────┐                        │
│  │         CloudFront + Lambda@Edge            │                        │
│  │         (Midway gate + CDN)                 │                        │
│  └──────────────────┬──────────────────────────┘                        │
│                     │                                                    │
│  ┌──────────────────▼──────────────────────────┐                        │
│  │         API Gateway (alma-public-api)        │                        │
│  │  /sessions/* /banking/chat /nba/* /employee/*│                        │
│  └──────┬──────────────┬───────────────┬───────┘                        │
│         │              │               │                                 │
│  ┌──────▼──────┐ ┌────▼────────┐ ┌───▼──────────┐                     │
│  │ Session API │ │ Alma Agent  │ │ QuickSight   │                      │
│  │ (Lambda)    │ │ (AgentCore) │ │ Embed Lambda │                      │
│  └─────────────┘ └──────┬──────┘ └──────────────┘                     │
│                          │                                               │
│  ┌───────────────────────▼───────────────────────────────┐              │
│  │  STRANDS GRAPH AGENT (alma_graph-vZ5NGFDphP)           │              │
│  │                                                         │              │
│  │  router (Nova 2 Lite) → banking | kyc | loan | faq     │              │
│  │                        → next_best_action (NBA Agent)   │              │
│  │                                                         │              │
│  │  NBA Agent tools:                                       │              │
│  │    • persist_life_event    • get_customer_context       │              │
│  │    • persist_realtime_nba  • get_nba_templates          │              │
│  │    • execute_purchase      • list_customer_nbas         │              │
│  │    • get_financial_health_score                         │              │
│  └─────────────────────────────────────────────────────────┘              │
│                          │                                               │
│  ┌───────────────────────▼───────────────────────────────┐              │
│  │  BATCH PIPELINE (Step Functions + EventBridge)          │              │
│  │                                                         │              │
│  │  Every 6h: List customers → Map(40) → per customer:    │              │
│  │    gather context → evaluate rules → rank → reason →   │              │
│  │    persist                                              │              │
│  │                                                         │              │
│  │  Monthly: FHS computation (6 subscores, Neptune-powered)│              │
│  │  Daily: Cash-flow scanner, KPI export                   │              │
│  └─────────────────────────────────────────────────────────┘              │
│                          │                                               │
│  ┌───────────────────────▼───────────────────────────────┐              │
│  │  DATA LAYER                                             │              │
│  │                                                         │              │
│  │  Aurora MySQL: customers, accounts, transactions,       │              │
│  │    next_best_actions, nba_templates, nba_interactions,  │              │
│  │    customer_financial_health, customer_products,        │              │
│  │    product_catalog, nba_suppressions, execution_audit   │              │
│  │                                                         │              │
│  │  Neptune: Customer-360 graph (Louvain communities,      │              │
│  │    peer similarity, product adoption, household edges)  │              │
│  │                                                         │              │
│  │  DynamoDB: sessions, role-config, rate-limits           │              │
│  └─────────────────────────────────────────────────────────┘              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 11. Purchase Execution Flow

### 11.1 In-Chat Purchase (Real-time)

```
Customer: "I am travelling to London on May 25th"
    │
    ▼ NBA Agent (Nova Pro)
    ├── persist_life_event → customer_life_events
    ├── get_customer_context_for_nba
    ├── get_nba_templates
    ├── persist_realtime_nba → next_best_actions (dedup: UNIQUE index)
    └── Response: "Would you like me to set up travel insurance for BHD 12?"
    │
Customer: "yes"
    │
    ▼ NBA Agent (confirmation detected)
    └── execute_purchase → aibank-transaction-module Lambda
        ├── Lookup price from product_catalog
        ├── Debit account (INSERT transaction + UPDATE balance)
        ├── Create product record (INSERT customer_products)
        └── Return receipt_id + transaction_id + new_balance
```

### 11.2 For You Page Purchase (CTA → Alma Chat)

```
Customer clicks "Purchase Now" on For You page
    │
    ▼ Frontend (for-you.html)
    └── Navigate to: /banking/alma-chat.html?prompt=I+was+recommended+travel+insurance...
    │
    ▼ Alma Chat (alma-chat.html)
    ├── Read URL param (?prompt=...)
    ├── Retry loop (500ms × 10) until sendMessage is ready
    └── Auto-send: "I was recommended travel insurance for my upcoming trip..."
    │
    ▼ Alma Agent
    └── Asks confirmation → Customer says "yes" → execute_purchase
```

### 11.3 Action Completed Badge

The "✅ Action Completed" badge appears on For You cards when the customer has already purchased the associated product.

| Component | Logic |
|---|---|
| **Data source** | `customer_products` table (product_type, status='active') |
| **Matching** | NBA `product_type` column OR fallback `template_id → product_type` mapping |
| **Frontend** | `a.actioned` flag → renders green badge in tile header |

Template → Product mapping:
| Template | Product type |
|---|---|
| `opportunity.travel_insurance_on_trip` | `travel_insurance_international` |
| `opportunity.fixed_deposit` | `fixed_deposit` |
| `opportunity.goal_saver_for_child` | `goal_saver` |
| `wellness.salary_day_allocation` | `salary_allocation` |

### 11.4 Purchase Confirmation Rule

The NBA agent NEVER executes a purchase on the first message. Flow:
1. Customer expresses intent ("I want to buy", "set it up") → Agent asks: "I can set up X for BHD Y. Shall I proceed?"
2. Customer confirms with short reply ("yes", "go ahead", "sure") → Agent calls `execute_purchase`

### 11.5 Transaction Module

| Lambda | `aibank-transaction-module` |
|---|---|
| Actions | `purchase`, `disburse_loan`, `debit`, `credit` |
| Free products | Creates `customer_products` record (no debit) |
| Paid products | Debits account + creates `customer_products` + returns receipt |
| Price lookup | Reads from `product_catalog` table (no hardcoded prices) |

Available products (from `product_catalog`):
| Product type | Price | Category |
|---|---|---|
| `travel_insurance_regional` | BHD 8 | opportunity |
| `travel_insurance_international` | BHD 12 | opportunity |
| `goal_saver` | Free | opportunity |
| `salary_allocation` | Free | wellness |
| `fixed_deposit` | BHD 500 min | opportunity |
| `credit_card_classic` | Free (1st year) | opportunity |
| `credit_card_gold` | BHD 25/year | opportunity |
| `life_insurance_basic` | BHD 15/month | security |
| `bnpl_split_pay` | Free | wellness |

## 12. Deduplication Strategy

### 12.1 Real-time NBA Dedup

| Layer | Mechanism |
|---|---|
| **Application** | SELECT before INSERT (catches most cases) |
| **Database** | UNIQUE INDEX on `(customer_id, template_id, status)` |
| **INSERT** | `INSERT IGNORE` (silently skips on duplicate key) |
| **Verification** | Post-INSERT check — if skipped, finds existing and returns "refreshed" |

This prevents duplicates even when the LLM calls `persist_realtime_nba` multiple times in parallel within the same invocation.

### 12.2 Batch NBA Dedup

Batch generator deletes all `source='rule'` NBAs before regenerating. No dedup needed — full refresh every run.

## Document History



| Version | Date | Changes |
|---|---|---|
| 1.0 | 13 May 2026 | Initial — two-path architecture (batch + real-time) |
| 2.0 | 15 May 2026 | Full implementation: Strands Graph Agent, Neptune peer insights, transaction module, Identity Center SSO, QuickSight embedded, Lambda@Edge Midway gate, product catalog, explainability panel |
| 2.1 | 15 May 2026 | Pattern scanner, Fixed Deposit template, DB-level dedup, purchase confirmation, actioned badge, Nova Pro |
| 2.2 | 15 May 2026 | Automated daily pipeline, Neptune enrichment, merged pattern scanner, materialized peer stats, pipeline status reporting |
| 2.3 | 15 May 2026 | Purchase execution flow (in-chat + For You CTA), Action Completed badge, transaction module fix (free products), dedup strategy (UNIQUE index + INSERT IGNORE), purchase confirmation rule, For You → Alma prefill (URL param + retry), `<response>` tag stripping, salary_allocation product |
