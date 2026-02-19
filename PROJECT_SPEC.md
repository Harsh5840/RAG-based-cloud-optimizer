# Cloud Cost Optimizer

**Project 16 • Full Build Guide • Architecture, Folder Structure & Implementation**

## 1. Project Overview

Cloud bills spiral out of control because engineers lack visibility into what is expensive and have no clear path to fix it. This project builds a fully automated system that ingests cloud billing data, detects cost anomalies and waste, uses a RAG knowledge base to find optimizations, generates Terraform code for the fix, and opens a GitHub PR — all without human intervention.

### Core Problem → Solution

| | |
|---|---|
| **Problem** | Engineers receive a massive monthly bill with no insight into which services or resources are wasting money. |
| **Solution** | An automated pipeline that detects anomalies hourly, looks up best practices via RAG, generates IaC fixes, and creates pull requests with savings estimates. |

**Tech Stack:** Python 3.11+ • InfluxDB / TimescaleDB • Pinecone (Vector DB) • Claude API • Terraform / GitHub • APScheduler

---

## 2. System Architecture

The system is a six-stage pipeline where each layer feeds the next.

### Stage 1 — Cloud Provider APIs
- AWS Cost Explorer API (`get_cost_and_usage`)
- GCP Billing API
- Per-service, per-account, per-resource breakdown
- Daily granularity with 30-day history

### Stage 2 — Data Ingestion Service
- Fetch billing data on a daily cron schedule
- Normalize cost data across AWS and GCP schemas
- Calculate rolling trends and waste scores
- Write normalized points into InfluxDB

### Stage 3 — Time-Series Database
- InfluxDB or TimescaleDB for efficient time-series writes
- Measurements: `aws_costs`, `ec2_resources`, `gcp_costs`
- Tags: `service`, `account`, `region`, `instance_id`, `instance_type`
- Fields: `cost` ($), `usage` quantity, `waste_score` (0-100)

### Stage 4 — RAG System (Knowledge Base)
- Pinecone vector database for semantic search
- Indexed sources: AWS Well-Architected docs, cost optimization guides, past successful optimizations, Terraform modules
- Embedding model: `all-MiniLM-L6-v2` (384-dim vectors)
- Filter by service tag to improve retrieval precision

### Stage 5 — Claude Analysis Engine
- Receives anomaly objects from the detector
- Queries RAG to pull relevant optimization context
- Calls Claude API with anomaly + context prompt
- Returns: root cause, actions, Terraform code, savings estimate, risk level, rollback plan

### Stage 6 — Action Pipeline
- Creates a feature branch in the infra GitHub repo
- Commits generated Terraform HCL to the correct file path
- Opens a pull request with a structured description
- Posts Slack notification with savings amount and PR link

---

## 2.1 Data Flow Diagram

```
Cloud Provider APIs  (AWS Cost Explorer / GCP Billing)
       ↓  Daily pull via boto3 / google-cloud-billing
Data Ingestion Service  (ingest.py)
       ↓  Normalized Point objects
Time-Series DB  (InfluxDB :8086)
       ↓  Flux query for 30-day window
Anomaly Detector  (detector.py)  ← runs hourly
       ↓  Anomaly dict: {service, resource_id, issue_type, metrics, cost}
RAG Retrieval  (optimization_rag.py)  ← queries Pinecone
       ↓  Top-5 relevant doc chunks
Claude Analysis Engine  (claude API call)
       ↓  Recommendation + Terraform HCL
Action Pipeline  (GitHub PR + Slack notify)
```

---

## 3. Folder Structure

```
cloud-cost-optimizer/
├── ingest/
│   ├── __init__.py
│   ├── ingest.py           # AWS Cost Explorer + EC2 data → InfluxDB
│   ├── gcp_ingest.py       # GCP Billing API equivalent
│   └── waste_score.py      # Waste scoring logic
├── rag/
│   ├── __init__.py
│   ├── optimization_rag.py # Pinecone index + retrieval
│   ├── scraper.py          # Scrapes AWS Well-Architected docs
│   └── embedder.py         # SentenceTransformer wrapper
├── detect/
│   ├── __init__.py
│   ├── detector.py         # Flux queries for cost spikes + waste
│   └── models.py           # Anomaly dataclass definitions
├── actions/
│   ├── __init__.py
│   ├── terraform_gen.py    # Claude → Terraform HCL generation
│   ├── github_pr.py        # Branch + commit + PR via PyGithub
│   └── slack_notify.py     # Slack webhook notifications
├── scheduler/
│   ├── __init__.py
│   └── scheduler.py        # APScheduler orchestration
├── config/
│   ├── __init__.py
│   ├── settings.py         # Env var reader
│   └── influx_schema.md    # InfluxDB schema documentation
├── terraform/
│   ├── ec2_rightsizing/     # Sample Terraform module
│   └── rds_scheduler/      # Sample Terraform module
├── tests/
│   ├── __init__.py
│   ├── test_ingest.py
│   ├── test_detector.py
│   └── test_rag.py
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## 4. Component Deep Dives

### 4.1 Data Ingestion (`ingest/ingest.py`)

Entry point of the pipeline. Runs on daily cron, pulls 30 days of unblended costs from AWS Cost Explorer grouped by service and linked account. Drills down to individual EC2 instances, calculates waste score, writes to InfluxDB.

**Key design decisions:**
- Use `UnblendedCost` (not `BlendedCost`)
- `GroupBy SERVICE + LINKED_ACCOUNT`
- Waste score calculated locally (no LLM)
- InfluxDB tags indexed for fast Flux queries

**Waste Score Logic:**
```python
def calculate_waste_score(cpu_util, instance_type, state):
    score = 0
    if state == 'running' and cpu_util < 5:   score += 80
    if cpu_util < 20:                          score += 50
    if 'xlarge' in instance_type and cpu_util < 30: score += 60
    if state == 'stopped':                     score += 40
    return min(score, 100)
```

### 4.2 RAG Knowledge Base (`rag/optimization_rag.py`)

Indexes three types of content into Pinecone, retrieves top-5 chunks per anomaly.

**Indexed Sources:**
1. AWS Well-Architected Framework — Cost Optimization Pillar
2. AWS Cost Optimization Terraform Modules
3. Historical Optimizations (past PRs)

**Retrieval Flow:**
1. Build query string from anomaly object
2. Encode with `all-MiniLM-L6-v2`
3. Query Pinecone (top_k=5, filter by service)
4. Concatenate chunk texts into context block
5. Pass to Claude with structured prompt

### 4.3 Anomaly Detector (`detect/detector.py`)

Runs hourly via APScheduler. Two detection modes:
- **Cost Spike:** 2-sigma rule on 30-day daily costs per service
- **Waste Pattern:** threshold on `waste_score > 70`

### 4.4 Claude Analysis Engine

Structured prompt with anomaly data + RAG context → returns root cause, Terraform code, savings estimate, risk level, rollback plan.

### 4.5 Action Pipeline (`actions/`)

1. Create branch: `cost-opt/{service}-{resource_id}`
2. Commit Terraform HCL
3. Open PR with savings/risk/rollback in body
4. Post Slack notification

---

## 5. Environment Variables

| Variable | Description |
|---|---|
| `AWS_ACCESS_KEY_ID` | AWS credentials |
| `AWS_SECRET_ACCESS_KEY` | AWS secret |
| `AWS_DEFAULT_REGION` | e.g. `us-east-1` |
| `INFLUX_URL` | e.g. `http://localhost:8086` |
| `INFLUX_TOKEN` | InfluxDB API token |
| `INFLUX_ORG` | InfluxDB organization |
| `PINECONE_API_KEY` | Pinecone API key |
| `PINECONE_ENVIRONMENT` | e.g. `us-east-1-aws` |
| `ANTHROPIC_API_KEY` | Claude API key |
| `GITHUB_TOKEN` | PAT with repo scope |
| `GITHUB_REPO` | `owner/repo-name` |
| `SLACK_WEBHOOK_URL` | Slack webhook URL |
