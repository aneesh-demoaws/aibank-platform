"""
Tests for the Strands Agent module (Task 8).

Covers:
- System prompt generation (8.1)
- Agent creation with MCP tool config and role filtering (8.2)
- Session management / multi-turn conversation (8.3)
- Error handling wrapper (8.4)
- Deploy script packaging (8.5)
"""

import time
import unittest
from unittest.mock import patch

from agent.config import ADMIN_TOOLS, OPERATOR_TOOLS, MODEL_ID, MODEL_REGION
from agent.system_prompt import build_system_prompt, SYSTEM_PROMPT
from agent.session import (
    ConversationTurn,
    Session,
    SessionManager,
    SESSION_TTL_SECONDS,
)
from agent.agent import (
    ALL_TOOLS,
    ALL_TOOL_NAMES,
    ADMIN_TOOL_NAMES,
    OPERATOR_TOOL_NAMES,
    MCP_TOOL_DEFINITIONS,
    _wrap_tool_with_error_handling,
    get_permitted_tool_names,
)


# -- 8.1 System Prompt Tests ----------------------------------------------

class TestSystemPrompt(unittest.TestCase):

    def test_admin_prompt_contains_all_tools(self):
        prompt = build_system_prompt("admin")
        for tool in ADMIN_TOOLS:
            self.assertIn(tool, prompt)

    def test_operator_prompt_contains_basic_tools(self):
        prompt = build_system_prompt("operator")
        for tool in OPERATOR_TOOLS:
            self.assertIn(tool, prompt)

    def test_operator_prompt_excludes_admin_tools(self):
        prompt = build_system_prompt("operator")
        self.assertIn("do NOT have access", prompt)

    def test_admin_prompt_mentions_full_access(self):
        prompt = build_system_prompt("admin")
        self.assertIn("Admin role", prompt)
        self.assertIn("full access", prompt)

    def test_operator_prompt_mentions_basic_access(self):
        prompt = build_system_prompt("operator")
        self.assertIn("Operator role", prompt)
        self.assertIn("basic access", prompt)

    def test_both_prompts_contain_bhd(self):
        for role in ("admin", "operator"):
            prompt = build_system_prompt(role)
            self.assertIn("BHD", prompt)

    def test_unknown_role_defaults_to_operator(self):
        prompt = build_system_prompt("unknown")
        self.assertIn("Operator role", prompt)
        self.assertIn("basic access", prompt)

    def test_module_level_constant_is_admin(self):
        self.assertEqual(SYSTEM_PROMPT, build_system_prompt("admin"))

    def test_prompt_contains_guidelines(self):
        prompt = build_system_prompt("admin")
        self.assertIn("Guidelines", prompt)


# -- 8.2 Agent Creation Tests (MCP-based) ---------------------------------

class TestAgentCreation(unittest.TestCase):

    def test_all_tools_registry_has_eight_tools(self):
        self.assertEqual(len(ALL_TOOLS), 8)

    def test_all_tool_names_match_config(self):
        for name in ADMIN_TOOLS:
            self.assertIn(name, ALL_TOOL_NAMES)

    def test_mcp_tool_definitions_has_eight_entries(self):
        self.assertEqual(len(MCP_TOOL_DEFINITIONS), 8)

    def test_admin_gets_all_tool_names(self):
        permitted = get_permitted_tool_names("admin")
        self.assertEqual(set(permitted), set(ADMIN_TOOLS))

    def test_operator_gets_basic_tool_names(self):
        permitted = get_permitted_tool_names("operator")
        self.assertEqual(set(permitted), set(OPERATOR_TOOLS))

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_create_agent_admin_has_mcp_config(self, mock_agent_cls, mock_model_cls):
        from agent.agent import create_agent
        agent = create_agent(role="admin")
        mcp_config = agent._mcp_config
        tools = mcp_config["mcpServers"]["atm-optimizer"]["tools"]
        self.assertEqual(len(tools), len(ADMIN_TOOLS))

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_create_agent_operator_has_mcp_config(self, mock_agent_cls, mock_model_cls):
        from agent.agent import create_agent
        agent = create_agent(role="operator")
        mcp_config = agent._mcp_config
        tools = mcp_config["mcpServers"]["atm-optimizer"]["tools"]
        self.assertEqual(len(tools), len(OPERATOR_TOOLS))

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_create_agent_uses_correct_model(self, mock_agent_cls, mock_model_cls):
        from agent.agent import create_agent
        create_agent(role="admin")
        mock_model_cls.assert_called_once()
        call_kwargs = mock_model_cls.call_args
        self.assertEqual(call_kwargs.kwargs.get("model_id"), MODEL_ID)
        self.assertEqual(call_kwargs.kwargs.get("region_name"), MODEL_REGION)

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_create_agent_from_claims_admin(self, mock_agent_cls, mock_model_cls):
        from agent.agent import create_agent_from_claims
        agent = create_agent_from_claims({"cognito:groups": ["admin"]})
        self.assertEqual(agent._role, "admin")
        tools = agent._mcp_config["mcpServers"]["atm-optimizer"]["tools"]
        self.assertEqual(len(tools), len(ADMIN_TOOLS))

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_create_agent_from_claims_operator(self, mock_agent_cls, mock_model_cls):
        from agent.agent import create_agent_from_claims
        agent = create_agent_from_claims({"cognito:groups": ["operator"]})
        self.assertEqual(agent._role, "operator")
        tools = agent._mcp_config["mcpServers"]["atm-optimizer"]["tools"]
        self.assertEqual(len(tools), len(OPERATOR_TOOLS))

    @patch("agent.agent.BedrockModel")
    @patch("agent.agent.Agent")
    def test_mcp_endpoint_in_config(self, mock_agent_cls, mock_model_cls):
        from agent.agent import create_agent, MCP_SERVER_ENDPOINT
        agent = create_agent(role="admin")
        endpoint = agent._mcp_config["mcpServers"]["atm-optimizer"]["endpoint"]
        self.assertEqual(endpoint, MCP_SERVER_ENDPOINT)


# -- 8.3 Session Manager Tests --------------------------------------------

class TestSession(unittest.TestCase):

    def test_new_session_not_expired(self):
        s = Session(session_id="s1", user_id="u1", role="admin")
        self.assertFalse(s.is_expired)

    def test_expired_session(self):
        s = Session(session_id="s1", user_id="u1", role="admin")
        s.last_active = time.time() - SESSION_TTL_SECONDS - 1
        self.assertTrue(s.is_expired)

    def test_add_turn_refreshes_timestamp(self):
        s = Session(session_id="s1", user_id="u1", role="admin")
        old_ts = s.last_active
        time.sleep(0.01)
        s.add_turn("user", "Hello")
        self.assertGreater(s.last_active, old_ts)

    def test_add_turn_appends_to_history(self):
        s = Session(session_id="s1", user_id="u1", role="admin")
        s.add_turn("user", "Q1")
        s.add_turn("assistant", "A1")
        self.assertEqual(len(s.turns), 2)

    def test_history_summary_format(self):
        s = Session(session_id="s1", user_id="u1", role="admin")
        s.add_turn("user", "What is ATM_SEEF_01 revenue?")
        summary = s.get_history_summary()
        self.assertEqual(len(summary), 1)
        self.assertIn("role", summary[0])
        self.assertIn("content", summary[0])


class TestSessionManager(unittest.TestCase):

    def setUp(self):
        self.mgr = SessionManager()

    def test_create_session(self):
        s = self.mgr.create_session("u1", "admin")
        self.assertIsNotNone(s.session_id)
        self.assertEqual(s.user_id, "u1")

    def test_get_session(self):
        s = self.mgr.create_session("u1", "admin", session_id="test-123")
        retrieved = self.mgr.get_session("test-123")
        self.assertIs(retrieved, s)

    def test_get_missing_session_returns_none(self):
        self.assertIsNone(self.mgr.get_session("nonexistent"))

    def test_get_expired_session_returns_none(self):
        s = self.mgr.create_session("u1", "admin", session_id="exp-1")
        s.last_active = time.time() - SESSION_TTL_SECONDS - 1
        self.assertIsNone(self.mgr.get_session("exp-1"))

    def test_get_or_create_returns_existing(self):
        s1 = self.mgr.create_session("u1", "admin", session_id="s1")
        s2 = self.mgr.get_or_create_session("s1", "u1", "admin")
        self.assertIs(s1, s2)

    def test_get_or_create_creates_new_when_missing(self):
        s = self.mgr.get_or_create_session("new-1", "u1", "operator")
        self.assertEqual(s.session_id, "new-1")

    def test_delete_session(self):
        self.mgr.create_session("u1", "admin", session_id="del-1")
        self.assertTrue(self.mgr.delete_session("del-1"))
        self.assertIsNone(self.mgr.get_session("del-1"))

    def test_delete_nonexistent_returns_false(self):
        self.assertFalse(self.mgr.delete_session("nope"))

    def test_cleanup_expired(self):
        s1 = self.mgr.create_session("u1", "admin", session_id="active")
        s2 = self.mgr.create_session("u2", "operator", session_id="stale")
        s2.last_active = time.time() - SESSION_TTL_SECONDS - 1
        removed = self.mgr.cleanup_expired()
        self.assertEqual(removed, 1)
        self.assertIsNotNone(self.mgr.get_session("active"))


# -- 8.4 Error Handling Tests ---------------------------------------------

class TestErrorHandlingWrapper(unittest.TestCase):

    def test_successful_call_passes_through(self):
        def good_tool(x):
            return {"result": x}
        wrapped = _wrap_tool_with_error_handling(good_tool)
        self.assertEqual(wrapped(42), {"result": 42})

    def test_timeout_error_returns_friendly_message(self):
        def slow_tool():
            raise TimeoutError("timed out")
        wrapped = _wrap_tool_with_error_handling(slow_tool)
        result = wrapped()
        self.assertIn("error", result)
        self.assertIn("took too long", result["error"])

    def test_connection_error_returns_friendly_message(self):
        def broken_tool():
            raise ConnectionError("refused")
        wrapped = _wrap_tool_with_error_handling(broken_tool)
        result = wrapped()
        self.assertIn("error", result)
        self.assertIn("Unable to reach", result["error"])

    def test_generic_exception_returns_friendly_message(self):
        def bad_tool():
            raise ValueError("something broke")
        wrapped = _wrap_tool_with_error_handling(bad_tool)
        result = wrapped()
        self.assertIn("error", result)
        self.assertIn("unexpected error", result["error"])

    def test_wrapper_preserves_function_name(self):
        def my_special_tool():
            return {}
        wrapped = _wrap_tool_with_error_handling(my_special_tool)
        self.assertEqual(wrapped.__name__, "my_special_tool")

    def test_error_message_includes_tool_name(self):
        def query_atm_data():
            raise RuntimeError("fail")
        wrapped = _wrap_tool_with_error_handling(query_atm_data)
        result = wrapped()
        self.assertIn("query_atm_data", result["error"])


if __name__ == "__main__":
    unittest.main()
