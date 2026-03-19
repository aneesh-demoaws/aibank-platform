# KYC — Intelligent Document Processing — Design

## Architecture Source
Adapted from proven NeoBank KYC pipeline (us-west-2) — see [EXISTING-NEOBANK-KYC-REVIEW.md](./EXISTING-NEOBANK-KYC-REVIEW.md)

## Key Decision: eu-west-1 for BDA
BDA is available in eu-west-1. Blueprints recreated from NeoBank (they're just JSON schemas, no training needed). All compute in eu-west-1, data in me-south-1.

## Architecture

```
Customer in Alma Banking Chat
  → "I want to verify my identity"
  → Alma tool: generate_kyc_upload_url(customer_id, doc_type)
    │
    ▼
API Gateway (eu-west-1) → Lambda: aibank-kyc-presigned-url
  → Returns S3 presigned PUT URL
    │
    ▼ (Frontend uploads directly to S3)
S3: aibank-kyc-documents (me-south-1)
  documents/input/{customer_id}/{type}/{uuid}_{filename}
  type = "identity" | "address"
    │
    ├──▶ Lambda: aibank-kyc-document-processor (S3 trigger, eu-west-1)
    │    • Validate file (≤10MB, PDF/JPG/PNG)
    │    • Create/update DynamoDB record (PROCESSING)
    │
    └──▶ Lambda: aibank-kyc-bda-extraction (S3 trigger, eu-west-1)
         │
         ▼
    Bedrock Data Automation (eu-west-1)
    ┌─────────────────────────────────────────────┐
    │  Project: AIBank-KYC                         │
    │  ARN: ...data-automation-project/8f1b377c2305│
    │                                              │
    │  Blueprints (Phase 1):                       │
    │  ✅ Passport_Blueprint  (84c71660e8a1)       │
    │  ✅ Bahrain_CPR_v2      (fd3e92741bea)       │
    │  ✅ Bahrain_License     (e509dda6daed)       │
    │                                              │
    │  Phase 2 (to add):                           │
    │  🔲 Salary_Certificate                       │
    │  🔲 Saudi_Iqama                              │
    │  🔲 UAE_Emirates_ID                          │
    └─────────────────────────────────────────────┘
         │
         ▼
    DynamoDB: aibank-customer-kyc (me-south-1)
    • Stores extracted fields per customer
    • Atomic counters for document collection
    • Trigger: 2 ID docs + 1 address → invoke verification
         │
         ├──▶ Lambda: aibank-kyc-verification (async invoke)
         │    • Cross-check extracted name/DOB vs Aurora onboarding data
         │    • Fuzzy match with confidence scoring
         │    • Set kyc_status = VERIFIED (>80%) or REJECTED (<80%)
         │
         └──▶ DynamoDB Stream → Lambda: aibank-kyc-sync
              • Sync kyc_status to Aurora Core Banking (Data API)
              • On VERIFIED: update customers.kyc_status
              • Phase 2: populate employment_info, trigger Neptune/Personalize
```

## AWS Resources

### Already Created ✅
| Resource | Region | ARN / ID |
|----------|--------|----------|
| BDA Project: AIBank-KYC | eu-west-1 | `arn:aws:bedrock:eu-west-1:519124228967:data-automation-project/8f1b377c2305` |
| Blueprint: Passport | eu-west-1 | `arn:aws:bedrock:eu-west-1:519124228967:blueprint/84c71660e8a1` |
| Blueprint: Bahrain_CPR_v2 | eu-west-1 | `arn:aws:bedrock:eu-west-1:519124228967:blueprint/fd3e92741bea` |
| Blueprint: Bahrain_License | eu-west-1 | `arn:aws:bedrock:eu-west-1:519124228967:blueprint/e509dda6daed` |
| Aurora Core Banking | me-south-1 | `arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking` |

### To Create
| Resource | Region | Purpose |
|----------|--------|---------|
| S3: `aibank-kyc-documents` | me-south-1 | Document storage (data residency) |
| DynamoDB: `aibank-customer-kyc` | me-south-1 | Processing state + extraction data |
| Lambda × 4 | eu-west-1 | Pipeline functions |
| API Gateway endpoint | eu-west-1 | Presigned URL generation |
| IAM roles | — | Lambda → S3, DynamoDB, BDA, Aurora Data API |

## Differences from NeoBank

| Aspect | NeoBank (us-west-2) | AI Bank |
|--------|---------------------|---------|
| BDA region | us-west-2 | eu-west-1 ✅ |
| Data region | us-west-2 | me-south-1 (S3, DynamoDB, Aurora) |
| Integration | Separate upload page | Chat-integrated via Alma tool |
| Verification | Mock (auto-PASS) | Real cross-check vs onboarding data |
| Aurora sync | pymysql direct connection | Aurora Data API (serverless) |
| BDA profile ARN | `us.data-automation-v1` | `eu.data-automation-v1` |
| CORS origin | neobank.demoaws.com | aibank.demoaws.com |
| Blueprints | 6 (3 relevant + 3 bonus) | 3 Phase 1 + 3 Phase 2 |

## DynamoDB Schema: aibank-customer-kyc

```json
{
  "customer_id": "CUST00000001",          // Partition Key
  "kyc_status": "VERIFIED",
  "full_name": "ANEESH MOHAN",
  "date_of_birth": "13/01/1986",
  "gender": "Male",
  "nationality": "INDIAN",
  "passport_number": "Z2693882",
  "id_number": "860126340",
  "document_expiry": "11/06/2028",
  "address": "Flat 123, Building 456, Manama",
  "address_document_type": "Bahrain_License",
  "total_id_collected_no": 2,
  "total_id_verified_no": 2,
  "total_address_collected_no": 1,
  "total_address_verified_no": 1,
  "verification_details": {
    "overall_status": "VERIFIED",
    "identity_verification": { "status": "PASSED", "confidence": 0.95 },
    "address_verification": { "status": "PASSED", "confidence": 0.90 },
    "name_match": { "extracted": "ANEESH MOHAN", "onboarding": "Aneesh Mohan", "score": 0.98 },
    "dob_match": { "extracted": "13/01/1986", "onboarding": "1986-01-13", "score": 1.0 }
  },
  "created_at": "2026-02-27T10:00:00Z",
  "last_updated": "2026-02-27T10:05:00Z"
}
```

## S3 Bucket Structure
```
aibank-kyc-documents/
├── documents/
│   ├── input/{customer_id}/
│   │   ├── identity/    ← Passport, CPR, License
│   │   └── address/     ← License (has address), utility bill
│   ├── output/{customer_id}/
│   │   ├── identity/    ← BDA extraction results (JSON)
│   │   └── address/     ← BDA extraction results (JSON)
│   ├── quarantine/      ← Invalid files
│   └── error/           ← Processing failures
```

## Status: 🚧 IN PROGRESS — Phase 1 Infrastructure
