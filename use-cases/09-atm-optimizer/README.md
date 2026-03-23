# 🏧 ATM Profitability Optimizer

An AI-powered ATM network optimization solution built on Amazon Bedrock AgentCore. It helps retail banks analyze ATM performance, identify coverage gaps, assess competitive landscape, and recommend optimal placement for new ATMs.

![Architecture](docs/architecture-diagram.png)

## Features

- **ATM Performance Analytics** — Transaction volumes, revenue trends, and profitability ranking across the ATM network
- **Competitor Analysis** — Competition index scoring, coverage gap detection, and market share analysis by region
- **ATM Placement Recommendations** — AI-driven optimal location suggestions based on coverage gaps and competitor density
- **What-If Scenario Modeling** — Simulate competitor ATM openings/closings and assess revenue impact
- **Anomaly Detection** — Identify ATMs with unusual transaction patterns
- **Cash Level Forecasting** — Monitor cash levels and predict replenishment needs
- **Role-Based Access Control** — 4-tier RBAC (Admin, Operator) via Amazon Cognito
- **Conversation Memory** — Persistent context across sessions via AgentCore Memory

## Architecture

The solution uses a dual-region architecture for data sovereignty:

| Component | Region | Purpose |
|-----------|--------|---------|
| S3 Data Lake, Athena, Cognito | `me-south-1` (Bahrain) | Banking data stays in-country |
| Bedrock AgentCore, Lambda MCP, Gateway | `eu-west-1` (Ireland) | AI services and agent runtime |
| EC2 (Streamlit Frontend) | `me-south-1` | User-facing web application |

### Data Flow

```
User → Streamlit (me-south-1) → AgentCore Runtime (eu-west-1)
  → Strands Agent → AgentCore Gateway (eu-west-1)
  → Lambda MCP Server (eu-west-1) → Athena (me-south-1)
  → S3 Parquet Data (me-south-1)
```

### MCP Tools (12 tools)

| Tool | Access | Description |
|------|--------|-------------|
| `query_atm_data` | Operator | ATM transaction summaries |
| `query_branch_proximity` | Operator | Nearby ATMs/branches |
| `query_revenue_data` | Operator | Revenue metrics |
| `query_competitor_analysis` | Operator | Competition index scores |
| `query_coverage_analysis` | Operator | Coverage gaps and market share |
| `query_maintenance_costs` | Admin | Maintenance cost history |
| `query_cash_levels` | Admin | Cash levels and forecasts |
| `calculate_impact_analysis` | Admin | Revenue impact modeling |
| `detect_anomalies` | Admin | Unusual pattern detection |
| `profitability_ranking` | Admin | ATM profitability ranking |
| `simulate_competitor_scenario` | Admin | Competitor what-if scenarios |
| `recommend_atm_placement` | Admin | Optimal new ATM locations |

## Prerequisites

- AWS Account with access to `me-south-1` and `eu-west-1`
- AWS CLI v2 configured with appropriate credentials
- Python 3.11+
- Amazon Bedrock model access (Claude Sonnet 4) enabled in `eu-west-1`
- Bedrock AgentCore CLI (`pip install bedrock-agentcore`)

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/aneesh-demoaws/atm-profitability-optimizer.git
cd atm-profitability-optimizer
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Deploy Infrastructure

Deploy the AWS resources in order:

#### a) S3 Data Lake & Athena (me-south-1)

```bash
aws cloudformation deploy \
  --template-file infrastructure/cloudformation/s3-athena.yaml \
  --stack-name atm-optimizer-data \
  --region me-south-1 \
  --capabilities CAPABILITY_NAMED_IAM
```

#### b) Generate & Upload Sample Data

```bash
# Generate sample CSV data
python data/generate_sample_data.py

# Upload CSVs to S3
python infrastructure/scripts/upload-competitor-csvs.py

# Convert to Parquet format
python infrastructure/scripts/step1_parquet.py

# Build competition index
python infrastructure/scripts/step2_competition_index.py
```

#### c) Setup Athena Tables

```bash
python infrastructure/scripts/setup-athena-tables.py
```

#### d) Amazon Cognito (me-south-1)

```bash
aws cloudformation deploy \
  --template-file infrastructure/cloudformation/cognito.yaml \
  --stack-name atm-optimizer-cognito \
  --region me-south-1 \
  --capabilities CAPABILITY_NAMED_IAM
```

#### e) Lambda MCP Server (eu-west-1)

```bash
aws cloudformation deploy \
  --template-file infrastructure/cloudformation/lambda-mcp.yaml \
  --stack-name atm-optimizer-mcp \
  --region eu-west-1 \
  --capabilities CAPABILITY_NAMED_IAM
```

#### f) Bedrock AgentCore Runtime (eu-west-1)

```bash
# Initialize AgentCore project
agentcore init

# Deploy the agent runtime
agentcore deploy
```

#### g) AgentCore Gateway

```bash
python infrastructure/scripts/deploy-agentcore-gateway.py
```

#### h) EC2 Streamlit Frontend (me-south-1)

```bash
aws cloudformation deploy \
  --template-file infrastructure/cloudformation/ec2-streamlit.yaml \
  --stack-name atm-optimizer-frontend \
  --region me-south-1 \
  --capabilities CAPABILITY_NAMED_IAM
```

Then deploy the application code:

```bash
python infrastructure/scripts/deploy-streamlit-agentcore.py
```

### 4. Configure Environment Variables

Set these on the EC2 instance (via SSM Parameter Store or directly):

```bash
# S3 & Athena
export ATM_S3_DATA_BUCKET="your-bucket-name-me-south-1"
export ATM_ATHENA_DATABASE="your_athena_database"

# Cognito
export ATM_COGNITO_USER_POOL_ID="me-south-1_XXXXXXXXX"
export ATM_COGNITO_APP_CLIENT_ID="your-cognito-client-id"

# AgentCore
export ATM_AGENTCORE_RUNTIME_ARN="arn:aws:bedrock-agentcore:eu-west-1:ACCOUNT:runtime/YOUR_RUNTIME_ID"

# Model (optional — defaults to Claude Sonnet 4)
export ATM_MODEL_ID="anthropic.claude-sonnet-4-20250514-v1:0"
```

For the AgentCore Runtime container, set these in the Dockerfile or runtime environment:

```bash
export ATM_GATEWAY_URL="https://your-gateway-id.gateway.bedrock-agentcore.eu-west-1.amazonaws.com/mcp"
export ATM_MEMORY_ID="your-memory-id"
```

### 5. Access the Application

Navigate to `http://<ec2-public-ip>:8503` and log in with your Cognito credentials.

## Project Structure

```
atm-profitability-optimizer/
├── agent/                          # AI Agent layer
│   ├── agentcore_app.py           # AgentCore Runtime entry point
│   ├── agent.py                   # Local Strands Agent (dev/test)
│   ├── config.py                  # Configuration (env-var driven)
│   ├── bank_alias.py             # Bank name aliasing via SSM
│   ├── system_prompt.py          # System prompts
│   ├── session.py                # Session management
│   ├── auth/                     # RBAC layer
│   │   ├── role_manager.py       # Role-based tool filtering
│   │   └── tool_filter.py        # Tool access control
│   └── tools/                    # MCP Tool implementations
│       ├── _athena_queries.py    # Shared Athena query helpers
│       ├── query_atm_data.py
│       ├── query_revenue_data.py
│       ├── query_branch_proximity.py
│       ├── query_maintenance_costs.py
│       ├── query_cash_levels.py
│       ├── query_competitor_analysis.py
│       ├── query_coverage_analysis.py
│       ├── calculate_impact_analysis.py
│       ├── detect_anomalies.py
│       ├── profitability_ranking.py
│       ├── simulate_competitor_scenario.py
│       └── recommend_atm_placement.py
├── mcp_server/                    # MCP Server (Lambda)
│   ├── server.py                 # FastMCP server with 12 tools
│   ├── lambda_handler.py         # Lambda entry point
│   └── athena_client.py          # Athena query client
├── frontend/                      # Streamlit Web UI
│   ├── app.py                    # Main application
│   ├── auth.py                   # Cognito authentication
│   ├── config.py                 # Frontend configuration
│   └── components/
│       ├── chat.py               # Chat interface
│       ├── tabs.py               # Dashboard tabs
│       ├── map_view.py           # Map visualization
│       └── export.py             # Data export
├── infrastructure/
│   ├── cloudformation/           # CloudFormation templates
│   │   ├── s3-athena.yaml       # S3 + Athena + Glue
│   │   ├── cognito.yaml         # Cognito User Pool
│   │   ├── lambda-mcp.yaml     # Lambda MCP Server
│   │   ├── ec2-streamlit.yaml  # EC2 for Streamlit
│   │   ├── vpc-bahrain.yaml    # VPC in me-south-1
│   │   └── kms.yaml            # KMS encryption keys
│   ├── scripts/                  # Deployment & data scripts
│   └── security/                 # IAM roles & policies
├── data/                          # Sample data & generators
│   ├── generate_sample_data.py   # Generate synthetic ATM data
│   └── *.csv                     # Sample CSV files
├── tests/                         # Test suite (21 test files)
├── docs/                          # Documentation
├── Dockerfile                     # AgentCore container image
├── requirements.txt               # Frontend dependencies
├── requirements-agent.txt         # Agent runtime dependencies
└── pyproject.toml                 # Python project config
```

## Customization

### Adapting for Your Bank

1. **Bank Name**: Update the bank alias via SSM Parameter Store at `/atm-optimizer/bank-alias`
2. **ATM Data**: Replace sample CSVs in `data/` with your actual ATM locations and transaction data
3. **Competitor Data**: Update `data/competitor_atm_locations.csv` with local competitor information
4. **Geographic Bounds**: Adjust `BAHRAIN_LAT_*` / `BAHRAIN_LON_*` in `agent/config.py` for your country
5. **Regions**: Modify `DATA_REGION` and `AI_REGION` in `agent/config.py` for your preferred AWS regions

### Adding New MCP Tools

1. Create a new tool file in `agent/tools/`
2. Register it in `mcp_server/server.py`
3. Add to the appropriate RBAC tier in `agent/config.py`
4. Update the Lambda deployment

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test categories
pytest tests/test_tools.py -v          # Tool unit tests
pytest tests/test_security.py -v       # Security tests
pytest tests/test_competitor_analysis.py -v  # Competitor analysis
```

## Security

- All data stays in the designated data region (me-south-1 for Bahrain)
- Cognito-based authentication with MFA support
- Role-based access control (Admin/Operator)
- S3 server-side encryption (SSE-S3)
- VPC with private subnets for EC2
- IAM least-privilege roles for all services
- No hardcoded credentials — all sensitive values via environment variables or SSM Parameter Store

## License

This project is provided as a reference implementation for AWS customers. See [LICENSE](LICENSE) for details.
