# Use Case 02: Customer Onboarding Agent

AI-driven account opening agent deployed as an A2A server on AgentCore. Called by Alma FAQ agent when a customer wants to open an account.

## Architecture

```
Alma Agent → invoke_agent_runtime (A2A JSON-RPC) → Onboarding Agent (AgentCore, A2A)
                                                      ├── validate_age
                                                      ├── send_otp → SES email
                                                      ├── verify_otp → Aurora otp_codes
                                                      └── create_customer_account
                                                            ├── Cognito user
                                                            ├── Aurora customers + accounts
                                                            └── Welcome email via SES
```

## Onboarding Flow

1. Collect: first name, last name, DOB, email, phone, nationality, account type
2. `validate_age` — reject if under 18
3. `send_otp` — 6-digit code via email, stored in `otp_codes` table (5 min expiry)
4. `verify_otp` — max 3 attempts, precondition: OTP must exist
5. `create_customer_account` — precondition: OTP must be verified
   - Creates Cognito user with permanent password
   - Inserts into `customers` and `accounts` tables
   - Sends welcome email with credentials

## Precondition Guards

Tools enforce workflow order at the code level (not just prompt level):
- `verify_otp` checks that `send_otp` was called first
- `create_customer_account` checks that email is verified
- Phone numbers are normalized to E.164 for all GCC countries

## Phone Normalization

Supports all GCC countries: Bahrain (+973), Saudi (+966), UAE (+971), Oman (+968), Qatar (+974), Kuwait (+965).

## Prerequisites

- Foundation layer deployed
- Use Case 01 (Alma FAQ) deployed
- `ONBOARDING_RUNTIME_ARN` will be set after deploy

## Deploy

```bash
./deploy.sh
```

This deploys the agent as an A2A server on AgentCore (port 9000, ARM64 container).
After deploy, update Alma's `ONBOARDING_ARN` and add IAM permissions for Alma to invoke this runtime.
