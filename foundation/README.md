# Foundation Layer

Shared infrastructure required by all use cases. Deploy this first.

## Components

| Module | Creates | Region |
|--------|---------|--------|
| `01-aurora` | Aurora Serverless v2 + 15-table core banking schema | me-south-1 |
| `02-cognito` | Cognito User Pool with custom:customer_id attribute | me-south-1 |
| `03-ses` | Cross-account SES credentials in Secrets Manager (optional) | eu-west-1 |

## Schema Tables

**Core**: customers, accounts, transactions, otp_codes
**Loans**: loan_applications, loan_workflow_steps, loan_documents, loan_decisions, loan_contracts, loan_segment_configs
**Analytics**: next_best_offers, customer_360_metrics, customer_insights, customer_goals
**Views**: customer_360_summary
