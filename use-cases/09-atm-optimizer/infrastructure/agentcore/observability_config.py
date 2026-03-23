"""
AgentCore Observability configuration for ATM Profitability Optimizer.

Sets up:
- OpenTelemetry tracing for agent invocations and MCP tool calls
- CloudWatch dashboard for key agent metrics
- CloudWatch alarms: error rate >5%, response time >30s

Validates: Requirement 22 (AgentCore Observability)
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AI_REGION = "eu-west-1"
AGENT_NAME = "neobank-atm-profitability-optimizer"
NAMESPACE = "ATMOptimizer/AgentCore"

# Alarm thresholds
ERROR_RATE_THRESHOLD_PERCENT = 5.0
RESPONSE_TIME_THRESHOLD_SECONDS = 30


@dataclass(frozen=True)
class TracingConfig:
    """OpenTelemetry tracing configuration for AgentCore."""

    enabled: bool = True
    service_name: str = AGENT_NAME
    exporter: str = "otlp"  # OTLP exporter to CloudWatch
    sampling_rate: float = 1.0  # Sample all requests
    propagation: str = "tracecontext"  # W3C Trace Context

    # Spans to capture
    trace_agent_invocations: bool = True
    trace_mcp_tool_calls: bool = True
    trace_model_calls: bool = True

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "serviceName": self.service_name,
            "exporter": self.exporter,
            "samplingRate": self.sampling_rate,
            "propagation": self.propagation,
            "spans": {
                "agentInvocations": self.trace_agent_invocations,
                "mcpToolCalls": self.trace_mcp_tool_calls,
                "modelCalls": self.trace_model_calls,
            },
        }


@dataclass(frozen=True)
class CloudWatchDashboard:
    """CloudWatch dashboard definition for agent monitoring."""

    name: str = f"{AGENT_NAME}-dashboard"
    region: str = AI_REGION
    namespace: str = NAMESPACE

    # Widgets to display on the dashboard
    widgets: tuple[dict, ...] = (
        {
            "title": "Agent Invocations",
            "metric": "InvocationCount",
            "stat": "Sum",
            "period_seconds": 300,
        },
        {
            "title": "Average Response Time",
            "metric": "ResponseLatency",
            "stat": "Average",
            "period_seconds": 300,
        },
        {
            "title": "Error Rate",
            "metric": "ErrorCount",
            "stat": "Sum",
            "period_seconds": 300,
        },
        {
            "title": "MCP Tool Latency",
            "metric": "ToolCallLatency",
            "stat": "p99",
            "period_seconds": 300,
        },
        {
            "title": "Token Usage",
            "metric": "TokensConsumed",
            "stat": "Sum",
            "period_seconds": 300,
        },
        {
            "title": "Active Sessions",
            "metric": "ActiveSessions",
            "stat": "Maximum",
            "period_seconds": 60,
        },
    )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "region": self.region,
            "namespace": self.namespace,
            "widgets": list(self.widgets),
        }


@dataclass(frozen=True)
class CloudWatchAlarm:
    """Single CloudWatch alarm definition."""

    name: str
    metric: str
    threshold: float
    comparison: str  # GreaterThanThreshold, LessThanThreshold, etc.
    evaluation_periods: int = 3
    period_seconds: int = 300
    stat: str = "Average"
    namespace: str = NAMESPACE

    def to_dict(self) -> dict:
        return {
            "alarmName": self.name,
            "namespace": self.namespace,
            "metricName": self.metric,
            "threshold": self.threshold,
            "comparisonOperator": self.comparison,
            "evaluationPeriods": self.evaluation_periods,
            "period": self.period_seconds,
            "statistic": self.stat,
        }


# Pre-configured alarms per requirements
ERROR_RATE_ALARM = CloudWatchAlarm(
    name=f"{AGENT_NAME}-high-error-rate",
    metric="ErrorRate",
    threshold=ERROR_RATE_THRESHOLD_PERCENT,
    comparison="GreaterThanThreshold",
    stat="Average",
)

RESPONSE_TIME_ALARM = CloudWatchAlarm(
    name=f"{AGENT_NAME}-high-response-time",
    metric="ResponseLatency",
    threshold=RESPONSE_TIME_THRESHOLD_SECONDS,
    comparison="GreaterThanThreshold",
    stat="Average",
)


@dataclass(frozen=True)
class ObservabilityConfig:
    """Top-level AgentCore Observability configuration."""

    tracing: TracingConfig = field(default_factory=TracingConfig)
    dashboard: CloudWatchDashboard = field(default_factory=CloudWatchDashboard)
    alarms: tuple[CloudWatchAlarm, ...] = (ERROR_RATE_ALARM, RESPONSE_TIME_ALARM)

    def to_dict(self) -> dict:
        """Full observability configuration for AgentCore deployment."""
        return {
            "tracing": self.tracing.to_dict(),
            "dashboard": self.dashboard.to_dict(),
            "alarms": [a.to_dict() for a in self.alarms],
        }
