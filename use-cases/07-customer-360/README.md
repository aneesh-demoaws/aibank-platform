# Customer 360 — RM Portal

AI-powered, employee-facing portal giving Relationship Managers a complete, actionable view of each customer.

## Architecture

```
Frontend (customer-360.html)
    │
    ├── C360 API Lambda (/c360/customers, /c360/detail, /c360/graph)
    │     ├── Aurora MySQL (operational data: profile, FHS, NBAs, transactions)
    │     └── Neptune Analytics (graph: peers, merchants, products, goals)
    │
    ├── QuickSight Embedded Dashboard (portfolio KPIs, What-If analysis)
    │     └── Athena → Glue → S3 (Neptune peer stats export)
    │
    └── Quick Suite Chat Agent (AI Advisor for RM questions)
          └── QuickSight datasets + Spaces (knowledge sources)
```

## Components

| Component | Path | Description |
|---|---|---|
| C360 API | `lambda/c360-api.py` | Lambda: 3 endpoints (customers, detail, graph) |
| Portal | `portal/customer-360.html` | Frontend: 10 panels with D3.js graph |
| Neptune Export | `athena/neptune-export.py` | Daily S3 export for Athena/QuickSight |
| Glue DDL | `athena/glue-table.sql` | Athena table definition |
| BRD + Design | `docs/Customer360-BRD-Solution-Design.md` | Full requirements + architecture |
| Console Steps | `docs/CONSOLE-SETUP.md` | Manual setup for QuickSight + Chat Agent |

## Deployment

```bash
# 1. Deploy Lambda
zip c360.zip c360-api.py
aws lambda create-function --function-name aibank-c360-api \
  --runtime python3.12 --handler lambda_function.lambda_handler \
  --role <execution-role-arn> --zip-file fileb://c360.zip

# 2. Deploy Frontend
aws s3 cp portal/customer-360.html s3://<ui-bucket>/employee/rm/customer-360.html

# 3. Create API Gateway routes
# GET /c360/customers, GET /c360/detail, GET /c360/graph → aibank-c360-api

# 4. Set up Athena path
aws glue create-database --database-input '{"Name":"neptune_c360"}'
# Run athena/glue-table.sql in Athena console

# 5. Console setup (see docs/CONSOLE-SETUP.md)
# - QuickSight dataset + dashboard
# - Quick Suite Chat Agent
# - Embed in portal
```

## Access

- URL: `https://aibank.demoaws.com/employee/rm/customer-360.html?id=CUST20250100`
- Auth: IAM Identity Center SSO (same session as QuickSight + Quick Suite)
- User: `crm@demoaws.com`
