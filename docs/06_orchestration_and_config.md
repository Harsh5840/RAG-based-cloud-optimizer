# 06 - Orchestration & Configuration

To tie all the independent services together, the project requires an execution engine. We do not use Airflow; instead, we use a lightweight thread-blocking scheduler.

## 1. APScheduler Integration (`scheduler.py`)

The entire system lifecycle runs inside `scheduler.py` via `BlockingScheduler`, listening for SIGINT and SIGTERM OS signals to perform graceful shutdown.

### Scheduled Jobs:

#### A. Daily Data Refresher (`ingest_job`)
**Trigger:** `CronTrigger(hour=2, minute=0)` (2:00 AM UTC).
It executes `run_ingestion()` and `run_gcp_ingestion()` to pull the prior days total expenses into InfluxDB. It is wrapped in isolated Try-Excepts to ensure the failure of AWS doesn't stall GCP.

#### B. Active Detective Engine (`detection_job`)
**Trigger:** `IntervalTrigger(hours=1)` (Hourly).
Every hour it looks over the new metrics to find waste spikes.
If `Anomalies` exist, it iterates over them gracefully:
`_process_anomaly()` sequentially calls RAG -> Claude -> GitHub -> Slack. 
Once finished processing all hourly anomalies, it tallies the entire cost-savings and fires `send_summary_notification()` to Slack as a daily aggregate digest.

## 2. Environment Configuration (`config/settings.py`)

The application embraces standard 12-factor App principles, exclusively using environment variables read from the local `.env`. It reads through standard libraries (`python-dotenv` & `os.getenv`).

If any application configuration value is required across multiple submodules—meaning both ingestion and RAG need the AWS Region—both files import `config.settings.settings`, acting as a global read-only dataclass preventing spaghetti variables.
