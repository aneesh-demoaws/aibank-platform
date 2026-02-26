# Next Best Action (NBA) — Tasks

## Prerequisites
- [x] Foundation (Aurora, Cognito, SES)
- [x] Alma FAQ + Customer Onboarding
- [x] Synthetic Data Seeding (82 customers, 33K transactions)
- [ ] **KYC Processing (USE CASE 03)** ← MUST COMPLETE FIRST
  - KYC provides: employer, salary, job_title, city, verified identity
  - Without KYC: Neptune graph has no WORKS_AT/LIVES_IN edges
  - Without KYC: Personalize user metadata missing salary_band/segment

## Phase 1: Foundation (Week 1)
- [ ] Compute customer_360_metrics for all 82 customers (batch Lambda)
- [ ] Set up Amazon Personalize dataset group `aibank-nba`
- [ ] Create Item Interactions dataset + schema (from transactions)
- [ ] Create Actions dataset + schema (15 banking actions)
- [ ] Create Action Interactions dataset + schema (synthetic from profiles)
- [ ] Create Users dataset + schema (from customers)
- [ ] Export CSVs to S3, import into Personalize
- [ ] Create solution (Next-Best-Action recipe), train solution version
- [ ] Create campaign `aibank-nba-campaign`
- [ ] Test: GetActionRecommendations for sample customers

## Phase 2: Graph (Week 2)
- [ ] Create Neptune Serverless cluster in me-south-1
- [ ] Load Customer nodes (83)
- [ ] Load Employer nodes (48 unique employers)
- [ ] Load Location nodes (~15 cities)
- [ ] Load Product nodes (4 account types)
- [ ] Create WORKS_AT edges (from employment_info JSON)
- [ ] Create LIVES_IN edges (from customer country + city)
- [ ] Create HOLDS edges (from accounts table)
- [ ] Detect TRANSFERS_TO edges (from transaction pairs)
- [ ] Compute FAMILY_OF edges (transfers + shared employer + co-location)
- [ ] Compute SIMILAR_PROFILE edges (same employer + salary band + location)
- [ ] Test: openCypher queries — household, peer gaps, network signals

## Phase 3: Agent + MCP Tools (Week 3)
- [ ] Build MCP tool: `get_customer_360` (Aurora 360 view + metrics)
- [ ] Build MCP tool: `get_personalize_scores` (Personalize campaign)
- [ ] Build MCP tool: `get_graph_signals` (Neptune queries)
- [ ] Build MCP tool: `check_eligibility` (rule engine)
- [ ] Build MCP tool: `check_contact_policy` (frequency caps)
- [ ] Build MCP tool: `calculate_financial_health` (real-time from txns)
- [ ] Build MCP tool: `get_customer_goals` (goals table)
- [ ] Build MCP tool: `log_decision` (audit trail)
- [ ] Register tools in AgentCore Gateway
- [ ] Build NBA Strands agent with reasoning system prompt
- [ ] Deploy on AgentCore Runtime (HTTP, ARM64)
- [ ] Build NBA Lambda proxy (fast/rich path routing)
- [ ] API Gateway: /nba/recommend, /nba/explain, /nba/batch
- [ ] Test: end-to-end for 5 sample customers

## Phase 4: Frontend & Integration (Week 4)
- [ ] Customer "For You" cards on dashboard
- [ ] RM Advisor console with reasoning display
- [ ] Outcome tracking UI (accept/decline/defer)
- [ ] Real-time feedback to Personalize (PutActionInteractions)
- [ ] Daily batch pipeline (EventBridge → Lambda)
- [ ] Neptune sync pipeline (Aurora → Neptune)
- [ ] CloudWatch dashboards and alarms

## Phase 5: Polish & Launch (Week 5)
- [ ] Marketing batch campaign console
- [ ] A/B testing framework (with/without graph signals)
- [ ] Load testing at scale
- [ ] Documentation and runbooks
- [ ] Demo script for GCC FSI stakeholders

## Status: Blocked on KYC Processing ❌
