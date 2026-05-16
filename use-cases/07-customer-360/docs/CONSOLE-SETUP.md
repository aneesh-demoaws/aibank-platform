# Customer 360 — Console Setup Steps

These steps require AWS Console access and cannot be automated via CLI.

## 1. QuickSight Dataset (Athena → Neptune)

1. Open QuickSight → Datasets → New Dataset
2. Choose **Athena** as data source
3. Data source name: `neptune-c360`
4. Workgroup: `primary`
5. Database: `neptune_c360`
6. Table: `customer_peer_stats`
7. Import to SPICE (daily refresh)
8. Save

## 2. QuickSight Dashboard

1. Create Analysis from the `neptune-c360` dataset
2. Add visuals:
   - KPI: Total customers, Avg FHS, Avg peer count
   - Bar chart: FHS distribution by band
   - Scatter: Balance vs peer_pct_products
   - Table: Customers with peer_pct_high_balance > 90%
3. Add What-If Parameters:
   - `loan_amount` (slider: 1000-50000)
   - `interest_rate` (slider: 3-8%)
   - `tenure_years` (dropdown: 5,10,15,20)
4. Add calculated field: `monthly_payment = loan_amount * (interest_rate/12) / (1 - (1+interest_rate/12)^(-tenure_years*12))`
5. Publish as Dashboard: `C360 RM Dashboard`
6. Share with `crm@demoaws.com` via Identity Center

## 3. Quick Suite Chat Agent

1. Open Amazon Quick Suite console
2. Navigate to **Chat agents** → **Create chat agent**
3. Name: `C360 Advisor`
4. Persona instructions:
   ```
   You are a Customer 360 advisor for AI Bank relationship managers.
   Help RMs understand customer financial health, recommend products,
   explain peer comparisons, and suggest next best actions.
   Use data from the linked QuickSight datasets to answer questions.
   Always cite specific numbers. Be concise and actionable.
   ```
5. Tone: Executive
6. Response format: Bullet points for lists, concise paragraphs
7. Knowledge sources: Link the `neptune-c360` QuickSight dataset
8. Upload reference docs:
   - Product catalog (from use-cases/05-nba/db/migrations.sql)
   - FHS methodology
9. Actions: Create follow-up task (optional)
10. Launch chat agent
11. Share with `crm@demoaws.com`

## 4. Embed in Portal

Once the dashboard and chat agent are created:

1. Get the Dashboard ID from QuickSight console
2. Get the Chat Agent ID from Quick Suite console
3. Update `customer-360.html`:
   - Replace QuickSight placeholder with embed SDK code
   - Replace Chat Agent placeholder with Quick Suite embed SDK code
4. Both use `GenerateEmbedUrlForRegisteredUser` API (same pattern as NBA Insights)

## 5. IAM Identity Center

The RM user `crm@demoaws.com` is already provisioned in:
- IAM Identity Center: `ssoins-68040e1f934f09da`
- Employee Cognito Pool: `eu-west-1_ALFkxDepn`
- QuickSight: Reader role

No additional setup needed — same SSO session covers all components.
