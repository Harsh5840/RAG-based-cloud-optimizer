# InfluxDB Schema — Cloud Cost Optimizer

## Bucket

| Name | Retention |
|------|-----------|
| `cloud-costs` | 90 days |

---

## Measurements

### `aws_costs`

Daily aggregated AWS cost data per service and account.

| Type | Name | Description |
|------|------|-------------|
| **Tag** | `service` | AWS service name (e.g. `EC2`, `RDS`, `S3`) |
| **Tag** | `account` | AWS linked account ID |
| **Tag** | `region` | AWS region |
| **Field** | `cost` | Daily unblended cost in USD (float) |
| **Field** | `usage_quantity` | Usage amount in service-native units (float) |

### `ec2_resources`

Per-instance EC2 resource metrics and waste scores.

| Type | Name | Description |
|------|------|-------------|
| **Tag** | `instance_id` | EC2 instance ID (e.g. `i-0abc123def456`) |
| **Tag** | `instance_type` | Instance type (e.g. `m5.xlarge`) |
| **Tag** | `account` | AWS linked account ID |
| **Tag** | `region` | AWS region |
| **Tag** | `state` | Instance state (`running`, `stopped`) |
| **Field** | `cpu_utilization` | Average CPU utilization % (float) |
| **Field** | `cost` | Estimated monthly cost in USD (float) |
| **Field** | `waste_score` | Computed waste score 0-100 (int) |

### `gcp_costs`

Daily aggregated GCP cost data per service and project.

| Type | Name | Description |
|------|------|-------------|
| **Tag** | `service` | GCP service name (e.g. `Compute Engine`) |
| **Tag** | `project` | GCP project ID |
| **Tag** | `region` | GCP region |
| **Field** | `cost` | Daily cost in USD (float) |
| **Field** | `usage_quantity` | Usage in service-native units (float) |
