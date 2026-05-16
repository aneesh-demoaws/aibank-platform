# Customer 360 — Business Requirements & Solution Design
## AI Bank Employee-Facing RM Portal

| Attribute | Value |
|---|---|
| **Version** | 1.0 |
| **Date** | 15 May 2026 |
| **Status** | Design |
| **Stakeholders** | Relationship Managers, Branch Managers, CRM Team |
| **Region** | AWS eu-west-1 |

---

## 1. Executive Summary

Customer 360 is an AI-powered, employee-facing portal that gives Relationship Managers (RMs) a complete, actionable view of each customer — combining financial data, behavioural insights, graph-powered relationships, predictive analytics, and agentic AI recommendations in a single screen.

Unlike traditional CRM dashboards that show static data, this Customer 360 is **agentic** — it proactively surfaces insights, answers RM questions in natural language, runs what-if scenarios, and suggests next actions with supporting evidence from the customer's peer network.

**Key differentiators from legacy C360:**
- **Agentic AI Advisor**: RM asks "Why is this customer's FHS declining?" → AI agent queries graph + transactions + history and provides a grounded answer
- **Neptune Graph Visualization**: Interactive relationship map showing household, merchant connections, peer cluster, and product adoption paths
- **What-If Scenario Engine**: "What if we offer this customer a home loan at 4.5%?" → instant impact simulation on FHS, debt ratio, and peer comparison
- **QuickSight Embedded Analytics**: Real-time KPIs, spending trends, and cohort comparisons embedded in the portal
- **Amazon Q in QuickSight**: Natural language questions about customer data ("Show me customers with declining FHS in this RM's portfolio")

---

## 2. Research Insights

### 2.1 Industry Trends (McKinsey, BCG, Finantrix 2024-2026)

| Trend | Insight | Our approach |
|---|---|---|
| **Agentic RM productivity** | Banks using agentic AI see 3-15% higher revenue per RM and 20-40% lower cost to serve (McKinsey 2026) | AI Advisor agent embedded in C360 |
| **Graph-powered relationships** | "Customer 360 seeks to change the dynamic by aligning core, digital, and engagement layers" (Finacle) | Neptune graph as the unifying layer |
| **Proactive not reactive** | "AI agents perceive context, adapt, and act with autonomy throughout the client lifecycle" (BearingPoint) | Signals + NBA + life events surfaced proactively |
| **CLV increase** | "Customer lifetime value increases of 25-30% driven by enhanced retention and cross-selling" (BankingDive) | What-if analysis shows CLV impact of each action |
| **Real-time intelligence** | "Some use cases require real-time intelligence for product recommendations and predicting customer needs" (Oracle) | Neptune materialized stats + streaming signals |

### 2.2 AWS Reference Architecture

Based on AWS blog "Building a customer 360 knowledge repository with Amazon Neptune and Amazon Redshift":
- Neptune as the **knowledge graph** (relationships, communities, product adoption)
- Aurora as the **operational store** (transactions, accounts, FHS)
- QuickSight as the **analytics layer** (embedded dashboards + Q for NL queries)
- Bedrock as the **intelligence layer** (agentic reasoning, summarization)

---

## 3. Business Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-01 | **Unified Customer Profile**: Single screen showing demographics, accounts, balances, KYC status, tenure | P0 |
| FR-02 | **Financial Health Score**: FHS ring with 6 subscores, trend (30d), peer percentile, improvement actions | P0 |
| FR-03 | **Active Recommendations (NBA)**: All active NBAs with status, reasoning, interaction history | P0 |
| FR-04 | **Life Events Timeline**: Detected events (travel, baby, job change) with dates and triggered actions | P0 |
| FR-05 | **Transaction Intelligence**: Spending by category, trends, anomalies, recurring patterns | P0 |
| FR-06 | **Neptune Graph View**: Interactive visualization of customer's relationship network (household, merchants, peers, products) | P1 |
| FR-07 | **AI Advisor (Agentic Chat)**: RM asks questions about the customer → AI agent provides grounded answers from all data sources | P1 |
| FR-08 | **What-If Scenario Engine**: Simulate product offers (loan, FD, insurance) → show impact on FHS, debt ratio, CLV | P1 |
| FR-09 | **QuickSight Embedded Dashboard**: Portfolio-level KPIs, cohort comparison, trend analysis | P1 |
| FR-10 | **Amazon Q Search Bar**: Natural language queries across the RM's portfolio ("customers with FHS < 60") | P1 |
| FR-11 | **Product Holdings & History**: All products owned, purchase history, receipts | P0 |
| FR-12 | **Loan Applications**: Status, history, documents, decisions | P0 |
| FR-13 | **Customer Goals**: Active goals, progress, target dates | P0 |
| FR-14 | **Behavioural Signals**: Pattern scanner outputs (idle balance, subscription spike, peer gap) | P1 |
| FR-15 | **Peer Comparison Panel**: How this customer compares to their Neptune community (FHS, spend, products) | P1 |

### 3.2 Non-Functional Requirements

| ID | Requirement |
|---|---|
| NFR-01 | Page load < 2 seconds (profile + FHS + NBAs) |
| NFR-02 | Graph visualization renders < 3 seconds (up to 50 nodes) |
| NFR-03 | AI Advisor responds < 8 seconds |
| NFR-04 | What-if simulation < 3 seconds |
| NFR-05 | Role-based access: RM sees only their assigned customers |
| NFR-06 | Audit trail: every RM action logged |
| NFR-07 | SSO via Identity Center (same as NBA employee portal) |

---

## 4. Solution Architecture

### 4.1 High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CUSTOMER 360 — RM PORTAL                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  FRONTEND (Single Page — customer-360/detail.html)                   │    │
│  │                                                                       │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │    │
│  │  │ Profile  │ │   FHS    │ │   NBA    │ │  Graph   │ │ AI Chat  │  │    │
│  │  │ Panel    │ │ Ring +   │ │ Active + │ │ Neptune  │ │ Advisor  │  │    │
│  │  │          │ │ Subscores│ │ History  │ │ Viz      │ │ (Agent)  │  │    │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │    │
│  │                                                                       │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────────────┐   │    │
│  │  │ Spending │ │ Life     │ │ What-If  │ │ QuickSight Embedded  │   │    │
│  │  │ Trends   │ │ Events   │ │ Scenario │ │ Dashboard + Q Bar    │   │    │
│  │  │ (Charts) │ │ Timeline │ │ Engine   │ │                      │   │    │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────────────────┘   │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│  ┌─────────────────────────────────▼───────────────────────────────────┐    │
│  │  API LAYER (Lambda: aibank-c360-api)                                 │    │
│  │                                                                       │    │
│  │  GET /c360/customers          — Portfolio list with summary          │    │
│  │  GET /c360/detail?id=X        — Full 360 view                       │    │
│  │  GET /c360/graph?id=X         — Neptune graph data (nodes + edges)  │    │
│  │  POST /c360/chat              — AI Advisor (agentic)                 │    │
│  │  POST /c360/what-if           — Scenario simulation                 │    │
│  │  GET /c360/quicksight-url     — Embedded dashboard URL              │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│  ┌─────────────────────────────────▼───────────────────────────────────┐    │
│  │  INTELLIGENCE LAYER                                                   │    │
│  │                                                                       │    │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │    │
│  │  │ AI Advisor Agent│  │ What-If Engine  │  │ Amazon Q in QS     │ │    │
│  │  │ (Bedrock Nova   │  │ (Lambda +       │  │ (Generative BI     │ │    │
│  │  │  Pro / Sonnet)  │  │  Bedrock)       │  │  NL queries)       │ │    │
│  │  │                 │  │                 │  │                     │ │    │
│  │  │ Tools:          │  │ Simulates:      │  │ Answers:            │ │    │
│  │  │ • query_aurora  │  │ • FHS impact    │  │ • Portfolio queries │ │    │
│  │  │ • query_neptune │  │ • Debt ratio    │  │ • Cohort analysis   │ │    │
│  │  │ • get_fhs       │  │ • CLV change    │  │ • Trend questions   │ │    │
│  │  │ • get_nbas      │  │ • Peer compare  │  │                     │ │    │
│  │  │ • get_signals   │  │                 │  │                     │ │    │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│  ┌─────────────────────────────────▼───────────────────────────────────┐    │
│  │  DATA LAYER                                                           │    │
│  │                                                                       │    │
│  │  Aurora MySQL          Neptune Analytics       DynamoDB               │    │
│  │  • customers           • Customer nodes        • sessions             │    │
│  │  • accounts            • SIMILAR_TO edges      • role-config          │    │
│  │  • transactions        • HAS_PRODUCT edges     • pipeline-runs        │    │
│  │  • customer_fhs        • TRANSACTS_WITH        │                      │    │
│  │  • next_best_actions   • community_id          │                      │    │
│  │  • customer_products   • peer stats (mat.)     │                      │    │
│  │  • customer_signals    • HAS_GOAL edges        │                      │    │
│  │  • life_events         │                       │                      │    │
│  │  • loan_applications   │                       │                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Component Design

#### 4.2.1 AI Advisor — Amazon Quick Suite Embedded Chat Agent

The AI Advisor uses **Amazon Quick Suite Custom Chat Agent** embedded directly in the C360 portal. This provides a fully managed agentic chat that queries structured data, searches documents, and triggers actions — all under the same IAM Identity Center SSO session.

**Why Quick Suite Chat Agent (not custom Bedrock agent):**
- Same license as QuickSight embedded dashboard (no additional cost)
- Same IAM Identity Center integration (crm@demoaws.com already provisioned)
- Zero agent code — configured via Quick Suite console
- Can query QuickSight datasets (structured) + Spaces (documents) + Actions (workflows)
- Same QuickSight Embedding SDK (v2.10+) already used for NBA Insights

**Identity Flow (single SSO):**
```
crm@demoaws.com → AD → IAM Identity Center (ssoins-68040e1f934f09da)
                            ├── Employee Cognito Pool (SAML) → C360 Portal
                            ├── Amazon Quick Suite → Chat Agent
                            └── Amazon QuickSight → Embedded Dashboard + What-If
```

**Chat Agent Configuration:**
| Setting | Value |
|---|---|
| Name | C360 Advisor |
| Persona | "You are a Customer 360 advisor for AI Bank relationship managers. Help RMs understand customer financial health, recommend products, and explain peer comparisons." |
| Tone | Executive |
| Response format | Bullet points for lists, concise paragraphs for explanations |
| Knowledge sources | QuickSight datasets (customers, FHS, transactions, NBAs), Product catalog Space |
| Actions | Create follow-up task, Trigger NBA refresh, Flag for review |
| Reference docs | Product eligibility rules, FHS scoring methodology, NBA template descriptions |

**Example interactions:**
- "Why is Aneesh's FHS declining?" → Queries FHS dataset → "His savings subscore dropped from 65 to 55. Entertainment spend increased 40% in the last 60 days while savings rate dropped from 25% to 15%."
- "Which customers in my portfolio have FHS below 60?" → Returns table + chart from QuickSight dataset
- "What products should I recommend?" → Queries NBA dataset + product catalog → "Based on his idle balance, a Fixed Deposit is the strongest match. 96% of similar peers have significant idle balances."
- "Create a follow-up for next week" → Triggers action workflow

**Embedding (same SDK as NBA dashboard):**
```javascript
const { createEmbeddingContext } = QuickSightEmbedding;
const context = await createEmbeddingContext();
const chatAgent = await context.embedGenerativeQnA(container, {
    url: embedUrl,  // from GenerateEmbedUrlForRegisteredUser API
    height: '500px',
    width: '100%'
});
```

#### 4.2.2 Neptune Graph Visualization

Interactive graph rendered with D3.js/vis.js showing:
- **Center node**: The customer
- **Household ring**: Joint account holders, family members
- **Merchant ring**: Top merchants (sized by transaction volume)
- **Peer ring**: Top 5 similar customers (edge weight = shared merchants)
- **Product nodes**: Products owned (green) vs. recommended (amber)

RM can click nodes to drill down, see shared patterns, and understand why certain recommendations were made.

#### 4.2.3 What-If Scenario Engine

RM selects a scenario → engine calculates impact:

| Scenario | Inputs | Outputs |
|---|---|---|
| "Offer home loan" | Loan amount, rate, tenure | New debt ratio, FHS impact, monthly payment vs. income |
| "Offer fixed deposit" | Amount, rate, tenure | New savings subscore, FHS improvement, opportunity cost |
| "Customer loses job" | Income reduction % | FHS projection, at-risk products, recommended actions |
| "Increase credit limit" | New limit | Utilization change, credit subscore impact, risk assessment |

Uses Bedrock to generate a natural language summary: "If Aneesh takes a BHD 7,200 home loan at 4.5% for 20 years, his monthly payment would be BHD 45.5 (3% of income). His debt subscore would decrease from 90 to 82, but his overall FHS would remain in the 'good' band at 72."

#### 4.2.4 QuickSight Embedded Components

| Component | Purpose | Data source |
|---|---|---|
| **Portfolio Dashboard** | RM's customer portfolio KPIs (total AUM, avg FHS, NBA conversion rate) | SPICE (daily refresh) |
| **Customer Trend Chart** | Individual customer spending/income trend over 12 months | Direct query to Aurora |
| **Cohort Comparison** | This customer vs. segment average (FHS, spend, products) | SPICE |
| **Amazon Q Search Bar** | NL queries: "Show me customers with FHS below 60 and no active NBA" | QuickSight Q |

---

## 5. Data Model

### 5.1 Data Access Strategy

```
┌─────────────────────────────────────────────────────────────────────┐
│  QuickSight + Quick Chat Agent                                       │
│                                                                       │
│  ┌─────────────────────┐    ┌──────────────────────────────────┐    │
│  │ SPICE (cached)       │    │ Athena Federated Query (live)    │    │
│  │ • Portfolio KPIs     │    │ • Neptune graph via connector    │    │
│  │ • FHS trends         │    │ • Peer comparisons (real-time)   │    │
│  │ • NBA conversion     │    │ • Community insights             │    │
│  │ • Spending aggregates│    │ • Product adoption paths         │    │
│  │                       │    │                                  │    │
│  │ Source: Aurora        │    │ Source: Neptune Analytics         │    │
│  │ Refresh: Daily        │    │ Refresh: Live (per query)        │    │
│  └─────────────────────┘    └──────────────────────────────────┘    │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 Athena Neptune Connector

The Athena Neptune Connector is a pre-built Lambda that translates SQL into openCypher, enabling QuickSight and Quick Chat Agent to query the graph as a SQL table.

| Component | Value |
|---|---|
| Connector | `AthenaNeptuneConnector` (AWS Serverless Application Repository) |
| Lambda | Deployed in same VPC as Neptune |
| Catalog name | `neptune_c360` |
| Connection | Neptune Analytics endpoint (`g-ruhyz8aj39`) |

**Example Athena queries over Neptune:**
```sql
-- Peer comparison for a customer's community
SELECT customer_id, peer_count, peer_pct_home_loan, peer_avg_merchants
FROM neptune_c360.graph.customer_nodes
WHERE community_id = 13

-- Product adoption in peer group
SELECT product_name, count(*) as adopters
FROM neptune_c360.graph.has_product_edges
WHERE source_community = 13
GROUP BY product_name

-- Similar customers
SELECT target as peer_id, score as similarity
FROM neptune_c360.graph.similar_to_edges
WHERE source = 'CUST20250100'
ORDER BY score DESC LIMIT 10
```

### 5.3 Aurora Tables (from NBA project — no new tables needed)

| Table | Key fields for C360 |
|---|---|
| `customers` | Profile, KYC, tenure, demographics |
| `accounts` | Balances, types, status |
| `transactions` | Full history, categories, merchants |
| `customer_financial_health` | FHS score, 6 subscores, trend, peer percentile |
| `next_best_actions` | Active NBAs, reasoning, status, interaction count |
| `customer_life_events` | Detected events, dates, status |
| `customer_products` | Owned products, purchase dates |
| `customer_goals` | Goals, progress, targets |
| `customer_signals` | Behavioural signals from pattern scanner |
| `loan_applications` | Loan history, status, decisions |
| `product_catalog` | Available products, pricing |

### 5.4 Neptune Graph (enriched daily, queried via Athena + direct API)

| Node/Edge | Properties | Accessed by |
|---|---|---|
| `Customer` | fhs_score, income_band, balance, peer_count, peer_pct_*, community_id, merchant_count | Athena + C360 API |
| `SIMILAR_TO` | score (shared merchants) | Athena + Graph Viz |
| `TRANSACTS_WITH` | (customer → merchant) | Athena + Graph Viz |
| `HAS_PRODUCT` | (customer → product) | Athena + Graph Viz |
| `HAS_GOAL` | (customer → goal) | Athena + Graph Viz |
| `JOINT_HOLDER` | (customer → account) | Athena + Graph Viz |

---

## 6. AWS Services

| Service | Feature | Purpose |
|---|---|---|
| **Amazon Quick Suite** | Custom Chat Agent | AI Advisor — agentic chat for RM questions about customers |
| **Amazon Quick Suite** | Embedded Chat SDK | Embed chat agent in C360 portal (same SDK as QuickSight) |
| **Amazon Quick Suite** | Spaces | Knowledge sources (product docs, policies, FHS methodology) |
| **Amazon Quick Suite** | Actions | Trigger workflows (follow-up tasks, NBA refresh, flag for review) |
| **Amazon QuickSight** | Embedded Dashboard | Portfolio KPIs, spending trends, cohort comparison |
| **Amazon QuickSight** | What-If Parameters | Interactive scenario sliders (loan amount, rate, tenure) |
| **Amazon QuickSight** | SPICE | Cached datasets for fast dashboard rendering |
| **Amazon Athena** | Federated Query | Neptune connector — enables QuickSight + Chat Agent to query graph as SQL |
| **Amazon Athena** | Neptune Connector | Pre-built Lambda translating SQL → openCypher for Neptune |
| **Neptune Analytics** | openCypher queries | Graph visualization data (direct API for D3.js panel) |
| **Aurora MySQL** | Serverless v2 | All operational data (reused from NBA project) |
| **IAM Identity Center** | SSO | Single sign-on for RM (same instance: ssoins-68040e1f934f09da) |
| **AWS Lambda** | Functions | C360 API, embed URL generation, what-if engine |
| **API Gateway** | HTTP API | REST endpoints for the portal |
| **CloudFront** | CDN | Static frontend + embed URL routing |
| **DynamoDB** | Role config | RM → customer assignment, RBAC (reused) |

**Key architectural decision:** Amazon Quick Suite provides the AI Advisor (chat agent), QuickSight provides the analytics (dashboards + what-if), and Neptune provides the graph (peer relationships). All three share the same IAM Identity Center authentication — one login, one license, full C360 experience.

---

## 7. Implementation Plan

### Phase 1 — Core C360 Portal + Data (2 days)
- [ ] Update C360 API Lambda to include FHS, NBAs, life events, products, signals from existing tables
- [ ] Update detail.html with all panels (profile, FHS ring, NBA cards, life events timeline, spending)
- [ ] Add peer comparison panel (read materialized Neptune stats)
- [ ] Deploy and test with crm@demoaws.com

### Phase 2 — Amazon Quick Suite Chat Agent (2 days)
- [ ] Create Custom Chat Agent "C360 Advisor" in Quick Suite console
- [ ] Configure persona, tone, response format
- [ ] Create Space with product catalog, FHS methodology docs, NBA template descriptions
- [ ] Link QuickSight datasets as knowledge sources (customers, FHS, transactions, NBAs)
- [ ] Configure actions (create follow-up, trigger NBA refresh)
- [ ] Share chat agent with crm@demoaws.com via IAM Identity Center
- [ ] Embed chat agent in C360 portal using Quick Suite Embedding SDK

### Phase 3 — QuickSight Dashboard + What-If (2 days)
- [ ] Create C360 QuickSight dashboard (portfolio KPIs, spending trends, cohort comparison)
- [ ] Add What-If parameters (loan amount slider, rate slider, tenure dropdown)
- [ ] Create calculated fields for FHS impact simulation
- [ ] Embed dashboard in C360 portal (same embed URL generation pattern as NBA Insights)
- [ ] Add QuickSight Q search bar for portfolio-level NL queries

### Phase 4 — Neptune Graph Visualization (1 day)
- [ ] Add `/c360/graph` endpoint (Neptune openCypher → nodes + edges JSON)
- [ ] Add D3.js force-directed graph panel in detail page
- [ ] Show: customer (center), merchants, peers, products, household
- [ ] Click-to-drill: click peer → see shared merchants, click product → see adoption rate

### Phase 5 — Polish + Deploy (1 day)
- [ ] RBAC: RM sees only assigned customers (DynamoDB mapping)
- [ ] Audit logging (RM actions tracked)
- [ ] Performance optimization (parallel API calls, lazy-load graph)
- [ ] Push to GitHub repo (use-cases/07-customer-360/)
- [ ] Update documentation

---

## 8. Wireframe — Customer Detail Page

```
┌─────────────────────────────────────────────────────────────────────────┐
│  ← Back to Portfolio    Customer 360: Aneesh Mohan    [AI Advisor 💬]   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─── PROFILE ────────────┐  ┌─── FINANCIAL HEALTH ──────────────────┐ │
│  │ Aneesh Mohan           │  │  ┌────┐                                │ │
│  │ CUST20250100           │  │  │ 75 │  Good                         │ │
│  │ aneesh@demoaws.com     │  │  └────┘                                │ │
│  │ +973 3817 5284         │  │  Debt: 90 │ Savings: 55 │ Spend: 85   │ │
│  │ Bahraini │ 30 months   │  │  Income: 88 │ Credit: 72 │ Behav: 68  │ │
│  │ KYC: Verified ✓        │  │  Trend: ↑3 (30d) │ Peer: 72nd pctl   │ │
│  │ Balance: BHD 27,071    │  │  ⚠️ Savings subscore declining         │ │
│  └────────────────────────┘  └────────────────────────────────────────┘ │
│                                                                          │
│  ┌─── ACTIVE RECOMMENDATIONS ────────────────────────────────────────┐  │
│  │ 🟢 Fixed Deposit (P70)     │ 🟡 Home Loan (P65)  │ 🔵 Alerts    │  │
│  │ "96% of peers have idle    │ "4% explored loans"  │ (P60)        │  │
│  │  balances like yours"      │                      │              │  │
│  │ [✅ Actioned] Travel Ins.  │ [Goal Saver ✅]      │              │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─── GRAPH ─────────────────┐  ┌─── WHAT-IF ────────────────────────┐ │
│  │                            │  │ Scenario: [Offer Home Loan ▼]      │ │
│  │    [Peer1]                 │  │ Amount: [7,200] Rate: [4.5%]       │ │
│  │       \                    │  │ Tenure: [20 years]                 │ │
│  │  [Lulu]--[ANEESH]--[Peer2]│  │                                    │ │
│  │       /       \            │  │ Impact:                            │ │
│  │  [Netflix]  [HomeLoan]     │  │  FHS: 75 → 72 (still Good)        │ │
│  │              (recommended) │  │  Debt ratio: 0% → 3%              │ │
│  │                            │  │  Monthly: BHD 45.5 (3% of income) │ │
│  └────────────────────────────┘  └────────────────────────────────────┘ │
│                                                                          │
│  ┌─── LIFE EVENTS ───────────┐  ┌─── SPENDING TRENDS ────────────────┐ │
│  │ 📅 May 15 — Travel: Goa   │  │  [QuickSight Embedded Chart]       │ │
│  │ 👶 Jul — Expecting baby    │  │  Monthly spend by category         │ │
│  │ 💼 May 15 — Promotion      │  │  ████ Groceries  BHD 320          │ │
│  │    Salary → BHD 2,500      │  │  ███  Shopping   BHD 280          │ │
│  └────────────────────────────┘  │  ██   Dining     BHD 180          │ │
│                                   └────────────────────────────────────┘ │
│                                                                          │
│  ┌─── AI ADVISOR ────────────────────────────────────────────────────┐  │
│  │ 💬 Ask about this customer...                              [Send] │  │
│  │                                                                    │  │
│  │ RM: "Why is his savings subscore low?"                            │  │
│  │ AI: "Aneesh's savings subscore is 55 because his balance-to-     │  │
│  │      income ratio (18x) is high but he has no structured savings  │  │
│  │      products. His idle balance of BHD 25,849 sits in a current  │  │
│  │      account earning 0%. Recommending a Fixed Deposit would       │  │
│  │      improve this subscore by ~15 points."                        │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
│  ┌─── QUICKSIGHT Q ─────────────────────────────────────────────────┐   │
│  │ 🔍 Ask about your portfolio: "customers with FHS below 60"       │   │
│  └───────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Pros & Cons

### Pros
1. **Single pane of glass**: RM sees everything without switching between 5 systems
2. **Agentic intelligence**: AI answers "why" questions that dashboards can't
3. **Graph-powered peer context**: "Customers like this one did X" — evidence-based selling
4. **What-if reduces risk**: RM can simulate before recommending, reducing bad offers
5. **Zero new data infrastructure**: Reuses all NBA project tables + Neptune graph

### Cons
1. **AI Advisor latency**: 5-8 seconds per response (acceptable for RM use, not customer-facing)
2. **Graph visualization complexity**: Large peer networks (200+ nodes) need filtering
3. **QuickSight Q accuracy**: NL queries may misinterpret ambiguous questions
4. **RBAC complexity**: RM-to-customer assignment needs a mapping table
5. **Cost**: QuickSight Q + embedded = ~$250/month for 10 RMs

---

## 10. Success Metrics

| Metric | Target | Measurement |
|---|---|---|
| RM time-to-insight | < 30 seconds (from 5+ minutes today) | Session analytics |
| NBA conversion rate | +15% (with C360 context vs. without) | A/B test |
| Customer retention | +5% for RM-managed customers | Quarterly cohort |
| RM satisfaction | > 4.2/5 | Survey |
| What-if usage | > 3 scenarios/RM/day | Usage logs |

---

## Document History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 15 May 2026 | Initial BRD + Solution Design |
