FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim
WORKDIR /app

ENV UV_SYSTEM_PYTHON=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    PYTHONUNBUFFERED=1 \
    AWS_REGION=eu-west-1 \
    AWS_DEFAULT_REGION=eu-west-1 \
    AGENT_OBSERVABILITY_ENABLED=true

COPY requirements.txt requirements.txt
RUN uv pip install -r requirements.txt

RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

EXPOSE 9000

COPY . .

# Auto-instrument with AWS Distro for OpenTelemetry so Strands + boto3 spans
# flow into CloudWatch GenAI Observability. AgentCore Runtime injects the
# OTEL_RESOURCE_ATTRIBUTES / log-group / session-id env vars at invocation time.
CMD ["opentelemetry-instrument", "python", "main.py"]
