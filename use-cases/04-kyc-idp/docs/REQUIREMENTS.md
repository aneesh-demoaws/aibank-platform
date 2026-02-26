# KYC Document Processing (Use Case 03) — Requirements

## Overview
AI-powered document verification and identity extraction that moves customers from KYC PENDING → VERIFIED. This is the critical data enrichment step that unlocks NBA (Next Best Action) by populating employer, salary, job title, and address data.

## Depends On
| Dependency | Status |
|-----------|--------|
| Foundation (Aurora, Cognito) | ✅ Done |
| Customer Onboarding (account creation) | ✅ Done |

## Unlocks
| Downstream | What KYC Provides |
|-----------|-------------------|
| NBA — Neptune Graph | Employer → WORKS_AT edges, City → LIVES_IN edges |
| NBA — Personalize | User metadata: salary_band, segment |
| NBA — Eligibility | Verified identity required for loans, credit cards |
| Customer 360 | Complete employment_info, financial health scoring |
| Loan Applications | Salary verification, employer confirmation |

## Customer Journey
```
Customer logs in (KYC = PENDING)
  → Dashboard shows "Complete your verification" banner
  → Upload: National ID / Passport + Salary Certificate + Proof of Address
  → AI extracts: name, DOB, nationality, ID number, employer, salary, address
  → Validation: extracted data vs onboarding data (name, DOB match?)
  → If match: KYC = VERIFIED, employment_info updated in Aurora
  → If mismatch: KYC = PROCESSING (manual review queue)
```

## Key Requirements
| ID | Requirement | Priority | Notes |
|----|-------------|----------|-------|
| KY-01 | Document upload UI (ID, salary cert, proof of address) | P0 | S3 presigned URLs, max 10MB per doc |
| KY-02 | AI document extraction (Bedrock Data Automation) | P0 | Extract structured fields via custom blueprints |
| KY-03 | Intelligent document classification (BDA) | P0 | Auto-classify: passport vs CPR vs license vs salary cert |
| KY-04 | Data validation — match extracted vs onboarding data | P0 | Name, DOB, nationality cross-check |
| KY-05 | Employment info extraction from salary certificate | P0 | Employer name, job title, monthly salary |
| KY-06 | Address extraction from utility bill / proof of address | P0 | City, country, area |
| KY-07 | KYC status update in Aurora (PENDING → VERIFIED/REJECTED) | P0 | Update customers.kyc_status |
| KY-08 | Populate employment_info JSON in Aurora | P0 | {employer, job_title, monthly_salary, employment_type, city} |
| KY-09 | Step Functions workflow orchestration | P0 | Upload → Extract → Validate → Update |
| KY-10 | Manual review queue for mismatches | P1 | RM dashboard for PROCESSING status |
| KY-11 | Document storage in S3 with encryption | P0 | SSE-S3, lifecycle policy |
| KY-12 | GCC document support (CPR, Iqama, Emirates ID) | P0 | Bahrain CPR, Saudi Iqama, UAE Emirates ID |

## GCC Document Types
| Country | ID Document | Format |
|---------|-----------|--------|
| BH | CPR (Central Population Registry) card | 9-digit number |
| SA | Iqama (Residency Permit) or National ID | 10-digit number |
| AE | Emirates ID | 15-digit number |
| All | Passport | Standard ICAO format |

## Status: NEXT TO BUILD
