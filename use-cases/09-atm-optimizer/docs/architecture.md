# NeoBank ATM Profitability Optimizer — Architecture Documentation

## 1. High-Level Architecture

The system spans two AWS regions with a strict data sovereignty boundary: all banking data remains in Bahrain (me-south-1), while AI inference runs in Ireland (eu-west-1) where Bedrock AgentCore is available.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        NeoBank Internal Network                                  │
│                                                                              │
│  ┌─────────────────────────────────────┐   ┌──────────────────────────────┐ │
│  │   AWS Bahrain (me-south-1)          │   │  AWS Ireland (eu-west-1)     │ │
│  │   DATA + COMPUTE                    │   │  AI SERVICES ONLY            │ │
│  │                                     │   │                              │ │
│  │  ┌───────────┐  ┌───────────────┐  │   │  ┌────────────────────────┐ │ │
│  │  │ Cognito   │  │ Internal ALB  │  │   │  │ Bedrock AgentCore      │ │ │
│  │  │ User Pool │  │ (HTTPS/443)   │  │   │  │                        │ │ │
│  │  │ MFA+RBAC  │  └───────┬───────┘  │   │  │ ┌──────────────────┐  │ │ │
│  │  └───────────┘          │          │   │  │ │ AgentCore Gateway│  │ │ │
│  │                  ┌──────▼───────┐  │   │  │ │ (MCP routing)    │  │ │ │
│  │                  │ EC2 t3.med   │  │   │  │ └────────┬─────────┘  │ │ │
│  │                  │ Streamlit UI │  │   │  │          │            │ │ │
│  │                  │ Private Only │──┼───┼──│──► Strands Agent     │ │ │
│  │                  └──────────────┘  │   │  │   (Claude Sonnet 4)  │ │ │
│  │                                    │   │  │          │            │ │ │
│  │  ┌───────────┐  ┌──────────────┐  │   │  │ ┌────────▼─────────┐ │ │ │
│  │  │ KMS Keys  │  │ Lambda MCP   │◄─┼───┼──│─│ AgentCore        │ │ │ │
│  │  │ (3 CMKs)  │  │ Function URL │  │   │  │ │ Identity+Memory  │ │ │ │
│  │  └───────────┘  │ IAM auth     │  │   │  │ └──────────────────┘ │ │ │
│  │                  └──────┬───────┘  │   │  │                      │ │ │
│  │                        │          │   │  └────────────────────────┘ │ │
│  │  ┌─────────────────────▼───────┐  │   └──────────────────────────────┘ │
│  │  │ Athena ──► S3 Data Lake     │  │                                    │
│  │  │ (11 tables) (KMS encrypted) │  │                                    │
│  │  └─────────────────────────────┘  │                                    │
│  └─────────────────────────────────────┘                                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 2. Low-Level VPC Architecture (me-south-1)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  VPC: 10.0.0.0/16  (ATM-Profitability-Optimizer-VPC)                    │
│  DNS Support: Enabled | DNS Hostnames: Enabled                          │
│  NO Internet Gateway | NO NAT Gateway | NO Public Subnets               │
│                                                                          │
│  ┌────────────────────────────────┐  ┌────────────────────────────────┐ │
│  │  Private Subnet A: 10.0.1.0/24│  │  Private Subnet B: 10.0.2.0/24│ │
│  │  AZ: me-south-1a              │  │  AZ: me-south-1b              │ │
│  │                                │  │                                │ │
│  │  ┌──────────────────────────┐ │  │                                │ │
│  │  │ EC2 (i-XXXXXXXXXXXXXXXXX)│ │  │                                │ │
│  │  │ t3.medium, AL2023        │ │  │                                │ │
│  │  │ SG: sg-ec2               │ │  │                                │ │
│  │  │  IN:  8501/tcp ← VPC     │ │  │                                │ │
│  │  │  OUT: 443/tcp → VPC      │ │  │                                │ │
│  │  │  OUT: S3 prefix list     │ │  │                                │ │
│  │  │ Role: ATM-...-EC2-Role   │ │  │                                │ │
│  │  └──────────────────────────┘ │  │                                │ │
│  │                                │  │                                │ │
│  │  ┌──────────────────────────┐ │  │  ┌──────────────────────────┐ │ │
│  │  │ Internal ALB             │ │  │  │ Internal ALB             │ │ │
│  │  │ SG: sg-alb               │ │  │  │ (cross-AZ target)       │ │ │
│  │  │  IN:  443/tcp ← VPC     │ │  │  │                          │ │ │
│  │  │  OUT: 8501/tcp → sg-ec2  │ │  │  │                          │ │ │
│  │  └──────────────────────────┘ │  │  └──────────────────────────┘ │ │
│  │                                │  │                                │ │
│  │  ┌──────────────────────────┐ │  │  ┌──────────────────────────┐ │ │
│  │  │ VPC Interface Endpoints  │ │  │  │ VPC Interface Endpoints  │ │ │
│  │  │ SG: sg-endpoints         │ │  │  │ (same SG, Subnet B ENIs)│ │ │
│  │  │  IN: 443/tcp ← VPC      │ │  │  │                          │ │ │
│  │  │                          │ │  │  │                          │ │ │
│  │  │ • athena                 │ │  │  │ • athena                 │ │ │
│  │  │ • cognito-idp            │ │  │  │ • cognito-idp            │ │ │
│  │  │ • kms                    │ │  │  │ • kms                    │ │ │
│  │  │ • logs                   │ │  │  │ • logs                   │ │ │
│  │  │ • ssm                    │ │  │  │ • ssm                    │ │ │
│  │  │ • ssmmessages            │ │  │  │ • ssmmessages            │ │ │
│  │  │ • ec2messages            │ │  │  │ • ec2messages            │ │ │
│  │  └──────────────────────────┘ │  │  └──────────────────────────┘ │ │
│  └────────────────────────────────┘  └────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Route Table: ATM-Profitability-Optimizer-Private-RT               │ │
│  │  ┌──────────────────┬──────────────────────────────────────────┐  │ │
│  │  │ Destination      │ Target                                   │  │ │
│  │  ├──────────────────┼──────────────────────────────────────────┤  │ │
│  │  │ 10.0.0.0/16      │ local                                    │  │ │
│  │  │ S3 prefix list   │ vpce-XXXXXXXXXXXXXXXXX (S3 Gateway Endpoint)           │  │ │
│  │  └──────────────────┴──────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  S3 Gateway Endpoint (vpce-XXXXXXXXXXXXXXXXX)                                    │ │
│  │  Policy: Allow GetObject, PutObject, ListBucket, GetBucketLocation │ │
│  │  - Project bucket: neobank-atm-optimizer-data-ACCOUNT_ID-me-south-1  │ │
│  │  - System S3: Resource '*' (read-only, for AL2023 repos + SSM)     │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  VPC Flow Logs → CloudWatch /vpc/ATM-Profitability-Optimizer/      │ │
│  │  Retention: 90 days | KMS encrypted (ApplicationDataKey)           │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

## 3. Data Flow Diagram

```
User (NeoBank Analyst)
  │
  │ 1. Login (email + MFA/TOTP)
  ▼
┌──────────────────┐
│ Cognito User Pool│ ──► JWT { sub, email, cognito:groups: [admin|operator] }
│ (me-south-1)     │     Token validity: 60 min access, 30 day refresh
└──────────────────┘
  │
  │ 2. Natural language query + JWT
  ▼
┌──────────────────┐
│ Streamlit UI     │ ──► Renders chat, map (Folium), CSV/PDF export
│ EC2 (me-south-1) │
└──────────────────┘
  │
  │ 3. Query + JWT forwarded via PrivateLink
  ▼
┌──────────────────┐
│ AgentCore Gateway│ ──► Routes MCP tool calls, enforces rate limits
│ (eu-west-1)      │
└──────────────────┘
  │
  │ 4. JWT validated, role extracted
  ▼
┌──────────────────┐
│ AgentCore        │ ──► Validates issuer, audience, extracts cognito:groups
│ Identity         │     Maps to admin (13 tools) or operator (5 tools)
│ (eu-west-1)      │
└──────────────────┘
  │
  │ 5. Agent reasons, selects tools based on role
  ▼
┌──────────────────┐
│ Strands Agent    │ ──► Claude Sonnet 4 (temp=0.1, max_tokens=4096)
│ (eu-west-1)      │     System prompt: NeoBank ATM analysis persona
└──────────────────┘
  │
  │ 6. MCP tool call routed to Lambda Function URL (me-south-1)
  ▼
┌──────────────────┐
│ Lambda MCP Server│ ──► 13 tools: query_atm_data, query_branch_proximity,
│ Function URL     │     query_revenue_data, query_maintenance_costs,
│ (me-south-1)     │     query_cash_levels, calculate_impact_analysis,
│ IAM auth (SigV4) │     detect_anomalies, profitability_ranking,
│                  │     query_competitor_analysis, query_coverage_analysis,
│                  │     simulate_competitor_scenario, recommend_atm_placement
└──────────────────┘
  │
  │ 7. SQL query via Athena API (same region)
  ▼
┌──────────────────┐
│ Amazon Athena    │ ──► Workgroup: neobank-atm-optimizer
│ (me-south-1)     │     Database: neobank_atm_optimizer (11 tables)
└──────────────────┘     Results encrypted with KMS
  │
  │ 8. Reads CSV data from S3
  ▼
┌──────────────────┐
│ Amazon S3        │ ──► neobank-atm-optimizer-data-ACCOUNT_ID-me-south-1
│ (me-south-1)     │     11 prefixes, KMS-SSE, versioned, VPC-restricted
└──────────────────┘
```

## 4. Security Architecture

### 4.1 Defense-in-Depth (4 Layers)

```
Layer 1: Cognito Authentication
  └─ MFA required (TOTP), password policy (12+ chars, mixed case, symbols)
  └─ Advanced security: ENFORCED mode
  └─ Groups: admin, operator

Layer 2: AgentCore Identity
  └─ JWT validation (issuer, audience, expiry)
  └─ Role extraction from cognito:groups claim
  └─ Credential management for cross-region access

Layer 3: Strands Agent Role Check
  └─ Tool filtering based on user role
  └─ Operator: 5 tools (query_atm_data, query_branch_proximity, query_revenue_data, query_competitor_analysis, query_coverage_analysis)
  └─ Admin: All 13 tools

Layer 4: AgentCore Gateway Filtering
  └─ MCP tool routing enforcement
  └─ Rate limiting, request validation
```

### 4.2 Encryption

| Data State | Mechanism | Key |
|------------|-----------|-----|
| S3 at rest | SSE-KMS (BucketKey) | neobank-atm-transaction-key |
| Athena results | SSE-KMS | neobank-atm-transaction-key |
| VPC flow logs | KMS | neobank-atm-application-key |
| Cognito tokens | KMS | neobank-atm-user-key |
| In transit (VPC) | TLS 1.3 via VPC endpoints | N/A |
| In transit (ALB) | TLS 1.3 (ELBSecurityPolicy-TLS13-1-2-2021-06) | ACM cert |

### 4.3 Network Isolation

- Zero public subnets, zero internet gateways, zero NAT gateways
- All AWS service access via VPC endpoints (7 interface + 1 gateway)
- S3 bucket policy: Deny all access not from VPC endpoint (when enabled)
- EC2 security group: Inbound only port 8501 from VPC CIDR
- ALB: Internal scheme only, HTTPS from VPC CIDR
- Session Manager (SSM) for admin access — no SSH, no bastion

### 4.4 IAM Least Privilege

| Role | Permissions |
|------|-------------|
| EC2 Role | S3 read-only, Athena query, Cognito read, CloudWatch write, KMS decrypt |
| Lambda MCP Role | Athena query, Glue read, S3 read/write (data + results), KMS decrypt/generate, CloudWatch write |
| VPC Flow Log Role | CloudWatch Logs write only |
| AgentCore Role | Bedrock invoke, STS assume-role for cross-region, Lambda Function URL invoke (SigV4) |

## 5. CloudFormation Stack Dependencies

```
atm-optimizer-kms (no dependencies)
  │
  ├──► atm-optimizer-vpc (imports KMS ApplicationDataKey for flow logs)
  │      │
  │      ├──► atm-optimizer-s3-athena (imports KMS TransactionDataKey, VPC endpoint ID)
  │      │
  │      └──► atm-optimizer-ec2 (imports VPC, subnets, SGs, S3/Athena/Cognito/KMS refs)
  │
  ├──► atm-optimizer-cognito (no VPC/KMS dependency — standalone)
  │
  └──► atm-optimizer-lambda-mcp (imports KMS TransactionDataKey, S3 bucket, Athena DB)
```

Deploy order: `kms` → `vpc` → `cognito` (parallel with vpc) → `s3-athena` → `ec2` + `lambda-mcp` (parallel)


## 6. Component Inventory

### CloudFormation Stacks (me-south-1)

| Stack | Status | Resources |
|-------|--------|-----------|
| atm-optimizer-kms | CREATE_COMPLETE | 3 KMS keys + 3 aliases |
| atm-optimizer-vpc | CREATE_COMPLETE | VPC, 2 subnets, 8 VPC endpoints, 3 SGs, route table, flow logs |
| atm-optimizer-s3-athena | CREATE_COMPLETE | S3 bucket, bucket policy, Athena workgroup, Glue DB + 11 tables |
| atm-optimizer-cognito | CREATE_COMPLETE | User pool, 2 groups, app client |
| atm-optimizer-ec2 | CREATE_COMPLETE | EC2 instance, IAM role, instance profile, ALB, target group, listener, ALB SG, log group |
| atm-optimizer-lambda-mcp | PENDING | Lambda function, IAM role, Function URL (IAM auth), resource-based policy |

### Data Tables (Athena/Glue)

| Table | S3 Prefix | Columns | Description |
|-------|-----------|---------|-------------|
| atm_transactions | atm_transactions/ | 6 | Transaction history (ID, ATM, timestamp, type, amount, fee) |
| atm_locations | atm_locations/ | 8 | 28 ATM locations with GPS, type, capacity, status |
| branch_locations | branch_locations/ | 6 | NeoBank branch locations with footfall data |
| atm_proximity | proximity_data/ | 4 | Distance matrix between ATM pairs |
| maintenance_costs | maintenance_costs/ | 5 | Maintenance records with cost and downtime |
| cash_levels | cash_levels/ | 7 | Daily cash balances and replenishment data |
| daily_atm_stats | daily_atm_stats/ | 5 | Pre-aggregated daily transaction stats per ATM |
| atm_profitability | atm_profitability/ | 6 | Pre-aggregated revenue, maintenance, cash costs per ATM |
| competitor_atm_locations | competitor_atm_locations/ | 7 | 82 competitor ATM locations with GPS and bank name |
| competitor_proximity | competitor_proximity/ | 5 | Distance matrix between NeoBank ATMs and competitor ATMs |
| competition_index | competition_index/ | 7 | Pre-aggregated competition index per NeoBank ATM at 2km radius |

### MCP Tools

| Tool | Access | Description |
|------|--------|-------------|
| query_atm_data | Operator+Admin | Transaction summary for ATM + date range |
| query_branch_proximity | Operator+Admin | Nearby ATMs/branches within radius |
| query_revenue_data | Operator+Admin | Revenue metrics (gross, net, fees, trend) |
| query_maintenance_costs | Admin only | Maintenance cost history and breakdown |
| query_cash_levels | Admin only | Cash balances and replenishment forecast |
| calculate_impact_analysis | Admin only | Downtime revenue impact + traffic reallocation |
| detect_anomalies | Admin only | Performance anomaly detection |
| profitability_ranking | Admin only | ATM profitability ranking |
| query_competitor_analysis | Operator+Admin | Competitor density and competition index analysis |
| query_coverage_analysis | Operator+Admin | Network coverage gaps and white-space analysis |
| simulate_competitor_scenario | Admin only | What-if simulation for competitor ATM add/remove |
| recommend_atm_placement | Admin only | AI-driven ATM placement recommendations |

---

## 7. Deployment Root Cause Analysis

### Problem Summary

During deployment of the 5 CloudFormation stacks, several issues were encountered that blocked full operational readiness. The root cause is a self-locking S3 bucket policy pattern, compounded by security group and VPC endpoint misconfigurations.

### Issue 1: Self-Locking S3 Bucket Policy (CRITICAL)

**Symptom**: Once the `RestrictToVPCEndpoint` bucket policy statement was applied, no operations from outside the VPC could modify the bucket — including CloudFormation itself.

**Root Cause**: The bucket policy contains:
```json
{
  "Sid": "RestrictToVPCEndpoint",
  "Effect": "Deny",
  "Principal": "*",
  "Action": "s3:*",
  "Condition": {
    "StringNotEquals": {
      "aws:sourceVpce": "vpce-XXXXXXXXXXXXXXXXX"
    }
  }
}
```

This denies ALL S3 API calls not originating from the VPC endpoint. CloudFormation makes S3 API calls from AWS-internal infrastructure, not through the customer's VPC endpoint. Therefore:
- CloudFormation cannot update the bucket policy (locked out)
- External CLI cannot upload data files
- Any stack update touching the bucket policy fails

**Fix Applied**: Added `EnableVpcRestriction` parameter (default: `true`) with a CloudFormation Condition. Setting it to `false` removes the Deny statement. However, this only works if the stack is updated BEFORE the Deny is applied, or from within the VPC.

**Recommended Permanent Fix**:
1. Add an exception for CloudFormation's service principal or the deploying role:
```json
"Condition": {
  "StringNotEquals": {
    "aws:sourceVpce": "vpce-XXXXXXXXXXXXXXXXX"
  },
  "ArnNotLike": {
    "aws:PrincipalArn": "arn:aws:iam::ACCOUNT:role/aws-service-role/*"
  }
}
```
2. Or use a two-phase deployment: deploy bucket without VPC restriction, upload data, then enable restriction via stack update.

**Current State**: The bucket policy may still have the Deny statement active. Resolution requires either:
- AWS Console access (root/admin) to manually remove the statement
- SSM Session Manager into the EC2 instance to run AWS CLI from within the VPC
- Delete and recreate the s3-athena stack (bucket has `DeletionPolicy: Retain`)

### Issue 2: EC2 Security Group Blocking S3 Gateway Traffic

**Symptom**: EC2 instance could not reach S3 despite the S3 Gateway endpoint being configured.

**Root Cause**: The EC2 security group egress rule only allowed HTTPS (443) to the VPC CIDR (10.0.0.0/16). S3 Gateway endpoints work by adding routes to the route table pointing to S3's public IP ranges via the gateway. Traffic to S3 goes to public IPs, not VPC IPs, so the egress rule blocked it.

**Fix Applied**: Added an egress rule allowing HTTPS to the S3 managed prefix list (`pl-XXXXXXXXXXXXXXXXX`):
```
aws ec2 authorize-security-group-egress \
  --group-id sg-XXXXXXXXXXXXXXXXX \
  --ip-permissions IpProtocol=tcp,FromPort=443,ToPort=443,PrefixListIds=[{PrefixListId=pl-XXXXXXXXXXXXXXXXX}]
```

**Recommended Permanent Fix**: Add the S3 prefix list egress rule to the CloudFormation template for `EC2SecurityGroup`. This requires looking up the prefix list ID dynamically or using `AWS::EC2::ManagedPrefixList` data source.

### Issue 3: S3 Gateway Endpoint Policy Too Restrictive

**Symptom**: `dnf update` and `dnf install` failed on EC2 because Amazon Linux repos use S3 (including dualstack URLs) and the endpoint policy only allowed access to the project bucket.

**Root Cause**: The original S3 Gateway endpoint policy only allowed actions on the project bucket ARN. Amazon Linux 2023 package repos, SSM agent, and other system services need to read from various AWS-managed S3 buckets.

**Fix Applied**: Added a second statement allowing read-only access to `Resource: '*'`:
```yaml
- Sid: AllowSystemS3Access
  Effect: Allow
  Principal: '*'
  Action:
    - 's3:GetObject'
    - 's3:ListBucket'
    - 's3:GetBucketLocation'
  Resource: '*'
```

**Status**: Fixed in the CloudFormation template. This is an acceptable trade-off for a private VPC with no internet access.

### Issue 4: EC2 Role Missing s3:PutObject

**Symptom**: Could not upload CSV data files to S3 from the EC2 instance.

**Root Cause**: The EC2 IAM role only had S3 read permissions (`GetObject`, `ListBucket`). Data upload requires `PutObject`.

**Fix Applied**: Added temporary inline policy `TempS3Upload` to the EC2 role.

**Recommended Permanent Fix**: Either:
- Add a separate "data upload" IAM role/policy for initial setup
- Use a CI/CD pipeline with its own role for data deployment
- Remove the temporary policy after data upload is complete

### Issue 5: Missing SSM VPC Endpoints

**Symptom**: Could not connect to EC2 via Session Manager.

**Root Cause**: Session Manager requires three VPC endpoints: `ssm`, `ssmmessages`, and `ec2messages`. Only the first was initially configured.

**Fix Applied**: Added all three endpoints to the VPC CloudFormation template. All now deployed.

### Deployment Sequence Diagram (What Happened)

```
1. Deploy kms          ✓ CREATE_COMPLETE
2. Deploy vpc          ✓ CREATE_COMPLETE (after adding SSM endpoints)
3. Deploy cognito      ✓ CREATE_COMPLETE
4. Deploy s3-athena    ✓ CREATE_COMPLETE
   └─ Bucket policy with VPC restriction applied
   └─ ⚠️ CloudFormation now locked out of bucket policy updates
5. Deploy ec2          ✓ CREATE_COMPLETE (after SG + endpoint fixes)
   └─ User-data partially failed (dnf blocked by S3 endpoint policy)
   └─ Fixed endpoint policy, but EC2 needs instance replacement or re-run
6. Upload data files   ✗ BLOCKED (bucket policy denies external access)
7. Deploy Streamlit    ✗ BLOCKED (depends on working EC2)
8. Deploy agent        ✗ NOT STARTED (eu-west-1, independent)
```

### Recommended Recovery Plan

```
Step 1: Fix S3 bucket policy
  Option A: Use AWS Console to remove RestrictToVPCEndpoint statement
  Option B: SSM into EC2, run: aws s3api delete-bucket-policy --bucket BUCKET
  Option C: Delete s3-athena stack, recreate with EnableVpcRestriction=false

Step 2: Upload 11 data files to S3 (Parquet format)
  aws s3 cp data/sample_transactions.csv s3://BUCKET/atm_transactions/
  aws s3 cp data/atm_locations.csv s3://BUCKET/atm_locations/
  aws s3 cp data/branch_locations.csv s3://BUCKET/branch_locations/
  aws s3 cp data/atm_proximity.csv s3://BUCKET/proximity_data/
  aws s3 cp data/sample_maintenance.csv s3://BUCKET/maintenance_costs/
  aws s3 cp data/sample_cash_levels.csv s3://BUCKET/cash_levels/
  aws s3 cp data/daily_atm_stats.parquet s3://BUCKET/daily_atm_stats/
  aws s3 cp data/atm_profitability.parquet s3://BUCKET/atm_profitability/
  aws s3 cp data/competitor_atm_locations.parquet s3://BUCKET/competitor_atm_locations/
  aws s3 cp data/competitor_proximity.parquet s3://BUCKET/competitor_proximity/
  aws s3 cp data/competition_index.parquet s3://BUCKET/competition_index/

Step 3: Terminate and recreate EC2 instance (re-run user-data)
  OR: SSM into existing instance and run setup commands manually

Step 4: Deploy Streamlit app code to EC2 via SSM

Step 5: Re-enable VPC restriction on S3 bucket policy
  aws cloudformation update-stack --stack-name atm-optimizer-s3-athena \
    --template-body file://s3-athena.yaml \
    --parameters ParameterKey=EnableVpcRestriction,ParameterValue=true

Step 6: Remove temporary TempS3Upload inline policy from EC2 role

Step 7: Deploy agent to AgentCore in eu-west-1
  python infrastructure/scripts/deploy-agent.py
```

---

## 8. Lessons Learned

1. **Never use `Deny s3:*` with VPC endpoint condition without an escape hatch.** Always exclude the CloudFormation service role or deploying principal from the Deny. Otherwise the policy becomes self-locking.

2. **S3 Gateway endpoints route to public IPs.** Security group egress rules must allow traffic to the S3 managed prefix list, not just the VPC CIDR.

3. **System packages need broad S3 read access.** Amazon Linux repos, SSM agent, and CloudWatch agent all read from AWS-managed S3 buckets. VPC endpoint policies must account for this.

4. **Session Manager needs 3 endpoints.** `ssm`, `ssmmessages`, and `ec2messages` are all required. Missing any one breaks the connection.

5. **Use a two-phase deployment for VPC-restricted buckets.** Phase 1: Deploy without restriction, upload data. Phase 2: Enable VPC restriction via stack update.
