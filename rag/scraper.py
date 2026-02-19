"""
AWS documentation scraper for the RAG knowledge base.

Scrapes the AWS Well-Architected Framework (Cost Optimization Pillar) and
related cost-optimization documentation.  Chunks the content by section and
saves to a JSON file for the embedder to process.

Usage
-----
    python -m rag.scraper
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Output path for scraped documents
_OUTPUT_DIR = Path(__file__).resolve().parent / "data"
_OUTPUT_FILE = _OUTPUT_DIR / "scraped_docs.json"

# ──────────────────────────────────────────────────────────────────────────────
# AWS Well-Architected URLs
# ──────────────────────────────────────────────────────────────────────────────

# Key pages from the AWS Cost Optimization Pillar and related guides
_SOURCE_URLS: list[dict[str, str]] = [
    {
        "url": "https://docs.aws.amazon.com/wellarchitected/latest/cost-optimization-pillar/welcome.html",
        "source": "AWS Well-Architected - Cost Optimization Pillar",
    },
    {
        "url": "https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-resize.html",
        "source": "AWS EC2 - Instance Resizing Guide",
    },
    {
        "url": "https://docs.aws.amazon.com/cur/latest/userguide/what-is-cur.html",
        "source": "AWS Cost and Usage Report",
    },
]

# Built-in knowledge chunks for when scraping is unavailable (offline dev)
_BUILTIN_KNOWLEDGE: list[dict[str, Any]] = [
    {
        "text": (
            "Right-sizing is the process of matching instance types and sizes to "
            "your workload performance and capacity requirements at the lowest "
            "possible cost. It is the most effective way to reduce AWS costs. "
            "Use CloudWatch metrics like CPUUtilization, NetworkIn/Out, and "
            "DiskReadOps to identify underutilized instances. Consider switching "
            "to Graviton-based instances for up to 40% cost savings."
        ),
        "source": "AWS Well-Architected - Right Sizing",
        "service": "EC2",
        "category": "rightsizing",
    },
    {
        "text": (
            "Reserved Instances (RIs) provide up to 72% discount compared to "
            "On-Demand pricing. Savings Plans offer similar discounts with more "
            "flexibility. Analyze your usage patterns over 30-60 days before "
            "committing. Use AWS Cost Explorer RI recommendations API."
        ),
        "source": "AWS Well-Architected - Pricing Models",
        "service": "EC2",
        "category": "pricing",
    },
    {
        "text": (
            "Identify and terminate idle resources: EC2 instances with CPU < 5%, "
            "unattached EBS volumes, idle Elastic Load Balancers, and unused "
            "Elastic IPs. These resources incur charges even when not serving "
            "traffic. Use AWS Trusted Advisor for automated idle detection."
        ),
        "source": "AWS Well-Architected - Idle Resources",
        "service": "EC2",
        "category": "idle_resources",
    },
    {
        "text": (
            "Use Auto Scaling groups with target tracking policies to match "
            "capacity to demand. Set minimum instances to handle baseline load "
            "and maximum to cap costs. Use predictive scaling for workloads "
            "with predictable traffic patterns."
        ),
        "source": "AWS Well-Architected - Auto Scaling",
        "service": "EC2",
        "category": "auto_scaling",
    },
    {
        "text": (
            "RDS cost optimization: Use reserved instances for steady-state "
            "databases. Stop dev/test instances outside business hours using "
            "Lambda + CloudWatch Events. Consider Aurora Serverless for "
            "variable workloads. Enable storage autoscaling to avoid "
            "over-provisioning."
        ),
        "source": "AWS Well-Architected - RDS Optimization",
        "service": "RDS",
        "category": "database",
    },
    {
        "text": (
            "S3 cost optimization: Implement lifecycle policies to transition "
            "objects to cheaper storage classes (Standard → IA → Glacier). "
            "Enable S3 Intelligent-Tiering for unpredictable access patterns. "
            "Delete incomplete multipart uploads. Use S3 Storage Lens for "
            "visibility into usage."
        ),
        "source": "AWS Well-Architected - S3 Optimization",
        "service": "S3",
        "category": "storage",
    },
    {
        "text": (
            "Lambda cost optimization: Right-size memory allocation — more "
            "memory means faster execution, which can actually be cheaper. "
            "Use Graviton2 for 20% lower cost. Enable Provisioned Concurrency "
            "only for latency-sensitive endpoints. Use Power Tuning tool to "
            "find optimal memory/cost configuration."
        ),
        "source": "AWS Well-Architected - Lambda Optimization",
        "service": "Lambda",
        "category": "serverless",
    },
    {
        "text": (
            "Terraform best practice for EC2 right-sizing: use a variable for "
            "instance_type and create a terraform plan that changes only the "
            "instance type. Use lifecycle { create_before_destroy = true } for "
            "zero-downtime resizing. Tag instances with cost-center and "
            "environment labels for chargeback tracking."
        ),
        "source": "Terraform Module - EC2 Right-Sizing",
        "service": "EC2",
        "category": "terraform",
    },
    {
        "text": (
            "Terraform module for RDS scheduling: Create a Lambda function "
            "triggered by CloudWatch Events at 8pm to stop RDS instances and "
            "at 8am to start them. Use the aws_db_instance_automated_backups "
            "resource. Tag instances with schedule=business-hours. "
            "Expected savings: 65% for dev/test databases."
        ),
        "source": "Terraform Module - RDS Scheduler",
        "service": "RDS",
        "category": "terraform",
    },
    {
        "text": (
            "Stopped EC2 instances still incur charges for attached EBS "
            "volumes, Elastic IPs, and any associated resources. To fully "
            "eliminate costs, create an AMI from the instance, then terminate "
            "it and delete associated EBS volumes. Recreate from AMI when "
            "needed. This approach saves 100% of compute and EBS costs."
        ),
        "source": "AWS Well-Architected - Stopped Instances",
        "service": "EC2",
        "category": "idle_resources",
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# Scraping Logic
# ──────────────────────────────────────────────────────────────────────────────


def _scrape_page(url: str, source: str) -> list[dict[str, Any]]:
    """
    Scrape a single web page, strip HTML, and chunk by section headers.

    Returns a list of document chunks:
        ``[{text, source, url, service}, ...]``
    """
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "CloudCostOptimizer/1.0"})
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Failed to scrape %s: %s", url, exc)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove script, style, nav elements
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    # Extract text, split into paragraphs
    text = soup.get_text(separator="\n", strip=True)

    # Chunk by section (split on lines that look like headers)
    chunks: list[dict[str, Any]] = []
    current_chunk: list[str] = []
    current_section = source

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Detect section headers (capitalized, short lines without punctuation)
        if len(line) < 100 and line[0].isupper() and not line.endswith("."):
            if current_chunk:
                chunk_text = " ".join(current_chunk)
                if len(chunk_text) > 50:  # Skip tiny fragments
                    chunks.append(
                        {
                            "text": chunk_text[:2000],  # Cap chunk size
                            "source": current_section,
                            "url": url,
                            "service": _detect_service(chunk_text),
                        }
                    )
                current_chunk = []
            current_section = f"{source} — {line}"

        current_chunk.append(line)

    # Flush last chunk
    if current_chunk:
        chunk_text = " ".join(current_chunk)
        if len(chunk_text) > 50:
            chunks.append(
                {
                    "text": chunk_text[:2000],
                    "source": current_section,
                    "url": url,
                    "service": _detect_service(chunk_text),
                }
            )

    logger.info("Scraped %d chunks from %s", len(chunks), url)
    return chunks


def _detect_service(text: str) -> str:
    """Heuristically detect the AWS service a text chunk relates to."""
    text_lower = text.lower()
    service_keywords = {
        "EC2": ["ec2", "instance", "ami", "ebs", "elastic compute"],
        "RDS": ["rds", "aurora", "database", "db instance"],
        "S3": ["s3", "bucket", "object storage", "glacier"],
        "Lambda": ["lambda", "serverless", "function"],
        "ECS": ["ecs", "fargate", "container"],
        "EKS": ["eks", "kubernetes"],
        "CloudFront": ["cloudfront", "cdn"],
        "ElastiCache": ["elasticache", "redis", "memcached"],
    }

    for service, keywords in service_keywords.items():
        if any(kw in text_lower for kw in keywords):
            return service
    return "General"


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────


def scrape_all_sources() -> list[dict[str, Any]]:
    """
    Scrape all configured documentation sources.

    Falls back to built-in knowledge if scraping fails or yields no results.
    Always includes built-in knowledge for reliable baseline coverage.
    """
    all_docs: list[dict[str, Any]] = []

    # Try live scraping
    for source_info in _SOURCE_URLS:
        chunks = _scrape_page(source_info["url"], source_info["source"])
        all_docs.extend(chunks)

    # Always include built-in knowledge
    all_docs.extend(_BUILTIN_KNOWLEDGE)

    logger.info("Total documents collected: %d", len(all_docs))
    return all_docs


def save_documents(docs: list[dict[str, Any]], output_path: Path | None = None) -> Path:
    """Save scraped documents to a JSON file for the embedder."""
    out = output_path or _OUTPUT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(docs, f, indent=2, ensure_ascii=False)

    logger.info("Saved %d documents to %s", len(docs), out)
    return out


def run_scraper() -> Path:
    """Full scraping pipeline: scrape → save → return output path."""
    docs = scrape_all_sources()
    return save_documents(docs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    path = run_scraper()
    print(f"Scraping complete: {path}")
