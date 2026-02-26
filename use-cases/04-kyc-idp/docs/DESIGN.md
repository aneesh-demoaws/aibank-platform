# KYC — Intelligent Document Processing (Use Case 03)

## Design — Based on Proven NeoBank Architecture

**Source:** neobank.demoaws.com KYC pipeline (us-west-2) — reviewed and documented in [EXISTING-NEOBANK-KYC-REVIEW.md](./EXISTING-NEOBANK-KYC-REVIEW.md)

## Depends On
Foundation ✅, Customer Onboarding ✅

## Proven Architecture (Reuse from NeoBank)

```
Customer uploads docs via presigned URL
    │
    ▼
API Gateway → Lambda (presigned URL generator)
    │
    ▼ (direct S3 upload from browser)
S3: aibank-kyc-processing/documents/input/{customer_id}/{type}/{uuid}_{file}
    │
    ├──▶ Lambda: document-processor (S3 trigger)
    │    • Validate file (≤10MB, PDF/JPG/PNG)
    │    • Create DynamoDB record (PROCESSING)
    │
    └──▶ Lambda: bda-extraction (S3 trigger) ← CORE PROCESSOR
         │
         ▼
    Bedrock Data Automation (BDA)
    ┌─────────────────────────────────────────────┐
    │  Project: AIBank-KYC                         │
    │                                              │
    │  Blueprints (reuse + new):                   │
    │  ✅ Passport_Blueprint (reuse)               │
    │  ✅ Bahrain_CPR_v2 (reuse)                   │
    │  ✅ Bahrain_License (reuse)                   │
    │  🆕 Salary_Certificate (NEW — for NBA)       │
    │  🆕 Saudi_Iqama (NEW — SA customers)         │
    │  🆕 UAE_Emirates_ID (NEW — AE customers)     │
    └─────────────────────────────────────────────┘
         │
         ▼
    DynamoDB: aibank-customer-kyc (intermediary)
    • Stores extracted fields per customer
    • Trigger: 2 ID docs + 1 address → invoke verification
         │
         ├──▶ Lambda: verification
         │    • Cross-check extracted name/DOB vs Aurora onboarding data
         │    • Set kyc_status = VERIFIED or REJECTED
         │
         └──▶ DynamoDB Stream → Lambda: kyc-sync
              • Sync kyc_status to Aurora Core Banking
              • On VERIFIED: populate employment_info JSON
              • On VERIFIED: trigger Neptune + Personalize enrichment (NBA)
```

## Key Differences from NeoBank

| Aspect | NeoBank (us-west-2) | AI Bank |
|--------|-------------------|---------|
| Data region | us-west-2 | me-south-1 (S3, DynamoDB, Aurora) |
| Compute region | us-west-2 | eu-west-1 (Lambda, BDA) |
| BDA project | NeoBank-KYC (6 blueprints) | AIBank-KYC (6+3 new blueprints) |
| Verification | Mock (auto-PASS) | Real cross-check vs onboarding data |
| Sync target | neo-bank-core-banking (pymysql) | aibank-core-banking (Data API) |
| Post-verify | Just status update | + employment_info + Neptune + Personalize |
| GCC docs | Bahrain only | Bahrain + Saudi + UAE |
| Salary cert | Not processed | BDA blueprint extracts employer, salary |

## New BDA Blueprints Needed

| Blueprint | Fields to Extract | Purpose |
|-----------|------------------|---------|
| Salary_Certificate | employer_name, job_title, monthly_salary, employment_date, currency | Populate employment_info → Neptune WORKS_AT edge |
| Saudi_Iqama | iqama_number, name, nationality, DOB, expiry, employer, occupation | SA customer identity |
| UAE_Emirates_ID | emirates_id, name, nationality, DOB, expiry, card_number | AE customer identity |

## Post-Verification Data Flow (NEW — feeds NBA)

```
KYC VERIFIED
  │
  ├── Aurora: UPDATE customers SET kyc_status='VERIFIED'
  │
  ├── Aurora: UPDATE customers SET employment_info = JSON
  │   {employer, job_title, monthly_salary, employment_type, city}
  │   (extracted from salary certificate by BDA)
  │
  ├── Neptune: CREATE (:Customer)-[:WORKS_AT]->(:Employer)
  │            CREATE (:Customer)-[:LIVES_IN]->(:Location)
  │
  ├── Personalize: UpdateUser(salary_band, segment, country)
  │
  └── customer_360_metrics: Recalculate financial health
```

## AWS Services

| Service | Region | Purpose |
|---------|--------|---------|
| S3 | me-south-1 | Document storage (data residency) |
| Bedrock Data Automation | eu-west-1* | Document classification + extraction |
| DynamoDB | me-south-1 | Intermediary KYC state |
| Lambda (5 functions) | eu-west-1 | Processing pipeline |
| API Gateway | eu-west-1 | Presigned URL endpoint |
| Aurora | me-south-1 | Core Banking (sync target) |

*BDA availability in eu-west-1 needs verification. Fallback: us-west-2 with cross-region S3 access.

## Status: NEXT TO BUILD
