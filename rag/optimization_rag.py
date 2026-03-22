"""
RAG retrieval system for Cloud Cost Optimizer.

Queries the Pinecone vector database with anomaly-derived queries and returns
relevant optimization context — documentation, Terraform snippets, and
historical optimization records — to ground Claude's analysis.

Usage
-----
    from rag.optimization_rag import retrieve_context
    context = retrieve_context(anomaly, top_k=5)
"""

from __future__ import annotations

import logging
from typing import Any

from config.settings import settings
from detect.models import Anomaly

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Query Building
# ──────────────────────────────────────────────────────────────────────────────


def build_query(anomaly: Anomaly) -> str:
    """
    Build a natural-language search query from an anomaly object.

    The query is designed to match relevant cost-optimization documentation,
    Terraform modules, and historical optimization records in the vector DB.

    Examples
    --------
    >>> build_query(Anomaly(service="EC2", issue_type=AnomalyType.IDLE_RESOURCE, ...))
    "EC2 idle resource optimization cpu utilization 1.2% instance m5.xlarge cost reduction"
    """
    parts = [
        anomaly.service,
        anomaly.issue_type.value.replace("_", " "),
        "optimization",
        "cost reduction",
    ]

    # Add metric details for richer matching
    metrics = anomaly.metrics
    if "cpu_utilization" in metrics:
        parts.append(f"cpu utilization {metrics['cpu_utilization']}%")
    if "instance_type" in metrics:
        parts.append(f"instance {metrics['instance_type']}")
    if "state" in metrics:
        parts.append(f"{metrics['state']} instance")
    if anomaly.waste_score > 0:
        parts.append(f"waste score {anomaly.waste_score}")

    query = " ".join(parts)
    logger.debug("Built RAG query: %s", query)
    return query


# ──────────────────────────────────────────────────────────────────────────────
# Pinecone Retrieval
# ──────────────────────────────────────────────────────────────────────────────


def _get_pinecone_index():
    """Return the Pinecone index for querying."""
    from pinecone import Pinecone

    pc = Pinecone(api_key=settings.PINECONE_API_KEY)
    return pc.Index(settings.PINECONE_INDEX_NAME)


def _encode_query(query: str) -> list[float]:
    """Encode a query string to a dense vector using the same model as the embedder."""
    from rag.embedder import encode_texts

    vectors = encode_texts([query])
    return vectors[0]


def retrieve_context(
    anomaly: Anomaly,
    top_k: int = 5,
) -> str:
    """
    Retrieve relevant optimization context for an anomaly.

    (MOCKED FOR DEMO VERSION)

    Parameters
    ----------
    anomaly : Anomaly
        The detected anomaly to find context for.
    top_k : int
        Number of top results to return (default 5).

    Returns
    -------
    str
        Formatted context string ready for inclusion in a Claude prompt.
    """
    query_text = build_query(anomaly)
    logger.info("Mocking RAG context retrieval for query: %s", query_text)
    return _fallback_context(anomaly)


def _fallback_context(anomaly: Anomaly) -> str:
    """
    Provide basic fallback context when Pinecone is unavailable.

    This ensures Claude always has *some* grounding information.
    """
    fallback_tips = {
        "EC2": (
            "General EC2 optimization tips:\n"
            "- Right-size instances based on CPU/memory utilization\n"
            "- Use Reserved Instances or Savings Plans for steady-state workloads\n"
            "- Terminate idle instances (CPU < 5%)\n"
            "- Consider Graviton instances for 40% savings\n"
            "- Use Auto Scaling for variable workloads"
        ),
        "RDS": (
            "General RDS optimization tips:\n"
            "- Stop dev/test instances outside business hours\n"
            "- Use Reserved Instances for production databases\n"
            "- Consider Aurora Serverless for variable workloads\n"
            "- Enable storage autoscaling\n"
            "- Delete unused snapshots"
        ),
        "S3": (
            "General S3 optimization tips:\n"
            "- Implement lifecycle policies (Standard → IA → Glacier)\n"
            "- Enable Intelligent-Tiering for unpredictable access patterns\n"
            "- Delete incomplete multipart uploads\n"
            "- Use S3 Storage Lens for visibility"
        ),
    }

    return fallback_tips.get(
        anomaly.service,
        "Review AWS Well-Architected Framework Cost Optimization Pillar for best practices.",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Batch Context Retrieval
# ──────────────────────────────────────────────────────────────────────────────


def retrieve_contexts_batch(
    anomalies: list[Anomaly],
    top_k: int = 5,
) -> dict[str, str]:
    """
    Retrieve context for multiple anomalies.

    Returns a dict mapping ``anomaly_id`` → ``context_string``.
    The anomaly_id is ``{service}_{resource_id}`` or ``{service}_{issue_type}``.
    """
    contexts: dict[str, str] = {}

    for anomaly in anomalies:
        key = f"{anomaly.service}_{anomaly.resource_id or anomaly.issue_type.value}"
        contexts[key] = retrieve_context(anomaly, top_k=top_k)

    return contexts
