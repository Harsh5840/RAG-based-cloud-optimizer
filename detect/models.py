"""
Anomaly data models for Cloud Cost Optimizer.

Defines the typed structures used throughout the detection → analysis pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class AnomalyType(str, Enum):
    """Classification of anomaly types detected by the system."""

    COST_SPIKE = "cost_spike"
    IDLE_RESOURCE = "idle_resource"
    OVERPROVISIONED = "overprovisioned"
    STOPPED_BUT_BILLED = "stopped_but_billed"
    WASTE_PATTERN = "waste_pattern"


class RiskLevel(str, Enum):
    """Risk level for a proposed optimization action."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Anomaly:
    """
    Represents a detected cost anomaly or waste pattern.

    This is the primary data structure that flows through the entire
    pipeline: detection → RAG retrieval → Claude analysis → action.
    """

    service: str
    """AWS/GCP service name (e.g. 'EC2', 'Compute Engine')."""

    issue_type: AnomalyType
    """Type of anomaly detected."""

    current_cost: float
    """Current cost in USD (daily or monthly depending on context)."""

    resource_id: str = ""
    """Specific resource identifier, if applicable."""

    expected_cost: float = 0.0
    """Expected/baseline cost in USD."""

    waste_score: int = 0
    """Waste score (0-100), populated for resource-level anomalies."""

    metrics: dict[str, Any] = field(default_factory=dict)
    """Additional metric key-value pairs (cpu_util, instance_type, etc.)."""

    account: str = ""
    """AWS account ID or GCP project."""

    region: str = ""
    """Cloud region."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """When the anomaly was detected."""

    @property
    def increase_pct(self) -> float:
        """Percentage increase over expected cost."""
        if self.expected_cost <= 0:
            return 0.0
        return ((self.current_cost - self.expected_cost) / self.expected_cost) * 100

    @property
    def estimated_monthly_waste(self) -> float:
        """Rough estimate of monthly waste based on current cost and waste score."""
        return self.current_cost * (self.waste_score / 100.0)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON/logging."""
        return {
            "service": self.service,
            "resource_id": self.resource_id,
            "issue_type": self.issue_type.value,
            "current_cost": self.current_cost,
            "expected_cost": self.expected_cost,
            "increase_pct": round(self.increase_pct, 1),
            "waste_score": self.waste_score,
            "metrics": self.metrics,
            "account": self.account,
            "region": self.region,
            "timestamp": self.timestamp.isoformat(),
        }

    def __str__(self) -> str:
        parts = [
            f"[{self.issue_type.value}]",
            f"{self.service}",
        ]
        if self.resource_id:
            parts.append(f"({self.resource_id})")
        parts.append(f"${self.current_cost:.2f}")
        if self.waste_score:
            parts.append(f"waste={self.waste_score}")
        return " ".join(parts)


@dataclass
class Recommendation:
    """
    Structured recommendation from the Claude analysis engine.

    Produced by ``actions/terraform_gen.py`` after Claude processes an anomaly
    with RAG context.
    """

    anomaly: Anomaly
    """The original anomaly that triggered this recommendation."""

    root_cause: str
    """Human-readable explanation of why this anomaly occurred."""

    actions: list[str]
    """Ordered list of recommended actions."""

    terraform_code: str
    """Generated Terraform HCL code to implement the fix."""

    savings_estimate: float
    """Estimated monthly savings in USD."""

    risk_level: RiskLevel
    """Risk level of applying this optimization."""

    rollback_plan: str
    """Steps to roll back the change if needed."""

    confidence: float = 0.0
    """Model confidence score (0.0 - 1.0)."""

    def to_dict(self) -> dict[str, Any]:
        """Serialize for PR body / Slack message."""
        return {
            "anomaly": self.anomaly.to_dict(),
            "root_cause": self.root_cause,
            "actions": self.actions,
            "terraform_code": self.terraform_code,
            "savings_estimate": self.savings_estimate,
            "risk_level": self.risk_level.value,
            "rollback_plan": self.rollback_plan,
            "confidence": self.confidence,
        }
