"""
Tests for the anomaly detection module.

Feeds synthetic time-series data and verifies:
- 2-sigma cost spike detection
- Waste score threshold detection
- Anomaly model serialization
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from detect.models import Anomaly, AnomalyType, Recommendation, RiskLevel


# ──────────────────────────────────────────────────────────────────────────────
# Anomaly Model Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestAnomalyModel:
    """Tests for the Anomaly dataclass."""

    def test_increase_pct_calculation(self):
        """increase_pct should calculate percentage above expected cost."""
        anomaly = Anomaly(
            service="EC2",
            issue_type=AnomalyType.COST_SPIKE,
            current_cost=150.0,
            expected_cost=100.0,
        )
        assert anomaly.increase_pct == 50.0

    def test_increase_pct_zero_expected(self):
        """increase_pct should return 0 when expected cost is 0."""
        anomaly = Anomaly(
            service="EC2",
            issue_type=AnomalyType.COST_SPIKE,
            current_cost=150.0,
            expected_cost=0.0,
        )
        assert anomaly.increase_pct == 0.0

    def test_estimated_monthly_waste(self):
        """estimated_monthly_waste should scale cost by waste score."""
        anomaly = Anomaly(
            service="EC2",
            issue_type=AnomalyType.IDLE_RESOURCE,
            current_cost=100.0,
            waste_score=80,
        )
        assert anomaly.estimated_monthly_waste == 80.0

    def test_to_dict(self):
        """to_dict should produce a JSON-serializable dict."""
        anomaly = Anomaly(
            service="EC2",
            resource_id="i-abc123",
            issue_type=AnomalyType.IDLE_RESOURCE,
            current_cost=420.0,
            expected_cost=100.0,
            waste_score=95,
            metrics={"cpu_utilization": 1.2},
        )
        d = anomaly.to_dict()

        assert d["service"] == "EC2"
        assert d["resource_id"] == "i-abc123"
        assert d["issue_type"] == "idle_resource"
        assert d["current_cost"] == 420.0
        assert d["waste_score"] == 95
        assert d["increase_pct"] == 320.0

    def test_str_representation(self):
        """__str__ should produce a readable summary."""
        anomaly = Anomaly(
            service="EC2",
            resource_id="i-abc123",
            issue_type=AnomalyType.IDLE_RESOURCE,
            current_cost=420.0,
            waste_score=95,
        )
        s = str(anomaly)
        assert "idle_resource" in s
        assert "EC2" in s
        assert "i-abc123" in s


class TestRecommendationModel:
    """Tests for the Recommendation dataclass."""

    def test_to_dict(self):
        anomaly = Anomaly(
            service="EC2",
            issue_type=AnomalyType.IDLE_RESOURCE,
            current_cost=200.0,
        )
        rec = Recommendation(
            anomaly=anomaly,
            root_cause="Instance is idle",
            actions=["Terminate instance", "Delete EBS volumes"],
            terraform_code='resource "aws_instance" "example" {}',
            savings_estimate=180.0,
            risk_level=RiskLevel.LOW,
            rollback_plan="Launch new instance from AMI",
            confidence=0.9,
        )
        d = rec.to_dict()

        assert d["savings_estimate"] == 180.0
        assert d["risk_level"] == "low"
        assert len(d["actions"]) == 2
        assert d["confidence"] == 0.9


# ──────────────────────────────────────────────────────────────────────────────
# Cost Spike Detection Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestCostSpikeDetection:
    """Tests for the 2-sigma cost spike detector."""

    @patch("detect.detector._query_api")
    def test_detects_spike_above_2_sigma(self, mock_query_api):
        """Should flag when latest cost > mean + 2*std."""
        from detect.detector import detect_cost_spikes

        # Build mock records: 29 days of ~$100, then a spike to $300
        mock_client = MagicMock()
        mock_query = MagicMock()
        mock_query_api.return_value = (mock_client, mock_query)

        normal_costs = [100.0 + (i % 5) for i in range(29)]  # ~$100-$104
        spike_cost = 300.0  # Way above 2 sigma

        # Build mock table/records structure
        records = []
        for cost in normal_costs + [spike_cost]:
            record = MagicMock()
            record.values = {"service": "EC2"}
            record.get_value.return_value = cost
            records.append(record)

        mock_table = MagicMock()
        mock_table.records = records
        mock_query.query.return_value = [mock_table]

        anomalies = detect_cost_spikes()

        assert len(anomalies) == 1
        assert anomalies[0].service == "EC2"
        assert anomalies[0].issue_type == AnomalyType.COST_SPIKE
        assert anomalies[0].current_cost == 300.0

    @patch("detect.detector._query_api")
    def test_no_spike_when_stable(self, mock_query_api):
        """Should not flag when costs are within normal range."""
        from detect.detector import detect_cost_spikes

        mock_client = MagicMock()
        mock_query = MagicMock()
        mock_query_api.return_value = (mock_client, mock_query)

        # Stable costs
        records = []
        for cost in [100.0, 102.0, 98.0, 101.0, 99.0, 103.0, 97.0, 100.5]:
            record = MagicMock()
            record.values = {"service": "EC2"}
            record.get_value.return_value = cost
            records.append(record)

        mock_table = MagicMock()
        mock_table.records = records
        mock_query.query.return_value = [mock_table]

        anomalies = detect_cost_spikes()
        assert len(anomalies) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Waste Pattern Detection Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestWasteDetection:
    """Tests for the waste pattern detector."""

    @patch("detect.detector._query_api")
    def test_detects_idle_resource(self, mock_query_api):
        """Should flag instances with waste_score > 70 and low CPU as idle."""
        from detect.detector import detect_waste_patterns

        mock_client = MagicMock()
        mock_query = MagicMock()
        mock_query_api.return_value = (mock_client, mock_query)

        record = MagicMock()
        record.values = {
            "instance_id": "i-abc123",
            "instance_type": "m5.xlarge",
            "state": "running",
            "cpu_utilization": 1.5,
            "waste_score": 95,
            "cost": 140.0,
            "account": "123456789",
            "region": "us-east-1",
        }

        mock_table = MagicMock()
        mock_table.records = [record]
        mock_query.query.return_value = [mock_table]

        anomalies = detect_waste_patterns()

        assert len(anomalies) == 1
        assert anomalies[0].issue_type == AnomalyType.IDLE_RESOURCE
        assert anomalies[0].resource_id == "i-abc123"
        assert anomalies[0].waste_score == 95

    @patch("detect.detector._query_api")
    def test_detects_stopped_but_billed(self, mock_query_api):
        """Should flag stopped instances as STOPPED_BUT_BILLED."""
        from detect.detector import detect_waste_patterns

        mock_client = MagicMock()
        mock_query = MagicMock()
        mock_query_api.return_value = (mock_client, mock_query)

        record = MagicMock()
        record.values = {
            "instance_id": "i-stopped456",
            "instance_type": "t3.medium",
            "state": "stopped",
            "cpu_utilization": 0.0,
            "waste_score": 90,
            "cost": 30.0,
            "account": "123456789",
            "region": "us-east-1",
        }

        mock_table = MagicMock()
        mock_table.records = [record]
        mock_query.query.return_value = [mock_table]

        anomalies = detect_waste_patterns()

        assert len(anomalies) == 1
        assert anomalies[0].issue_type == AnomalyType.STOPPED_BUT_BILLED


# ──────────────────────────────────────────────────────────────────────────────
# Combined Detection Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestRunDetection:
    """Tests for the combined detection runner."""

    @patch("detect.detector.detect_waste_patterns")
    @patch("detect.detector.detect_cost_spikes")
    def test_combines_all_anomalies(self, mock_spikes, mock_waste):
        """run_detection should combine results from all detectors."""
        from detect.detector import run_detection

        mock_spikes.return_value = [
            Anomaly(service="EC2", issue_type=AnomalyType.COST_SPIKE, current_cost=300.0)
        ]
        mock_waste.return_value = [
            Anomaly(service="EC2", issue_type=AnomalyType.IDLE_RESOURCE, current_cost=140.0)
        ]

        anomalies = run_detection()

        assert len(anomalies) == 2
        types = {a.issue_type for a in anomalies}
        assert AnomalyType.COST_SPIKE in types
        assert AnomalyType.IDLE_RESOURCE in types
