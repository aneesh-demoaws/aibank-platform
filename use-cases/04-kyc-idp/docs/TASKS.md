# KYC — Intelligent Document Processing (Use Case 03) — Tasks

## Architecture Source
Proven pipeline from neobank.demoaws.com — see [EXISTING-NEOBANK-KYC-REVIEW.md](./EXISTING-NEOBANK-KYC-REVIEW.md)

## Prerequisites
- [x] Foundation (Aurora, Cognito, SES)
- [x] Customer Onboarding (customers exist with KYC = PENDING)
- [x] Synthetic Data (82 customers with employment_info pre-populated)
- [x] NeoBank KYC pipeline reviewed and documented

## Phase 1: Infrastructure
- [ ] S3 bucket `aibank-kyc-processing` in me-south-1 (SSE-S3, lifecycle)
- [ ] DynamoDB table `aibank-customer-kyc` in me-south-1 (Stream enabled, NEW_AND_OLD_IMAGES)
- [ ] Check BDA availability in eu-west-1 (fallback: us-west-2)
- [ ] BDA project `AIBank-KYC` with blueprints:
  - [ ] Reuse: Passport, Bahrain CPR, Bahrain License
  - [ ] New: Salary Certificate (employer, salary, job_title)
  - [ ] New: Saudi Iqama
  - [ ] New: UAE Emirates ID
- [ ] IAM roles for Lambda → S3, DynamoDB, BDA, Aurora Data API

## Phase 2: Lambda Pipeline (adapt from NeoBank)
- [ ] Lambda 1: `aibank-kyc-presigned-url` — generate S3 upload URLs
  - Adapt from: kyc-upload-presigned-url (change bucket, CORS origin)
- [ ] Lambda 2: `aibank-kyc-document-processor` — validate + DynamoDB record
  - Adapt from: kyc-upload-document-processor (change table name, bucket)
- [ ] Lambda 3: `aibank-kyc-bda-extraction` — BDA invoke + poll + extract + map
  - Adapt from: neobank-kyc-document-id-extraction (change BDA project ARN, add salary cert mapping)
- [ ] Lambda 4: `aibank-kyc-verification` — cross-check extracted vs onboarding data
  - Adapt from: neobank-kyc-verification (replace mock with real name/DOB match)
- [ ] Lambda 5: `aibank-kyc-sync` — DynamoDB Stream → Aurora + NBA enrichment
  - Adapt from: kyc-sync-stream-dev (change to Data API, add employment_info, add Neptune/Personalize triggers)
- [ ] S3 event notifications → Lambda 2 + Lambda 3
- [ ] DynamoDB Stream → Lambda 5
- [ ] API Gateway endpoint: POST /kyc/upload/presigned-url

## Phase 3: Frontend
- [ ] KYC upload page in customer banking portal
  - Document upload UI (identity + address sections)
  - Drag-and-drop with file type validation
  - Progress: uploading → processing → verified/rejected
- [ ] Dashboard banner for KYC PENDING customers
- [ ] Add use case card: "KYC — Intelligent Document Processing" (status: live)

## Phase 4: Verification & Sync
- [ ] Real verification logic: match BDA-extracted name/DOB vs Aurora customer record
- [ ] Confidence scoring: exact match (100%), fuzzy match (>80%), mismatch (<80%)
- [ ] On VERIFIED: populate employment_info JSON in Aurora from salary certificate
- [ ] On VERIFIED: send confirmation email via SES
- [ ] Manual review queue for PROCESSING/REJECTED (RM dashboard)

## Phase 5: NBA Data Enrichment (post-KYC triggers)
- [ ] On VERIFIED + employment_info available:
  - [ ] Neptune: CREATE Customer→WORKS_AT→Employer edge
  - [ ] Neptune: CREATE Customer→LIVES_IN→Location edge
  - [ ] Personalize: UpdateUser with salary_band, segment
  - [ ] Recalculate customer_360_metrics

## Status: NEXT TO BUILD ← START HERE
