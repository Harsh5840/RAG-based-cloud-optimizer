# Cloud Cost Optimizer (Demo Version)

This branch contains a fully self-contained **Demo Version** of the Cloud Cost Optimizer. All external APIs (AWS, GCP, Claude, Pinecone, GitHub, Slack) have been mocked out so you can test and observe the pipeline purely locally, without needing to configure any API credentials.

## Setup Instructions

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the local Time-Series Database (InfluxDB):**
   ```bash
   docker-compose up -d influxdb
   ```
   *Wait a few seconds for InfluxDB to initialize.*

3. **Run the Demo Pipeline:**
   We have provided a convenient `demo_run.py` script that immediately triggers the Ingestion Phase, followed by the Anomaly Detection and Action phases.
   ```bash
   python demo_run.py
   ```

## What to Expect in the Demo
When you run `demo_run.py`:
- **Ingestion:** Synthetic AWS cost and EC2 resource data is generated with intentional artificial spikes and waste patterns.
- **Detection:** The pipeline queries InfluxDB and successfully detects the synthetic anomalies.
- **RAG & Claude:** Context retrieval and Claude's optimization analysis are mocked, returning a static Terraform configuration and savings estimate.
- **Actions:** PR Creation and Slack Notifications are mocked out. You will see comprehensive logs in your terminal mimicking the action pipeline.

## Reviewing the Architecture
You can find the original full project architecture, pipeline deep dives, and system context in [PROJECT_SPEC.md](PROJECT_SPEC.md).
