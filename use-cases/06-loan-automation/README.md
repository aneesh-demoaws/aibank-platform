# 06 — Loan Automation (5Cs Credit Assessment)

End-to-end AI-powered loan processing pipeline for AI Bank. Customer submits a loan application with documents → BDA extracts data → Step Functions orchestrates 24 Lambda functions for 5Cs credit assessment → loan officer reviews and decides.

## Architecture

```
Customer Portal (loans.html)
    ↓ /apply → DynamoDB (aibank-personal-loan)
    ↓ Upload docs → S3 → BDA extraction
    ↓ Document Processor → sets status: processing
    ↓ DynamoDB Stream Trigger
    ↓
Step Functions: 5Cs Loan Processing Workflow
    ├── Stage 0: Document Validation → Fraud Screening → Customer Profile
    ├── Stage 1: Customer Segmentation → Segment Config
    ├── Stage 2 (Parallel):
    │   ├── Credit Bureau / DTI Analysis
    │   ├── KYC Data Analysis → Social Media Analysis
    │   ├── Employer Analysis → Company Analysis → AI Summary
    │   └── Financial Behaviour Analysis (Aurora transactions)
    ├── Stage 3: Loan Underwriting (AI recommendation)
    └── Decision: Auto-approve / Manual Review / Reject
        ↓
Loan Officer Portal (loan-queue → application-review)
    ↓ Approve / Reject → DynamoDB status update
```

## Components

### Lambda Functions

| Function | Purpose |
|----------|---------|
| **loan-agent** | Customer-facing API: `/apply`, `/loans`, `/upload-urls` |
| **loan-reviewer** | Officer API: `/loans/pending`, `/application`, `/decisions` |
| **session-api** | Cookie-based session management (customer + employee pools) |
| **loan-api-authorizer** | Custom API Gateway authorizer (validates `aibank_sid` cookie) |

### Pipeline (Step Functions)

| Stage | Lambda | Description |
|-------|--------|-------------|
| 0 | document-validation | Validates uploaded documents |
| 0 | fraud-screening | Checks fraud indicators |
| 0 | customer-profile | Fetches KYC from `aibank-customer-kyc` (me-south-1), determines nationality/age |
| 1 | customer-segmentation | Classifies: Local/Expat × SalaryAccount/NonSalary |
| 1 | segment-config | Loads segment-specific thresholds from SSM |
| 2 | kyc-data-analysis | Writes full KYC details to loan record |
| 2 | social-media-analysis | LinkedIn search using Tavily + Strands AI agent |
| 2 | employer-analysis | Company financial data via Tavily search |
| 2 | company-analysis-summary | AI summary of employer stability |
| 2 | financial-profile-analysis-summary | Transaction analysis from Aurora via RDS Data API |
| 2 | stream-trigger | DynamoDB stream → starts Step Functions |
| 3 | loan-underwriting-summary | Final 5Cs AI assessment + recommendation |
| Post | decision-engine, notification-dispatcher, loan-terms-calculator | Post-decision processing |

### Portal

| File | Description |
|------|-------------|
| `customer/loans.html` | Loan application form with document upload |
| `officer/loan-queue.html` | Pending applications queue |
| `officer/application-review.html` | Full 5Cs review report with approve/reject |

## Data Flow

- **DynamoDB** `aibank-personal-loan` (eu-west-1): PK=`customer_id`, SK=`application_id`
- **DynamoDB** `aibank-customer-kyc` (me-south-1): PK=`customer_id` — BDA-extracted KYC
- **Aurora MySQL** `corebanking` (me-south-1): `customers`, `accounts`, `transactions`
- **S3** `aibank-loan-documents-*`: Uploaded salary certificates and bank statements

## Key Design Decisions

- `customer_id` uses internal `CUST00000001` format (not email)
- KYC source of truth: `aibank-customer-kyc` DynamoDB (document-verified)
- `social_name` from Aurora `customers` table overrides KYC name for LinkedIn search
- Aurora accessed via RDS Data API (cross-region me-south-1 from eu-west-1 Lambdas)
- Session cookie (`aibank_sid`) auth — no anonymous access on any resource
- Strands AI agents use `eu.anthropic.claude-3-haiku` for analysis summaries
- BDA runs in eu-west-1 for document extraction
