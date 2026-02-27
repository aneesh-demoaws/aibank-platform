# KYC — Intelligent Document Processing — Tasks

## Architecture Source
Adapted from NeoBank KYC pipeline — see [EXISTING-NEOBANK-KYC-REVIEW.md](./EXISTING-NEOBANK-KYC-REVIEW.md)

## Prerequisites
- [x] Foundation (Aurora, Cognito, SES)
- [x] Customer Onboarding (customers exist with KYC = PENDING)
- [x] Alma Banking Assistant (chat + voice working)
- [x] NeoBank KYC pipeline reviewed and documented
- [x] NeoBank Lambda code downloaded and analyzed
- [x] BDA blueprints recreated in eu-west-1
- [x] BDA project AIBank-KYC created in eu-west-1

## Phase 1: Infrastructure ← START HERE

### 1.1 S3 Bucket
- [ ] Create `aibank-kyc-documents` in me-south-1
  - SSE-S3 encryption
  - Lifecycle: move to IA after 90 days, delete after 1 year
  - CORS: allow aibank.demoaws.com PUT for presigned uploads
  - Event notifications → Lambda (for eu-west-1 Lambdas, use EventBridge or SNS cross-region)

### 1.2 DynamoDB Table
- [ ] Create `aibank-customer-kyc` in me-south-1
  - Partition key: `customer_id` (String)
  - Stream: NEW_AND_OLD_IMAGES
  - On-demand capacity
  - TTL on `ttl_expiry` field (set to 90 days after last_updated)

### 1.3 IAM Roles
- [ ] `aibank-kyc-presigned-url-role` — Lambda → S3 PutObject (me-south-1)
- [ ] `aibank-kyc-processor-role` — Lambda → S3 Get/Put, DynamoDB CRUD (cross-region)
- [ ] `aibank-kyc-bda-role` — Lambda → S3, DynamoDB, BDA InvokeDataAutomation (eu-west-1)
- [ ] `aibank-kyc-verification-role` — Lambda → DynamoDB, Aurora Data API (cross-region)
- [ ] `aibank-kyc-sync-role` — Lambda → DynamoDB Stream, Aurora Data API (cross-region)

## Phase 2: Lambda Pipeline (adapt from NeoBank)

### 2.1 Presigned URL Generator
- [ ] Lambda: `aibank-kyc-presigned-url` (eu-west-1)
  - Adapt from: `kyc-upload-presigned-url`
  - Changes: bucket → `aibank-kyc-documents`, region → me-south-1, CORS → aibank.demoaws.com
  - API Gateway: POST /kyc/upload/presigned-url (authenticated via session cookie)

### 2.2 Document Processor
- [ ] Lambda: `aibank-kyc-document-processor` (eu-west-1)
  - Adapt from: `kyc-upload-document-processor`
  - Trigger: S3 ObjectCreated (via EventBridge cross-region from me-south-1)
  - Changes: DynamoDB table → `aibank-customer-kyc` (me-south-1), bucket name

### 2.3 BDA Extraction (Core Processor)
- [ ] Lambda: `aibank-kyc-bda-extraction` (eu-west-1)
  - Adapt from: `neobank-kyc-document-id-extraction`
  - Changes:
    - BDA project ARN → `arn:aws:bedrock:eu-west-1:...:data-automation-project/8f1b377c2305`
    - BDA profile ARN → `arn:aws:bedrock:eu-west-1:519124228967:data-automation-profile/eu.data-automation-v1`
    - DynamoDB table → `aibank-customer-kyc` (me-south-1)
    - S3 bucket → `aibank-kyc-documents` (me-south-1)
  - Timeout: 900s (BDA processing takes 30-120s)

### 2.4 Verification
- [ ] Lambda: `aibank-kyc-verification` (eu-west-1)
  - Adapt from: `neobank-kyc-verification`
  - **Replace mock with real verification:**
    - Query Aurora Data API for customer's onboarding name + DOB
    - Fuzzy match extracted name vs onboarding name (Levenshtein or similar)
    - Exact match DOB (normalize DD/MM/YYYY vs YYYY-MM-DD)
    - Confidence: exact=1.0, fuzzy>0.8=PASS, <0.8=REJECT
  - Update DynamoDB with verification_details + confidence scores

### 2.5 Aurora Sync (DynamoDB Stream)
- [ ] Lambda: `aibank-kyc-sync` (eu-west-1)
  - Adapt from: `kyc-sync-stream-dev`
  - **Replace pymysql with Aurora Data API** (no VPC needed)
  - Trigger: DynamoDB Stream from `aibank-customer-kyc`
  - On kyc_status change → `UPDATE customers SET kyc_status = :s WHERE customer_id = :c`
  - Cross-region: Lambda eu-west-1 → Aurora Data API me-south-1

## Phase 3: Alma Chat Integration

### 3.1 KYC Upload Tool
- [ ] Add `generate_kyc_upload_url` tool to Alma Banking Agent
  - Calls presigned URL Lambda
  - Returns upload URL to frontend
  - Frontend shows file upload widget in chat

### 3.2 KYC Status Tool
- [ ] Add `check_kyc_status` tool to Alma Banking Agent
  - Queries DynamoDB for current KYC processing state
  - Reports: documents collected, verification status, what's still needed

### 3.3 Frontend Upload Widget
- [ ] Add file upload capability to alma-chat.html
  - Drag-and-drop or click-to-upload in chat
  - Calls presigned URL API, uploads to S3
  - Shows progress: uploading → processing → extracted → verified

### 3.4 Proactive KYC Prompt
- [ ] Alma detects KYC=PENDING on login and offers verification
  - "I notice your identity isn't verified yet. Would you like to complete that now?"

## Phase 4: Testing & Validation
- [ ] Test with real documents: Passport, CPR, License
- [ ] Verify BDA extraction accuracy in eu-west-1 (same as us-west-2?)
- [ ] Test concurrent uploads (2 docs simultaneously)
- [ ] Test verification: matching name/DOB, mismatching name/DOB
- [ ] Test DynamoDB Stream → Aurora sync latency
- [ ] End-to-end: upload via chat → VERIFIED in Aurora

## Phase 5: Phase 2 Features (Future)
- [ ] Salary Certificate blueprint + extraction
- [ ] Saudi Iqama blueprint
- [ ] UAE Emirates ID blueprint
- [ ] employment_info JSON population in Aurora on VERIFIED
- [ ] Neptune graph enrichment (WORKS_AT, LIVES_IN edges)
- [ ] Personalize user metadata update (salary_band, segment)
- [ ] Manual review queue for PROCESSING/REJECTED

## Status: 🚧 Phase 1 — Infrastructure
