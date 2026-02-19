"""
Tests for the RAG retrieval module.

Mocks Pinecone and SentenceTransformer to verify:
- Query building from anomaly objects
- Context retrieval and formatting
- Fallback behavior when Pinecone is unavailable
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from detect.models import Anomaly, AnomalyType


# ──────────────────────────────────────────────────────────────────────────────
# Query Building Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestQueryBuilding:
    """Tests for building natural-language queries from anomalies."""

    def test_idle_resource_query(self):
        """Query for an idle resource should include relevant terms."""
        from rag.optimization_rag import build_query

        anomaly = Anomaly(
            service="EC2",
            resource_id="i-abc123",
            issue_type=AnomalyType.IDLE_RESOURCE,
            current_cost=420.0,
            waste_score=95,
            metrics={
                "cpu_utilization": 1.2,
                "instance_type": "m5.xlarge",
                "state": "running",
            },
        )

        query = build_query(anomaly)

        assert "EC2" in query
        assert "idle" in query.lower() or "idle_resource" in query
        assert "optimization" in query
        assert "1.2" in query  # CPU util
        assert "m5.xlarge" in query

    def test_cost_spike_query(self):
        """Query for a cost spike should include service and spike info."""
        from rag.optimization_rag import build_query

        anomaly = Anomaly(
            service="RDS",
            issue_type=AnomalyType.COST_SPIKE,
            current_cost=500.0,
            expected_cost=100.0,
        )

        query = build_query(anomaly)

        assert "RDS" in query
        assert "cost" in query.lower() or "spike" in query.lower()

    def test_stopped_instance_query(self):
        """Query for a stopped instance should mention the state."""
        from rag.optimization_rag import build_query

        anomaly = Anomaly(
            service="EC2",
            issue_type=AnomalyType.STOPPED_BUT_BILLED,
            current_cost=30.0,
            metrics={"state": "stopped"},
        )

        query = build_query(anomaly)
        assert "stopped" in query.lower()


# ──────────────────────────────────────────────────────────────────────────────
# Context Retrieval Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestContextRetrieval:
    """Tests for Pinecone-based context retrieval."""

    @patch("rag.optimization_rag._encode_query")
    @patch("rag.optimization_rag._get_pinecone_index")
    def test_retrieves_and_formats_context(self, mock_index_fn, mock_encode):
        """Should return formatted context from Pinecone matches."""
        from rag.optimization_rag import retrieve_context

        # Mock embedding
        mock_encode.return_value = [0.1] * 384

        # Mock Pinecone results
        mock_index = MagicMock()
        mock_index_fn.return_value = mock_index
        mock_index.query.return_value = {
            "matches": [
                {
                    "id": "doc1",
                    "score": 0.92,
                    "metadata": {
                        "text": "Right-sizing is the process of matching instance types...",
                        "source": "AWS Well-Architected",
                        "service": "EC2",
                    },
                },
                {
                    "id": "doc2",
                    "score": 0.85,
                    "metadata": {
                        "text": "Use CloudWatch to monitor CPU utilization...",
                        "source": "AWS Best Practices",
                        "service": "EC2",
                    },
                },
            ]
        }

        anomaly = Anomaly(
            service="EC2",
            issue_type=AnomalyType.IDLE_RESOURCE,
            current_cost=420.0,
        )

        context = retrieve_context(anomaly, top_k=5)

        assert "Right-sizing" in context
        assert "CloudWatch" in context
        assert "AWS Well-Architected" in context
        assert "0.92" in context  # Score should be in output

    @patch("rag.optimization_rag._encode_query")
    @patch("rag.optimization_rag._get_pinecone_index")
    def test_fallback_on_empty_results(self, mock_index_fn, mock_encode):
        """Should return fallback context when Pinecone returns no matches."""
        from rag.optimization_rag import retrieve_context

        mock_encode.return_value = [0.1] * 384
        mock_index = MagicMock()
        mock_index_fn.return_value = mock_index
        mock_index.query.return_value = {"matches": []}

        anomaly = Anomaly(
            service="EC2",
            issue_type=AnomalyType.IDLE_RESOURCE,
            current_cost=100.0,
        )

        context = retrieve_context(anomaly)

        # Should get fallback EC2 tips
        assert "EC2" in context or "instance" in context.lower()
        assert len(context) > 20

    @patch("rag.optimization_rag._encode_query")
    @patch("rag.optimization_rag._get_pinecone_index")
    def test_fallback_on_pinecone_error(self, mock_index_fn, mock_encode):
        """Should return fallback context when Pinecone throws an error."""
        from rag.optimization_rag import retrieve_context

        mock_encode.return_value = [0.1] * 384
        mock_index = MagicMock()
        mock_index_fn.return_value = mock_index
        mock_index.query.side_effect = Exception("Connection refused")

        anomaly = Anomaly(
            service="RDS",
            issue_type=AnomalyType.COST_SPIKE,
            current_cost=500.0,
        )

        context = retrieve_context(anomaly)

        # Should get fallback RDS tips
        assert "RDS" in context or "database" in context.lower()

    def test_fallback_context_for_unknown_service(self):
        """Fallback context should still work for unrecognized services."""
        from rag.optimization_rag import _fallback_context

        anomaly = Anomaly(
            service="CustomService",
            issue_type=AnomalyType.COST_SPIKE,
            current_cost=100.0,
        )

        context = _fallback_context(anomaly)
        assert "Well-Architected" in context


# ──────────────────────────────────────────────────────────────────────────────
# Batch Retrieval Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestBatchRetrieval:
    """Tests for batch context retrieval."""

    @patch("rag.optimization_rag.retrieve_context")
    def test_batch_retrieves_all(self, mock_retrieve):
        """Should retrieve context for every anomaly in the batch."""
        from rag.optimization_rag import retrieve_contexts_batch

        mock_retrieve.return_value = "mock context"

        anomalies = [
            Anomaly(service="EC2", issue_type=AnomalyType.IDLE_RESOURCE, current_cost=100.0, resource_id="i-123"),
            Anomaly(service="RDS", issue_type=AnomalyType.COST_SPIKE, current_cost=500.0),
        ]

        results = retrieve_contexts_batch(anomalies)

        assert len(results) == 2
        assert mock_retrieve.call_count == 2
