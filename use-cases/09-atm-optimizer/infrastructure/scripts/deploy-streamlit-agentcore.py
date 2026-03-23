#!/usr/bin/env python3
"""
Deploy ATM Profitability Optimizer Streamlit app (AgentCore Runtime version)
to existing EC2 instance on port 8503.

Uses AgentCore Runtime invoke for the agent backend.

Usage:
    python3 infrastructure/scripts/deploy-streamlit-agentcore.py
"""

import boto3
import json
import os
import time
import sys
import base64

INSTANCE_ID = os.environ.get("ATM_EC2_INSTANCE_ID", "CHANGE_ME")
REGION = "me-south-1"
APP_PORT = 8503
APP_DIR = "/opt/atm-optimizer-v2"

# Cognito config
COGNITO_USER_POOL_ID = os.environ.get("ATM_COGNITO_USER_POOL_ID", "CHANGE_ME")
COGNITO_APP_CLIENT_ID = os.environ.get("ATM_COGNITO_APP_CLIENT_ID", "CHANGE_ME")

# AgentCore Runtime ARN (from agentcore status)
AGENTCORE_RUNTIME_ARN = os.environ.get("ATM_AGENTCORE_RUNTIME_ARN", "CHANGE_ME")

# AgentCore Gateway endpoint
AGENTCORE_GATEWAY_ENDPOINT = os.environ.get("ATM_AGENTCORE_GATEWAY_ENDPOINT", "CHANGE_ME")

ssm = boto3.client("ssm", region_name=REGION)
ec2 = boto3.client("ec2", region_name=REGION)


def run_command(commands, description, timeout=120):
    """Execute commands on EC2 via SSM and return output."""
    print(f"\n{'='*60}")
    print(f"  {description}")
    print(f"{'='*60}")

    resp = ssm.send_command(
        InstanceIds=[INSTANCE_ID],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": commands},
        TimeoutSeconds=timeout,
    )
    cmd_id = resp["Command"]["CommandId"]
    print(f"  Command ID: {cmd_id}")

    deadline = time.monotonic() + timeout + 30
    while time.monotonic() < deadline:
        try:
            result = ssm.get_command_invocation(
                CommandId=cmd_id, InstanceId=INSTANCE_ID
            )
            status = result["Status"]
            if status in ("Success", "Failed", "Cancelled", "TimedOut"):
                stdout = result.get("StandardOutputContent", "")
                stderr = result.get("StandardErrorContent", "")
                if stdout:
                    print(f"  STDOUT: {stdout[:2000]}")
                if stderr:
                    print(f"  STDERR: {stderr[:1000]}")
                if status != "Success":
                    print(f"  ❌ Command failed with status: {status}")
                    return False, stdout, stderr
                print(f"  ✅ Success")
                return True, stdout, stderr
        except ssm.exceptions.InvocationDoesNotExist:
            pass
        time.sleep(3)

    print("  ❌ Timed out waiting for command")
    return False, "", "Timeout"


def step1_create_app_directory():
    """Create the v2 application directory structure."""
    ok, _, _ = run_command([
        f"mkdir -p {APP_DIR}/frontend/components",
        f"mkdir -p {APP_DIR}/agent/tools",
        f"mkdir -p {APP_DIR}/agent/auth",
        f"mkdir -p {APP_DIR}/mcp_server",
        f"mkdir -p {APP_DIR}/data",
        f"ls -la {APP_DIR}/",
    ], "Step 1: Create v2 application directory structure")
    return ok


def step2_upload_app_code():
    """Upload application code files to EC2 via SSM."""
    file_paths = [
        "frontend/app.py",
        "frontend/auth.py",
        "frontend/config.py",
        "frontend/__init__.py",
        "frontend/components/__init__.py",
        "frontend/components/chat.py",
        "frontend/components/tabs.py",
        "frontend/components/map_view.py",
        "frontend/components/export.py",
        "agent/__init__.py",
        "agent/config.py",
        "agent/bank_alias.py",
        "agent/auth/__init__.py",
        "agent/auth/role_manager.py",
        "agent/auth/tool_filter.py",
        "agent/tools/__init__.py",
        "agent/tools/_athena_queries.py",
        "agent/tools/query_competitor_analysis.py",
        "agent/tools/query_coverage_analysis.py",
        "agent/tools/simulate_competitor_scenario.py",
        "agent/tools/recommend_atm_placement.py",
        "agent/tools/query_atm_data.py",
        "agent/tools/query_branch_proximity.py",
        "agent/tools/query_revenue_data.py",
        "agent/tools/query_maintenance_costs.py",
        "agent/tools/query_cash_levels.py",
        "agent/tools/calculate_impact_analysis.py",
        "agent/tools/detect_anomalies.py",
        "agent/tools/profitability_ranking.py",
        "agent/session.py",
        "agent/agent.py",
        "agent/system_prompt.py",
        "agent/agentcore_app.py",
        "mcp_server/__init__.py",
        "mcp_server/server.py",
        "mcp_server/athena_client.py",
        "mcp_server/lambda_handler.py",
    ]

    files_to_upload = {}
    for fp in file_paths:
        full_path = os.path.join(os.getcwd(), fp)
        if os.path.exists(full_path):
            with open(full_path, "r") as f:
                files_to_upload[fp] = f.read()
        elif fp.endswith("__init__.py"):
            files_to_upload[fp] = ""
        else:
            print(f"  ⚠️  File not found: {full_path}")

    for fp, content in files_to_upload.items():
        target = f"{APP_DIR}/{fp}"
        ok, _, _ = run_command([
            f"cat > {target} << 'KIRO_EOF'\n{content}\nKIRO_EOF",
        ], f"Upload {fp}", timeout=30)
        if not ok:
            b64 = base64.b64encode(content.encode()).decode()
            ok, _, _ = run_command([
                f"echo '{b64}' | base64 -d > {target}",
            ], f"Upload {fp} (base64)", timeout=30)
            if not ok:
                return False

    print(f"\n  Uploaded {len(files_to_upload)} files")
    return True


def step3_create_streamlit_config():
    """Create Streamlit config for port 8503."""
    config_content = f"""[server]
port = {APP_PORT}
address = "0.0.0.0"
headless = true
enableCORS = false
enableXsrfProtection = true

[theme]
primaryColor = "#1E88E5"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F5F5F5"
textColor = "#212121"
"""
    ok, _, _ = run_command([
        f"mkdir -p {APP_DIR}/.streamlit",
        f"cat > {APP_DIR}/.streamlit/config.toml << 'EOF'\n{config_content}\nEOF",
    ], "Step 3: Create Streamlit config (port 8503)")
    return ok


def step4_create_systemd_service():
    """Create a separate systemd service for the v2 app on port 8503."""
    service_content = f"""[Unit]
Description=NeoBank ATM Optimizer v2 (AgentCore Runtime) - Streamlit
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory={APP_DIR}
Environment=PYTHONPATH={APP_DIR}
Environment=ATM_COGNITO_USER_POOL_ID={COGNITO_USER_POOL_ID}
Environment=ATM_COGNITO_APP_CLIENT_ID={COGNITO_APP_CLIENT_ID}
Environment=ATM_AGENTCORE_GATEWAY_ENDPOINT={AGENTCORE_GATEWAY_ENDPOINT}
Environment=ATM_AGENTCORE_RUNTIME_ARN={AGENTCORE_RUNTIME_ARN}
Environment=AWS_DEFAULT_REGION=me-south-1
ExecStart=/usr/local/bin/streamlit run {APP_DIR}/frontend/app.py --server.port {APP_PORT} --server.address 0.0.0.0 --server.headless true
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
    ok, _, _ = run_command([
        f"cat > /etc/systemd/system/atm-optimizer-v2.service << 'EOF'\n{service_content}\nEOF",
        "systemctl daemon-reload",
        "systemctl enable atm-optimizer-v2",
        "systemctl restart atm-optimizer-v2",
        "sleep 3",
        "systemctl status atm-optimizer-v2 --no-pager",
    ], "Step 4: Create and start v2 systemd service (port 8503)", timeout=30)
    return ok


def step5_open_security_group_port():
    """Add port 8503 to the EC2 security group if not already open."""
    sg_id = "sg-0c7d3a0975ba40ccb"

    try:
        sg = ec2.describe_security_groups(GroupIds=[sg_id])
        rules = sg["SecurityGroups"][0]["IpPermissions"]
        port_open = any(
            r.get("FromPort") == APP_PORT and r.get("ToPort") == APP_PORT
            for r in rules
        )

        if port_open:
            print(f"  Port {APP_PORT} already open in {sg_id}")
        else:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[{
                    "IpProtocol": "tcp",
                    "FromPort": APP_PORT,
                    "ToPort": APP_PORT,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "ATM Optimizer v2 Streamlit"}],
                }],
            )
            print(f"  ✅ Opened port {APP_PORT} in {sg_id}")
        return True
    except Exception as e:
        print(f"  ⚠️  Security group update: {e}")
        return True


def step6_verify():
    """Verify both apps are running."""
    ok, stdout, _ = run_command([
        f"ss -tlnp | grep {APP_PORT}",
        f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{APP_PORT}/ || echo 'curl failed'",
        "systemctl is-active atm-optimizer-v2.service",
    ], "Step 6: Verify app running")
    return ok


def main():
    print("=" * 60)
    print("  NeoBank ATM Optimizer v2 — EC2 Streamlit Deploy (port 8503)")
    print(f"  Instance: {INSTANCE_ID} | Port: {APP_PORT}")
    print(f"  Runtime ARN: {AGENTCORE_RUNTIME_ARN[:60]}...")
    print("=" * 60)

    steps = [
        ("Create v2 app directory", step1_create_app_directory),
        ("Upload app code", step2_upload_app_code),
        ("Create Streamlit config", step3_create_streamlit_config),
        ("Create v2 systemd service", step4_create_systemd_service),
        ("Open security group port", step5_open_security_group_port),
        ("Verify", step6_verify),
    ]

    for i, (name, fn) in enumerate(steps, 1):
        print(f"\n{'#'*60}")
        print(f"# Step {i}/{len(steps)}: {name}")
        print(f"{'#'*60}")
        if not fn():
            print(f"\n❌ Deployment failed at step {i}: {name}")
            sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  ✅ Deployment complete!")
    print(f"  App:           http://16.24.68.24:{APP_PORT}")
    print(f"  Cognito Pool:  {COGNITO_USER_POOL_ID}")
    print(f"  Runtime ARN:   {AGENTCORE_RUNTIME_ARN}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
