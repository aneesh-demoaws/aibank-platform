# KYC — Intelligent Document Processing — Requirements

## Overview
AI-powered document verification and identity extraction integrated into the Alma Banking Assistant chat experience. Customers upload documents directly through the chat interface — Bedrock Data Automation (BDA) extracts structured fields, validates against onboarding data, and moves customers from KYC PENDING → VERIFIED.

## Depends On
| Dependency | Status |
|-----------|--------|
| Foundation (Aurora, Cognito, Sessions) | ✅ Done |
| Customer Onboarding (account creation) | ✅ Done |
| Alma Banking Assistant (chat + voice) | ✅ Done |

## Unlocks
| Downstream | What KYC Provides |
|-----------|-------------------|
| NBA — Neptune Graph | Employer → WORKS_AT edges, City → LIVES_IN edges |
| NBA — Personalize | User metadata: salary_band, segment |
| NBA — Eligibility | Verified identity required for loans, credit cards |
| Customer 360 | Complete employment_info, financial health scoring |
| Loan Applications | Salary verification, employer confirmation |

## Customer Journey (Chat-Integrated)
```
Customer logs in → Alma Banking Assistant
  → Alma detects KYC = PENDING, proactively offers verification
  → OR customer asks "How do I verify my identity?"
  → Alma: "I can help! Please upload your Passport or Bahrain CPR"
  → Customer uploads document via chat file upload widget
  → Document → S3 presigned URL → BDA extracts fields
  → Alma: "I found your details: [name], [DOB], [nationality]. Uploading your second ID..."
  → After 2 ID docs + 1 address doc:
    → Auto-verification: extracted name/DOB vs onboarding data
    → If match (>80%): KYC = VERIFIED ✅
    → If mismatch: KYC = PROCESSING (manual review)
  → Alma: "Great news! Your identity has been verified. You now have full access."
```

## Key Requirements
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| KY-01 | Chat-integrated document upload | P0 | Presigned URLs via Alma tool, max 10MB per doc |
| KY-02 | AI document extraction (BDA eu-west-1) | P0 | Custom blueprints: Passport, CPR, License |
| KY-03 | Intelligent document classification | P0 | BDA auto-classifies against all blueprints |
| KY-04 | Data validation — extracted vs onboarding | P0 | Name, DOB, nationality cross-check |
| KY-05 | DynamoDB intermediary for processing state | P0 | Atomic counters, stream triggers, extraction data |
| KY-06 | Aurora sync via DynamoDB Streams | P0 | kyc_status update in core banking |
| KY-07 | Alma real-time status updates | P0 | Agent reports extraction results + verification status |
| KY-08 | Document storage in S3 with encryption | P0 | SSE-S3, lifecycle policy, me-south-1 |
| KY-09 | GCC document support (Phase 1: BH) | P0 | Bahrain CPR, Passport, License |
| KY-10 | Salary certificate extraction | P1 | Employer, job_title, monthly_salary (Phase 2) |
| KY-11 | Saudi Iqama + UAE Emirates ID | P1 | Phase 2 blueprints |
| KY-12 | Manual review queue for mismatches | P1 | RM dashboard for PROCESSING status |
| KY-13 | Employment info population in Aurora | P1 | Phase 2: employment_info JSON on VERIFIED |

## GCC Document Types
| Country | ID Document | Format | Phase |
|---------|-----------|--------|-------|
| BH | CPR (Central Population Registry) card | 9-digit number | Phase 1 |
| BH | Driving License | License number | Phase 1 |
| All | Passport | Standard ICAO format | Phase 1 |
| SA | Iqama (Residency Permit) | 10-digit number | Phase 2 |
| AE | Emirates ID | 15-digit number | Phase 2 |

## Two-Database Architecture (Proven from NeoBank)
| Database | Region | Purpose |
|----------|--------|---------|
| DynamoDB `aibank-customer-kyc` | me-south-1 | Processing state: extracted fields, counters, triggers, confidence scores |
| Aurora `corebanking.customers` | me-south-1 | Source of truth: kyc_status column (PENDING/VERIFIED/REJECTED) |

**Why DynamoDB intermediary:**
- Atomic counters for concurrent document uploads (no race conditions)
- DynamoDB Streams for event-driven Aurora sync (no polling)
- Trigger logic: 2 ID docs + 1 address doc = invoke verification
- Semi-structured extraction data varies by document type
- TTL for auto-expiry of old processing records
- Clean separation: processing state vs banking state

## Status: 🚧 IN PROGRESS
