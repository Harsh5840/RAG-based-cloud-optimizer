# 02 - Data Ingestion

The `ingest/` directory is responsible for hitting the cloud provider billing APIs, fetching cost/utilization metrics, calculating local waste scores, and ingesting everything safely into InfluxDB.

## 1. AWS Ingestion (`ingest/ingest.py`)

Run daily by the APScheduler via `run_ingestion()`. It handles two completely different datasets:

### A. High-Level Costs (`fetch_aws_costs`)
- Uses `boto3` (`ce.get_cost_and_usage`)
- Looks over a rolling 30-day window (`TimePeriod`).
- Metric: `UnblendedCost` & `UsageQuantity`.
- Grouped by `SERVICE` and `LINKED_ACCOUNT`.
- Output: Daily time-series metrics per service.

### B. EC2 Drill-Down (`fetch_ec2_instances`)
- Iterates over **all** EC2 instances natively (`ec2.get_paginator('describe_instances')`).
- If an instance is "running", it queries CloudWatch (`cw.get_metric_statistics`) for average `CPUUtilization` over the last 24 hours.
- Uses `_INSTANCE_COST_MAP` for raw base cost estimates of instances to assign a dollar value.
- Calls `calculate_waste_score()` to assign a 0-100 score.

## 2. GCP Ingestion (`ingest/gcp_ingest.py`)
- Hits the Google Cloud BigQuery client to read from `gcp_billing_export_v1`.
- Because GCP exports billing to BQ, it uses standard SQL to query `SUM(cost)` grouped by service and project ID over the last X days.
- If it cannot connect to BQ (e.g. running locally without credentials), it has a robust synthetic data fallback `_generate_demo_data()` to keep testing resilient!

## 3. Waste Score Logic (`ingest/waste_score.py`)
Provides deterministic rulesets (0-100) to flag "obvious" problems efficiently without invoking an expensive LLM.
For instance:
- `CPU < 5%` and `running` = +80 points.
- `state == stopped` = +40 points (EBS volumes are still billed).
- `xlarge instances` under `30% CPU` = +60 points.

## 4. InfluxDB Client Writer
Written using `influxdb_client`'s `WriteApi(SYNCHRONOUS)`. 
Points are tagged effectively for fast cardinality search later (`tag("service", ...)`, `field("cost", ...)`).
