"""
Tests for the data ingestion module.

Mocks AWS and InfluxDB clients to verify:
- Cost Explorer response parsing
- EC2 instance metric collection
- Waste score calculation
- InfluxDB point construction and writes
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

from ingest.waste_score import calculate_waste_score, classify_waste


# ──────────────────────────────────────────────────────────────────────────────
# Waste Score Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestWasteScore:
    """Tests for the waste score calculator."""

    def test_idle_running_instance(self):
        """Running instance with < 5% CPU should score very high."""
        score = calculate_waste_score(cpu_util=1.2, instance_type="m5.xlarge", state="running")
        assert score >= 80, f"Idle running instance should score ≥ 80, got {score}"

    def test_overprovisioned_instance(self):
        """Instance with < 20% CPU should get overprovisioned penalty."""
        score = calculate_waste_score(cpu_util=15.0, instance_type="t3.medium", state="running")
        assert score >= 50, f"Overprovisioned instance should score ≥ 50, got {score}"

    def test_expensive_low_use(self):
        """Expensive xlarge instance with < 30% CPU should be flagged."""
        score = calculate_waste_score(cpu_util=25.0, instance_type="m5.2xlarge", state="running")
        assert score >= 60, f"Expensive underused instance should score ≥ 60, got {score}"

    def test_stopped_instance(self):
        """Stopped instance should get penalty (EBS still billed)."""
        score = calculate_waste_score(cpu_util=0.0, instance_type="t3.small", state="stopped")
        assert score >= 40, f"Stopped instance should score ≥ 40, got {score}"

    def test_well_utilized_instance(self):
        """Well-utilized instance should have low waste score."""
        score = calculate_waste_score(cpu_util=75.0, instance_type="t3.medium", state="running")
        assert score == 0, f"Well-utilized instance should score 0, got {score}"

    def test_max_score_is_100(self):
        """Score should never exceed 100."""
        score = calculate_waste_score(cpu_util=0.5, instance_type="m5.4xlarge", state="running")
        assert score <= 100, f"Score should be capped at 100, got {score}"

    def test_classify_critical(self):
        assert classify_waste(95) == "critical"

    def test_classify_high(self):
        assert classify_waste(65) == "high"

    def test_classify_medium(self):
        assert classify_waste(45) == "medium"

    def test_classify_low(self):
        assert classify_waste(25) == "low"

    def test_classify_none(self):
        assert classify_waste(5) == "none"


# ──────────────────────────────────────────────────────────────────────────────
# Cost Ingestion Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestCostIngestion:
    """Tests for AWS Cost Explorer data fetching and InfluxDB writing."""

    @patch("ingest.ingest.boto3.client")
    def test_fetch_aws_costs_parses_response(self, mock_boto_client):
        """Cost Explorer response should be parsed into normalized dicts."""
        from ingest.ingest import fetch_aws_costs

        # Mock Cost Explorer response
        mock_ce = MagicMock()
        mock_boto_client.return_value = mock_ce
        mock_ce.get_cost_and_usage.return_value = {
            "ResultsByTime": [
                {
                    "TimePeriod": {"Start": "2025-01-15", "End": "2025-01-16"},
                    "Groups": [
                        {
                            "Keys": ["Amazon Elastic Compute Cloud - Compute", "123456789012"],
                            "Metrics": {
                                "UnblendedCost": {"Amount": "142.50", "Unit": "USD"},
                                "UsageQuantity": {"Amount": "720.0", "Unit": "Hrs"},
                            },
                        },
                        {
                            "Keys": ["Amazon Simple Storage Service", "123456789012"],
                            "Metrics": {
                                "UnblendedCost": {"Amount": "23.10", "Unit": "USD"},
                                "UsageQuantity": {"Amount": "1500.0", "Unit": "GB-Mo"},
                            },
                        },
                    ],
                }
            ]
        }

        records = fetch_aws_costs(days=1)

        assert len(records) == 2
        assert records[0]["service"] == "Amazon Elastic Compute Cloud - Compute"
        assert records[0]["account"] == "123456789012"
        assert records[0]["cost"] == 142.50
        assert records[0]["date"] == "2025-01-15"
        assert records[1]["service"] == "Amazon Simple Storage Service"
        assert records[1]["cost"] == 23.10

    @patch("ingest.ingest._influx_client")
    def test_write_cost_points_creates_correct_points(self, mock_influx):
        """InfluxDB writer should create correctly tagged points."""
        from ingest.ingest import write_cost_points

        mock_client = MagicMock()
        mock_write_api = MagicMock()
        mock_influx.return_value = mock_client
        mock_client.write_api.return_value = mock_write_api

        records = [
            {
                "service": "EC2",
                "account": "123456789012",
                "date": "2025-01-15",
                "cost": 100.0,
                "usage_quantity": 500.0,
            }
        ]

        count = write_cost_points(records)

        assert count == 1
        mock_write_api.write.assert_called_once()

    @patch("ingest.ingest._influx_client")
    @patch("ingest.ingest.fetch_ec2_instances")
    @patch("ingest.ingest.fetch_aws_costs")
    def test_run_ingestion_returns_summary(self, mock_costs, mock_ec2, mock_influx):
        """run_ingestion should return a summary with point counts."""
        from ingest.ingest import run_ingestion

        mock_costs.return_value = [
            {"service": "EC2", "account": "1", "date": "2025-01-15", "cost": 50.0, "usage_quantity": 100.0}
        ]
        mock_ec2.return_value = [
            {
                "instance_id": "i-abc123",
                "instance_type": "t3.micro",
                "state": "running",
                "region": "us-east-1",
                "account": "1",
                "cpu_utilization": 5.0,
                "cost": 7.60,
                "waste_score": 50,
            }
        ]

        mock_client = MagicMock()
        mock_write_api = MagicMock()
        mock_influx.return_value = mock_client
        mock_client.write_api.return_value = mock_write_api

        result = run_ingestion()

        assert "cost_points" in result
        assert "ec2_points" in result
        assert result["cost_points"] == 1
        assert result["ec2_points"] == 1
