"""
AI Bank — Loan Strands Agent (Alma)
Adapted from NeoBank Kiku agent → integrated into Alma for aibank.demoaws.com
"""
import logging
import os
from strands import Agent
from strands.models import BedrockModel
from tools.loan_tools import calculate_loan_payment, apply_for_loan, get_loan_status
from tools.account_tools import get_account_balance, get_account_details
from tools.auth_tools import authenticate_user, get_user_profile

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Alma, the AI banking assistant for AI Bank — a premium digital bank in Bahrain.

You help customers with:
- Loan calculations and eligibility checks
- Loan applications (Instant Money and Personal Finance only)
- Loan status tracking
- Account balance and details

Loan products available:
- Instant Money: BHD 100–2,000, up to 24 months, 5.5% p.a.
- Personal Finance: BHD 500–20,000, up to 60 months, 4.5% p.a.

Guidelines:
- Always verify user identity before accessing account data
- Provide amounts in BHD (Bahraini Dinar)
- Be concise, professional, and empathetic
- Never fabricate data — only use what tools return
- If unsure, escalate to human support: support@aibank.demoaws.com
"""

class AIBankLoanAgent:
    def __init__(self):
        model = BedrockModel(
            model_id=os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-3-7-sonnet-20250219-v1:0"),
            region_name=os.environ.get("AWS_REGION", "eu-west-1"),
        )
        self._agent = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=[
                authenticate_user,
                get_user_profile,
                get_account_balance,
                get_account_details,
                calculate_loan_payment,
                apply_for_loan,
                get_loan_status,
            ],
        )

    def process(self, message: str, user_id: str = None, session_id: str = None) -> str:
        context = f"[UserID: {user_id}] " if user_id else ""
        try:
            result = self._agent(f"{context}{message}")
            return str(result)
        except Exception as e:
            logger.error(f"Agent error: {e}")
            raise
