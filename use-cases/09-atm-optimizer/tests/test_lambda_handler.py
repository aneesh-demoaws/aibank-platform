"""
Tests for the Lambda MCP Server handler.

Validates request parsing, routing, validation, and error handling.
"""

import json
import pytest
from unittest.mock import patch

from mcp_server.lambda_handler import handler, _validate_request, _parse_body, TOOL_REGISTRY


class TestRequestValidation:
    """Test request validation logic."""

    def test_missing_tool_name(self):
        error, _ = _validate_request({})
        assert error is not None
        assert "tool_name" in error

    def test_unknown_tool(self):
        error, _ = _validate_request({"tool_name": "nonexistent_tool"})
        assert error is not None
        assert "Unknown tool" in error

    def test_missing_required_params(self):
        error, _ = _validate_request({
            "tool_name": "query_atm_data",
            "parameters": {"atm_id": "ATM_SEEF_01"},
        })
        assert error is not None
        assert "Missing required" in error

    def test_valid_request(self):
        error, body = _validate_request({
            "tool_name": "query_atm_data",
            "parameters": {
                "atm_id": "ATM_SEEF_01",
                "start_date": "2024-01-01",
                "end_date": "2024-01-31",
            },
        })
        assert error is None
        assert body is not None

    def test_optional_params_not_required(self):
        error, body = _validate_request({
            "tool_name": "detect_anomalies",
            "parameters": {},
        })
        assert error is None

    def test_all_tools_registered(self):
        expected = {
            "query_atm_data", "query_branch_proximity", "query_revenue_data",
            "query_maintenance_costs", "query_cash_levels",
            "calculate_impact_analysis", "detect_anomalies", "profitability_ranking",
        }
        assert set(TOOL_REGISTRY.keys()) == expected


class TestParseBody:
    """Test body parsing from Function URL events."""

    def test_parse_string_body(self):
        event = {"body": '{"tool_name": "detect_anomalies"}'}
        result = _parse_body(event)
        assert result["tool_name"] == "detect_anomalies"

    def test_parse_base64_body(self):
        import base64
        payload = json.dumps({"tool_name": "detect_anomalies"})
        event = {
            "body": base64.b64encode(payload.encode()).decode(),
            "isBase64Encoded": True,
        }
        result = _parse_body(event)
        assert result["tool_name"] == "detect_anomalies"

    def test_parse_empty_body(self):
        result = _parse_body({})
        assert result == {}


class TestHandler:
    """Test the Lambda handler end-to-end."""

    def test_get_health_check(self):
        event = {
            "requestContext": {"http": {"method": "GET"}},
        }
        response = handler(event, None)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "healthy"
        assert "tools" in body

    def test_invalid_json(self):
        event = {"body": "not json{{{"}
        response = handler(event, None)
        assert response["statusCode"] == 400

    def test_missing_tool_name(self):
        event = {"body": json.dumps({"parameters": {}})}
        response = handler(event, None)
        assert response["statusCode"] == 400

    def test_successful_tool_call(self):
        mock_fn = patch("agent.tools.query_atm_data.query_atm_data").start()
        mock_fn.return_value = {
            "atm_id": "ATM_SEEF_01",
            "transaction_count": 150,
            "currency": "BHD",
        }
        with patch.dict(TOOL_REGISTRY, {
            "query_atm_data": {**TOOL_REGISTRY["query_atm_data"], "fn": mock_fn},
        }):
            event = {
                "body": json.dumps({
                    "tool_name": "query_atm_data",
                    "parameters": {
                        "atm_id": "ATM_SEEF_01",
                        "start_date": "2024-01-01",
                        "end_date": "2024-01-31",
                    },
                }),
            }
            response = handler(event, None)
            assert response["statusCode"] == 200
            body = json.loads(response["body"])
            assert body["status"] == "success"
            assert body["tool_name"] == "query_atm_data"
            mock_fn.assert_called_once_with(
                atm_id="ATM_SEEF_01",
                start_date="2024-01-01",
                end_date="2024-01-31",
            )
        patch.stopall()

    def test_tool_with_optional_params(self):
        mock_fn = patch("agent.tools.detect_anomalies.detect_anomalies").start()
        mock_fn.return_value = []
        with patch.dict(TOOL_REGISTRY, {
            "detect_anomalies": {**TOOL_REGISTRY["detect_anomalies"], "fn": mock_fn},
        }):
            event = {
                "body": json.dumps({
                    "tool_name": "detect_anomalies",
                    "parameters": {"period": "7d"},
                }),
            }
            response = handler(event, None)
            assert response["statusCode"] == 200
            mock_fn.assert_called_once_with(period="7d")
        patch.stopall()

    def test_tool_exception_returns_500(self):
        mock_fn = patch("agent.tools.profitability_ranking.profitability_ranking").start()
        mock_fn.side_effect = RuntimeError("Athena timeout")
        with patch.dict(TOOL_REGISTRY, {
            "profitability_ranking": {**TOOL_REGISTRY["profitability_ranking"], "fn": mock_fn},
        }):
            event = {
                "body": json.dumps({
                    "tool_name": "profitability_ranking",
                    "parameters": {},
                }),
            }
            response = handler(event, None)
            assert response["statusCode"] == 500
            body = json.loads(response["body"])
            assert body["status"] == "error"
            assert "Athena timeout" in body["error"]
        patch.stopall()
