# Use Case 07 — Customer 360

Relationship Manager (RM) portal showing comprehensive customer view across Aurora, Neptune Analytics, peer benchmarks, and embedded analytics.

## Architecture

```
                ┌─────────────────────────────────────────────────────────────┐
                │                  Customer 360 RM Portal                      │
                │                  (10-panel dashboard)                        │
                └─────────────────────────────────────────────────────────────┘
                          │                │                │             │
                          ▼                ▼                ▼             ▼
                 ┌──────────────┐ ┌──────────────┐ ┌─────────────┐ ┌───────────┐
                 │  c360-api     │ │  D3.js Graph  │ │  QuickSight  │ │ Quick Suite│
                 │  Lambda       │ │  Neptune      │ │  Dashboard   │ │ Chat Agent │
                 │  (REST)       │ │  Analytics    │ │  (Embedded)  │ │ (Embedded) │
                 └──────────────┘ └──────────────┘ └─────────────┘ └───────────┘
                          │                │
                          ▼                │
                 ┌──────────────┐          │
                 │  Aurora       │ ◄────────┘
                 │  (banking)    │
                 └──────────────┘
                          ▲
                          │ Refreshed daily
                          │
                 ┌──────────────────────────────────┐
                 │  NBA daily pipeline (use-case 05) │
                 │  exports peer_stats to S3 → Athena│
                 └──────────────────────────────────┘
                          │
                          ▼
                 ┌──────────────┐
                 │  S3 + Athena  │
                 │  Glue catalog │
                 │  customer_peer│
                 │  _stats       │
                 └──────────────┘
```

## Components

### Lambda Functions (`lambda/`)

- **`c360-api/`** — REST API serving the C360 portal. Endpoints:
  - `GET /c360/customers` — paginated customer list with filters
  - `GET /c360/detail?id=CUST...` — full 360 view (KYC, accounts, loans, transactions, family, peer benchmarks, NBAs)
  - `GET /c360/graph?id=CUST...` — Neptune Analytics neighborhood for D3.js graph rendering
- **`handler.py`** (legacy `aibank-loan-reviewer`) — also exposes `/c360/customers` and `/c360/detail` endpoints (used while migrating)

### Athena (`athena/`)

- **`glue-table.sql`** — Defines the Glue Data Catalog table `neptune_c360.customer_peer_stats` with 31 columns over the NBA pipeline's S3 Parquet export
- **`neptune-export.py`** — Daily Neptune → S3 export Lambda (called by NBA pipeline Step 5)

### Portal (`portal/rm/`)

- **`customers.html`** — Searchable, sortable customer list with quick filters (segment, KYC status, life events). Click any row → opens `customer360.html?id=CUST...`
- **`customer360.html`** — 10-panel customer 360 dashboard:
  1. **Profile** — name, KYC, contact, address
  2. **Accounts** — all accounts with balances and trends
  3. **Loans** — loan history with status badges
  4. **Recent Transactions** — last 30 days
  5. **Family / Household** — Neptune-derived household graph
  6. **D3.js Neptune Graph** — interactive force-directed graph showing peer connections, shared merchants, household, employer
  7. **Peer Comparison** — benchmark against similar customers (Athena query)
  8. **NBA Recommendations** — current and historical NBAs for this customer
  9. **Embedded QuickSight Dashboard** — RM-specific KPIs (`7d246f49-a2da-4b38-ac52-c5901530a7c6`)
  10. **Embedded Quick Suite Chat Agent** — "C360 Advisor" for natural-language Q&A about this customer (`57f0681c-1db4-4c49-8c1f-077073e47793`)

## Dependencies on Other Use Cases

| From | What |
|---|---|
| **05-nba** | Daily Aurora → Neptune → S3 pipeline produces `customer_peer_stats` Parquet files that the C360 portal queries via Athena |
| **05-nba** | `nba_actions` table queried for "current recommendations" panel |
| **04-kyc-idp** | KYC verification status displayed in Profile panel |
| **06-loan-automation** | Loan history table queried in Loans panel |

## Athena Connector — Aurora to Neptune Architecture

The C360 portal queries the `neptune_c360.customer_peer_stats` Glue table via Athena. The data path:

1. **NBA pipeline Step 5** exports Neptune Analytics graph stats to `s3://aibank-athena-results-eu-west-1/neptune-export/dt=YYYY-MM-DD/customer_peer_stats.parquet`
2. **Glue Crawler** (or static partition) registers the date partition
3. **Athena query** joins customer_peer_stats with Aurora's `customers` table (via Athena's federated query / RDS Aurora connector)
4. **C360 API** returns the joined result to the portal

## Deployment

```bash
cd use-cases/07-customer-360

# Deploy c360-api Lambda
cd lambda/c360-api
zip -r /tmp/c360-api.zip .
aws lambda update-function-code --function-name aibank-c360-api --zip-file fileb:///tmp/c360-api.zip

# Register Glue table (one-time)
aws athena start-query-execution \
  --query-string "$(cat athena/glue-table.sql)" \
  --result-configuration OutputLocation=s3://aibank-athena-results-eu-west-1/

# Upload portal HTML
aws s3 cp portal/rm/customer360.html s3://aibank-ui-prod-eu-west-1/employee/rm/customer360.html
aws s3 cp portal/rm/customers.html s3://aibank-ui-prod-eu-west-1/employee/rm/customers.html
```

## Auth

Uses IAM Identity Center (Microsoft AD) via Cognito federated employee user pool. Access controlled by DynamoDB `aibank-role-config` table — RM, Branch Manager, and Admin roles see this portal.

## Observability

- CloudWatch Logs: `/aws/lambda/aibank-c360-api`
- QuickSight embedded dashboard: `7d246f49-a2da-4b38-ac52-c5901530a7c6`
- Quick Suite Chat Agent: `57f0681c-1db4-4c49-8c1f-077073e47793` in account `qs-aiml`
