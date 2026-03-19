# NeoBank Kiku Personal Assistant — Implementation Review
## Learnings for Alma Banking Assistant

**Reviewed:** 2026-02-26
**Source:** neobank.demoaws.com (us-west-2) — 3 Lambda functions

---

## Architecture

```
Customer (WhatsApp / Web)
    → Lambda: neobank-customer-kiku-chat (entry point)
        → StrandsAgent class
            → MCPAgentManager (primary path)
                → awslabs.mysql-mcp-server (uvx, stdio)
                    → Aurora MySQL (Core Banking)
            → Banking tools (fallback if MCP fails)
        → DynamoDB: neobank-conversation-history (session)
        → WhatsApp API (for WhatsApp channel)
```

## What Kiku Does Well ✅
1. **MCP-based Text-to-SQL** — uses `awslabs.mysql-mcp-server` via stdio, agent writes SQL naturally
2. **Zero hallucination prompt** — strict rule: every financial detail must come from a DB query
3. **Schema in system prompt** — full table schemas embedded so Claude knows the data model
4. **Conversation history** — DynamoDB stores messages, agent gets context window of last 10
5. **WhatsApp + Web dual channel** — same agent serves both
6. **Fallback pattern** — if MCP fails, falls back to banking tools (which redirect to MCP anyway)

## What Kiku Does Poorly ❌
1. **Single capability** — only text-to-SQL, no KYC, no goals, no recommendations
2. **No multi-agent** — one monolithic agent with all tools
3. **MCP via stdio in Lambda** — spawns uvx subprocess per invocation, slow cold starts
4. **No file upload** — can't handle document processing
5. **Customer ID injection** — hacky regex extraction from prompt string
6. **No auth integration** — customer_id passed in context, no JWT validation
7. **Mock banking tools** — transfer_funds, pay_bill just return "use mobile app"
8. **SageMaker fallback** — complex dual-model setup (Claude + Mistral) adds confusion

## Key Technical Details

### MCP Server Setup (stdio)
```python
MCPClient(lambda: stdio_client(StdioServerParameters(
    command="/opt/python/bin/uvx",
    args=["--python", "3.12", "awslabs.mysql-mcp-server@latest",
          "--resource_arn", AURORA_ARN,
          "--secret_arn", SECRET_ARN,
          "--database", "corebanking",
          "--readonly", "True"],
    env=mcp_env
)))
```

### System Prompt Pattern
- Full DB schema embedded (customers, accounts, transactions tables)
- Zero hallucination rules
- WhatsApp-style short responses
- Customer phone injected into prompt: `"Customer phone: {phone}. Message: {text}"`

### Lambda Config
- Runtime: Python 3.12
- Memory: 1536MB (for MCP subprocess)
- Timeout: 300s
- Layer: uvx/uv for MCP server installation

## What to Reuse for Alma Banking Assistant
1. ✅ MCP-based text-to-SQL pattern (but use Lambda MCP server, not stdio)
2. ✅ Zero hallucination system prompt approach
3. ✅ Schema-in-prompt pattern
4. ✅ Conversation history in DynamoDB

## What to Improve
1. 🆕 Multi-capability agent (Text-to-SQL + KYC IDP + Goals + NBA)
2. 🆕 AgentCore Runtime (not Lambda) — persistent, no cold starts
3. 🆕 MCP tools via AgentCore Gateway (not stdio subprocess)
4. 🆕 Cognito JWT auth — customer_id from token, not prompt injection
5. 🆕 File upload support for KYC documents
6. 🆕 Scoped data access — agent can ONLY query authenticated customer's data
