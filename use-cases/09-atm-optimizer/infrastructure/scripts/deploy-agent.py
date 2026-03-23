#!/usr/bin/env python3
"""
Deploy the ATM Profitability Optimizer agent to AgentCore Runtime.

Packages the agent code with dependencies and deploys to Amazon Bedrock
AgentCore Runtime in eu-west-1.

Usage:
    python infrastructure/scripts/deploy-agent.py [--agent-name NAME] [--dry-run]

Validates: Requirements 20.5, 20.6
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# ── Constants ────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DEPLOY_REGION = "eu-west-1"
DEFAULT_AGENT_NAME = "neobank-atm-profitability-optimizer"
RUNTIME_MEMORY_MB = 2048
RUNTIME_TIMEOUT_SECONDS = 60

# Directories to include in the deployment package
PACKAGE_DIRS = ["agent"]
PACKAGE_FILES = ["requirements.txt"]

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


# ── Packaging ────────────────────────────────────────────────────────────

def _create_deployment_package(output_path: Path) -> Path:
    """Create a ZIP deployment package containing agent code and deps.

    Parameters
    ----------
    output_path:
        Directory where the ZIP file will be written.

    Returns
    -------
    Path
        Path to the created ZIP file.
    """
    zip_path = output_path / "agent-package.zip"
    logger.info("Creating deployment package: %s", zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add agent source code
        for dir_name in PACKAGE_DIRS:
            src_dir = PROJECT_ROOT / dir_name
            if not src_dir.exists():
                logger.warning("Directory %s not found, skipping", src_dir)
                continue
            for file_path in src_dir.rglob("*.py"):
                arcname = str(file_path.relative_to(PROJECT_ROOT))
                zf.write(file_path, arcname)
                logger.debug("Added %s", arcname)

        # Add top-level files
        for fname in PACKAGE_FILES:
            fpath = PROJECT_ROOT / fname
            if fpath.exists():
                zf.write(fpath, fname)
                logger.debug("Added %s", fname)

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    logger.info("Package created: %.2f MB", size_mb)
    return zip_path


def _install_dependencies(target_dir: Path) -> None:
    """Install Python dependencies into *target_dir* for packaging."""
    req_file = PROJECT_ROOT / "requirements.txt"
    if not req_file.exists():
        logger.warning("requirements.txt not found, skipping dependency install")
        return

    logger.info("Installing dependencies into %s", target_dir)
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-r",
            str(req_file),
            "--target",
            str(target_dir),
            "--quiet",
        ]
    )


# ── Deployment ───────────────────────────────────────────────────────────

def _get_or_create_agent_runtime(
    client: "boto3.client",
    agent_name: str,
) -> dict:
    """Get existing agent runtime config or create a new one.

    Parameters
    ----------
    client:
        Boto3 AgentCore / Bedrock-agent-runtime client.
    agent_name:
        Logical name for the agent.

    Returns
    -------
    dict
        Agent runtime metadata.
    """
    # Attempt to describe existing agent
    try:
        response = client.get_agent(agentName=agent_name)
        logger.info("Found existing agent: %s", agent_name)
        return response
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ResourceNotFoundException":
            logger.info("Agent %s not found, will create", agent_name)
        else:
            raise

    # Create new agent runtime
    logger.info(
        "Creating agent runtime: name=%s, memory=%dMB, timeout=%ds",
        agent_name,
        RUNTIME_MEMORY_MB,
        RUNTIME_TIMEOUT_SECONDS,
    )
    response = client.create_agent(
        agentName=agent_name,
        description="NeoBank ATM Profitability Optimizer — What-If simulation engine",
        agentResourceRoleArn=os.environ.get(
            "AGENTCORE_ROLE_ARN",
            f"arn:aws:iam::{_get_account_id()}:role/AgentCore-ATMOptimizer-Role",
        ),
        foundationModel="anthropic.claude-3-5-sonnet-20241022-v2:0",
        idleSessionTTLInSeconds=RUNTIME_TIMEOUT_SECONDS,
        memoryConfiguration={
            "enabledMemoryTypes": ["SESSION_SUMMARY"],
            "storageDays": 1,
        },
    )
    logger.info("Agent created: %s", response.get("agentId", "unknown"))
    return response


def _upload_package(
    client: "boto3.client",
    agent_name: str,
    package_path: Path,
) -> None:
    """Upload the deployment package to AgentCore.

    Parameters
    ----------
    client:
        Boto3 client for the agent service.
    agent_name:
        Agent name to deploy to.
    package_path:
        Path to the ZIP deployment package.
    """
    logger.info("Uploading package %s for agent %s", package_path, agent_name)
    with open(package_path, "rb") as f:
        package_bytes = f.read()

    try:
        client.update_agent(
            agentName=agent_name,
            agentResourceRoleArn=os.environ.get(
                "AGENTCORE_ROLE_ARN",
                f"arn:aws:iam::{_get_account_id()}:role/AgentCore-ATMOptimizer-Role",
            ),
            foundationModel="anthropic.claude-3-5-sonnet-20241022-v2:0",
            description="NeoBank ATM Profitability Optimizer — updated deployment",
            memoryConfiguration={
                "enabledMemoryTypes": ["SESSION_SUMMARY"],
                "storageDays": 1,
            },
        )
        logger.info("Agent updated successfully")
    except ClientError:
        logger.error("Failed to update agent %s", agent_name, exc_info=True)
        raise


def _get_account_id() -> str:
    """Return the current AWS account ID."""
    sts = boto3.client("sts", region_name=DEPLOY_REGION)
    return sts.get_caller_identity()["Account"]


# ── Main ─────────────────────────────────────────────────────────────────

def deploy(agent_name: str, dry_run: bool = False) -> None:
    """Package and deploy the agent to AgentCore Runtime.

    Parameters
    ----------
    agent_name:
        Logical name for the AgentCore agent.
    dry_run:
        If ``True``, create the package but skip the actual deployment.
    """
    logger.info("=== ATM Profitability Optimizer — Agent Deployment ===")
    logger.info("Region: %s", DEPLOY_REGION)
    logger.info("Agent name: %s", agent_name)
    logger.info("Runtime config: memory=%dMB, timeout=%ds",
                RUNTIME_MEMORY_MB, RUNTIME_TIMEOUT_SECONDS)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # 1. Create deployment package
        package_path = _create_deployment_package(tmp_path)

        if dry_run:
            logger.info("[DRY RUN] Package created at %s — skipping deployment", package_path)
            # Copy to project root for inspection
            dest = PROJECT_ROOT / "agent-package.zip"
            shutil.copy2(package_path, dest)
            logger.info("[DRY RUN] Package copied to %s", dest)
            return

        # 2. Deploy to AgentCore
        try:
            client = boto3.client("bedrock-agent", region_name=DEPLOY_REGION)
            _get_or_create_agent_runtime(client, agent_name)
            _upload_package(client, agent_name, package_path)
            logger.info("✓ Deployment complete: %s in %s", agent_name, DEPLOY_REGION)
        except ClientError as exc:
            logger.error(
                "Deployment failed: %s — %s",
                exc.response["Error"]["Code"],
                exc.response["Error"]["Message"],
            )
            sys.exit(1)
        except Exception:
            logger.error("Unexpected deployment error", exc_info=True)
            sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy ATM Profitability Optimizer agent to AgentCore Runtime"
    )
    parser.add_argument(
        "--agent-name",
        default=DEFAULT_AGENT_NAME,
        help=f"Agent name (default: {DEFAULT_AGENT_NAME})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Create package without deploying",
    )
    args = parser.parse_args()
    deploy(agent_name=args.agent_name, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
