# 03 - Anomaly Detection Engine

The `detect/` directory handles identifying anomalies. Now that all time-series data sits perfectly inside InfluxDB, the anomaly detector runs every 60 minutes (`detector.py`). 

## Key Models (`detect/models.py`)

Every detected spike generates an `Anomaly` dataclass.
```python
@dataclass
class Anomaly:
    service: str
    issue_type: AnomalyType
    current_cost: float
    resource_id: str
    waste_score: int
    ...
```
Types of anomalies (`AnomalyType`): `COST_SPIKE`, `IDLE_RESOURCE`, `OVERPROVISIONED`, `STOPPED_BUT_BILLED`, `WASTE_PATTERN`.

## Detection Strategies (`detect/detector.py`)

The application supports two primary search logic paradigms using the `Flux` query language.

### 1. Cost Spikes (2-Sigma Statistical Deviation)
`detect_cost_spikes()` issues a `Sum()` query over daily costs filtered by `aws_costs` for the last 30 days.
It groups the arrays (`service_costs`) securely and runs standard Numpy deviations:
- Calculates Mean & Standard Deviation over historical days.
- If `latest_cost > (mean + 2 * std)`, an anomaly is immediately thrown. 

### 2. Immediate Resource Waste Patterns
`detect_waste_patterns()` runs against the `ec2_resources` metrics inserted from our ingestion EC2 drilldown. 
Instead of looking at a 30-day window, it pivots the latest 24 hours of data.
- If `waste_score > 70`, we immediately classify it as problematic.
- Then, we classify the context cleanly:
  - If `state == "stopped"`, issue = `STOPPED_BUT_BILLED`.
  - If `cpu_util < 5`, issue = `IDLE_RESOURCE`.
  - Else, issue = `OVERPROVISIONED`.

Both strategies consolidate their output within `run_detection()` and return a simple list of `Anomaly` objects to the active orchestrator pipeline.
