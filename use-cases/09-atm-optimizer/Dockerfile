# NeoBank ATM Profitability Optimizer — AgentCore Runtime Container
# ARM64 image for Amazon Bedrock AgentCore Runtime deployment
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.11-slim-bookworm

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements-agent.txt ./
RUN pip install --no-cache-dir -r requirements-agent.txt

# Copy agent code
COPY agent/ ./agent/
COPY agent/agentcore_app.py ./

EXPOSE 8080

# AgentCore Runtime requires /invocations POST and /ping GET on port 8080
CMD ["uvicorn", "agentcore_app:app", "--host", "0.0.0.0", "--port", "8080"]
