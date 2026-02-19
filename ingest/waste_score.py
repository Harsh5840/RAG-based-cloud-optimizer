"""
Waste score calculator for cloud resources.

Assigns a deterministic waste score (0-100) to a cloud resource based on
utilization metrics, instance type, and current state. A score > 70 triggers
the anomaly detection pipeline.

This module is intentionally free of external dependencies — it runs locally
without any API calls or LLM inference.
"""

from __future__ import annotations


def calculate_waste_score(
    cpu_util: float,
    instance_type: str,
    state: str,
) -> int:
    """
    Calculate a waste score for an EC2-like instance.

    Parameters
    ----------
    cpu_util : float
        Average CPU utilization percentage (0-100).
    instance_type : str
        Instance type string, e.g. ``"m5.xlarge"``.
    state : str
        Instance state: ``"running"``, ``"stopped"``, etc.

    Returns
    -------
    int
        Waste score clamped to 0-100.  Higher = more waste.

    Scoring Rules
    -------------
    * **Idle running** — ``state == "running"`` and ``cpu < 5%`` → +80
    * **Overprovisioned** — ``cpu < 20%`` → +50
    * **Expensive + low use** — ``"xlarge"`` in type and ``cpu < 30%`` → +60
    * **Stopped but billed** — ``state == "stopped"`` → +40  (EBS still billed)
    """
    score = 0

    # Idle but still running — worst offender
    if state == "running" and cpu_util < 5:
        score += 80

    # General overprovisioning
    if cpu_util < 20:
        score += 50

    # Expensive instance type with low usage
    if "xlarge" in instance_type.lower() and cpu_util < 30:
        score += 60

    # Stopped instance — EBS volumes are still billed
    if state == "stopped":
        score += 40

    return min(score, 100)


def classify_waste(score: int) -> str:
    """Return a human-readable waste classification.

    Returns one of: ``"critical"``, ``"high"``, ``"medium"``, ``"low"``, ``"none"``.
    """
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "low"
    return "none"
