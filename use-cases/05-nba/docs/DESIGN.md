# Next Best Action (NBA) — Design Summary

## Depends On
Foundation ✅, Aurora ✅, Synthetic Data ✅, **KYC Processing ❌ (NEXT)**

## Architecture — Three Intelligence Engines

### Engine 1: Amazon Personalize (eu-west-1) — ML Scoring
- Recipe: `aws-next-best-action`
- 4 datasets: Item Interactions (33K txns), Actions (15), Action Interactions (synthetic), Users (83)
- Campaign with real-time inference (~50ms)
- Auto-updates every 2 hours

### Engine 2: Amazon Neptune Serverless (me-south-1) — Graph Intelligence
- Property graph with openCypher
- 5 node types: Customer, Employer, Location, Product, Action
- 7 edge types: WORKS_AT, LIVES_IN, HOLDS, TRANSFERS_TO, FAMILY_OF, TOOK_ACTION, SIMILAR_PROFILE
- Key queries: household detection, peer product gaps, network influence, community trends
- **Requires KYC data** for employer, salary, address → graph edges

### Engine 3: AgentCore + Claude (eu-west-1) — Agentic Reasoning
- Strands agent on AgentCore Runtime (HTTP, ARM64)
- 8 MCP tools via AgentCore Gateway
- Reasoning framework: Understand → Analyze → Filter → Rank → Explain
- GCC-native: Islamic finance, multi-currency, Ramadan patterns

## Two-Speed Architecture
- **Fast Path (~50ms):** Personalize campaign → ranked actions with scores → customer app
- **Rich Path (2-5s):** Agent pulls 360 + Personalize + Neptune → Claude reasons → RM console

## API Endpoints
- `GET /nba/recommend` — fast path, pre-computed or Personalize-only
- `POST /nba/explain` — rich path, full agent reasoning for RM
- `POST /nba/batch` — batch segment targeting for marketing

## Data Pipelines
- Real-time: per-request to Personalize + Neptune
- Daily batch: 360 metrics compute, graph sync, Personalize export
- Event-driven: outcome feedback → Personalize PutActionInteractions

## Full Design Document
See [NBA-SOLUTION-DESIGN.md](./NBA-SOLUTION-DESIGN.md) for complete architecture, graph model, API specs, frontend wireframes, security, and implementation phases.

## Status: Blocked on KYC Processing
