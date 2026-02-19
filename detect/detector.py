"""
Anomaly detector for Cloud Cost Optimizer.

Runs hourly via APScheduler.  Executes two detection strategies against
InfluxDB and returns a list of :class:`Anomaly` objects:

1. **Cost spike detection** — 2-sigma rule on 30-day daily costs per service.
2. **Waste pattern detection** — threshold on ``waste_score > 70`` from the
   latest EC2 resource data.

Usage
-----
    python -m detect.detector          # one-shot run
    from detect.detector import run_detection
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
from influxdb_client import InfluxDBClient

from config.settings import settings
from detect.models import Anomaly, AnomalyType

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# InfluxDB helpers
# ──────────────────────────────────────────────────────────────────────────────


def _query_api() -> Any:
    """Return an InfluxDB query API."""
    client = InfluxDBClient(
        url=settings.INFLUX_URL,
        token=settings.INFLUX_TOKEN,
        org=settings.INFLUX_ORG,
    )
    return client, client.query_api()


# ──────────────────────────────────────────────────────────────────────────────
# 1. Cost Spike Detection (2-sigma)
# ──────────────────────────────────────────────────────────────────────────────

_COST_SPIKE_QUERY = """
from(bucket: "{bucket}")
  |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "aws_costs" and r._field == "cost")
  |> group(columns: ["service"])
  |> aggregateWindow(every: 1d, fn: sum, createEmpty: false)
  |> yield(name: "daily_costs")
"""


def detect_cost_spikes() -> list[Anomaly]:
    """
    Detect services with daily cost spikes exceeding 2 standard deviations
    above their 30-day mean.

    Returns a list of :class:`Anomaly` objects of type ``COST_SPIKE``.
    """
    client, query = _query_api()
    flux = _COST_SPIKE_QUERY.format(bucket=settings.INFLUX_BUCKET)

    try:
        tables = query.query(flux)
    except Exception as exc:
        logger.error("Cost spike query failed: %s", exc)
        client.close()
        return []

    # Group daily costs by service
    service_costs: dict[str, list[float]] = {}
    for table in tables:
        for record in table.records:
            service = record.values.get("service", "unknown")
            cost = float(record.get_value())
            service_costs.setdefault(service, []).append(cost)

    anomalies: list[Anomaly] = []
    for service, costs in service_costs.items():
        if len(costs) < 7:
            # Need at least a week of data for meaningful stats
            continue

        arr = np.array(costs)
        mean = float(np.mean(arr))
        std = float(np.std(arr))
        latest = costs[-1]

        threshold = mean + 2 * std
        if latest > threshold and std > 0:
            anomaly = Anomaly(
                service=service,
                issue_type=AnomalyType.COST_SPIKE,
                current_cost=latest,
                expected_cost=round(mean, 2),
                metrics={
                    "mean_30d": round(mean, 2),
                    "std_dev": round(std, 2),
                    "threshold": round(threshold, 2),
                    "days_analyzed": len(costs),
                },
            )
            logger.warning("Cost spike detected: %s", anomaly)
            anomalies.append(anomaly)

    client.close()
    logger.info("Cost spike detection found %d anomalies", len(anomalies))
    return anomalies


# ──────────────────────────────────────────────────────────────────────────────
# 2. Waste Pattern Detection (threshold-based)
# ──────────────────────────────────────────────────────────────────────────────

_WASTE_QUERY = """
from(bucket: "{bucket}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "ec2_resources")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> filter(fn: (r) => r.waste_score > 70)
  |> yield(name: "waste_resources")
"""


def detect_waste_patterns() -> list[Anomaly]:
    """
    Detect EC2 instances with waste scores exceeding 70 in the last 24 hours.

    Returns a list of :class:`Anomaly` objects of type ``IDLE_RESOURCE``,
    ``OVERPROVISIONED``, or ``STOPPED_BUT_BILLED``.
    """
    client, query = _query_api()
    flux = _WASTE_QUERY.format(bucket=settings.INFLUX_BUCKET)

    try:
        tables = query.query(flux)
    except Exception as exc:
        logger.error("Waste pattern query failed: %s", exc)
        client.close()
        return []

    anomalies: list[Anomaly] = []
    for table in tables:
        for record in table.records:
            values = record.values

            waste_score = int(values.get("waste_score", 0))
            cpu_util = float(values.get("cpu_utilization", 0.0))
            state = values.get("state", "unknown")
            instance_type = values.get("instance_type", "unknown")

            # Classify the specific issue type
            if state == "stopped":
                issue = AnomalyType.STOPPED_BUT_BILLED
            elif cpu_util < 5:
                issue = AnomalyType.IDLE_RESOURCE
            else:
                issue = AnomalyType.OVERPROVISIONED

            anomaly = Anomaly(
                service="EC2",
                resource_id=values.get("instance_id", ""),
                issue_type=issue,
                current_cost=float(values.get("cost", 0.0)),
                waste_score=waste_score,
                account=values.get("account", ""),
                region=values.get("region", ""),
                metrics={
                    "cpu_utilization": cpu_util,
                    "instance_type": instance_type,
                    "state": state,
                },
            )
            logger.warning("Waste pattern detected: %s", anomaly)
            anomalies.append(anomaly)

    client.close()
    logger.info("Waste detection found %d anomalies", len(anomalies))
    return anomalies


# ──────────────────────────────────────────────────────────────────────────────
# Combined Detection
# ──────────────────────────────────────────────────────────────────────────────


def run_detection() -> list[Anomaly]:
    """
    Run all detection strategies and return a combined list of anomalies.

    Called hourly by the scheduler.
    """
    logger.info("Starting anomaly detection pass")

    anomalies: list[Anomaly] = []
    anomalies.extend(detect_cost_spikes())
    anomalies.extend(detect_waste_patterns())

    logger.info(
        "Detection complete: %d total anomalies (%d spikes, %d waste)",
        len(anomalies),
        sum(1 for a in anomalies if a.issue_type == AnomalyType.COST_SPIKE),
        sum(1 for a in anomalies if a.issue_type != AnomalyType.COST_SPIKE),
    )
    return anomalies


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    results = run_detection()
    for anomaly in results:
        print(anomaly.to_dict())
