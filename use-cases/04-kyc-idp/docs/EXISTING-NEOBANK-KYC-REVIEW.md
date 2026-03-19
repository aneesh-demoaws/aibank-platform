# NeoBank KYC Implementation Review
## Existing Architecture on neobank.demoaws.com (us-west-2)

**Reviewed:** 2026-02-26
**Purpose:** Reuse this proven architecture for AI Bank KYC (me-south-1 / eu-west-1)

---

## Architecture Flow

```
Customer uploads document (PDF/JPG)
    │
    ▼
┌─ API Gateway (neo-bank-kyc-api) ─────────────────────────────┐
│  POST /kyc/upload/presigned-url                               │
│  → Lambda: kyc-upload-presigned-url                           │
│  → Returns S3 presigned PUT URL                               │
└───────────────────────────────────────────────────────────────┘
    │
    ▼ (Frontend uploads directly to S3)
┌─ S3: neobank-kyc-processing ─────────────────────────────────┐
│  documents/input/{customer_id}/{type}/{uuid}_{filename}.pdf   │
│  type = "identity" | "address"                                │
│  S3 Event Notification on ObjectCreated                       │
└───────────────────────────────────────────────────────────────┘
    │
    ├──▶ Lambda: kyc-upload-document-processor (S3 trigger)
    │    • Validates file (size ≤10MB, type PDF/JPG/PNG)
    │    • Extracts customer_id + doc_type from S3 key path
    │    • Creates/updates DynamoDB record (kyc_status = PROCESSING)
    │    • Moves invalid files to documents/quarantine/
    │
    └──▶ Lambda: neobank-kyc-document-id-extraction (S3 trigger)
         • THE MAIN PROCESSOR — uses Bedrock Data Automation (BDA)
         │
         ▼
┌─ Bedrock Data Automation (BDA) ──────────────────────────────┐
│  Project: NeoBank-KYC                                         │
│  ARN: arn:aws:bedrock:us-west-2:...:data-automation-project/  │
│        7640a4dae6f7                                           │
│                                                               │
│  6 Custom Blueprints:                                         │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ 1. Passport_Blueprint    — passport number, name, DOB,  │ │
│  │                            nationality, gender, expiry   │ │
│  │ 2. Bahrain_CPR_v2        — personal_number (CPR), name, │ │
│  │                            nationality, DOB, expiry      │ │
│  │ 3. Bahrain_License       — licence_number, name, gender,│ │
│  │                            nationality, DOB, expiry,     │ │
│  │                            address                       │ │
│  │ 4. Vehicle_Ownership_Certificate (bonus)                 │ │
│  │ 5. mileage_extraction_video (bonus)                      │ │
│  │ 6. Accident_Image_Processing_v2 (bonus)                  │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                               │
│  Flow: invoke_data_automation_async → poll status → read      │
│        output from S3 → extract inference_result              │
│                                                               │
│  Output: documents/output/{customer_id}/{type}/               │
│          → job metadata JSON → segment_metadata →             │
│            custom_output_path → inference_result JSON          │
└───────────────────────────────────────────────────────────────┘
         │
         ▼
┌─ DynamoDB: neo-bank-customer-kyc (Intermediary) ─────────────┐
│  Partition Key: customer_id                                   │
│  Stream: NEW_AND_OLD_IMAGES enabled                           │
│                                                               │
│  Schema (from real data):                                     │
│  {                                                            │
│    customer_id: "CUST20250101",                               │
│    kyc_status: "VERIFIED",                                    │
│    full_name: "ANEESH MOHAN",                                 │
│    date_of_birth: "13/01/1986",                               │
│    gender: "Male",                                            │
│    nationality: "INDIAN",                                     │
│    passport_number: "Z2693882",                               │
│    id_number: "860126340",        // CPR number               │
│    document_expiry: "11/06/2028",                             │
│    address_document_type: "Bahrain_CPR_v2",                   │
│    total_id_collected_no: 2,                                  │
│    total_id_verified_no: 2,                                   │
│    total_address_collected_no: 1,                             │
│    total_address_verified_no: 1,                              │
│    verification_details: {                                    │
│      overall_status: "VERIFIED",                              │
│      identity_verification: { status: "PASSED", conf: 0.95 },│
│      address_verification: { status: "PASSED", conf: 0.90 }  │
│    }                                                          │
│  }                                                            │
│                                                               │
│  Trigger Logic:                                               │
│  • ID docs collected ≥ 2 AND address docs ≥ 1                │
│    → Set verified_no = -1 (trigger flag)                      │
│    → Invoke verification Lambda                               │
└───────────────────────────────────────────────────────────────┘
         │
         ├──▶ Lambda: neobank-kyc-verification (async invoke)
         │    • Mock verification (auto-PASS for demo)
         │    • Sets kyc_status = VERIFIED in DynamoDB
         │    • Sets verification_details with confidence scores
         │
         └──▶ DynamoDB Stream → Lambda: kyc-sync-stream-dev
              • Watches for kyc_status changes
              • Syncs to Core Banking Aurora MySQL:
                UPDATE customers SET kyc_status = %s WHERE customer_id = %s
              • Uses pymysql direct connection (not Data API)

```

---

## Lambda Functions Summary

| Function | Trigger | Purpose | Runtime | Timeout |
|----------|---------|---------|---------|---------|
| `kyc-upload-presigned-url` | API Gateway POST | Generate S3 presigned upload URL | Python 3.12 | 30s |
| `kyc-upload-document-processor` | S3 ObjectCreated | Validate file, create DynamoDB record | Python 3.12 | 300s |
| `neobank-kyc-document-id-extraction` | S3 ObjectCreated | BDA processing, extract fields, update DynamoDB | Python 3.12 | 900s |
| `neobank-kyc-verification` | Lambda invoke (async) | Verify extracted data, set VERIFIED status | Python 3.12 | 300s |
| `kyc-sync-stream-dev` | DynamoDB Stream | Sync kyc_status to Aurora Core Banking | Python 3.9 | 60s |

## BDA Processing Details

**Invocation pattern:**
```python
response = run_client.invoke_data_automation_async(
    dataAutomationConfiguration={
        "dataAutomationProjectArn": project_arn,
        "stage": "LIVE"
    },
    dataAutomationProfileArn="arn:aws:bedrock:us-west-2:...:data-automation-profile/us.data-automation-v1",
    inputConfiguration={'s3Uri': f"s3://{bucket}/{key}"},
    outputConfiguration={'s3Uri': f"s3://{bucket}/documents/output/{customer_id}/{doc_type}"}
)
# Then poll with get_data_automation_status until Success
```

**Blueprint matching:**
- BDA auto-classifies the document against all 6 blueprints
- Returns `custom_output_status: "MATCH"` for the matching blueprint
- `inference_result` contains extracted key-value pairs
- Blueprint name tells us the document type (Passport, CPR, License)

**Field mapping (blueprint → DynamoDB):**
- Passport: passport_number, name, gender, nationality, DOB, expiry
- CPR: personal_number → id_number, name, nationality, DOB, expiry
- License: licence_number, name, gender, nationality, DOB, expiry, address

---

## What to Reuse for AI Bank

### Keep As-Is
1. **S3 presigned URL pattern** — secure client-side upload
2. **BDA project with custom blueprints** — Passport, CPR, License extraction
3. **DynamoDB as intermediary** — decouple extraction from verification
4. **DynamoDB Stream → Aurora sync** — event-driven status propagation
5. **Trigger logic** — 2 ID docs + 1 address doc = ready for verification

### Adapt for AI Bank
1. **Region:** us-west-2 → me-south-1 (data) + eu-west-1 (compute/BDA)
2. **S3 bucket:** neobank-kyc-processing → aibank-kyc-processing
3. **DynamoDB table:** neo-bank-customer-kyc → aibank-customer-kyc
4. **Aurora target:** aibank-core-banking cluster (me-south-1)
5. **BDA region:** Check BDA availability in eu-west-1, may need us-west-2
6. **Add salary certificate blueprint** — extract employer, job_title, monthly_salary
7. **Add Iqama (Saudi) and Emirates ID blueprints** — for SA/AE customers
8. **Real verification** — replace mock with actual cross-check (extracted name/DOB vs onboarding data)
9. **NBA enrichment triggers** — on VERIFIED: update Neptune graph + Personalize user metadata
10. **CORS origin:** neobank.demoaws.com → aibank.demoaws.com

### New for AI Bank (not in NeoBank)
1. **Salary certificate processing** — BDA blueprint to extract employer, salary, job_title
2. **employment_info JSON population** — write to Aurora customers.employment_info
3. **Neptune graph enrichment** — create WORKS_AT, LIVES_IN edges on VERIFIED
4. **Personalize user update** — update salary_band, segment metadata
5. **GCC multi-country docs** — Saudi Iqama, UAE Emirates ID blueprints

---

## S3 Bucket Structure
```
neobank-kyc-processing/
├── documents/
│   ├── input/{customer_id}/
│   │   ├── identity/    ← Passport, CPR, License, Iqama, Emirates ID
│   │   └── address/     ← Utility bill, CPR (also serves as address proof)
│   ├── output/{customer_id}/
│   │   ├── identity/    ← BDA extraction results (JSON)
│   │   └── address/     ← BDA extraction results (JSON)
│   ├── quarantine/      ← Invalid files
│   └── error/           ← Processing failures
```

## Real Document Samples in S3
- Aneesh_Passport_2_FirstPage.pdf (1.2MB)
- Aneesh-Bahrain-CPR.pdf (652KB)
- Aneesh-Bahrain-License.pdf (659KB)
- Salary_Certificate.pdf (5.8MB)
- December Stmt.pdf (4.4MB) — bank statement as address proof
