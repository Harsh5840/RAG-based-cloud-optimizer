"""
AWS data ingestion for Cloud Cost Optimizer.

Pulls 30 days of unblended costs from AWS Cost Explorer grouped by service and
linked account, drills down to individual EC2 instances with CloudWatch CPU
metrics, calculates waste scores, and writes structured time-series points to
InfluxDB.

Usage
-----
    python -m ingest.ingest          # one-shot run
    from ingest.ingest import run_ingestion  # programmatic
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

from config.settings import settings
from ingest.waste_score import calculate_waste_score

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# AWS Cost Explorer
# ──────────────────────────────────────────────────────────────────────────────


def _ce_client() -> Any:
    """Return a boto3 Cost Explorer client."""
    return boto3.client(
        "ce",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_DEFAULT_REGION,
    )


def fetch_aws_costs(days: int = 30) -> list[dict]:
    """
    Fetch daily unblended costs from AWS Cost Explorer.

    Groups results by SERVICE and LINKED_ACCOUNT for per-account chargeback
    visibility.

    Returns a list of dicts:
        ``[{service, account, date, cost, currency}, ...]``
    """
    ce = _ce_client()
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)

    logger.info("Fetching AWS costs from %s to %s", start, end)

    response = ce.get_cost_and_usage(
        TimePeriod={"Start": str(start), "End": str(end)},
        Granularity="DAILY",
        Metrics=["UnblendedCost", "UsageQuantity"],
        GroupBy=[
            {"Type": "DIMENSION", "Key": "SERVICE"},
            {"Type": "DIMENSION", "Key": "LINKED_ACCOUNT"},
        ],
    )

    records: list[dict] = []
    for result_by_time in response.get("ResultsByTime", []):
        date_str = result_by_time["TimePeriod"]["Start"]
        for group in result_by_time.get("Groups", []):
            keys = group["Keys"]
            metrics = group["Metrics"]
            records.append(
                {
                    "service": keys[0],
                    "account": keys[1] if len(keys) > 1 else "unknown",
                    "date": date_str,
                    "cost": float(metrics["UnblendedCost"]["Amount"]),
                    "usage_quantity": float(metrics["UsageQuantity"]["Amount"]),
                    "currency": metrics["UnblendedCost"]["Unit"],
                }
            )

    logger.info("Fetched %d cost records", len(records))
    return records


# ──────────────────────────────────────────────────────────────────────────────
# EC2 Instance Metrics + Waste Score
# ──────────────────────────────────────────────────────────────────────────────


def _ec2_client() -> Any:
    """Return a boto3 EC2 client."""
    return boto3.client(
        "ec2",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_DEFAULT_REGION,
    )


def _cloudwatch_client() -> Any:
    """Return a boto3 CloudWatch client."""
    return boto3.client(
        "cloudwatch",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_DEFAULT_REGION,
    )


def _get_cpu_utilization(cw: Any, instance_id: str, hours: int = 24) -> float:
    """Query CloudWatch for average CPU utilization over the last *hours*."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    response = cw.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start,
        EndTime=end,
        Period=3600,
        Statistics=["Average"],
    )
    datapoints = response.get("Datapoints", [])
    if not datapoints:
        return 0.0
    return sum(dp["Average"] for dp in datapoints) / len(datapoints)


# Rough monthly cost estimates by instance type family (USD)
_INSTANCE_COST_MAP: dict[str, float] = {
    "t2.micro": 8.50,
    "t2.small": 17.00,
    "t2.medium": 34.00,
    "t3.micro": 7.60,
    "t3.small": 15.20,
    "t3.medium": 30.40,
    "m5.large": 70.00,
    "m5.xlarge": 140.00,
    "m5.2xlarge": 280.00,
    "m5.4xlarge": 560.00,
    "c5.large": 62.00,
    "c5.xlarge": 124.00,
    "c5.2xlarge": 248.00,
    "r5.large": 91.00,
    "r5.xlarge": 182.00,
    "r5.2xlarge": 364.00,
}


def fetch_ec2_instances() -> list[dict]:
    """
    Describe all EC2 instances, fetch CPU utilization, and compute waste score.

    Returns a list of dicts ready for InfluxDB:
        ``[{instance_id, instance_type, state, region, cpu_util, cost, waste_score}, ...]``
    """
    ec2 = _ec2_client()
    cw = _cloudwatch_client()

    paginator = ec2.get_paginator("describe_instances")
    instances: list[dict] = []

    for page in paginator.paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                instance_id = inst["InstanceId"]
                instance_type = inst["InstanceType"]
                state = inst["State"]["Name"]
                region = settings.AWS_DEFAULT_REGION

                cpu_util = _get_cpu_utilization(cw, instance_id) if state == "running" else 0.0

                estimated_cost = _INSTANCE_COST_MAP.get(instance_type, 100.0)
                waste = calculate_waste_score(cpu_util, instance_type, state)

                instances.append(
                    {
                        "instance_id": instance_id,
                        "instance_type": instance_type,
                        "state": state,
                        "region": region,
                        "account": inst.get("OwnerId", "unknown"),
                        "cpu_utilization": round(cpu_util, 2),
                        "cost": estimated_cost,
                        "waste_score": waste,
                    }
                )

    logger.info("Fetched %d EC2 instances", len(instances))
    return instances


# ──────────────────────────────────────────────────────────────────────────────
# InfluxDB Writer
# ──────────────────────────────────────────────────────────────────────────────


def _influx_client() -> InfluxDBClient:
    """Return an InfluxDB client configured from settings."""
    return InfluxDBClient(
        url=settings.INFLUX_URL,
        token=settings.INFLUX_TOKEN,
        org=settings.INFLUX_ORG,
    )


def write_cost_points(records: list[dict]) -> int:
    """
    Write cost records to the ``aws_costs`` measurement in InfluxDB.

    Returns the number of points written.
    """
    client = _influx_client()
    write_api = client.write_api(write_options=SYNCHRONOUS)

    points: list[Point] = []
    for rec in records:
        p = (
            Point("aws_costs")
            .tag("service", rec["service"])
            .tag("account", rec["account"])
            .tag("region", settings.AWS_DEFAULT_REGION)
            .field("cost", rec["cost"])
            .field("usage_quantity", rec.get("usage_quantity", 0.0))
            .time(
                datetime.strptime(rec["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc),
                WritePrecision.S,
            )
        )
        points.append(p)

    write_api.write(bucket=settings.INFLUX_BUCKET, record=points)
    client.close()

    logger.info("Wrote %d cost points to InfluxDB", len(points))
    return len(points)


def write_ec2_points(instances: list[dict]) -> int:
    """
    Write EC2 resource records to the ``ec2_resources`` measurement in InfluxDB.

    Returns the number of points written.
    """
    client = _influx_client()
    write_api = client.write_api(write_options=SYNCHRONOUS)

    points: list[Point] = []
    for inst in instances:
        p = (
            Point("ec2_resources")
            .tag("instance_id", inst["instance_id"])
            .tag("instance_type", inst["instance_type"])
            .tag("account", inst["account"])
            .tag("region", inst["region"])
            .tag("state", inst["state"])
            .field("cpu_utilization", inst["cpu_utilization"])
            .field("cost", inst["cost"])
            .field("waste_score", inst["waste_score"])
            .time(datetime.now(timezone.utc), WritePrecision.S)
        )
        points.append(p)

    write_api.write(bucket=settings.INFLUX_BUCKET, record=points)
    client.close()

    logger.info("Wrote %d EC2 points to InfluxDB", len(points))
    return len(points)


# ──────────────────────────────────────────────────────────────────────────────
# Public Entry Point
# ──────────────────────────────────────────────────────────────────────────────


def run_ingestion() -> dict[str, int]:
    """
    Execute a full AWS ingestion cycle.

    1. Fetch 30 days of costs from Cost Explorer.
    2. Fetch EC2 instances with CPU utilization and waste scores.
    3. Write everything to InfluxDB.

    Returns a summary dict of point counts.
    """
    logger.info("Starting AWS ingestion cycle")

    # Cost data
    cost_records = fetch_aws_costs(days=30)
    cost_count = write_cost_points(cost_records)

    # EC2 instance data
    ec2_records = fetch_ec2_instances()
    ec2_count = write_ec2_points(ec2_records)

    summary = {"cost_points": cost_count, "ec2_points": ec2_count}
    logger.info("Ingestion complete: %s", summary)
    return summary


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    result = run_ingestion()
    print(f"Ingestion complete: {result}")
