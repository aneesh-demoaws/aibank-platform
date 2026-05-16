# Use Case 05 — Next Best Action (NBA)

Daily AI-driven recommendations for every customer, powered by graph analytics and pattern scanning across Aurora, Neptune Analytics, and Bedrock.

## Architecture

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────┐    ┌──────────┐
│  Aurora MySQL │ → │ Neptune Loader   │ → │ Neptune Analytics │ → │  S3      │
│  (customers,  │   │ (Step Function)  │   │  (graph algos)    │   │ (peer    │
│  loans, txns) │   │                  │   │                   │   │  stats)  │
└─────────────┘    └──────────────────┘    └─────────────────┘    └──────────┘
                                                     ↓
                                          ┌────────────────────┐
                                          │ Pattern Scanner +  │
                                          │ Personal Reasoning │
                                          │ (LLM-powered)      │
                                          └────────────────────┘
                                                     ↓
                                          ┌────────────────────┐
                                          │  nba_actions table │
                                          │  (For You page)    │
                                          └────────────────────┘
```

## Components

### Lambda Functions (`lambda/`)

| Function | Purpose |
|---|---|
| `neptune-loader/` | Aurora → Neptune sync (customers, loans, transactions as graph nodes/edges) |
| `neptune-enrichment/` | 5-step pipeline: Sync → Analytics → Enrich+Signals → Materialize peer_stats → S3 export |
| `analytics-runner/` | Runs Neptune Analytics SIMILAR_TO algorithms (5+ shared merchants, matching segments) |
| `pattern-scanner/` | Detects life events from transaction patterns (job change, marriage, baby, home purchase) |
| `personal-reasoning/` | Bedrock LLM generates personalized why-this explanations per recommendation |
| `batch-generator/` | Processes a customer batch — generates NBAs and writes to `nba_actions` table |
| `realtime-agent/` | On-demand NBA generation via API (used by Alma Graph Agent) |
| `graph-context/` | Returns graph neighborhood context for a customer (used by C360 + Alma) |
| `kpi-export/` | Exports daily KPI metrics for QuickSight dashboards |
| `life-event/` | Detects single life event and triggers immediate NBA refresh |
| `event-placeholder/` | EventBridge placeholder for ad-hoc triggers |
| `execution/` | Records NBA action execution (acceptance, dismissal, completion) |
| `doc-understanding/` | Bedrock Data Automation for transaction document analysis |
| `quicksight-embed/` | Generates QuickSight embed URLs for the NBA Insights dashboard |

### Step Functions (`stepfunctions/`)

- **`aibank-nba-daily-pipeline.asl.json`** — Daily refresh: NeptuneEnrichment → GetCustomerList → BatchGenerate (Map, 40 concurrent) → ReportSuccess
- **`aibank-nba-batch-workflow.asl.json`** — On-demand batch reprocessing for a customer subset

### EventBridge Schedules (`eventbridge/`)

| Rule | Schedule | Triggers |
|---|---|---|
| `aibank-nba-daily-pipeline-trigger` | `cron(0 6 * * ? *)` | Full daily refresh at 6 AM UTC |
| `nba-batch-refresh-6h` | `rate(6 hours)` | Quick batch refresh every 6 hours |
| `nba-cashflow-scanner-daily` | `cron(0 9 * * ? *)` | Cashflow pattern scanning at 9 AM UTC |

### Frontend (`frontend/`)

| File | Audience |
|---|---|
| `for-you.html` | Customer For You page — NBA cards with peer insights, accept/dismiss actions |
| `financial-health.html` | Customer FHS score with subscores and personalized recommendations |
| `employee/nba-insights.html` | Employee NBA Insights dashboard (QuickSight embedded) |

## The Daily Aurora → Neptune → S3 Pipeline

The `neptune-enrichment` Lambda runs in 5 sequential steps when called with `step=all`:

### Step 1 — Sync (Aurora → Neptune Analytics)
Reads from Aurora: `customers`, `accounts`, `loans`, `transactions`, `customer_products`, `life_events`. Writes graph nodes and edges to Neptune Analytics graph `g-ruhyz8aj39`.

### Step 2 — Analytics (Neptune Analytics queries)
Runs:
- **SIMILAR_TO**: Customers sharing 5+ merchants in last 90 days
- **Segment matching**: Customers in the same age band, income tier, life stage
- **Community detection**: Co-spending circles for cross-sell signals

### Step 3 — Enrich + Signals
For each customer, computes:
- Top 10 peer customer IDs (with similarity score)
- Peer adoption rates per product category
- Custom signals (income trend, savings rate, debt-to-income ratio)

### Step 4 — Materialize Peer Stats (Aurora write-back)
Writes aggregated peer stats to Aurora `peer_stats` table — used by the For You page to show "people like you also bought..."

### Step 5 — S3 Export
Exports daily snapshot to `s3://aibank-athena-results-eu-west-1/neptune-export/dt=YYYY-MM-DD/customer_peer_stats.parquet` for Athena/QuickSight queries.

## Deployment

```bash
# Prerequisite: Aurora schema, Neptune Analytics graph, S3 bucket exist
cd use-cases/05-nba

# Deploy each Lambda with its config (memory, timeout, env vars from lambda-config.json)
for fn in lambda/*/; do
  fn_name="aibank-nba-$(basename $fn)"
  zip -r /tmp/$fn_name.zip $fn
  aws lambda update-function-code --function-name $fn_name --zip-file fileb:///tmp/$fn_name.zip
done

# Deploy Step Functions
aws stepfunctions create-state-machine \
  --name aibank-nba-daily-pipeline \
  --definition file://stepfunctions/aibank-nba-daily-pipeline.asl.json \
  --role-arn $NBA_SFN_ROLE_ARN

# Deploy EventBridge schedules
for rule in eventbridge/*.json; do
  aws events put-rule --cli-input-json file://$rule
done
```

## Tables (Aurora)

| Table | Purpose |
|---|---|
| `nba_actions` | Materialized NBAs per customer (template_id, status, expires_at) |
| `peer_stats` | Aggregated peer adoption rates (refreshed daily) |
| `nba_templates` | Catalog of NBA templates with copy + targeting rules |
| `customer_products` | Products owned by customer (used for cross-sell exclusion) |
| `life_events` | Detected life events (job change, marriage, etc.) |

## Observability

- CloudWatch Logs: each Lambda function has its own log group
- Step Function execution history: [Console](https://console.aws.amazon.com/states/home?region=eu-west-1#/statemachines/view/arn:aws:states:eu-west-1:519124228967:stateMachine:aibank-nba-daily-pipeline)
- QuickSight dashboard: NBA Insights `08e4366c-8586-4758-ba28-fd63be63d0cd`
