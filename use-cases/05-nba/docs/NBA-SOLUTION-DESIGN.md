# AI Bank — Next Best Action (NBA) Solution Design
## Complete Architecture for a "Wow" Product

**Version:** 1.0  
**Date:** 2026-02-26  
**Status:** Design Complete  
**Inspired by:** CommBank's Customer Engagement Engine (35M+ decisions/day)

---

## 1. Vision

> "Every interaction with AI Bank should feel like a conversation with a trusted financial advisor who knows you, your family, your goals, and your community."

NBA is not a recommendation engine. It's a **decision intelligence platform** that combines:
- **ML propensity scoring** (what the data says you'll do)
- **Graph relationship intelligence** (what your network reveals)
- **Agentic AI reasoning** (why this action, why now, explained in human terms)

The goal: make every one of AI Bank's customer touchpoints smarter — app notifications, RM conversations, email campaigns, and in-app "For You" experiences.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CONSUMER TOUCHPOINTS                         │
│  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Customer  │  │  RM Advisor  │  │  Email /  │  │  In-App "For  │  │
│  │ Dashboard │  │  Console     │  │  Push     │  │  You" Feed    │  │
│  └─────┬─────┘  └──────┬───────┘  └─────┬────┘  └──────┬────────┘  │
│        │               │                │               │           │
└────────┼───────────────┼────────────────┼───────────────┼───────────┘
         │               │                │               │
         ▼               ▼                ▼               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     API GATEWAY (eu-west-1)                         │
│              /nba/recommend    /nba/explain    /nba/batch            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    NBA ORCHESTRATION LAYER                           │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              NBA Lambda Proxy (eu-west-1)                    │   │
│  │  • Auth check (Cognito JWT)                                  │   │
│  │  • Route: fast path vs rich path vs batch                    │   │
│  │  • Contact policy enforcement                                │   │
│  │  • Response caching (ElastiCache)                            │   │
│  └──────────┬──────────────────────────────┬────────────────────┘   │
│             │                              │                        │
│    ┌────────▼────────┐          ┌──────────▼──────────┐            │
│    │   FAST PATH     │          │    RICH PATH         │            │
│    │   (~50ms)       │          │    (2-5 seconds)     │            │
│    │                 │          │                      │            │
│    │  Amazon         │          │  AgentCore Runtime   │            │
│    │  Personalize    │          │  (Strands + Claude)  │            │
│    │  Campaign       │          │                      │            │
│    │  NBA Recipe     │          │  8 MCP Tools via     │            │
│    │                 │          │  AgentCore Gateway   │            │
│    │  Returns:       │          │                      │            │
│    │  • Action IDs   │          │  Returns:            │            │
│    │  • Scores 0-1   │          │  • Ranked actions    │            │
│    │                 │          │  • Full reasoning    │            │
│    └────────┬────────┘          │  • Talking points    │            │
│             │                   │  • Risk factors      │            │
│             │                   └──────────┬───────────┘            │
│             │                              │                        │
└─────────────┼──────────────────────────────┼────────────────────────┘
              │                              │
              ▼                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       DATA & INTELLIGENCE LAYER                     │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────────┐    │
│  │   Aurora      │  │   Neptune     │  │   Amazon Personalize   │    │
│  │   (me-south-1)│  │  (me-south-1) │  │   (eu-west-1)          │    │
│  │              │  │              │  │                        │    │
│  │  • customers │  │  • Customer  │  │  • Item Interactions   │    │
│  │  • accounts  │  │    nodes     │  │  • Actions dataset     │    │
│  │  • txns      │  │  • Employer  │  │  • Action Interactions │    │
│  │  • 360 view  │  │    nodes     │  │  • Users dataset       │    │
│  │  • 360       │  │  • Product   │  │                        │    │
│  │    metrics   │  │    nodes     │  │  Recipe:               │    │
│  │  • insights  │  │  • Location  │  │  Next-Best-Action      │    │
│  │  • offers    │  │    nodes     │  │  (aws-next-best-action)│    │
│  │  • goals     │  │              │  │                        │    │
│  │              │  │  Edges:      │  │  Campaign:             │    │
│  │              │  │  WORKS_AT    │  │  aibank-nba-campaign   │    │
│  │              │  │  LIVES_IN    │  │                        │    │
│  │              │  │  HOLDS       │  │  Event Tracker:        │    │
│  │              │  │  TRANSFERS_TO│  │  Real-time feedback    │    │
│  │              │  │  FAMILY_OF   │  │                        │    │
│  └──────────────┘  └──────────────┘  └────────────────────────┘    │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    BATCH COMPUTE (Daily)                      │   │
│  │  EventBridge → Lambda → Compute 360 metrics for all customers│   │
│  │  EventBridge → Lambda → Export interactions → Personalize     │   │
│  │  EventBridge → Lambda → Sync Aurora → Neptune graph           │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. The Three Intelligence Engines

### 3.1 Engine 1: Amazon Personalize — ML Propensity Scoring

**Purpose:** Fast, scalable ML predictions. "Which actions will this customer most likely take?"

**Recipe:** `aws-next-best-action`
- Learns from item interactions (transactions) to understand customer interests
- Learns from action interactions (which customers took which banking actions)
- Returns ranked actions with propensity scores (0.0 to 1.0)
- Auto-updates every 2 hours with new interaction data
- Supports exploration (surfaces new/less-seen actions)

**Datasets:**

#### Item Interactions (from Aurora transactions)
```csv
USER_ID,ITEM_ID,TIMESTAMP,EVENT_TYPE,EVENT_VALUE
CUST00000001,MER_LULU_001,1706140800,Purchase,45.500
CUST00000001,MER_BAPCO_001,1706227200,Purchase,25.000
CUST00000001,SAL_EMPLOYER,1706313600,Salary,3500.000
```
- Maps to: 33,132 existing transactions
- USER_ID = customer_id, ITEM_ID = merchant_id or category, EVENT_TYPE = transaction_type
- Minimum: 1,000 interactions ✅ (we have 33K+)

#### Actions Dataset
```csv
ACTION_ID,ACTION_TYPE,PRODUCT_CATEGORY,VALUE,REPEAT_FREQUENCY,CREATION_TIMESTAMP
ACT001,cross_sell,premium_account,500,30,1704067200
ACT002,cross_sell,personal_loan,1000,60,1704067200
ACT003,cross_sell,savings_account,200,30,1704067200
ACT004,nudge,kyc_verification,100,14,1704067200
ACT005,engagement,auto_save_goal,150,30,1704067200
ACT006,advice,budget_alerts,50,14,1704067200
ACT007,engagement,referral,300,60,1704067200
ACT008,cross_sell,credit_card,800,60,1704067200
ACT009,cross_sell,business_account,400,30,1704067200
ACT010,advice,spending_insight,30,7,1704067200
ACT011,nudge,app_feature_discovery,20,14,1704067200
ACT012,cross_sell,investment_fund,600,60,1704067200
ACT013,advice,salary_advance,200,30,1704067200
ACT014,engagement,financial_health_check,100,30,1704067200
ACT015,cross_sell,insurance,700,60,1704067200
```
- 15 banking actions across 4 types: cross_sell, nudge, advice, engagement
- VALUE = business value weight (higher = more valuable to bank)
- REPEAT_FREQUENCY = days before re-recommending after interaction

#### Action Interactions (synthetic, based on customer profiles)
```csv
USER_ID,ACTION_ID,TIMESTAMP,EVENT_TYPE
CUST00000003,ACT001,1706140800,Taken
CUST00000005,ACT001,1706227200,Not taken
CUST00000010,ACT004,1706313600,Taken
CUST00000001,ACT008,1706400000,Viewed
```
- Generated from customer profiles: high-salary customers → Taken premium upgrade
- Customers with savings > threshold → Taken investment fund
- KYC PENDING customers → Taken/Not taken KYC verification
- Minimum: 50 Taken + 50 Not taken/Viewed within 6 weeks ✅

#### Users Dataset (from Aurora customers)
```csv
USER_ID,COUNTRY,NATIONALITY,SEGMENT,SALARY_BAND,ACCOUNT_AGE_MONTHS
CUST00000001,BH,Bahraini,MEDIUM_VALUE,3000_5000,24
CUST00000002,BH,Indian,HIGH_VALUE,5000_8000,18
```
- Enriches recommendations with demographic signals
- 83 customers with full profiles

**Personalize Output Example:**
```json
{
  "actionList": [
    {"actionId": "ACT001", "score": 0.87},
    {"actionId": "ACT008", "score": 0.72},
    {"actionId": "ACT005", "score": 0.65}
  ]
}
```


---

### 3.2 Engine 2: Amazon Neptune — Graph Relationship Intelligence

**Purpose:** Discover hidden relationships and community patterns that tabular data cannot reveal.

**Why Neptune, not just Aurora?**
Aurora answers: "What did THIS customer do?"
Neptune answers: "What did people CONNECTED to this customer do?"

This is the difference between a good recommendation and a *wow* recommendation.

**Graph Data Model (Property Graph, openCypher):**

```
┌─────────────┐     WORKS_AT      ┌─────────────┐
│  Customer    │─────────────────▶│  Employer    │
│             │                   │             │
│ id          │     LIVES_IN      │ name        │
│ name        │──────────┐        │ sector      │
│ segment     │          │        │ size        │
│ salary_band │          ▼        └─────────────┘
│ nationality │   ┌─────────────┐
└──────┬──────┘   │  Location   │
       │          │             │
       │          │ city        │
       │          │ country     │
       │          │ area        │
       │          └─────────────┘
       │
       │  HOLDS                    TRANSFERS_TO
       ▼                          ┌──────────────┐
┌─────────────┐                   │              │
│  Product    │    Customer ──────▶ Customer     │
│             │    (recurring      │ (detected    │
│ type        │     transfers)     │  household)  │
│ tier        │                   └──────────────┘
│ opened_date │
└─────────────┘         SIMILAR_PROFILE
                       ┌──────────────┐
                       │              │
               Customer ─────────────▶ Customer
               (same employer +       (peer group)
                same salary band +
                same location)
```

**Node Types:**

| Node | Properties | Source | Count |
|------|-----------|--------|-------|
| Customer | id, name, segment, salary_band, nationality, kyc_status | Aurora customers | 83 |
| Employer | name, sector, city, country | Aurora employment_info JSON | 48 |
| Location | city, country, area | Aurora customers | ~15 |
| Product | type (savings/premium/current/business), tier | Aurora accounts | 4 types |
| Action | id, type, category, value | Personalize actions | 15 |

**Edge Types:**

| Edge | From → To | Properties | How Detected |
|------|-----------|-----------|--------------|
| WORKS_AT | Customer → Employer | job_title, since | employment_info JSON |
| LIVES_IN | Customer → Location | — | customer.country + city |
| HOLDS | Customer → Product | account_id, opened_date, balance | accounts table |
| TRANSFERS_TO | Customer → Customer | frequency, avg_amount, last_date | transactions (matching account pairs) |
| FAMILY_OF | Customer → Customer | confidence, evidence | Same employer + same address + recurring transfers |
| TOOK_ACTION | Customer → Action | timestamp, outcome | action_interactions |
| SIMILAR_PROFILE | Customer → Customer | similarity_score | Computed: same employer + salary band + location |

**Key Graph Queries (openCypher):**

```cypher
// 1. Household Detection — find likely family members
MATCH (c:Customer {id: $customerId})-[:TRANSFERS_TO]->(other:Customer)
WHERE other.country = c.country
WITH c, other, count(*) as transfer_count
WHERE transfer_count >= 3
OPTIONAL MATCH (c)-[:WORKS_AT]->(e:Employer)<-[:WORKS_AT]-(other)
RETURN other.id, other.name, transfer_count,
       CASE WHEN e IS NOT NULL THEN 'high' ELSE 'medium' END as confidence

// 2. Peer Product Gaps — what do my colleagues have that I don't?
MATCH (c:Customer {id: $customerId})-[:WORKS_AT]->(e:Employer)<-[:WORKS_AT]-(peer:Customer)
MATCH (peer)-[:HOLDS]->(p:Product)
WHERE NOT EXISTS { MATCH (c)-[:HOLDS]->(:Product {type: p.type}) }
RETURN p.type, count(peer) as peer_count,
       round(100.0 * count(peer) / size(collect(peer)), 1) as adoption_pct
ORDER BY peer_count DESC

// 3. Network Influence Score — how many in my network took an action?
MATCH (c:Customer {id: $customerId})-[:WORKS_AT]->(e:Employer)<-[:WORKS_AT]-(peer:Customer)
MATCH (peer)-[:TOOK_ACTION]->(a:Action {id: $actionId})
WHERE peer.segment IN ['HIGH_VALUE', 'MEDIUM_VALUE']
RETURN a.id, count(peer) as peers_who_took,
       round(100.0 * count(peer) / size(collect(peer)), 1) as network_adoption_pct

// 4. Community Spending Trends — what's trending in my peer group?
MATCH (c:Customer {id: $customerId})-[:WORKS_AT]->(e:Employer)<-[:WORKS_AT]-(peer:Customer)
MATCH (peer)-[:HOLDS]->(a:Product)<-[:HOLDS]-(peer)
// Compare spending categories between customer and peers
// Identify categories where peers spend significantly more
```

**Graph Signals Output Example:**
```json
{
  "household": {
    "members": ["CUST00000042"],
    "gaps": ["CUST00000042 has no savings account"],
    "opportunity": "Family savings bundle"
  },
  "peer_network": {
    "employer": "Gulf Air",
    "total_peers": 12,
    "premium_adoption": "83%",
    "signal": "10 of 12 Gulf Air colleagues have Premium accounts"
  },
  "community_trends": {
    "trending_up": ["travel", "investment"],
    "trending_down": ["dining"],
    "signal": "Peer group travel spending up 40% — travel credit card opportunity"
  }
}
```


---

### 3.3 Engine 3: Agentic AI — Reasoning & Explanation (AgentCore + Claude)

**Purpose:** The "brain" that combines all signals, applies business rules, and generates human-quality explanations.

**Why an Agent, not just rules?**
- Rules can rank. Agents can *reason*.
- "ACT001 scored 0.87" is data. "Upgrade to Premium because 83% of your Gulf Air colleagues already have it, your savings rate of 22% shows financial discipline, and the BHD 0 upgrade fee this month makes it risk-free" is a *conversation*.
- This is CommBank's "Next Best Conversation" concept — not just what to offer, but *how to talk about it*.

**Agent Framework:** Strands Agents on AgentCore Runtime (eu-west-1)
**Model:** Claude Sonnet (via Bedrock)
**Protocol:** HTTP (same pattern as Alma FAQ agent)

**System Prompt — Reasoning Framework:**

```
You are the AI Bank Next Best Action advisor. Your role is to analyze customer data
and generate personalized, explainable recommendations.

## Decision Framework

For each customer, follow this reasoning chain:

1. UNDERSTAND — Who is this customer?
   - Demographics, employment, financial profile
   - Current products, account age, engagement level
   - KYC status, risk category

2. ANALYZE — What does the data say?
   - Financial health: savings rate, debt-to-income, expense patterns
   - Personalize ML scores: which actions have highest propensity?
   - Graph signals: what are peers doing? household gaps? network trends?

3. FILTER — What's actually eligible and appropriate?
   - Eligibility rules: salary thresholds, account age, KYC status
   - Contact policy: when was this last presented? frequency caps
   - Regulatory: Sharia compliance for Islamic products, age restrictions

4. RANK — Prioritize by combined score
   - ML propensity (40% weight)
   - Business value (20% weight)
   - Graph signal strength (20% weight)
   - Customer financial health alignment (20% weight)

5. EXPLAIN — Generate human-quality reasoning
   - For RM: full analysis with talking points, objection handling, risk factors
   - For Customer: friendly, concise "why this is good for you" message
   - Always include: the evidence, the benefit, and the next step

## GCC Context
- Respect Islamic finance principles (no interest-based products for Islamic customers)
- Multi-currency awareness (BHD, SAR, AED)
- Salary transfer is a key banking relationship anchor in GCC
- Ramadan/Eid seasonal patterns affect spending and saving
- Expat vs national customer journeys differ significantly

## Contact Policy Rules
- Maximum 3 actions per customer per week
- Minimum 7 days between same action type
- Never present declined actions within 30 days
- KYC nudge: maximum once per week until completed
- High-value cross-sell: only after 3+ months account age

## Output Format
Return a JSON object with:
- actions: ranked list with scores, reasoning, and presentation copy
- customer_summary: one-paragraph financial profile
- risk_factors: any concerns or caveats
- rm_talking_points: conversation starters for relationship managers
```

**8 MCP Tools:**

| # | Tool | Source | Latency | Purpose |
|---|------|--------|---------|---------|
| 1 | `get_customer_360` | Aurora (customer_360_summary view + customer_360_metrics) | ~100ms | Full customer profile with financial metrics |
| 2 | `get_personalize_scores` | Amazon Personalize Campaign | ~50ms | ML propensity scores for all actions |
| 3 | `get_graph_signals` | Neptune | ~200ms | Household, peer network, community trends |
| 4 | `check_eligibility` | Lambda (rule engine) | ~20ms | Hard eligibility rules per action |
| 5 | `check_contact_policy` | Aurora (next_best_offers table) | ~50ms | Frequency caps, declined history |
| 6 | `calculate_financial_health` | Aurora (transactions + accounts) | ~150ms | Real-time savings rate, expense ratio, trends |
| 7 | `get_customer_goals` | Aurora (customer_goals table) | ~50ms | Active savings/investment goals |
| 8 | `log_decision` | Aurora (next_best_offers + DynamoDB) | ~50ms | Audit trail: what was recommended, why, outcome |

**Tool Flow in Agent:**

```
User/RM requests NBA for CUST00000001
    │
    ▼
┌─ Agent receives request ─────────────────────────────────┐
│                                                           │
│  Step 1: Parallel data gathering                         │
│  ┌─────────────────┐ ┌──────────────┐ ┌───────────────┐ │
│  │get_customer_360 │ │get_personalize│ │get_graph_     │ │
│  │                 │ │_scores       │ │signals        │ │
│  └────────┬────────┘ └──────┬───────┘ └──────┬────────┘ │
│           │                 │                │           │
│  Step 2: Enrichment                                      │
│  ┌──────────────────┐ ┌──────────────────┐              │
│  │calculate_         │ │get_customer_     │              │
│  │financial_health   │ │goals             │              │
│  └────────┬──────────┘ └────────┬─────────┘              │
│           │                     │                        │
│  Step 3: Filtering                                       │
│  ┌──────────────────┐ ┌──────────────────┐              │
│  │check_eligibility │ │check_contact_    │              │
│  │                  │ │policy            │              │
│  └────────┬─────────┘ └────────┬─────────┘              │
│           │                    │                         │
│  Step 4: Claude reasons over all data                    │
│  → Ranks actions                                         │
│  → Generates explanations                                │
│  → Creates RM talking points                             │
│           │                                              │
│  Step 5: Log & Return                                    │
│  ┌──────────────┐                                        │
│  │log_decision  │                                        │
│  └──────┬───────┘                                        │
│         │                                                │
└─────────┼────────────────────────────────────────────────┘
          ▼
    Response to caller
```


---

## 4. The "Wow" Moments — What Makes This Special

### 4.1 Wow #1: Social Proof from Real Peer Data

**Traditional NBA:** "We recommend Premium account based on your profile."
**AI Bank NBA:** "83% of Gulf Air employees at AI Bank have Premium accounts. You're one of the few who hasn't upgraded yet. This month, the upgrade fee is waived."

The graph makes this possible. Without Neptune, you're guessing. With Neptune, you have *evidence*.

### 4.2 Wow #2: Household Intelligence

**Traditional NBA:** Treats each customer independently.
**AI Bank NBA:** "We noticed you regularly transfer BHD 500 to Deepthy Vijayan. She recently opened a savings account but doesn't have auto-save enabled. Would you like us to suggest a family savings goal?"

Graph detects family relationships through transfer patterns, shared employers, and co-location. This unlocks household-level recommendations that feel genuinely helpful, not salesy.

### 4.3 Wow #3: Explainable AI with Talking Points

**Traditional NBA:** Score: 0.87. Action: Premium upgrade.
**AI Bank NBA (RM Console):**

```
┌─────────────────────────────────────────────────────────┐
│  RECOMMENDED ACTION: Premium Account Upgrade            │
│  Confidence: 87% │ Priority: 1 of 3 │ Type: Cross-sell │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  WHY THIS CUSTOMER:                                     │
│  • Monthly salary BHD 3,500 (threshold: BHD 2,000) ✅  │
│  • Savings rate 22% — financially disciplined           │
│  • Account age 24 months — loyal customer               │
│  • 83% of Gulf Air peers have Premium                   │
│                                                         │
│  TALKING POINTS:                                        │
│  "Mr. Mohan, I noticed you've been a loyal customer     │
│   for 2 years. Most of your colleagues at Gulf Air      │
│   have already upgraded to Premium — it comes with      │
│   airport lounge access which I imagine would be        │
│   useful given your travel patterns."                   │
│                                                         │
│  OBJECTION HANDLING:                                    │
│  • "Is there a fee?" → Waived this month                │
│  • "What's the benefit?" → Lounge access, higher        │
│    savings rate, priority support                        │
│  • "I need to think about it" → No pressure, the        │
│    offer is valid for 30 days                           │
│                                                         │
│  RISK FACTORS:                                          │
│  • KYC still PENDING — complete before upgrade          │
│  • No previous premium product experience               │
│                                                         │
│  [Present to Customer] [Schedule Follow-up] [Decline]   │
└─────────────────────────────────────────────────────────┘
```

### 4.4 Wow #4: Real-Time Feedback Loop

When an RM presents an action and the customer accepts/declines:
1. Outcome logged to Aurora (`next_best_offers.status`)
2. Event sent to Personalize via `PutActionInteractions` API
3. Personalize model auto-updates within 2 hours
4. Next recommendation for similar customers is already smarter

This creates a **learning system** — every interaction makes the next one better.

### 4.5 Wow #5: Context-Aware Timing

The agent considers:
- **Salary day patterns** — recommend savings goals right after salary credit
- **Spending spikes** — "You spent 40% more on dining this month. Want to set a budget alert?"
- **Life events** — large one-time transactions (rent deposit → new home → home insurance opportunity)
- **Seasonal** — Ramadan spending patterns, Eid gifting, back-to-school
- **Inactivity** — "You haven't logged in for 30 days. Here's what's new."

---

## 5. Data Flow & Pipelines

### 5.1 Real-Time Flow (per request)

```
Customer/RM request
    → API Gateway (JWT auth)
    → NBA Lambda Proxy
    → Route decision:
        ├── Fast path only? → Personalize GetActionRecommendations → return
        └── Rich path? → AgentCore NBA Agent
                            → Parallel: Aurora 360 + Personalize + Neptune
                            → Claude reasoning
                            → log_decision
                            → return
```

### 5.2 Daily Batch Pipeline (EventBridge, 02:00 UTC)

```
Step 1: Compute 360 Metrics (Lambda)
    → For each customer: calculate financial_health_score, savings_rate,
      engagement_score, transaction_frequency from last 90 days
    → Write to customer_360_metrics table

Step 2: Sync Graph (Lambda)
    → Export new/changed customers, accounts, transactions from Aurora
    → Upsert nodes and edges in Neptune
    → Recompute SIMILAR_PROFILE edges
    → Detect new FAMILY_OF relationships from transfer patterns

Step 3: Export to Personalize (Lambda)
    → Export new transactions as item interactions (CSV → S3)
    → Export new action outcomes as action interactions (CSV → S3)
    → Trigger dataset import job

Step 4: Pre-compute NBA for Top Customers (Lambda)
    → Run NBA agent for top 20% customers (by value segment)
    → Cache results in next_best_offers table
    → These are served instantly when customer opens app
```

### 5.3 Event-Driven Updates

```
Customer accepts/declines offer
    → Lambda writes to next_best_offers (status update)
    → Lambda calls Personalize PutActionInteractions (real-time event)
    → Lambda updates Neptune edge (TOOK_ACTION)

New transaction recorded
    → Lambda calls Personalize PutEvents (real-time item interaction)
    → If salary credit: trigger NBA refresh for this customer

New customer onboarded
    → Lambda creates Neptune node + edges (employer, location)
    → Lambda creates Personalize user
    → After 7 days: first NBA run
```

---

## 6. API Design

### 6.1 GET /nba/recommend

**Fast path** — returns pre-computed or Personalize-only recommendations.

```json
// Request
GET /nba/recommend?customer_id=CUST00000001&limit=3
Authorization: Bearer <JWT>

// Response (50-100ms)
{
  "customer_id": "CUST00000001",
  "recommendations": [
    {
      "action_id": "ACT001",
      "action_type": "cross_sell",
      "title": "Upgrade to Premium Account",
      "subtitle": "Join 83% of your colleagues who already have Premium",
      "score": 0.87,
      "cta": "Learn More",
      "card_image": "premium-upgrade.png"
    },
    {
      "action_id": "ACT005",
      "title": "Set Up Auto-Save Goal",
      "subtitle": "You saved BHD 770 last month — put it to work",
      "score": 0.65,
      "cta": "Start Saving",
      "card_image": "auto-save.png"
    }
  ],
  "source": "personalize+cache",
  "latency_ms": 52
}
```

### 6.2 POST /nba/explain

**Rich path** — full agent reasoning for RM console.

```json
// Request
POST /nba/explain
Authorization: Bearer <JWT>
{
  "customer_id": "CUST00000001",
  "context": "rm_advisor",
  "action_id": "ACT001"  // optional: explain specific action
}

// Response (2-5s)
{
  "customer_id": "CUST00000001",
  "customer_summary": "Aneesh Mohan is a 24-month customer at Gulf Air earning BHD 3,500/month. Financial health score 78/100 with a 22% savings rate. Holds savings and current accounts. KYC pending.",
  "recommendations": [
    {
      "action_id": "ACT001",
      "rank": 1,
      "title": "Premium Account Upgrade",
      "confidence": 0.87,
      "reasoning": "Strong candidate: salary exceeds BHD 2,000 threshold, 22% savings rate shows financial discipline, 24-month tenure demonstrates loyalty. 83% of Gulf Air peers (10/12) have Premium. Waived upgrade fee this month reduces friction.",
      "evidence": {
        "ml_score": 0.87,
        "peer_adoption": "83%",
        "salary_threshold_met": true,
        "financial_health": 78,
        "account_age_months": 24
      },
      "talking_points": [
        "Most of your colleagues at Gulf Air have already upgraded",
        "With your travel patterns, the airport lounge access alone is worth it",
        "The upgrade fee is waived this month — no risk to try"
      ],
      "objection_handling": {
        "fee_concern": "Upgrade fee waived this month, monthly fee BHD 5 offset by higher savings rate",
        "need_to_think": "Offer valid for 30 days, no pressure",
        "whats_the_benefit": "Lounge access, 0.5% higher savings rate, priority support line"
      },
      "risk_factors": [
        "KYC still PENDING — must complete before upgrade",
        "No previous premium product experience"
      ],
      "customer_message": "Hi Aneesh! Did you know most of your colleagues already enjoy Premium benefits like airport lounge access? This month, the upgrade is free. Interested?",
      "cta": "Present to Customer"
    }
  ],
  "source": "agent+personalize+neptune",
  "latency_ms": 3200
}
```

### 6.3 POST /nba/batch

**Batch** — generate recommendations for a segment.

```json
// Request
POST /nba/batch
Authorization: Bearer <JWT> (admin only)
{
  "segment": "HIGH_VALUE",
  "country": "BH",
  "action_type": "cross_sell",
  "limit": 50
}

// Response
{
  "job_id": "batch-20260226-001",
  "status": "processing",
  "estimated_completion": "2026-02-26T07:30:00Z",
  "customers_queued": 52
}
```


---

## 7. Frontend Experiences

### 7.1 Customer App — "For You" Feed

Location: In-app tab or dashboard section
Source: Fast path (Personalize + cache)
Latency: <100ms

```
┌─────────────────────────────────────┐
│  🎯 For You                         │
├─────────────────────────────────────┤
│                                     │
│  ┌─────────────────────────────┐   │
│  │ ⭐ Premium Upgrade           │   │
│  │                             │   │
│  │ 83% of your colleagues      │   │
│  │ already enjoy Premium.      │   │
│  │ Upgrade free this month.    │   │
│  │                             │   │
│  │ [Learn More]                │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │ 💰 Auto-Save Goal           │   │
│  │                             │   │
│  │ You saved BHD 770 last      │   │
│  │ month. Set a goal and       │   │
│  │ watch it grow.              │   │
│  │                             │   │
│  │ [Start Saving]              │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │ 📊 Spending Insight          │   │
│  │                             │   │
│  │ Your dining spend is 15%    │   │
│  │ above your peer group.      │   │
│  │ Want to set a budget?       │   │
│  │                             │   │
│  │ [Set Budget Alert]          │   │
│  └─────────────────────────────┘   │
│                                     │
└─────────────────────────────────────┘
```

### 7.2 RM Advisor Console

Location: `/employee/rm/nba-advisor.html`
Source: Rich path (Agent + full reasoning)
Latency: 2-5s (acceptable for RM workflow)

Features:
- Customer search → full NBA analysis
- Ranked action cards with expand for reasoning
- One-click "Present to Customer" (sends in-app notification or email)
- Outcome tracking (Accepted / Declined / Deferred)
- Conversation mode: RM can ask follow-up questions ("What if the customer asks about fees?")

### 7.3 Marketing Campaign Console

Location: `/employee/marketing/nba-campaigns.html`
Source: Batch path
Features:
- Select action → see eligible customer count
- Filter by segment, country, salary band
- Preview sample recommendations
- Launch campaign → generates personalized messages per customer
- Track: sent, opened, clicked, converted

---

## 8. AWS Services & Infrastructure

| Service | Region | Purpose | Tier |
|---------|--------|---------|------|
| **Amazon Personalize** | eu-west-1 | ML propensity scoring, NBA recipe | Campaign (auto-scaling) |
| **Amazon Neptune Serverless** | me-south-1 | Graph relationships, peer analysis | Serverless (2.5-128 NCU) |
| **Aurora Serverless v2** | me-south-1 | Customer data, 360 metrics, offers | Existing cluster |
| **AgentCore Runtime** | eu-west-1 | NBA reasoning agent (Strands + Claude) | HTTP, ARM64 |
| **AgentCore Gateway** | eu-west-1 | MCP tool hosting (8 tools) | Managed |
| **Lambda** | eu-west-1 | NBA proxy, batch compute, ETL | ARM64, 256MB |
| **Lambda** | me-south-1 | Neptune sync, 360 metrics compute | ARM64, 256MB |
| **API Gateway** | eu-west-1 | REST API with Cognito authorizer | Regional |
| **EventBridge** | eu-west-1 | Daily batch schedule (02:00 UTC) | Scheduler |
| **S3** | eu-west-1 | Personalize dataset storage | Standard |
| **ElastiCache (Redis)** | eu-west-1 | NBA response cache (fast path) | Serverless |
| **CloudWatch** | both | Monitoring, dashboards, alarms | Standard |
| **Cognito** | me-south-1 | Auth (existing pool) | Existing |

**Cost Estimate (monthly, 83 customers demo scale):**
- Personalize: ~$25/month (training + inference at low volume)
- Neptune Serverless: ~$15/month (min 2.5 NCU, bursty usage)
- Additional Lambda: ~$5/month
- ElastiCache Serverless: ~$10/month
- **Total incremental: ~$55/month for demo**

---

## 9. Security & Compliance

| Concern | Approach |
|---------|----------|
| Data residency | Customer PII stays in me-south-1 (Aurora + Neptune). Only anonymized IDs sent to Personalize in eu-west-1 |
| Authentication | Cognito JWT on all API endpoints. RM endpoints require `employee` group |
| Authorization | Customer can only see their own NBA. RM can see assigned customers |
| Audit trail | Every recommendation logged with timestamp, reasoning, outcome in `next_best_offers` |
| Contact policy | Hard-coded frequency caps prevent over-contacting |
| Islamic finance | Agent system prompt includes Sharia compliance rules. Islamic customers filtered from interest-based products |
| GDPR/PDPL | Customer can opt out of NBA. Decline = 30-day suppression |
| Model bias | Personalize exploration ensures minority segments get fair representation |

---

## 10. Metrics & KPIs

| Metric | Target | How Measured |
|--------|--------|-------------|
| Recommendation acceptance rate | >15% | Accepted / Presented |
| Time to first recommendation | <100ms (fast), <5s (rich) | API latency |
| RM adoption rate | >80% of RMs use weekly | Console login tracking |
| Revenue per recommendation | Track per action type | Conversion × product value |
| Customer satisfaction (NBA) | >4.0/5.0 | Post-interaction survey |
| Model accuracy (Personalize) | >0.25 normalized discounted cumulative gain | Personalize metrics |
| Graph signal contribution | Measure lift from Neptune signals | A/B: with/without graph |
| Feedback loop velocity | <24h from outcome to model update | Personalize event lag |

---

## 11. Implementation Phases

### Phase 1: Foundation (Week 1) ← START HERE
- [ ] Compute customer_360_metrics for all 82 customers
- [ ] Set up Amazon Personalize dataset group + datasets
- [ ] Export transactions → item interactions CSV
- [ ] Define 15 actions → actions dataset CSV
- [ ] Generate synthetic action interactions from customer profiles
- [ ] Import all datasets, train NBA solution, create campaign
- [ ] Test: GetActionRecommendations for sample customers

### Phase 2: Graph (Week 2)
- [ ] Create Neptune Serverless cluster in me-south-1
- [ ] Design and load graph: customers, employers, locations, products
- [ ] Compute edges: WORKS_AT, LIVES_IN, HOLDS, TRANSFERS_TO
- [ ] Detect FAMILY_OF relationships from transfer patterns
- [ ] Compute SIMILAR_PROFILE edges
- [ ] Test: openCypher queries for household, peer gaps, network signals

### Phase 3: Agent (Week 3)
- [ ] Build 8 MCP tools as Lambda functions
- [ ] Register tools in AgentCore Gateway
- [ ] Build NBA Strands agent with reasoning system prompt
- [ ] Deploy on AgentCore Runtime (HTTP, ARM64)
- [ ] Build NBA Lambda proxy with fast/rich path routing
- [ ] API Gateway endpoints: /nba/recommend, /nba/explain
- [ ] Test: end-to-end for 5 sample customers

### Phase 4: Frontend & Integration (Week 4)
- [ ] Customer "For You" cards on dashboard
- [ ] RM Advisor console with full reasoning display
- [ ] Outcome tracking (accept/decline/defer)
- [ ] Real-time feedback to Personalize
- [ ] Daily batch pipeline (EventBridge + Lambda)
- [ ] Neptune sync pipeline
- [ ] CloudWatch dashboards and alarms

### Phase 5: Polish & Launch (Week 5)
- [ ] Marketing batch console
- [ ] A/B testing framework (with/without graph signals)
- [ ] Load testing at 1000 customers scale
- [ ] Documentation and runbooks
- [ ] Demo script for GCC FSI stakeholders

---

## 12. What Makes This a "Wow" Product — Summary

| Dimension | Traditional NBA | AI Bank NBA |
|-----------|----------------|-------------|
| **Intelligence** | Rules + basic ML | ML + Graph + Agentic AI (three engines) |
| **Personalization** | Segment-level | Individual + household + peer network |
| **Explanation** | "Recommended for you" | Full reasoning with evidence and talking points |
| **Speed** | One speed | Two-speed: 50ms fast + 5s rich |
| **Learning** | Retrain weekly | Real-time feedback loop, auto-update every 2h |
| **Context** | Product features | Life events, salary timing, peer behavior, family |
| **GCC-native** | Generic | Islamic finance, multi-currency, expat/national, Ramadan |
| **Audience** | Customer only | Customer + RM + Marketing (three interfaces) |
| **Inspiration** | Basic recommender | CommBank CEE (35M decisions/day) adapted for GCC |

---

*This is not a recommendation engine. This is a decision intelligence platform that makes every customer interaction smarter, every RM conversation more informed, and every marketing campaign more targeted. Built on AWS, native to GCC.*
